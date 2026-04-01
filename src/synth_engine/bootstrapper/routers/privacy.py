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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _emit_pre_reset_audit(
    operator: str,
    ledger_id: int,
    prev_allocated: str,
    prev_spent: str,
    new_alloc: Decimal | None,
    justification: str,
) -> JSONResponse | None:
    """Emit PRIVACY_BUDGET_REFRESH audit event; return 500 JSONResponse on failure.

    Args:
        operator: Authenticated operator sub claim.
        ledger_id: Ledger primary key (for resource path).
        prev_allocated: Pre-reset allocated epsilon string.
        prev_spent: Pre-reset spent epsilon string.
        new_alloc: Optional new allocated epsilon.
        justification: Operator-supplied justification text.

    Returns:
        None on success; a 500 JSONResponse on audit write failure.
    """
    try:
        get_audit_logger().log_event(
            event_type="PRIVACY_BUDGET_REFRESH",
            actor=operator,
            resource=f"privacy_ledger/{ledger_id}",
            action="refresh_budget",
            details={
                "justification": justification,
                "prev_allocated_epsilon": prev_allocated,
                "prev_spent_epsilon": prev_spent,
                "new_allocated_epsilon": str(new_alloc)
                if new_alloc is not None
                else prev_allocated,
            },
        )
        return None
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


def _run_reset_with_compensation(
    ledger_id: int,
    new_alloc: Decimal | None,
    operator: str,
    prev_allocated: str,
    prev_spent: str,
    justification: str,
) -> JSONResponse | None:
    """Run the budget reset; emit compensating audit event on failure.

    Args:
        ledger_id: Ledger primary key.
        new_alloc: Optional new allocation ceiling.
        operator: Authenticated operator sub claim.
        prev_allocated: Pre-reset allocated epsilon (for compensating audit).
        prev_spent: Pre-reset spent epsilon (for compensating audit).
        justification: Operator-supplied justification.

    Returns:
        None on success; a 500 JSONResponse on reset failure.
    """
    try:
        _run_reset_budget(ledger_id=ledger_id, new_allocated_epsilon=new_alloc)
        return None
    except Exception:
        _logger.exception("Budget reset failed after audit — emitting compensating event (T70.8)")
        try:
            get_audit_logger().log_event(
                event_type="BUDGET_RESET_FAILED",
                actor=operator,
                resource=f"privacy_ledger/{ledger_id}",
                action="refresh_budget",
                details={
                    "justification": justification,
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

    T70.8 audit-before-mutation: audit is emitted BEFORE the reset.  If the
    audit write fails, 500 is returned and the budget is NOT reset.

    Args:
        body: Refresh request with justification and optional new allocation.
        session: Database session injected by FastAPI DI.
        current_operator: Authenticated operator sub claim (injected).

    Returns:
        :class:`BudgetResponse` on success, RFC 7807 404/500 on failure.
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
    ledger_id: int = ledger.id  # type: ignore[assignment]
    prev_allocated = str(ledger.total_allocated_epsilon)
    prev_spent = str(ledger.total_spent_epsilon)
    new_alloc: Decimal | None = (
        Decimal(str(body.new_allocated_epsilon)) if body.new_allocated_epsilon is not None else None
    )
    audit_err = _emit_pre_reset_audit(
        current_operator, ledger_id, prev_allocated, prev_spent, new_alloc, body.justification
    )
    if audit_err is not None:
        return audit_err
    reset_err = _run_reset_with_compensation(
        ledger_id, new_alloc, current_operator, prev_allocated, prev_spent, body.justification
    )
    if reset_err is not None:
        return reset_err
    session.expire(ledger)
    session.refresh(ledger)
    _logger.info("Budget refreshed: spent reset to 0, allocated=%s", ledger.total_allocated_epsilon)
    return _ledger_to_budget_response(ledger)
