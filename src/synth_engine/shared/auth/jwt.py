"""Zero-Trust JWT authentication with client IP/mTLS binding.

Every access token is cryptographically bound to the client identity
(mTLS Subject Alternative Name or IP address) that was present at
issuance.  Re-use from a different origin is detected at validation
time and rejected with HTTP 401.

The binding uses a SHA-256 hash of the raw client identifier so that
the token never contains a plain-text IP or SAN.
"""

import hashlib
import os
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import BaseModel
from starlette.requests import Request

from synth_engine.shared.auth.scopes import Scope, has_required_scope

# ---------------------------------------------------------------------------
# OAuth2 scheme — token URL is handled by the bootstrapper router
# ---------------------------------------------------------------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


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

    return request.client.host  # type: ignore[union-attr]


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

    return str(jwt.encode(claims, config.secret_key, algorithm=config.algorithm))


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def verify_token(token: str, request: Request, config: JWTConfig) -> TokenPayload:
    """Verify a JWT and assert client binding.

    Decodes the token, validates the signature and expiry, then computes
    the SHA-256 hash of the current request's client identifier and
    compares it to the ``bound_client_hash`` claim.

    Args:
        token: Compact serialised JWT string.
        request: The incoming request used to derive the client identifier.
        config: JWT signing configuration used at issuance.

    Returns:
        Decoded and validated :class:`TokenPayload`.

    Raises:
        HTTPException: 401 when the token is expired, has an invalid
            signature, or is not bound to the current client.
    """
    try:
        raw_payload: dict[str, Any] = jwt.decode(
            token,
            config.secret_key,
            algorithms=[config.algorithm],
        )
    except ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    payload = TokenPayload.model_validate(raw_payload)

    client_identifier = extract_client_identifier(request, config.trusted_proxy_header)
    expected_hash = _hash_client_identifier(client_identifier)

    if payload.bound_client_hash != expected_hash:
        raise HTTPException(status_code=401, detail="Token not bound to this client")

    return payload


# ---------------------------------------------------------------------------
# FastAPI dependency factory
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


# Type alias for the async dependency signature returned by get_current_user.
_DependencyFn = Callable[..., Coroutine[Any, Any, TokenPayload]]


def get_current_user(required_scope: Scope | None = None) -> _DependencyFn:
    """FastAPI dependency factory that validates a JWT and optionally checks scope.

    Returns an ``async`` dependency that:
    1. Extracts the bearer token via ``oauth2_scheme``.
    2. Calls :func:`verify_token` to validate signature, expiry, and client binding.
    3. When *required_scope* is supplied, checks that the token's scopes satisfy
       it via :func:`~synth_engine.shared.auth.scopes.has_required_scope`.

    Args:
        required_scope: Optional scope that the caller must possess.  When
            ``None`` any valid token is accepted.

    Returns:
        An async FastAPI dependency callable that yields :class:`TokenPayload`.

    Example::

        @router.get("/datasets")
        async def list_datasets(
            user: TokenPayload = Depends(get_current_user(Scope.READ_RESULTS)),
        ) -> list[DatasetSummary]:
            ...
    """

    async def dependency(
        request: Request,
        token: str = Depends(oauth2_scheme),
        config: JWTConfig = Depends(get_jwt_config),
    ) -> TokenPayload:
        """Validate the bearer token and enforce the required scope.

        Args:
            request: Incoming request (injected by FastAPI).
            token: Bearer token extracted by the OAuth2 scheme.
            config: JWT configuration resolved from the environment.

        Returns:
            Validated token payload.

        Raises:
            HTTPException: 401 for invalid/expired/unbound tokens,
                403 for insufficient scope.
        """
        payload = verify_token(token, request, config)
        if required_scope is not None and not has_required_scope(payload.scopes, required_scope):
            raise HTTPException(status_code=403, detail="Insufficient scope")
        return payload

    return dependency
