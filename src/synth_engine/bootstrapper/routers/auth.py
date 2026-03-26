"""FastAPI router for authentication endpoints.

Implements:
- POST /auth/token — issues a short-lived JWT Bearer token in exchange for
  valid operator credentials (username + passphrase).

This endpoint is explicitly exempt from :class:`AuthenticationGateMiddleware`
(listed in :data:`AUTH_EXEMPT_PATHS`) so that operators can obtain a token
before they have one.

All 401 responses use RFC 7807 Problem Details format consistent with
the rest of the application.

Authentication model
--------------------
For MVP, a single-operator model is used: one operator identity with a
bcrypt-hashed passphrase stored in ``ConclaveSettings.operator_credentials_hash``.
The username field is accepted for display/logging purposes but not used for
credential dispatch — the single configured hash is checked against the supplied
passphrase.

Future extension: replace with a multi-operator registry backed by the
vault KEK-encrypted operator store (tracked as post-T39.1 backlog item).

Token scopes
------------
The default scope list issued to any authenticated operator is:
``["read", "write", "security:admin", "settings:write"]``.

This is a single-operator system — the one configured operator receives all
scopes unconditionally.  Future multi-operator support would require
per-operator scope assignment at registration time.

CONSTITUTION Priority 0: Security — credentials never logged, bcrypt verify
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T39.1 — Add Authentication Middleware (JWT Bearer Token)
Task: T47.1 — Scope-based auth for security endpoints
Task: T47.3 — Scope-based auth for settings write endpoints
Task: T59.3 — OpenAPI Documentation Enrichment
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from synth_engine.bootstrapper.dependencies.auth import create_token, verify_operator_credentials
from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES

_logger = logging.getLogger(__name__)

#: OAuth2 / RFC 6750 token scheme identifier.
_TOKEN_SCHEME = "bearer"  # noqa: S105  # nosec B105 — token scheme identifier (RFC 6750), not a password

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

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenRequest(BaseModel):
    """Request body for POST /auth/token.

    Attributes:
        username: Operator identifier (logged but not used for dispatch in MVP).
        passphrase: Plain-text passphrase to verify against the stored hash.
    """

    username: str = Field(
        description="Operator username.",
        min_length=1,
    )
    passphrase: str = Field(
        description="Operator passphrase (plain text — transmitted only over TLS).",
        min_length=1,
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
    short-lived HS256 JWT containing ``sub``, ``exp``, ``iat``, and
    ``scope`` claims.

    The issued token scope list is :data:`_DEFAULT_OPERATOR_SCOPES`, granting
    all permissions to the single configured operator including
    ``security:admin`` and ``settings:write`` for T47.1/T47.3 endpoints.

    The issued token can be used as ``Authorization: Bearer <token>`` on all
    subsequent requests to authenticated endpoints.

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
    """
    if not verify_operator_credentials(body.passphrase):
        _logger.warning(
            "Failed authentication attempt for username=%r",
            body.username,
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

    token = create_token(sub=body.username, scope=_DEFAULT_OPERATOR_SCOPES)
    _logger.info("Issued JWT token for operator=%r", body.username)
    return TokenResponse(access_token=token, token_type=_TOKEN_SCHEME)
