"""FastAPI dependency factory for JWT zero-trust authentication.

This module provides the web-framework-binding layer over the pure JWT
logic in :mod:`synth_engine.shared.auth.jwt`.  Placed in
``bootstrapper/dependencies/`` so that ``shared/`` remains
framework-agnostic per the Modular Monolith architectural contract.

The :func:`get_current_user` factory translates
:class:`~synth_engine.shared.auth.jwt.TokenVerificationError` (a plain
Python exception) into FastAPI ``HTTPException`` responses, keeping the
framework coupling out of the shared layer.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from starlette.requests import Request

from synth_engine.shared.auth.jwt import (
    JWTConfig,
    TokenPayload,
    TokenVerificationError,
    get_jwt_config,
    verify_token,
)
from synth_engine.shared.auth.scopes import Scope, has_required_scope

# OAuth2 bearer-token extractor — tokenUrl handled by the bootstrapper router.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

_DependencyFn = Callable[..., Coroutine[Any, Any, TokenPayload]]


def get_current_user(required_scope: Scope | None = None) -> _DependencyFn:
    """FastAPI dependency factory that validates a JWT and optionally checks scope.

    Returns an ``async`` dependency that:

    1. Extracts the bearer token via ``oauth2_scheme``.
    2. Calls :func:`~synth_engine.shared.auth.jwt.verify_token` to validate
       signature, expiry, and client binding.
    3. When *required_scope* is supplied, checks that the token's scopes
       satisfy it via :func:`~synth_engine.shared.auth.scopes.has_required_scope`.

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
            HTTPException: 400/401 for invalid/expired/unbound tokens,
                403 for insufficient scope.
        """
        try:
            payload = verify_token(token, request, config)
        except TokenVerificationError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc
        if required_scope is not None and not has_required_scope(payload.scopes, required_scope):
            raise HTTPException(status_code=403, detail="Insufficient scope")
        return payload

    return dependency
