"""FastAPI router for Privacy Budget Management endpoints.

Implements the budget read and administrative refresh operations:
    - ``GET /privacy/budget``: Returns the current ledger state including
      total allocated epsilon, total spent epsilon, remaining epsilon, and
      whether the budget is exhausted.
    - ``POST /privacy/budget/refresh``: Resets the spent counter (and
      optionally sets a new allocation ceiling).  This operation emits a
      WORM HMAC-signed audit event capturing the operator identity and
      justification.

Budget exhaustion is enforced at the synthesis layer; this router only
provides visibility and the administrative refresh workflow.

All error responses use RFC 7807 Problem Details format via
:func:`synth_engine.bootstrapper.errors.problem_detail`.

Authentication: Both endpoints require a valid JWT Bearer token via the
:func:`~synth_engine.bootstrapper.dependencies.auth.get_current_operator`
dependency (ADV-024).  The authenticated operator's JWT sub claim is used
as the audit actor identity — replacing the previous ``X-Operator-Id``
header fallback.

WORM audit logging:
    Every refresh call MUST emit a signed :class:`~synth_engine.shared.security.audit.AuditEvent`
    via :func:`~synth_engine.shared.security.audit.get_audit_logger`.
    The event type is ``"PRIVACY_BUDGET_REFRESH"`` and the actor is the
    authenticated operator's JWT sub claim.

Domain delegation:
    Budget mutation is delegated to
    :func:`~synth_engine.modules.privacy.accountant.reset_budget` via an
    ``asyncio.run()`` bridge, following the same pattern as
    :func:`~synth_engine.bootstrapper.factories.build_spend_budget_fn`.
    This ensures the ledger is mutated under ``SELECT ... FOR UPDATE``
    pessimistic locking, preventing races with concurrent ``spend_budget()``
    calls.

Audit ordering (T70.8):
    Audit BEFORE mutation.  If the audit write fails, 500 is returned and
    the budget is NOT reset.  If the reset fails AFTER the audit succeeds,
    a compensating ``BUDGET_RESET_FAILED`` event is emitted and 500 returned.

Import boundaries:
    ``bootstrapper/`` CAN import from ``modules/privacy/`` (allowed direction).
    Do NOT import from other modules.

Task: P22-T22.4 — Budget Management API
Task: T70.8 — Audit-before-mutation ordering standardisation
Task: T70.9 — AUDIT_WRITE_FAILURE_TOTAL Prometheus counter
CONSTITUTION Priority 0: Security — WORM audit emission
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from synth_engine.bootstrapper.dependencies.auth import get_current_operator
from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import (
    COMMON_ERROR_RESPONSES,
    CONFLICT_ERROR_RESPONSES,
)
from synth_engine.bootstrapper.schemas.privacy import BudgetRefreshRequest, BudgetResponse
from synth_engine.modules.privacy.accountant import reset_budget
from synth_engine.modules.privacy.ledger import PrivacyLedger
from synth_engine.shared.observability import AUDIT_WRITE_FAILURE_TOTAL
from synth_engine.shared.security.audit import get_audit_logger

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/privacy", tags=["privacy"])

# T71.5 — Use shared AUDIT_WRITE_FAILURE_TOTAL counter from shared/observability.py.


def _ledger_to_budget_response(ledger: PrivacyLedger) -> BudgetResponse:
    """Convert a :class:`PrivacyLedger` ORM row to a :class:`BudgetResponse`.

    Computes ``remaining_epsilon`` and ``is_exhausted`` from the ledger fields.

    Args:
        ledger: The ``PrivacyLedger`` row to convert.

    Returns:
        A :class:`BudgetResponse` reflecting the current budget state.
    """
    allocated = float(ledger.total_allocated_epsilon)
    spent = float(ledger.total_spent_epsilon)
    remaining = max(0.0, allocated - spent)
    return BudgetResponse(
        total_allocated_epsilon=allocated,
        total_spent_epsilon=spent,
        remaining_epsilon=remaining,
        is_exhausted=remaining <= 0.0,
    )


def _run_reset_budget(
    *,
    ledger_id: int,
    new_allocated_epsilon: Decimal | None,
) -> tuple[Decimal, Decimal]:
    """Run :func:`reset_budget` synchronously via ``asyncio.run()``.

    Builds a fresh async engine and session per call — the same pattern used
    by :func:`~synth_engine.bootstrapper.factories.build_spend_budget_fn`.
    This ensures each reset runs in its own transaction with a dedicated
    async engine/session, which is required by ``reset_budget``'s concurrency
    contract.

    Args:
        ledger_id: Primary key of the :class:`PrivacyLedger` row to reset.
        new_allocated_epsilon: Optional new allocation ceiling.  Passed
            through to :func:`reset_budget` unchanged.

    Returns:
        A 2-tuple ``(allocated, spent)`` reflecting the post-reset state,
        as returned by :func:`reset_budget`.

    Raises:
        sqlalchemy.exc.NoResultFound: If the ledger row does not exist.
    """  # noqa: DOC502

    async def _async_reset() -> tuple[Decimal, Decimal]:
        from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
        from sqlalchemy.ext.asyncio import create_async_engine

        from synth_engine.shared.settings import get_settings

        database_url = get_settings().database_url or "sqlite+aiosqlite:///:memory:"

        if "postgresql://" in database_url and "+asyncpg" not in database_url:
            async_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif "sqlite:///" in database_url and "+aiosqlite" not in database_url:
            async_url = database_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        else:
            async_url = database_url

        engine = create_async_engine(async_url)
        async with _AsyncSession(engine) as session:
            return await reset_budget(
                ledger_id=ledger_id,
                session=session,
                new_allocated_epsilon=new_allocated_epsilon,
            )

    return asyncio.run(_async_reset())


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------
#
# Transaction handling note (T62.1 context):
# These route handlers do NOT use explicit session.commit() wrapping around
# the handler bodies.  Budget mutation is delegated to reset_budget() via an
# asyncio.run() bridge (see _run_reset_budget above), which runs in its own
# async session and transaction.  The DI-provided sync Session is used only
# for read operations (SELECT PrivacyLedger).  EpsilonAccountant and
# reset_budget() manage their own transaction boundaries; adding a redundant
# outer commit here would create a double-commit risk with the async session.


@router.get(
    "/budget",
    summary="Get privacy budget",
    description="Return current epsilon/delta budget allocation and consumption.",
    responses=COMMON_ERROR_RESPONSES,
    response_model=BudgetResponse,
)
def get_budget(
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> BudgetResponse | JSONResponse:
    """Return the current privacy budget ledger state.

    Reads the first available ``PrivacyLedger`` row and returns its
    current state including the computed ``remaining_epsilon`` and the
    ``is_exhausted`` flag.

    Args:
        session: Database session injected by FastAPI DI.
        current_operator: Authenticated operator sub claim (injected by FastAPI DI).

    Returns:
        :class:`BudgetResponse` on success, or RFC 7807 404
        :class:`fastapi.responses.JSONResponse` if no ledger row exists.
    """
    ledger = session.exec(select(PrivacyLedger)).first()
    if ledger is None:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail="Privacy budget ledger has not been initialised.",
            ),
        )
    return _ledger_to_budget_response(ledger)


@router.post(
    "/budget/refresh",
    summary="Refresh privacy budget",
    description="Reset the spent epsilon counter. Emits a WORM-audited event.",
    responses=CONFLICT_ERROR_RESPONSES,
    response_model=BudgetResponse,
)
def refresh_budget(
    body: BudgetRefreshRequest,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> BudgetResponse | JSONResponse:
    """Reset the privacy budget and emit a WORM audit event.

    Resets ``total_spent_epsilon`` to zero via :func:`reset_budget` (which
    uses ``SELECT ... FOR UPDATE`` to prevent races with concurrent
    ``spend_budget()`` calls).  If ``body.new_allocated_epsilon`` is provided,
    the ``total_allocated_epsilon`` ceiling is also updated.

    A HMAC-signed WORM audit event (``PRIVACY_BUDGET_REFRESH``) is emitted
    BEFORE the budget reset (T70.8 audit-before-mutation standardisation).
    If the audit write fails, 500 is returned and the budget is NOT reset.
    If the reset fails after a successful audit, a compensating
    ``BUDGET_RESET_FAILED`` event is emitted and 500 is returned.

    Args:
        body: Refresh request containing justification and optional new
            allocation ceiling.
        session: Database session injected by FastAPI DI.
        current_operator: Authenticated operator sub claim (injected by FastAPI DI).

    Returns:
        :class:`BudgetResponse` reflecting the post-refresh state on success,
        RFC 7807 404 :class:`fastapi.responses.JSONResponse` if no ledger
        row exists, or RFC 7807 500 if the audit write or budget reset fails.
    """
    # Check ledger existence first (sync read — no mutation yet).
    ledger = session.exec(select(PrivacyLedger)).first()
    if ledger is None:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail="Privacy budget ledger has not been initialised.",
            ),
        )

    ledger_id: int = ledger.id  # type: ignore[assignment]

    # Capture pre-refresh state for the audit details.
    prev_allocated = str(ledger.total_allocated_epsilon)
    prev_spent = str(ledger.total_spent_epsilon)
    new_alloc: Decimal | None = (
        Decimal(str(body.new_allocated_epsilon)) if body.new_allocated_epsilon is not None else None
    )

    # T70.8: Emit audit event BEFORE the budget reset.
    # If the audit write fails (any exception), return 500 — no mutation proceeds.
    try:
        audit = get_audit_logger()
        audit.log_event(
            event_type="PRIVACY_BUDGET_REFRESH",
            actor=current_operator,
            resource=f"privacy_ledger/{ledger_id}",
            action="refresh_budget",
            details={
                "justification": body.justification,
                "prev_allocated_epsilon": prev_allocated,
                "prev_spent_epsilon": prev_spent,
                "new_allocated_epsilon": str(new_alloc)
                if new_alloc is not None
                else prev_allocated,
            },
        )
    except (ValueError, OSError):
        AUDIT_WRITE_FAILURE_TOTAL.labels(router="privacy", endpoint="/privacy/budget/refresh").inc()
        _logger.exception("WORM audit emission failed BEFORE budget reset — aborting (T70.8)")
        return JSONResponse(
            status_code=500,
            content=problem_detail(
                status=500,
                title="Internal Server Error",
                detail="Audit write failed. Budget reset was NOT performed.",
            ),
        )

    # Delegate mutation to reset_budget() via asyncio.run() bridge.
    # reset_budget() uses SELECT ... FOR UPDATE — safe against concurrent spends.
    # T70.8: If reset fails AFTER audit, emit compensating event and return 500.
    try:
        _run_reset_budget(ledger_id=ledger_id, new_allocated_epsilon=new_alloc)
    # Broad catch intentional: asyncio.run() bridge + async SQLAlchemy can raise
    # RuntimeError, SQLAlchemyError, or driver-specific exceptions — compensating
    # audit event must fire for any failure type.
    except Exception:
        _logger.exception("Budget reset failed after audit — emitting compensating event (T70.8)")
        try:
            audit.log_event(
                event_type="BUDGET_RESET_FAILED",
                actor=current_operator,
                resource=f"privacy_ledger/{ledger_id}",
                action="refresh_budget",
                details={
                    "justification": body.justification,
                    "prev_allocated_epsilon": prev_allocated,
                    "prev_spent_epsilon": prev_spent,
                },
            )
        except (ValueError, OSError):
            _logger.exception("Compensating audit event BUDGET_RESET_FAILED also failed")
        return JSONResponse(
            status_code=500,
            content=problem_detail(
                status=500,
                title="Internal Server Error",
                detail="Budget reset failed after audit was written.",
            ),
        )

    # Re-read the ledger for response construction (the async session has its own
    # connection; we need the sync session to build the response).
    session.expire(ledger)
    session.refresh(ledger)

    _logger.info(
        "Budget refreshed: spent reset to 0, allocated=%s",
        ledger.total_allocated_epsilon,
    )

    return _ledger_to_budget_response(ledger)
