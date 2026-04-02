"""FastAPI router for webhook registration CRUD endpoints.

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
"""

from __future__ import annotations

import json
import logging
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Path, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from synth_engine.bootstrapper.dependencies.auth import get_current_operator
from synth_engine.bootstrapper.dependencies.db import get_db_session
from synth_engine.bootstrapper.errors.formatter import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES
from synth_engine.bootstrapper.schemas.webhooks import (
    WebhookDelivery,
    WebhookDeliveryListResponse,
    WebhookDeliveryResponse,
    WebhookRegistration,
    WebhookRegistrationListResponse,
    WebhookRegistrationRequest,
    WebhookRegistrationResponse,
)
from synth_engine.shared.observability import AUDIT_WRITE_FAILURE_TOTAL
from synth_engine.shared.security.audit import get_audit_logger
from synth_engine.shared.settings import get_settings
from synth_engine.shared.ssrf import resolve_and_pin_ips, validate_callback_url

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_url_for_log(url: str) -> str:
    """Strip query string and fragment from ``url`` before logging.

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


def _check_registration_preconditions(
    callback_url: str,
    session: Session,
    operator: str,
) -> JSONResponse | None:
    """Check HTTPS, SSRF, and registration limit before persisting.

    Returns a JSONResponse (400 or 409) if any check fails, else None.

    Args:
        callback_url: The proposed callback URL.
        session: Database session for registration count query.
        operator: Authenticated operator sub claim.

    Returns:
        JSONResponse with an RFC 7807 body on failure; None on success.
    """
    settings = get_settings()
    if settings.is_production() and callback_url.startswith("http://"):
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
    try:
        validate_callback_url(callback_url, strict=True)
    except ValueError as exc:
        _logger.warning("SSRF validation rejected callback URL for operator %s: %s", operator, exc)
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
    max_reg = settings.webhook_max_registrations
    if _count_active_registrations(session, operator) >= max_reg:
        return JSONResponse(
            status_code=409,
            content=problem_detail(
                status=409,
                title="Registration Limit Exceeded",
                detail=(
                    f"Maximum {max_reg} active webhook registrations per operator. "
                    "Deactivate an existing registration first."
                ),
            ),
        )
    return None


def _pin_ips_for_url(callback_url: str, operator: str) -> tuple[str | None, JSONResponse | None]:
    """Resolve and pin IPs for ``callback_url`` at registration time.

    Args:
        callback_url: The callback URL to resolve.
        operator: Authenticated operator sub claim (for logging).

    Returns:
        ``(pinned_json, None)`` on success;
        ``(None, error_response)`` if DNS resolution fails.
    """
    hostname = urlparse(callback_url).hostname or ""
    try:
        pinned = resolve_and_pin_ips(hostname)
        return json.dumps(pinned), None
    except ValueError as pin_exc:
        _logger.warning("DNS pinning failed for callback URL (operator=%s): %s", operator, pin_exc)
        return None, JSONResponse(
            status_code=400,
            content=problem_detail(
                status=400,
                title="Invalid Callback URL",
                detail=(
                    "The callback URL hostname could not be resolved or resolves to "
                    "a private address. DNS pinning failed at registration time."
                ),
            ),
        )


def _commit_registration(
    session: Session, reg: WebhookRegistration, operator: str
) -> JSONResponse | None:
    """Commit the webhook registration row; return a 500 response on DB error.

    Args:
        session: Open SQLModel Session with ``reg`` already added.
        reg: The WebhookRegistration to persist.
        operator: Authenticated operator sub claim (for logging).

    Returns:
        None on success; a 500 JSONResponse on SQLAlchemyError.
    """
    try:
        session.commit()
        session.refresh(reg)
        return None
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "register_webhook: SQLAlchemyError for operator=%s", operator, exc_info=True
        )
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Database operation failed. Please retry.",
            },
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/",
    summary="Register a webhook",
    description=(
        "Register a callback URL to receive job lifecycle events. Payloads are HMAC-signed."
    ),
    responses=COMMON_ERROR_RESPONSES,
    status_code=201,
    response_model=WebhookRegistrationResponse,
)
def register_webhook(
    body: WebhookRegistrationRequest,
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> WebhookRegistrationResponse | JSONResponse:
    """Register a new webhook callback URL.

    Validates the callback URL for SSRF risk before persisting.  In
    production mode, only ``https://`` URLs are accepted.

    Args:
        body: Registration request with ``callback_url``, ``signing_key``, and ``events``.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected).

    Returns:
        :class:`WebhookRegistrationResponse` on success, RFC 7807 400/409/500 on failure.
    """
    err = _check_registration_preconditions(body.callback_url, session, current_operator)
    if err is not None:
        return err

    pinned_json, pin_err = _pin_ips_for_url(body.callback_url, current_operator)
    if pin_err is not None:
        return pin_err

    reg = WebhookRegistration(
        owner_id=current_operator,
        callback_url=body.callback_url,
        signing_key=body.signing_key,
        events=json.dumps(body.events),
        active=True,
        pinned_ips=pinned_json,
    )
    session.add(reg)
    db_err = _commit_registration(session, reg, current_operator)
    if db_err is not None:
        return db_err

    _logger.info(
        "Webhook registered: id=%s owner=%s url=%s",
        reg.id,
        current_operator,
        _safe_url_for_log(reg.callback_url),
    )
    return WebhookRegistrationResponse.from_orm_model(reg)


@router.get(
    "/",
    summary="List webhooks",
    description="Return all registered webhook endpoints for the authenticated operator.",
    responses=COMMON_ERROR_RESPONSES,
    response_model=WebhookRegistrationListResponse,
)
def list_webhooks(
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> WebhookRegistrationListResponse:
    """List all webhook registrations owned by the authenticated operator.

    Returns both active and inactive registrations.  The ``signing_key`` is
    never included in responses.

    Args:
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected).

    Returns:
        :class:`WebhookRegistrationListResponse` with up to 100 owner-scoped items.
    """
    stmt = (
        select(WebhookRegistration)
        .where(WebhookRegistration.owner_id == current_operator)
        .limit(100)
    )
    registrations = session.exec(stmt).all()
    return WebhookRegistrationListResponse(
        items=[WebhookRegistrationResponse.from_orm_model(r) for r in registrations]
    )


def _audit_and_soft_delete_webhook(
    session: Session,
    reg: WebhookRegistration,
    webhook_id: str,
    operator: str,
) -> Response | JSONResponse | None:
    """Emit audit event then soft-delete the registration row.

    Audit-before-commit: if the audit write fails, return 500 without DB change.

    Args:
        session: Open SQLModel Session.
        reg: The registration to deactivate.
        webhook_id: Registration UUID (for audit and logging).
        operator: Authenticated operator sub claim.

    Returns:
        None on success (caller returns 204); a JSONResponse on audit or DB error.
    """
    try:
        get_audit_logger().log_event(
            event_type="WEBHOOK_DEACTIVATED",
            actor=operator,
            resource=f"webhook/{webhook_id}",
            action="deactivate",
            details={"webhook_id": webhook_id},
        )
    except (ValueError, OSError, UnicodeError):
        AUDIT_WRITE_FAILURE_TOTAL.labels(router="webhooks", endpoint="/webhooks/{id}").inc()
        _logger.exception("Audit logging failed for deactivate_webhook id=%s; aborting", webhook_id)
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Audit write failed. Webhook was NOT deactivated.",
            },
        )
    reg.active = False
    session.add(reg)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        _logger.warning(
            "deactivate_webhook: SQLAlchemyError for webhook_id=%s operator=%s",
            webhook_id,
            operator,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Database operation failed. Please retry.",
            },
        )
    return None


@router.delete(
    "/{webhook_id}",
    summary="Delete a webhook",
    description="Remove a registered webhook endpoint.",
    responses=COMMON_ERROR_RESPONSES,
    status_code=204,
)
def deactivate_webhook(
    webhook_id: Annotated[str, Path(max_length=255)],
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> Response:
    """Deactivate a webhook registration by ID.

    Returns 404 for any ID not owned by the caller (IDOR protection, no 403).

    Args:
        webhook_id: UUID string primary key of the registration to deactivate.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected).

    Returns:
        HTTP 204 No Content on success, RFC 7807 404 if not found, or 500 on error.
    """
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
    err = _audit_and_soft_delete_webhook(session, reg, webhook_id, current_operator)
    if err is not None:
        return err
    _logger.info("Webhook deactivated: id=%s owner=%s", webhook_id, current_operator)
    return Response(status_code=204)


@router.get(
    "/{webhook_id}/deliveries",
    summary="List webhook deliveries",
    description=(
        "Return recent delivery attempts for a webhook registration. "
        "Results are scoped to the authenticated operator (IDOR protection)."
    ),
    responses=COMMON_ERROR_RESPONSES,
    response_model=WebhookDeliveryListResponse,
)
def list_webhook_deliveries(
    webhook_id: Annotated[str, Path(max_length=255)],
    session: Annotated[Session, Depends(get_db_session)],
    current_operator: Annotated[str, Depends(get_current_operator)],
) -> WebhookDeliveryListResponse | JSONResponse:
    """List delivery attempts for a webhook registration.

    Returns up to 100 most recent delivery attempts, ordered by creation
    date descending.  Returns 404 for any ``webhook_id`` not owned by the
    authenticated operator (IDOR protection).

    Args:
        webhook_id: UUID string primary key of the parent registration.
        session: Database session (injected by FastAPI DI).
        current_operator: Authenticated operator sub claim (injected).

    Returns:
        :class:`WebhookDeliveryListResponse` with up to 100 delivery records,
        or RFC 7807 404 if the registration is not found or not owned by the caller.
    """
    reg_stmt = select(WebhookRegistration).where(
        WebhookRegistration.id == webhook_id,
        WebhookRegistration.owner_id == current_operator,
    )
    reg = session.exec(reg_stmt).first()
    if reg is None:
        return JSONResponse(
            status_code=404,
            content=problem_detail(
                status=404,
                title="Not Found",
                detail=f"Webhook registration with id={webhook_id!r} not found.",
            ),
            media_type="application/problem+json",
        )

    delivery_stmt = (
        select(WebhookDelivery)
        .where(WebhookDelivery.registration_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())  # type: ignore[attr-defined]
        .limit(100)
    )
    deliveries = session.exec(delivery_stmt).all()

    return WebhookDeliveryListResponse(
        items=[WebhookDeliveryResponse.model_validate(d) for d in deliveries]
    )
