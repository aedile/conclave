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

Import boundaries:
    ``bootstrapper/`` CAN import from ``modules/privacy/`` (allowed direction).
    Do NOT import from other modules.

Task: P22-T22.4 — Budget Management API
CONSTITUTION Priority 0: Security — WORM audit emission
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.schemas.privacy import BudgetRefreshRequest, BudgetResponse
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


@router.get("/budget", response_model=BudgetResponse)
def get_budget(
    session: Annotated[Session, Depends(get_db_session)],
) -> BudgetResponse | JSONResponse:
    """Return the current privacy budget ledger state.

    Reads the canonical ``PrivacyLedger`` row (id=1) and returns its
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

    Resets ``total_spent_epsilon`` to zero.  If ``body.new_allocated_epsilon``
    is provided, the ``total_allocated_epsilon`` ceiling is also updated to
    that value.

    A HMAC-signed WORM audit event (``PRIVACY_BUDGET_REFRESH``) is emitted via
    :func:`~synth_engine.shared.security.audit.get_audit_logger` capturing the
    operator identity (``X-Operator-Id`` header) and the justification text.

    Args:
        body: Refresh request containing justification and optional new
            allocation ceiling.
        request: The raw Starlette request (used to read the ``X-Operator-Id``
            header for the audit event actor identity).
        session: Database session injected by FastAPI DI.

    Returns:
        :class:`BudgetResponse` reflecting the post-refresh state on success,
        or RFC 7807 404 :class:`fastapi.responses.JSONResponse` if no ledger
        row exists.
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

    # Capture pre-refresh state for the audit details.
    prev_allocated = str(ledger.total_allocated_epsilon)
    prev_spent = str(ledger.total_spent_epsilon)

    # Apply the refresh: reset spent; optionally update the allocation ceiling.
    if body.new_allocated_epsilon is not None:
        ledger.total_allocated_epsilon = Decimal(str(body.new_allocated_epsilon))
    ledger.total_spent_epsilon = Decimal("0.0")

    session.add(ledger)
    session.commit()
    session.refresh(ledger)

    # Resolve the operator identity from the request header.
    actor: str = request.headers.get("X-Operator-Id", _UNKNOWN_OPERATOR)

    # Emit WORM audit event — MUST happen after the DB commit so a DB failure
    # does not produce an orphaned audit record.
    audit_details: dict[str, str] = {
        "justification": body.justification,
        "prev_allocated_epsilon": prev_allocated,
        "prev_spent_epsilon": prev_spent,
        "new_allocated_epsilon": str(ledger.total_allocated_epsilon),
    }
    audit = get_audit_logger()
    audit.log_event(
        event_type="PRIVACY_BUDGET_REFRESH",
        actor=actor,
        resource=f"privacy_ledger/{ledger.id}",
        action="refresh_budget",
        details=audit_details,
    )
    _logger.info(
        "Budget refreshed by actor=%s: spent reset to 0, allocated=%s",
        actor,
        ledger.total_allocated_epsilon,
    )

    return _ledger_to_budget_response(ledger)
