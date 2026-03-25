"""FastAPI router for webhook registration CRUD endpoints — T45.3.

Implements:
- POST /webhooks/ — register a new webhook callback.
- GET /webhooks/ — list active webhook registrations for the operator.
- DELETE /webhooks/{id} — deactivate a webhook registration.

Security posture
----------------
- All endpoints require JWT authentication (``get_current_operator``).
- All CRUD operations are scoped to ``owner_id`` (IDOR protection).
- DELETE returns 404 for any ID not owned by the caller (prevents enumeration).
- ``signing_key`` is accepted at registration but never returned in responses.
- SSRF validation on ``callback_url`` at registration time (strict=True,
  fail-closed: DNS failures cause rejection).
- HTTPS-only in production mode (``settings.is_production()``).
- Max 10 active registrations per operator.
- Callback URL is sanitized (query string stripped) before logging to prevent
  accidental exposure of embedded tokens in query parameters.

RFC 7807 Problem Details format for all error responses.

Boundary constraints (import-linter enforced):
    - bootstrapper/ may import from shared/ and modules/.

CONSTITUTION Priority 0: Security — SSRF, IDOR, key write-only, safe logging
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T45.3 — Implement Webhook Callbacks for Task Completion
Task: P45 review — F2 (safe URL logging), F4 (import shared/ssrf), F11 (dead code)
Task: T55.4 — SSRF registration fail-closed on DNS failure
"""

from __future__ import annotations

import json
import logging
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from synth_engine.bootstrapper.dependencies.auth import get_current_operator
from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors.formatter import problem_detail
from synth_engine.bootstrapper.schemas.webhooks import (
    WebhookRegistration,
    WebhookRegistrationListResponse,
    WebhookRegistrationRequest,
    WebhookRegistrationResponse,
)
from synth_engine.shared.settings import get_settings
from synth_engine.shared.ssrf import validate_callback_url

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_url_for_log(url: str) -> str:
    """Strip query string and fragment from ``url`` before logging.

    Prevents accidental exposure of embedded auth tokens (e.g. ``?token=…``)
    in log output.

    Args:
        url: Raw callback URL from the request body.

    Returns:
        URL with query string and fragment removed, safe to include in logs.
    """
    return urlparse(url)._replace(query="", fragment="").geturl()


def _count_active_registrations(session: Session, owner_id: str) -> int:
    """Count active webhook registrations for ``owner_id``.

    Args:
        session: Open SQLModel Session.
        owner_id: Operator sub claim.

    Returns:
        Number of active registrations for this operator.
    """
    stmt = select(WebhookRegistration).where(
        WebhookRegistration.owner_id == owner_id,
        WebhookRegistration.active.is_(True),  # type: ignore[attr-defined]
    )
    results = session.exec(stmt).all()
    return len(results)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/", status_code=201, response_model=WebhookRegistrationResponse)
def register_webhook(
    body: WebhookRegistrationRequest,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> WebhookRegistrationResponse | JSONResponse:
    """Register a new webhook callback URL.

    Validates the callback URL for SSRF risk before persisting.  In
    production mode, only ``https://`` URLs are accepted.  The ``signing_key``
    is stored but never returned in any response.

    DNS failures during SSRF validation cause registration to be rejected
    (strict / fail-closed mode) to prevent DNS-pinning attacks.

    Args:
        body: Registration request with ``callback_url``, ``signing_key``,
            and ``events``.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected).

    Returns:
        :class:`WebhookRegistrationResponse` on success, or RFC 7807
        400/409 on validation failure.
    """
    settings = get_settings()

    # Production HTTPS-only enforcement
    if settings.is_production() and body.callback_url.startswith("http://"):
        return JSONResponse(
            status_code=400,
            content=problem_detail(
                status=400,
                title="Invalid Callback URL",
                detail=(
                    "Only HTTPS callback URLs are accepted in production mode. "
                    "Provide an https:// URL."
                ),
            ),
        )

    # SSRF validation — strict=True (fail-closed): DNS failures reject the URL.
    # Log only the sanitized URL (no query string) to avoid token leakage.
    try:
        validate_callback_url(body.callback_url, strict=True)
        # strict=True: DNS failures reject registration (fail-closed, T55.4)
    except ValueError as exc:
        _logger.warning(
            "SSRF validation rejected callback URL for operator %s: %s",
            current_operator,
            exc,
        )
        return JSONResponse(
            status_code=400,
            content=problem_detail(
                status=400,
                title="Invalid Callback URL",
                detail=(
                    "The callback URL resolves to a private or reserved address "
                    "and cannot be registered."
                ),
            ),
        )

    # Registration limit check
    max_registrations = settings.webhook_max_registrations
    current_count = _count_active_registrations(session, current_operator)
    if current_count >= max_registrations:
        return JSONResponse(
            status_code=409,
            content=problem_detail(
                status=409,
                title="Registration Limit Exceeded",
                detail=(
                    f"Maximum {max_registrations} active webhook registrations "
                    "per operator. Deactivate an existing registration first."
                ),
            ),
        )

    reg = WebhookRegistration(
        owner_id=current_operator,
        callback_url=body.callback_url,
        signing_key=body.signing_key,
        events=json.dumps(body.events),
        active=True,
    )
    session.add(reg)
    session.commit()
    session.refresh(reg)

    _logger.info(
        "Webhook registered: id=%s owner=%s url=%s",
        reg.id,
        current_operator,
        _safe_url_for_log(reg.callback_url),
    )
    return WebhookRegistrationResponse.from_orm_model(reg)


@router.get("/", response_model=WebhookRegistrationListResponse)
def list_webhooks(
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> WebhookRegistrationListResponse:
    """List all webhook registrations owned by the authenticated operator.

    Returns both active and inactive registrations so operators can see
    their history.  The ``signing_key`` is never included in responses.

    Args:
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected).

    Returns:
        :class:`WebhookRegistrationListResponse` with up to 100 owner-scoped items.
    """
    stmt = select(WebhookRegistration).where(WebhookRegistration.owner_id == current_operator).limit(100)
    registrations = session.exec(stmt).all()
    return WebhookRegistrationListResponse(
        items=[WebhookRegistrationResponse.from_orm_model(r) for r in registrations]
    )


@router.delete("/{webhook_id}", status_code=204)
def deactivate_webhook(
    webhook_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> Response:
    """Deactivate a webhook registration by ID.

    Sets ``active=False`` on the registration so no further deliveries
    are attempted.  Returns 404 for any ID not owned by the caller
    (prevents enumeration — no 403 is returned for cross-tenant IDs).

    Args:
        webhook_id: UUID string primary key of the registration to deactivate.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected).

    Returns:
        HTTP 204 No Content on success, or RFC 7807 404 if not found.
    """
    # Ownership-scoped lookup (IDOR protection: no 403, only 404)
    stmt = select(WebhookRegistration).where(
        WebhookRegistration.id == webhook_id,
        WebhookRegistration.owner_id == current_operator,
    )
    reg = session.exec(stmt).first()

    if reg is None:
        import json as _json

        return Response(
            status_code=404,
            content=_json.dumps(
                problem_detail(
                    status=404,
                    title="Not Found",
                    detail=f"Webhook registration with id={webhook_id!r} not found.",
                )
            ),
            media_type="application/problem+json",
        )

    reg.active = False
    session.add(reg)
    session.commit()

    _logger.info(
        "Webhook deactivated: id=%s owner=%s",
        webhook_id,
        current_operator,
    )
    return Response(status_code=204)
