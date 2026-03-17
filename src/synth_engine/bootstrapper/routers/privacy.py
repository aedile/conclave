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

WORM audit logging:
    Every refresh call MUST emit a signed :class:`~synth_engine.shared.security.audit.AuditEvent`
    via :func:`~synth_engine.shared.security.audit.get_audit_logger`.
    The event type is ``"PRIVACY_BUDGET_REFRESH"`` and the actor is read from
    the ``X-Operator-Id`` request header (falling back to ``"unknown-operator"``
    when absent).

Domain delegation:
    Budget mutation is delegated to
    :func:`~synth_engine.modules.privacy.accountant.reset_budget` via an
    ``asyncio.run()`` bridge, following the same pattern as
    :func:`~synth_engine.bootstrapper.factories.build_spend_budget_fn`.
    This ensures the ledger is mutated under ``SELECT ... FOR UPDATE``
    pessimistic locking, preventing races with concurrent ``spend_budget()``
    calls.

Import boundaries:
    ``bootstrapper/`` CAN import from ``modules/privacy/`` (allowed direction).
    Do NOT import from other modules.

Task: P22-T22.4 — Budget Management API
CONSTITUTION Priority 0: Security — WORM audit emission
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
"""

from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.schemas.privacy import BudgetRefreshRequest, BudgetResponse
from synth_engine.modules.privacy.accountant import reset_budget
from synth_engine.modules.privacy.ledger import PrivacyLedger
from synth_engine.shared.security.audit import get_audit_logger

_logger = logging.getLogger(__name__)

#: Fallback actor identity when the request carries no X-Operator-Id header.
_UNKNOWN_OPERATOR: str = "unknown-operator"

router = APIRouter(prefix="/privacy", tags=["privacy"])


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


def _emit_refresh_audit(
    *,
    actor: str,
    ledger_id: int,
    justification: str,
    prev_allocated: str,
    prev_spent: str,
    new_allocated: str,
) -> None:
    """Emit a WORM HMAC-signed audit event for a budget refresh.

    Calls :func:`~synth_engine.shared.security.audit.get_audit_logger` and
    logs a ``PRIVACY_BUDGET_REFRESH`` event.  Propagates any exception raised
    by the audit logger (callers must handle to return a 500 response).

    Args:
        actor: Operator identity (from ``X-Operator-Id`` header or fallback).
        ledger_id: Primary key of the refreshed ``PrivacyLedger`` row.
        justification: Human-readable reason for the refresh.
        prev_allocated: Pre-refresh ``total_allocated_epsilon`` as string.
        prev_spent: Pre-refresh ``total_spent_epsilon`` as string.
        new_allocated: Post-refresh ``total_allocated_epsilon`` as string.

    Raises:
        Any exception raised by the audit logger's ``log_event`` method.
    """
    audit_details: dict[str, str] = {
        "justification": justification,
        "prev_allocated_epsilon": prev_allocated,
        "prev_spent_epsilon": prev_spent,
        "new_allocated_epsilon": new_allocated,
    }
    audit = get_audit_logger()
    audit.log_event(
        event_type="PRIVACY_BUDGET_REFRESH",
        actor=actor,
        resource=f"privacy_ledger/{ledger_id}",
        action="refresh_budget",
        details=audit_details,
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
        Any exception raised by :func:`reset_budget`, including
        ``sqlalchemy.exc.NoResultFound`` if the ledger row does not exist.
    """

    async def _async_reset() -> tuple[Decimal, Decimal]:
        from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
        from sqlalchemy.ext.asyncio import create_async_engine

        database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

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


@router.get("/budget", response_model=BudgetResponse)
def get_budget(
    session: Annotated[Session, Depends(get_db_session)],
) -> BudgetResponse | JSONResponse:
    """Return the current privacy budget ledger state.

    Reads the first available ``PrivacyLedger`` row and returns its
    current state including the computed ``remaining_epsilon`` and the
    ``is_exhausted`` flag.

    Args:
        session: Database session injected by FastAPI DI.

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


@router.post("/budget/refresh", response_model=BudgetResponse)
def refresh_budget(
    body: BudgetRefreshRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
) -> BudgetResponse | JSONResponse:
    """Reset the privacy budget and emit a WORM audit event.

    Resets ``total_spent_epsilon`` to zero via :func:`reset_budget` (which
    uses ``SELECT ... FOR UPDATE`` to prevent races with concurrent
    ``spend_budget()`` calls).  If ``body.new_allocated_epsilon`` is provided,
    the ``total_allocated_epsilon`` ceiling is also updated.

    A HMAC-signed WORM audit event (``PRIVACY_BUDGET_REFRESH``) is emitted via
    :func:`~synth_engine.shared.security.audit.get_audit_logger` capturing the
    operator identity (``X-Operator-Id`` header) and the justification text.

    The DB write (via :func:`reset_budget`) occurs BEFORE the audit emit so
    that a DB failure does not produce an orphaned audit record.  If the audit
    emit fails after a successful DB write, a 500 is returned.

    Args:
        body: Refresh request containing justification and optional new
            allocation ceiling.
        request: The raw Starlette request (used to read the ``X-Operator-Id``
            header for the audit event actor identity).
        session: Database session injected by FastAPI DI.

    Returns:
        :class:`BudgetResponse` reflecting the post-refresh state on success,
        RFC 7807 404 :class:`fastapi.responses.JSONResponse` if no ledger
        row exists, or RFC 7807 500 if the audit emit fails.
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

    # Resolve the operator identity from the request header.
    actor: str = request.headers.get("X-Operator-Id", _UNKNOWN_OPERATOR)

    # Delegate mutation to reset_budget() via asyncio.run() bridge.
    # reset_budget() uses SELECT ... FOR UPDATE — safe against concurrent spends.
    new_alloc: Decimal | None = (
        Decimal(str(body.new_allocated_epsilon)) if body.new_allocated_epsilon is not None else None
    )
    _run_reset_budget(ledger_id=ledger_id, new_allocated_epsilon=new_alloc)

    # Re-read the ledger for response construction (the async session has its own
    # connection; we need the sync session to build the response).
    session.expire(ledger)
    session.refresh(ledger)

    # Emit WORM audit event — MUST happen after the DB commit so a DB failure
    # does not produce an orphaned audit record.  If audit fails, return 500.
    try:
        _emit_refresh_audit(
            actor=actor,
            ledger_id=ledger_id,
            justification=body.justification,
            prev_allocated=prev_allocated,
            prev_spent=prev_spent,
            new_allocated=str(ledger.total_allocated_epsilon),
        )
    except Exception:  # Broad catch intentional: audit failure must return 500, not propagate
        _logger.exception("WORM audit emission failed after budget reset — returning 500")
        return JSONResponse(
            status_code=500,
            content=problem_detail(
                status=500,
                title="Internal Server Error",
                detail="Budget was reset but audit emission failed.",
            ),
        )

    _logger.info(
        "Budget refreshed: spent reset to 0, allocated=%s",
        ledger.total_allocated_epsilon,
    )

    return _ledger_to_budget_response(ledger)
