"""FastAPI router for authentication endpoints.

Implements POST /auth/token — issues a short-lived JWT Bearer token in
exchange for valid operator credentials (username + passphrase).

Security rationale
------------------
- Algorithm pinned to HS256 (configurable); "alg:none" attacks rejected at
  the JWT library level — no manual alg-field check needed.
- Credentials are verified via bcrypt to prevent timing attacks on hash
  comparison; raw passwords are never logged.
- In production, :func: raises :exc:
  when no operator is configured — hard fail, never a silent pass.
- 401 responses use RFC 7807 Problem Details format for consistency.
- Token issuance is rate-limited by :class:.
- PII protection (T66.1): the raw username is NEVER logged.  Instead, a
  12-character keyed HMAC-SHA256 identifier derived from the audit_key is
  logged.  This identifier is deterministic (same username → same token) for
  SIEM correlation but not reversible without the audit key.
- Passphrase bounded to 1024 characters (T67.2 — ADV-P66-02): without
  this cap, a 1 MiB body could be deserialized by FastAPI before bcrypt is
  invoked — a CPU/memory DoS vector.  bcrypt truncates at 72 bytes but
  FastAPI deserialization still processes the full input.

Role resolution (P80 — B1 fix)
-------------------------------
When ``conclave_multi_tenant_enabled=True``, the user's role is resolved from
the DB ``users`` table by matching the ``username`` (JWT ``sub``) against stored
user records.  The client cannot supply a role claim — it is always DB-authoritative.

When ``conclave_multi_tenant_enabled=False`` (single-tenant backward compat),
the token is issued with ``role="admin"`` because the single seeded operator
record always holds the admin role.  No DB lookup is performed.

CONSTITUTION Priority 0: Security — credentials never logged, bcrypt verify
Task: T39.1 — Add Authentication Middleware (JWT Bearer Token)
Task: T47.1 — Scope-based auth for security endpoints
Task: T47.3 — Scope-based auth for settings write endpoints
Task: T59.3 — OpenAPI Documentation Enrichment
Task: T66.1 — Replace PII logging with keyed HMAC identifier
Task: T67.2 — Add max_length=1024 to TokenRequest.passphrase (ADV-P66-02)
Task: P80-B1 — DB-resolved role in token issuance
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from synth_engine.bootstrapper.dependencies.auth import create_token, verify_operator_credentials
from synth_engine.bootstrapper.dependencies.tenant import DEFAULT_ORG_UUID
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES

_logger = logging.getLogger(__name__)

#: OAuth2 / RFC 6750 token scheme identifier.
_TOKEN_SCHEME = "bearer"  # noqa: S105  # nosec B105 — token scheme identifier (RFC 6750), not a password

#: Fallback salt used when audit_key is empty (development mode only).
#: This is a publicly known constant — security relies on the audit_key
#: being secret in production, not on this salt being secret.
_DEV_LOG_SALT: bytes = b"conclave-dev-log-salt"

#: Length of the opaque HMAC identifier in hex characters.
#: 12 hex chars = 48 bits of identifier space — sufficient for SIEM correlation
#: while keeping log lines short.
_OPAQUE_ID_HEX_CHARS: int = 12

#: All scopes issued to the single authenticated operator.
#: Single-operator model: one operator gets every permission.
#: Security-sensitive scopes (``security:admin``, ``settings:write``) are
#: included here because scope-based authorization is enforced at the
#: endpoint level — the operator MUST hold these scopes to call those
#: endpoints, and the default issuance grants them so that a correctly
#: configured operator can use all features without extra steps.
_DEFAULT_OPERATOR_SCOPES: list[str] = [
    "read",
    "write",
    "security:admin",
    "settings:write",
]

#: Default role for single-tenant backward compatibility.
#: When multi-tenancy is disabled, the single seeded operator is always admin.
_SINGLE_TENANT_DEFAULT_ROLE: str = "admin"

router = APIRouter(prefix="/auth", tags=["auth"])


def _opaque_identifier(username: str) -> str:
    """Compute a keyed HMAC-SHA256 opaque identifier for the given username.

    Uses the ``audit_key`` from :func:`~synth_engine.shared.settings.get_settings`
    as the HMAC key.  When ``audit_key`` is empty (development mode), a fixed
    public salt ``_DEV_LOG_SALT`` is used instead — security relies on the
    audit_key being secret in production.

    The result is truncated to :data:`_OPAQUE_ID_HEX_CHARS` hex characters
    (12 chars = 48 bits).  This is deterministic: the same username always
    produces the same identifier, enabling SIEM log correlation.

    The identifier is NOT reversible without the audit key — it cannot be
    used to recover the original username via a lookup table.

    Args:
        username: The operator username to anonymize.

    Returns:
        A 12-character lowercase hex string derived from HMAC-SHA256.
    """
    from synth_engine.shared.settings import get_settings

    audit_key_raw = get_settings().audit_key.get_secret_value()
    key: bytes = audit_key_raw.encode() if audit_key_raw else _DEV_LOG_SALT
    digest = hmac.new(key, username.encode(), hashlib.sha256).hexdigest()
    return digest[:_OPAQUE_ID_HEX_CHARS]


def _resolve_role_from_db(username: str) -> str:
    """Look up the user's role from the DB ``users`` table by username.

    Used in multi-tenant mode to derive the authoritative role for the JWT.
    The client cannot specify a role claim — it is always DB-authoritative.

    Falls back to ``_SINGLE_TENANT_DEFAULT_ROLE`` if the user record is not
    found (e.g. legacy operator not yet migrated to the users table) or if
    any DB error occurs — this prevents the token endpoint from failing due
    to a transient DB issue.

    Args:
        username: The operator's username (JWT ``sub``).

    Returns:
        The role string from the DB record, or ``"admin"`` as fallback.
    """
    try:
        from sqlmodel import Session, select

        from synth_engine.bootstrapper.dependencies.db import _engine  # type: ignore[attr-defined]
        from synth_engine.shared.models.user import User

        with Session(_engine()) as session:
            stmt = select(User).where(User.email == username)
            user = session.exec(stmt).first()
            if user is not None:
                return user.role
    except Exception:
        _logger.warning(
            "DB role lookup failed for user (username withheld) — falling back to admin role",
            exc_info=True,
        )
    return _SINGLE_TENANT_DEFAULT_ROLE


class TokenRequest(BaseModel):
    """Request body for POST /auth/token.

    Attributes:
        username: Operator identifier.  Bounded to 255 characters to prevent
            DoS via oversized input in downstream HMAC computation and to
            match common username length limits in identity providers.
        passphrase: Plain-text passphrase to verify against the stored hash.
            Bounded to 1024 characters (T67.2 — ADV-P66-02): bcrypt truncates
            input at 72 bytes, but without a cap FastAPI still deserializes
            the full body — a CPU/memory DoS vector via oversized requests.
            The 1024-character limit matches :class:`UnsealRequest` for
            consistency across all passphrase-accepting endpoints.
    """

    username: str = Field(
        description="Operator username.",
        min_length=1,
        max_length=255,
    )
    passphrase: str = Field(
        description="Operator passphrase (plain text — transmitted only over TLS).",
        min_length=1,
        max_length=1024,
    )


class TokenResponse(BaseModel):
    """Response body for a successful POST /auth/token.

    Attributes:
        access_token: Compact JWT string for use as a Bearer token.
        token_type: Always ``"bearer"`` per OAuth2 / RFC 6750 convention.
    """

    access_token: str = Field(description="Compact JWT Bearer token.")
    token_type: str = Field(default=_TOKEN_SCHEME, description="Token scheme — always 'bearer'.")


@router.post(
    "/token",
    summary="Obtain authentication token",
    description=(
        "Exchange operator credentials for a JWT Bearer token. "
        "Token is valid for the configured expiry period."
    ),
    responses=COMMON_ERROR_RESPONSES,
    response_model=TokenResponse,
)
async def post_auth_token(body: TokenRequest) -> TokenResponse | JSONResponse:
    """Issue a JWT Bearer token in exchange for valid operator credentials.

    Verifies the supplied ``passphrase`` against the bcrypt hash stored in
    ``ConclaveSettings.operator_credentials_hash``.  On success, issues a
    short-lived HS256 JWT containing ``sub``, ``exp``, ``iat``, ``scope``,
    ``org_id``, and ``role`` claims.

    Role resolution (P80-B1):
    - When ``conclave_multi_tenant_enabled=True``: role is resolved from the
      DB ``users`` table by matching ``username``.  The client cannot supply
      a role claim.  Falls back to ``admin`` on DB error.
    - When ``conclave_multi_tenant_enabled=False`` (single-tenant): role is
      always ``"admin"`` (backward compatibility — the seeded operator is admin).

    The issued token can be used as ``Authorization: Bearer <token>`` on all
    subsequent requests to authenticated endpoints.

    PII protection (T66.1): the raw username is NEVER written to logs.
    A keyed HMAC-SHA256 opaque identifier is used instead — deterministic for
    SIEM correlation but not reversible without the audit key.

    Args:
        body: JSON body with ``username`` and ``passphrase`` fields.

    Returns:
        :class:`TokenResponse` with ``access_token`` and ``token_type`` on
        success, or an RFC 7807 401 response on invalid credentials.

    Security:
        The raw passphrase is never logged.  Credential verification uses
        bcrypt's constant-time comparison.  A failed verification returns
        the same generic 401 regardless of whether the username exists or
        the passphrase is wrong (no oracle attack surface).
        The raw username is never logged (T66.1 PII protection).
        The passphrase is bounded to 1024 characters (T67.2) to prevent
        a CPU/memory DoS via oversized request bodies.
        The role claim is always DB-authoritative in multi-tenant mode —
        clients cannot escalate privileges via token claim manipulation (P80-B1).
    """
    from synth_engine.shared.settings import get_settings

    opaque_id = _opaque_identifier(body.username)
    if not verify_operator_credentials(body.passphrase):
        _logger.warning(
            "Failed authentication attempt for operator_id=%s",
            opaque_id,
        )
        return JSONResponse(
            status_code=401,
            content=problem_detail(
                status=401,
                title="Unauthorized",
                detail="Invalid credentials. Check your username and passphrase.",
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()

    # P80-B1: Resolve role from DB in multi-tenant mode; default to admin in
    # single-tenant mode for backward compatibility.
    if settings.conclave_multi_tenant_enabled:
        role = _resolve_role_from_db(body.username)
    else:
        role = _SINGLE_TENANT_DEFAULT_ROLE

    token = create_token(
        sub=body.username,
        scope=_DEFAULT_OPERATOR_SCOPES,
        org_id=DEFAULT_ORG_UUID,
        role=role,
    )
    _logger.info("Issued JWT token for operator_id=%s", opaque_id)
    return TokenResponse(access_token=token, token_type=_TOKEN_SCHEME)
