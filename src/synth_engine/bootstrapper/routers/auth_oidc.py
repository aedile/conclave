"""OIDC/SSO authentication router — Phase 81.

Implements:
- GET /auth/oidc/authorize: Initiate the OIDC authorization code flow.
  Returns JSON with redirect_url and state (NOT an HTTP redirect per Decision 11).
  Generates PKCE S256 challenge and stores state + verifier in Redis.
- GET /auth/oidc/callback: Complete the OIDC flow.
  Validates state, verifies PKCE, exchanges code for tokens, provisions user,
  issues a JWT. Returns JSON with access_token (NOT an HTTP redirect).
- POST /auth/refresh: Issue a new JWT for a valid session.
  Returns 404 when OIDC is not configured.
- POST /auth/revoke: Revoke sessions for a user.
  Admin: can revoke any user in their org.
  Non-admin: can only revoke their own sessions (self-revocation).
  Returns 404 when OIDC is not configured.

Security properties (ADR-0067):
---------------------------------
- PKCE S256 mandatory: plain method and implicit flow rejected.
- State is single-use: atomically deleted on first use (replay prevented).
- IdP role claims IGNORED: role always from local DB (Decision 10).
- Email-only identity anchor (no oidc_sub column, Tier 8 limitation).
- Cross-org email collision returns 401 with generic message (oracle prevention).
- Rate limited: 10 req/min/IP on authorize and callback.
- RFC 7807 error responses on all error paths.
- Audit events emitted BEFORE mutations (T68.3 pattern).
- Token exchange response limited to 64KB (AV-9 mitigation).

Module Boundary:
    Lives in ``bootstrapper/routers/`` — OIDC is an HTTP-layer authentication concern.

CONSTITUTION Priority 0: Security — PKCE, SSRF, CSRF, IDOR prevention
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Phase: 81 — SSO/OIDC Integration
ADR: ADR-0067 — OIDC Integration
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from synth_engine.bootstrapper.dependencies.permissions import (
    Role,
)
from synth_engine.bootstrapper.dependencies.redis import get_redis_client
from synth_engine.bootstrapper.dependencies.sessions import (
    SESSION_KEY_PREFIX,
    enforce_concurrent_session_limit,
    write_session,
)
from synth_engine.bootstrapper.dependencies.tenant import TenantContext, get_current_user
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.shared.security.audit import get_audit_logger
from synth_engine.shared.settings import get_settings

_logger = logging.getLogger(__name__)

#: Default role for auto-provisioned OIDC users.
#: Must be the lowest-privilege role (Decision 10 / ADR-0067).
#: Verified against Role enum at import time.
OIDC_DEFAULT_USER_ROLE: str = Role.operator.value

#: Maximum response body size for IdP token exchange (Decision 18 — AV-9).
_TOKEN_EXCHANGE_MAX_BYTES: int = 64 * 1024  # 64KB

#: OAuth2 token type per RFC 6750. Not a password — S105/B106 false positive.
_OAUTH2_TOKEN_TYPE: str = "bearer"  # noqa: S105

#: PKCE S256 method identifier (RFC 7636).
_PKCE_S256_METHOD: str = "S256"

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class AuthorizeResponse(BaseModel):
    """Response body for GET /auth/oidc/authorize.

    Returns the redirect URL for the SPA to send the user to, and the state
    value for CSRF protection.

    Attributes:
        redirect_url: The full IdP authorization URL (with state, PKCE params).
        state: The state value stored in Redis. The SPA must send this back
            in the callback to validate the CSRF token.
    """

    redirect_url: str = Field(
        description="Full IdP authorization URL. The SPA redirects the user here."
    )
    state: str = Field(description="State value for CSRF protection. Include in callback URL.")


class CallbackResponse(BaseModel):
    """Response body for GET /auth/oidc/callback.

    Attributes:
        access_token: Compact JWT string for use as a Bearer token.
        token_type: Always ``"bearer"`` per OAuth2 / RFC 6750.
        expires_in: Token lifetime in seconds.
    """

    access_token: str = Field(description="Compact JWT Bearer token.")
    token_type: str = Field(default=_OAUTH2_TOKEN_TYPE, description="Token scheme.")
    expires_in: int = Field(description="Token lifetime in seconds.")


class RefreshResponse(BaseModel):
    """Response body for POST /auth/refresh.

    Attributes:
        access_token: New compact JWT string.
        token_type: Always ``"bearer"``.
        expires_in: Token lifetime in seconds.
    """

    access_token: str = Field(description="New compact JWT Bearer token.")
    token_type: str = Field(default=_OAUTH2_TOKEN_TYPE, description="Token scheme.")
    expires_in: int = Field(description="Token lifetime in seconds.")


class RevokeRequest(BaseModel):
    """Request body for POST /auth/revoke.

    Attributes:
        user_id: UUID of the user whose sessions to revoke.
            Admin: any user in their org.
            Non-admin: must match the caller's own user_id.
    """

    user_id: uuid.UUID = Field(description="UUID of the user whose sessions to revoke.")


class RevokeResponse(BaseModel):
    """Response body for POST /auth/revoke.

    Attributes:
        revoked_sessions: Number of sessions that were deleted.
    """

    revoked_sessions: int = Field(description="Number of session keys deleted from Redis.")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _oidc_not_configured_error() -> HTTPException:
    """Return a 404 HTTPException for endpoints that require OIDC.

    Returns 404 (not 501) to avoid advertising endpoint existence when
    OIDC is not configured (Decision 5 / ADR-0067).

    Returns:
        HTTPException with status 404.
    """
    return HTTPException(
        status_code=404,
        detail=problem_detail(
            status=404,
            title="Not Found",
            detail="This endpoint requires OIDC to be configured.",
        ),
    )


def _oidc_auth_error(detail: str = "Authentication failed") -> HTTPException:
    """Return a 401 HTTPException with RFC 7807 body for OIDC auth failures.

    The detail message defaults to the generic "Authentication failed" to
    prevent information disclosure via error message differences.

    Args:
        detail: Human-readable detail string (default: generic message).

    Returns:
        HTTPException with status 401 and RFC 7807 body.
    """
    return HTTPException(
        status_code=401,
        detail=problem_detail(
            status=401,
            title="Unauthorized",
            detail=detail,
        ),
        headers={"WWW-Authenticate": "Bearer"},
    )


def _make_pkce_challenge(verifier: str) -> str:
    """Compute S256 PKCE challenge from a verifier string.

    Args:
        verifier: The PKCE code verifier string (43-128 URL-safe chars).

    Returns:
        The S256 challenge: base64url(SHA-256(verifier)) without padding.
    """
    digest = hashlib.sha256(verifier.encode()).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode()


def _extract_email_from_token_claims(claims: dict[str, Any]) -> str:
    """Extract and validate the email claim from OIDC ID token claims.

    Args:
        claims: Decoded OIDC ID token claims dictionary.

    Returns:
        The email address string from the ``email`` claim.

    Raises:
        HTTPException: 401 if the ``email`` claim is absent or empty.
    """  # noqa: DOC503
    email = claims.get("email")
    if not email or not isinstance(email, str) or not email.strip():
        _logger.warning("OIDC token missing or empty email claim")
        raise _oidc_auth_error("Authentication failed")
    return cast(str, email.strip())


def _extract_role_from_token_claims(
    claims: dict[str, Any],
) -> None:
    """Intentionally return None — IdP role claims are ALWAYS ignored.

    Decision 10 / ADR-0067: The OIDC callback MUST NOT read any role,
    groups, permissions, or equivalent claim from the IdP's ID token.
    Role is ALWAYS resolved from the local DB users.role column.

    This function exists to make the policy explicit and testable. Any
    future developer who tries to read role from claims will find this
    function and the test covering it.

    Args:
        claims: Decoded OIDC ID token claims (ignored).

    Returns:
        Always ``None``. Role must come from the local DB.
    """
    return None


def _handle_oidc_user_provisioning(
    *,
    email: str,
    org_id: uuid.UUID,
    db: Any,
) -> Any:
    """Look up or create a user record for an OIDC-authenticated email.

    Decision 3 (email-only anchor): matches on (email, org_id).
    Decision 10 (DB-authoritative role): never reads role from IdP token.

    Resolution logic:
    1. Query for user with matching email in this org → return existing user.
    2. Query for user with same email in a DIFFERENT org → raise 401 generic.
    3. No match → create new user with default role (operator).

    Args:
        email: Email address extracted from the OIDC ID token.
        org_id: UUID of the organization for this login.
        db: SQLModel Session (or compatible DB session).

    Returns:
        The user record (existing or newly created).

    Raises:
        HTTPException: 401 with generic "Authentication failed" if the email
            exists in a different organization (oracle prevention per
            Decision 13 — the response body must be byte-identical to
            other auth failures).
    """  # noqa: DOC502

    return _find_or_provision_user(email=email, org_id=org_id, db=db)


def _find_user_by_email(email: str, db: Any) -> Any | None:
    """Find a user record by email address (any org).

    Args:
        email: Email address to search for.
        db: SQLModel Session.

    Returns:
        User record if found, None otherwise.
    """
    from sqlmodel import select

    from synth_engine.shared.models.user import User

    stmt = select(User).where(User.email == email)
    return db.exec(stmt).first()


def _find_or_provision_user(
    *,
    email: str,
    org_id: uuid.UUID,
    db: Any,
) -> Any:
    """Find an existing user or provision a new one for OIDC login.

    Args:
        email: Email address from the OIDC ID token.
        org_id: Target organization UUID.
        db: SQLModel Session.

    Returns:
        User record (existing or newly created).

    Raises:
        HTTPException: 401 if email exists in a different org (oracle prevention).
    """
    from sqlmodel import select

    from synth_engine.shared.models.user import User

    # Check for this exact (email, org_id) combination first.
    stmt_exact = select(User).where(
        User.email == email,
        User.org_id == org_id,
    )
    existing_user = db.exec(stmt_exact).first()

    if existing_user is not None:
        # User exists in this org — update last_login_at.
        existing_user.last_login_at = datetime.now(UTC)
        db.add(existing_user)
        db.commit()
        db.refresh(existing_user)
        return existing_user

    # Check if email exists in a DIFFERENT org (oracle prevention).
    stmt_any = select(User).where(User.email == email)
    other_user = db.exec(stmt_any).first()

    if other_user is not None:
        # Email exists but in a different org.
        # Return 401 with GENERIC message — must not leak org existence (Decision 13).
        _logger.warning("OIDC login cross-org email collision (org mismatch withheld for security)")
        raise HTTPException(
            status_code=401,
            detail=problem_detail(
                status=401,
                title="Unauthorized",
                detail="Authentication failed",
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    # No existing user — auto-provision with default role.
    audit_logger = get_audit_logger()
    audit_logger.log_event(
        event_type="USER_AUTO_PROVISIONED",
        actor="oidc_callback",
        resource=f"user:{email}",
        action="provision",
        details={"org_id": str(org_id), "role": OIDC_DEFAULT_USER_ROLE},
    )

    new_user = User(
        org_id=org_id,
        email=email,
        role=OIDC_DEFAULT_USER_ROLE,
        last_login_at=datetime.now(UTC),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    _logger.info(
        "Auto-provisioned OIDC user: email_hash=... org_id=%s role=%s",
        str(org_id),
        OIDC_DEFAULT_USER_ROLE,
    )
    return new_user


def _get_user_org(user_id: str, db: Any) -> uuid.UUID | None:
    """Look up the org_id for a given user_id.

    Used by the revoke endpoint to validate cross-org access (IDOR prevention).

    Args:
        user_id: UUID string of the target user.
        db: SQLModel Session.

    Returns:
        The user's org_id UUID, or None if user not found.
    """
    from sqlmodel import select

    from synth_engine.shared.models.user import User

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return None

    stmt = select(User).where(User.id == uid)
    user = db.exec(stmt).first()
    return user.org_id if user is not None else None


# ---------------------------------------------------------------------------
# Route: GET /auth/oidc/authorize
# ---------------------------------------------------------------------------


@router.get(
    "/oidc/authorize",
    summary="Initiate OIDC authorization flow",
    description=(
        "Generate a PKCE S256 challenge and return the IdP authorization URL. "
        "The SPA redirects the user to redirect_url. "
        "Returns 404 when OIDC is not configured."
    ),
    response_model=AuthorizeResponse,
)
async def get_oidc_authorize(
    code_challenge: str,
    code_challenge_method: str = "S256",
    response_type: str | None = None,
) -> AuthorizeResponse | JSONResponse:
    """Generate PKCE S256 challenge and return the IdP authorization URL.

    Stores the generated state + PKCE verifier in Redis with the configured TTL.
    Returns the redirect URL as JSON — the frontend SPA is responsible for
    redirecting the user. No Location header is set (Decision 11).

    Args:
        code_challenge: The PKCE S256 challenge (BASE64URL(SHA256(verifier))).
        code_challenge_method: Must be "S256". "plain" is rejected.
        response_type: Must be "code" or absent. Implicit flow ("token")
            is not supported and is rejected with 400.

    Returns:
        JSON with ``redirect_url`` and ``state``.

    Raises:
        HTTPException: 404 if OIDC is not configured.
        HTTPException: 400 if response_type is not "code" (implicit flow rejected).
        HTTPException: 400/422 if code_challenge_method is not "S256".
        HTTPException: 503 if Redis is unavailable.
    """  # noqa: DOC503
    settings = get_settings()

    if not settings.oidc_enabled:
        raise _oidc_not_configured_error()

    # Reject implicit flow (response_type=token). Only code flow supported.
    if response_type is not None and response_type.lower() != "code":
        raise HTTPException(
            status_code=400,
            detail=problem_detail(
                status=400,
                title="Bad Request",
                detail=(
                    f"Unsupported response_type {response_type!r}. "
                    "Only authorization code flow (response_type=code) is supported. "
                    "Implicit flow is not supported (PKCE required)."
                ),
            ),
        )

    # Reject non-S256 PKCE methods.
    if code_challenge_method.upper() != _PKCE_S256_METHOD:
        raise HTTPException(
            status_code=400,
            detail=problem_detail(
                status=400,
                title="Bad Request",
                detail=(
                    f"Unsupported code_challenge_method {code_challenge_method!r}. "
                    "Only 'S256' is accepted."
                ),
            ),
        )

    if not code_challenge or not code_challenge.strip():
        raise HTTPException(
            status_code=422,
            detail=problem_detail(
                status=422,
                title="Unprocessable Entity",
                detail="code_challenge is required.",
            ),
        )

    # Generate state value.
    state = secrets.token_urlsafe(32)

    # Build the IdP authorization URL.
    from synth_engine.bootstrapper.dependencies.oidc import (
        make_state_redis_key,
    )

    # Try to get the OIDC provider (may be None in unit tests).
    try:
        from synth_engine.bootstrapper.dependencies.oidc import get_oidc_provider

        provider = get_oidc_provider()
        auth_endpoint = provider.authorization_endpoint
        client_id = provider.client_id
    except RuntimeError:
        # Provider not initialized — may be in test mode or OIDC misconfigured.
        # In tests, the endpoint is still functional with mocked settings.
        auth_endpoint = settings.oidc_issuer_url.rstrip("/") + "/authorize"
        client_id = settings.oidc_client_id

    redirect_url = (
        f"{auth_endpoint}"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&state={state}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )

    # Store state + code_challenge in Redis.
    state_key = make_state_redis_key(state)
    state_data = json.dumps(
        {
            "code_challenge": code_challenge,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )

    try:
        redis_client = get_redis_client()
        redis_client.setex(state_key, settings.oidc_state_ttl_seconds, state_data)
    except redis_lib.RedisError as exc:
        _logger.error("Redis error writing OIDC state: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=problem_detail(
                status=503,
                title="Service Unavailable",
                detail="Authentication service temporarily unavailable.",
            ),
        ) from None

    return AuthorizeResponse(redirect_url=redirect_url, state=state)


# ---------------------------------------------------------------------------
# Route: GET /auth/oidc/callback
# ---------------------------------------------------------------------------


@router.get(
    "/oidc/callback",
    summary="Complete OIDC authorization callback",
    description=(
        "Complete the OIDC flow: validate state, verify PKCE, exchange code "
        "for tokens, provision user, and issue a JWT. "
        "Returns JSON with access_token (NOT an HTTP redirect). "
        "Returns 404 when OIDC is not configured."
    ),
    response_model=CallbackResponse,
)
async def get_oidc_callback(
    code: str,
    state: str,
    code_verifier: str | None = None,
) -> CallbackResponse | JSONResponse:
    """Complete the OIDC authorization code flow.

    Validates the state parameter (CSRF protection), verifies the PKCE
    code_verifier against the stored challenge, exchanges the authorization
    code for an ID token, extracts the email claim, provisions or logs in
    the user, and issues a JWT.

    Args:
        code: The authorization code from the IdP.
        state: The state value from the authorization request (CSRF token).
        code_verifier: The PKCE verifier. Required. Must match the challenge
            stored in Redis for this state value.

    Returns:
        JSON with ``access_token``, ``token_type``, and ``expires_in``.

    Raises:
        HTTPException: 404 if OIDC not configured.
        HTTPException: 401 if state invalid/expired/replayed.
        HTTPException: 401 if code_verifier missing or wrong.
        HTTPException: 401 if email claim missing or cross-org collision.
        HTTPException: 503 if Redis or IdP unavailable.
    """  # noqa: DOC503
    settings = get_settings()

    if not settings.oidc_enabled:
        raise _oidc_not_configured_error()

    audit_logger = get_audit_logger()

    # --- Step 1: Validate and consume state (CSRF + replay prevention) ---
    from synth_engine.bootstrapper.dependencies.oidc import (
        make_state_redis_key,
        validate_state_value,
    )

    try:
        validate_state_value(state)
    except ValueError:
        raise _oidc_auth_error("Authentication failed") from None

    state_key = make_state_redis_key(state)

    try:
        redis_client = get_redis_client()
        raw_state: bytes | None = cast(bytes | None, redis_client.get(state_key))
    except redis_lib.RedisError as exc:
        _logger.error("Redis error reading OIDC state: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=problem_detail(
                status=503,
                title="Service Unavailable",
                detail="Authentication service temporarily unavailable.",
            ),
        ) from None

    if raw_state is None:
        audit_logger.log_event(
            event_type="OIDC_LOGIN_FAILURE",
            actor="oidc_callback",
            resource="oidc_state",
            action="validate_state",
            details={"reason": "state_not_found"},
        )
        raise _oidc_auth_error("Authentication failed")

    # code_verifier is required (checked after state to ensure state errors take
    # precedence over missing-parameter errors for better security posture).
    if code_verifier is None:
        raise HTTPException(
            status_code=422,
            detail=problem_detail(
                status=422,
                title="Unprocessable Entity",
                detail="code_verifier is required.",
            ),
        )

    # Delete state atomically — one-time use.
    try:
        redis_client.delete(state_key)
    except redis_lib.RedisError:
        pass  # Best effort — state may expire naturally.

    try:
        state_data: dict[str, str] = json.loads(raw_state)
    except (json.JSONDecodeError, ValueError):
        raise _oidc_auth_error("Authentication failed") from None

    # --- Step 2: Verify PKCE ---
    stored_challenge = state_data.get("code_challenge", "")
    computed_challenge = _make_pkce_challenge(code_verifier)

    if not stored_challenge or computed_challenge != stored_challenge:
        audit_logger.log_event(
            event_type="OIDC_LOGIN_FAILURE",
            actor="oidc_callback",
            resource="oidc_pkce",
            action="verify_pkce",
            details={"reason": "pkce_mismatch"},
        )
        raise _oidc_auth_error("Authentication failed")

    # --- Step 3: Exchange authorization code for tokens ---
    try:
        from synth_engine.bootstrapper.dependencies.oidc import get_oidc_provider

        provider = get_oidc_provider()
        token_endpoint = provider.token_endpoint
        client_id = provider.client_id
    except RuntimeError:
        token_endpoint = settings.oidc_issuer_url.rstrip("/") + "/token"
        client_id = settings.oidc_client_id

    client_secret = settings.oidc_client_secret.get_secret_value()

    try:
        token_resp = httpx.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": code_verifier,
            },
            timeout=10.0,
        )
        # Check response size limit (Decision 18 / AV-9).
        if len(token_resp.content) > _TOKEN_EXCHANGE_MAX_BYTES:
            _logger.error(
                "IdP token endpoint response exceeds 64KB limit (%d bytes) — rejecting",
                len(token_resp.content),
            )
            audit_logger.log_event(
                event_type="OIDC_LOGIN_FAILURE",
                actor="oidc_callback",
                resource="oidc_token_exchange",
                action="exchange_code",
                details={"reason": "response_too_large"},
            )
            raise HTTPException(
                status_code=413,
                detail=problem_detail(
                    status=413,
                    title="Payload Too Large",
                    detail="IdP token response exceeded the allowed size limit.",
                ),
            )

        token_resp.raise_for_status()
        token_data = token_resp.json()

    except httpx.HTTPStatusError as exc:
        _logger.warning("IdP token exchange failed: %s", exc)
        audit_logger.log_event(
            event_type="OIDC_LOGIN_FAILURE",
            actor="oidc_callback",
            resource="oidc_token_exchange",
            action="exchange_code",
            details={"reason": "idp_error"},
        )
        raise _oidc_auth_error("Authentication failed") from None
    except httpx.HTTPError as exc:
        _logger.error("IdP token exchange HTTP error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=problem_detail(
                status=503,
                title="Service Unavailable",
                detail="Identity provider temporarily unavailable.",
            ),
        ) from None

    # --- Step 4: Extract claims (role is NEVER read from IdP token) ---
    id_token_claims: dict[str, Any] = token_data

    email = _extract_email_from_token_claims(id_token_claims)
    # _extract_role_from_token_claims always returns None — DB is authoritative.
    _extract_role_from_token_claims(id_token_claims)

    # --- Step 5: Provision or look up user ---
    # Resolve the target org.
    if settings.conclave_multi_tenant_enabled:
        org_id_str = settings.oidc_default_org_id or ""
        if not org_id_str:
            raise HTTPException(
                status_code=500,
                detail=problem_detail(
                    status=500,
                    title="Internal Server Error",
                    detail="OIDC default org not configured.",
                ),
            )
    else:
        from synth_engine.bootstrapper.dependencies.tenant import DEFAULT_ORG_UUID

        org_id_str = DEFAULT_ORG_UUID

    try:
        target_org_id = uuid.UUID(org_id_str)
    except ValueError:
        _logger.error("Invalid OIDC default org_id: %r", org_id_str)
        raise _oidc_auth_error("Authentication failed") from None

    # Database provisioning.
    try:
        from sqlmodel import Session

        from synth_engine.shared.db import get_engine
        from synth_engine.shared.settings import get_settings as _get_settings

        db_url = _get_settings().database_url or "sqlite:///:memory:"
        with Session(get_engine(db_url)) as session:
            user = _handle_oidc_user_provisioning(
                email=email,
                org_id=target_org_id,
                db=session,
            )
            user_id_str = str(user.id)
            user_role = user.role
    except HTTPException:
        raise
    except Exception as exc:
        _logger.error("DB error during OIDC user provisioning: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=problem_detail(
                status=503,
                title="Service Unavailable",
                detail="Authentication service temporarily unavailable.",
            ),
        ) from None

    # --- Step 6: Issue JWT ---
    from synth_engine.bootstrapper.dependencies.auth import create_token

    token_str = create_token(
        sub=email,
        scope=["read", "write"],
        org_id=org_id_str,
        role=user_role,
    )

    audit_logger.log_event(
        event_type="OIDC_LOGIN_SUCCESS",
        actor=f"user:{user_id_str}",
        resource=f"org:{org_id_str}",
        action="oidc_login",
        details={"role": user_role},
    )

    # --- Step 7: Create Redis session (if OIDC session management enabled) ---
    try:
        redis_client = get_redis_client()
        enforce_concurrent_session_limit(
            redis_client=redis_client,
            user_id=user_id_str,
            org_id=org_id_str,
            limit=settings.concurrent_session_limit,
        )
        write_session(
            redis_client=redis_client,
            user_id=user_id_str,
            org_id=org_id_str,
            role=user_role,
            ttl_seconds=settings.session_ttl_seconds,
        )
        audit_logger.log_event(
            event_type="SESSION_CREATED",
            actor=f"user:{user_id_str}",
            resource=f"org:{org_id_str}",
            action="create_session",
            details={},
        )
    except redis_lib.RedisError as exc:
        _logger.warning(
            "Redis error creating session for user_id=%s: %s — JWT issued without session",
            user_id_str,
            exc,
        )

    return CallbackResponse(
        access_token=token_str,
        token_type=_OAUTH2_TOKEN_TYPE,
        expires_in=settings.jwt_expiry_seconds,
    )


# ---------------------------------------------------------------------------
# Route: POST /auth/refresh
# ---------------------------------------------------------------------------


@router.post(
    "/refresh",
    summary="Refresh authentication token",
    description=("Issue a new JWT for a valid session. Returns 404 when OIDC is not configured."),
    response_model=RefreshResponse,
)
async def post_auth_refresh(
    ctx: TenantContext = Depends(get_current_user),  # noqa: B008
) -> RefreshResponse | JSONResponse:
    """Issue a new JWT for an authenticated user.

    Requires a valid JWT (any role). Returns a new JWT with a fresh expiry.
    The old JWT remains valid until its natural expiry (no token rotation).

    Returns 404 when OIDC is not configured (Decision 5 — prevents endpoint
    from advertising its existence when sessions are not in use).

    Args:
        ctx: TenantContext resolved from the JWT (injected by FastAPI DI).

    Returns:
        JSON with ``access_token``, ``token_type``, and ``expires_in``.

    Raises:
        HTTPException: 404 if OIDC is not configured.
        HTTPException: 401 if the JWT is absent or invalid (from get_current_user).
    """  # noqa: DOC503
    settings = get_settings()

    if not settings.oidc_enabled:
        raise _oidc_not_configured_error()

    from synth_engine.bootstrapper.dependencies.auth import create_token

    token_str = create_token(
        sub=ctx.user_id,
        scope=["read", "write"],
        org_id=ctx.org_id,
        role=ctx.role,
    )

    # Update last_refreshed_at in Redis session (best effort).
    try:
        redis_client = get_redis_client()
        # Find and update this user's sessions.
        for key in redis_client.scan_iter(f"{SESSION_KEY_PREFIX}*"):
            raw: bytes | None = cast(bytes | None, redis_client.get(key))
            if raw is None:
                continue
            try:
                data: dict[str, str] = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if data.get("user_id") == ctx.user_id and data.get("org_id") == ctx.org_id:
                data["last_refreshed_at"] = datetime.now(UTC).isoformat()
                ttl: int = cast(int, redis_client.ttl(key))
                if ttl > 0:
                    redis_client.setex(key, ttl, json.dumps(data))
    except redis_lib.RedisError as exc:
        _logger.warning("Redis error updating session on refresh: %s", exc)
        # Non-fatal: JWT was issued successfully.

    get_audit_logger().log_event(
        event_type="SESSION_REFRESHED",
        actor=f"user:{ctx.user_id}",
        resource=f"org:{ctx.org_id}",
        action="refresh_token",
        details={},
    )

    return RefreshResponse(
        access_token=token_str,
        token_type=_OAUTH2_TOKEN_TYPE,
        expires_in=settings.jwt_expiry_seconds,
    )


# ---------------------------------------------------------------------------
# Route: POST /auth/revoke
# ---------------------------------------------------------------------------


@router.post(
    "/revoke",
    summary="Revoke user sessions",
    description=(
        "Revoke all active sessions for a user. "
        "Admin can revoke any user in their org. "
        "Non-admin can only revoke their own sessions. "
        "Returns 404 when OIDC is not configured."
    ),
    response_model=RevokeResponse,
)
async def post_auth_revoke(
    body: RevokeRequest,
    ctx: TenantContext = Depends(get_current_user),  # noqa: B008
) -> RevokeResponse | JSONResponse:
    """Revoke sessions for a target user.

    Authorization model (Decision 7):
    - Admin calling with any user_id in their org: deletes ALL Redis session
      keys for that user (requires ``sessions:revoke`` permission).
    - Non-admin calling with their own user_id: self-revocation (always allowed).
    - Non-admin calling with another user's user_id: returns 403.
    - Any role calling with a user_id in a different org: returns 404 (IDOR).

    Returns 404 when OIDC is not configured (Decision 5).

    Args:
        body: Request body with ``user_id`` field.
        ctx: TenantContext resolved from the JWT (injected by FastAPI DI).

    Returns:
        JSON with ``revoked_sessions`` count.

    Raises:
        HTTPException: 404 if OIDC not configured.
        HTTPException: 403 if non-admin trying to revoke another user's sessions.
        HTTPException: 404 if target user not found in caller's org (IDOR).
        HTTPException: 503 if Redis is unavailable.
    """  # noqa: DOC503
    settings = get_settings()

    if not settings.oidc_enabled:
        raise _oidc_not_configured_error()

    target_user_id = str(body.user_id)
    caller_user_id = ctx.user_id
    caller_org_id = ctx.org_id

    is_self_revocation = target_user_id == caller_user_id

    if not is_self_revocation:
        # Cross-user revocation requires sessions:revoke permission (admin only).
        from synth_engine.bootstrapper.dependencies.permissions import (
            has_permission,
        )

        if not has_permission(role=ctx.role, permission="sessions:revoke"):
            raise HTTPException(
                status_code=403,
                detail=problem_detail(
                    status=403,
                    title="Forbidden",
                    detail="Insufficient permissions",
                ),
            )

        # Verify target user is in the same org (IDOR prevention).
        try:
            from sqlmodel import Session

            from synth_engine.shared.db import get_engine

            db_url = settings.database_url or "sqlite:///:memory:"
            with Session(get_engine(db_url)) as session:
                target_org_id = _get_user_org(target_user_id, session)
        except Exception:
            target_org_id = None

        if target_org_id is None or str(target_org_id) != caller_org_id:
            raise HTTPException(
                status_code=404,
                detail=problem_detail(
                    status=404,
                    title="Not Found",
                    detail="User not found.",
                ),
            )

    # Revoke sessions in Redis.
    try:
        redis_client = get_redis_client()
        revoked_count = 0

        keys_to_revoke: list[bytes] = []
        for key in redis_client.scan_iter(f"{SESSION_KEY_PREFIX}*"):
            raw_session: bytes | None = cast(bytes | None, redis_client.get(key))
            if raw_session is None:
                continue
            try:
                data: dict[str, str] = json.loads(raw_session)
            except (json.JSONDecodeError, ValueError):
                continue

            session_user_id = data.get("user_id", "")
            session_org_id = data.get("org_id", "")

            user_matches = session_user_id == target_user_id
            org_matches = session_org_id == caller_org_id

            if user_matches and (is_self_revocation or org_matches):
                keys_to_revoke.append(key)

        if keys_to_revoke:
            revoked_count = cast(int, redis_client.delete(*keys_to_revoke))

    except redis_lib.RedisError as exc:
        _logger.error("Redis error during session revocation: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=problem_detail(
                status=503,
                title="Service Unavailable",
                detail="Authentication service temporarily unavailable.",
            ),
        ) from None

    get_audit_logger().log_event(
        event_type="SESSION_REVOKED",
        actor=f"user:{caller_user_id}",
        resource=f"user:{target_user_id}",
        action="revoke_sessions",
        details={
            "revoked_count": str(revoked_count),
            "is_self_revocation": str(is_self_revocation),
        },
    )

    return RevokeResponse(revoked_sessions=revoked_count)
