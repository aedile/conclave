"""Zero-Trust JWT authentication with client IP/mTLS binding.

Every access token is cryptographically bound to the client identity
(mTLS Subject Alternative Name or IP address) that was present at
issuance.  Re-use from a different origin is detected at validation
time and rejected with a :class:`TokenVerificationError`.

This module is intentionally framework-agnostic.  No FastAPI or Starlette
imports are allowed here.  The FastAPI dependency factory that translates
:class:`TokenVerificationError` into ``HTTPException`` lives in
``synth_engine.bootstrapper.dependencies.auth``.
"""

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt as pyjwt
from jwt.exceptions import ExpiredSignatureError, PyJWTError
from pydantic import BaseModel
from starlette.requests import Request

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exception — framework-agnostic
# ---------------------------------------------------------------------------


class TokenVerificationError(Exception):
    """Raised when JWT verification fails.

    Attributes:
        detail: Human-readable error description.
        status_code: HTTP status code the caller should use (401 or 400).
    """

    def __init__(self, detail: str, status_code: int = 401) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class JWTConfig:
    """Runtime configuration for JWT creation and validation.

    Attributes:
        secret_key: HMAC signing secret (HS256) or PEM key (RS256/ES256).
        algorithm: JOSE algorithm identifier.
        access_token_expire_minutes: Lifetime of issued access tokens.
        trusted_proxy_header: Header name used to extract the real client IP
            when the service sits behind a reverse proxy.
    """

    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    trusted_proxy_header: str = field(default="X-Forwarded-For")


# ---------------------------------------------------------------------------
# Token payload model
# ---------------------------------------------------------------------------


class TokenPayload(BaseModel):
    """Claims carried inside a Conclave Engine access token.

    Attributes:
        sub: Subject — user or service identifier.
        scopes: List of RBAC scope strings granted to the subject.
        bound_client_hash: SHA-256 hex digest of the client identifier
            (IP or mTLS SAN) present at token issuance.
        exp: Unix timestamp at which the token expires.
        iat: Unix timestamp at which the token was issued.
    """

    sub: str
    scopes: list[str]
    bound_client_hash: str
    exp: int
    iat: int


# ---------------------------------------------------------------------------
# Client identifier helpers
# ---------------------------------------------------------------------------


def _hash_client_identifier(identifier: str) -> str:
    """Return the SHA-256 hex digest of a client identifier string.

    Args:
        identifier: Raw client identifier (IP address or mTLS SAN).

    Returns:
        64-character lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(identifier.encode()).hexdigest()


def extract_client_identifier(request: Request, trusted_proxy_header: str) -> str:
    """Extract the raw client identifier from an incoming request.

    Priority order:

    1. ``X-Client-Cert-SAN`` header — present when mTLS is terminated
       upstream and the SAN is forwarded by the proxy.
    2. First IP in *trusted_proxy_header* — typically ``X-Forwarded-For``,
       set by a trusted reverse proxy.
    3. ``request.client.host`` — direct TCP peer address.

    Args:
        request: The incoming Starlette/FastAPI request.
        trusted_proxy_header: Name of the header carrying the real client IP
            from a trusted reverse proxy (e.g. ``"X-Forwarded-For"``).

    Returns:
        Raw (un-hashed) client identifier string.

    Raises:
        TokenVerificationError: 400 when ``request.client`` is ``None`` and no
            proxy header is present (e.g. Unix socket or minimal ASGI transport).
    """
    headers = request.headers

    mtls_san = headers.get("X-Client-Cert-SAN")
    if mtls_san:
        return mtls_san

    forwarded_for = headers.get(trusted_proxy_header)
    if forwarded_for:
        # The header may carry a comma-separated list; the leftmost is the
        # original client IP.
        return forwarded_for.split(",")[0].strip()

    if request.client is None:
        raise TokenVerificationError(
            detail="Cannot determine client identity: no client connection information available",
            status_code=400,
        )
    return request.client.host


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------


def create_access_token(
    subject: str,
    scopes: list[str],
    client_identifier: str,
    config: JWTConfig,
) -> str:
    """Create a signed JWT access token bound to the given client identifier.

    Args:
        subject: Identifier of the user or service the token is issued for.
        scopes: List of RBAC scope strings to embed in the token.
        client_identifier: Raw client identifier (IP or mTLS SAN) that will
            be hashed and stored as ``bound_client_hash``.
        config: JWT signing configuration.

    Returns:
        Compact serialised JWT string.
    """
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=config.access_token_expire_minutes)

    claims: dict[str, object] = {
        "sub": subject,
        "scopes": scopes,
        "bound_client_hash": _hash_client_identifier(client_identifier),
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
    }

    return pyjwt.encode(claims, config.secret_key, algorithm=config.algorithm)


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def verify_token(token: str, request: Request, config: JWTConfig) -> TokenPayload:
    """Verify a JWT and assert client binding.

    Decodes the token, validates the signature and expiry, then computes
    the SHA-256 hash of the current request's client identifier and
    compares it to the ``bound_client_hash`` claim using a constant-time
    comparison to prevent timing side-channels.

    Args:
        token: Compact serialised JWT string.
        request: The incoming request used to derive the client identifier.
        config: JWT signing configuration used at issuance.

    Returns:
        Decoded and validated :class:`TokenPayload`.

    Raises:
        TokenVerificationError: 401 when the token is expired, has an invalid
            signature, or is not bound to the current client.  400 when the
            client identity cannot be determined.
    """
    try:
        raw_payload: dict[str, Any] = pyjwt.decode(
            token,
            config.secret_key,
            algorithms=[config.algorithm],
        )
    except ExpiredSignatureError as exc:
        raise TokenVerificationError(detail="Token expired", status_code=401) from exc
    except PyJWTError as exc:
        raise TokenVerificationError(detail="Invalid token", status_code=401) from exc

    payload = TokenPayload.model_validate(raw_payload)

    client_identifier = extract_client_identifier(request, config.trusted_proxy_header)
    expected_hash = _hash_client_identifier(client_identifier)

    if not hmac.compare_digest(payload.bound_client_hash, expected_hash):
        raise TokenVerificationError(
            detail="Token not bound to this client",
            status_code=401,
        )

    return payload


# ---------------------------------------------------------------------------
# Environment configuration factory
# ---------------------------------------------------------------------------


def get_jwt_config() -> JWTConfig:
    """Read JWT configuration from the environment.

    Reads ``JWT_SECRET_KEY`` from environment variables.  This function is
    intended to be used as a FastAPI dependency.

    Returns:
        Populated :class:`JWTConfig`.

    Raises:
        RuntimeError: When ``JWT_SECRET_KEY`` is not set in the environment.
    """
    secret = os.environ.get("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY environment variable is required but not set.")
    return JWTConfig(secret_key=secret)
