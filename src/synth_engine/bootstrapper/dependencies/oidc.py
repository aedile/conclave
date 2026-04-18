"""OIDC provider integration dependency — Phase 81.

Provides:
- :func:`initialize_oidc_provider`: Fetches OpenID Connect discovery document
  and JWKS at application startup. Fail-closed: raises on any error when OIDC
  is enabled.
- :func:`maybe_initialize_oidc_provider`: Boot-time wrapper that skips
  initialization when ``OIDC_ENABLED=false``.
- :func:`get_oidc_provider`: Returns the cached provider state (raises if
  OIDC not initialized).
- :func:`make_state_redis_key`: Constructs the Redis key for OIDC state storage.
- :func:`validate_state_value`: Validates a state value as URL-safe base64
  without colon characters.

OIDC Architecture (ADR-0067):
------------------------------
Authorization Code Flow with PKCE S256 only. No implicit flow.
State and PKCE verifier stored in Redis under a one-time-use key with TTL.
JWKS cached in memory at boot time — key rotation requires app restart.

Security properties:
- Boot-time fail-closed: OIDC enabled but IdP unreachable → startup fails.
- State is single-use: deleted atomically on first use.
- PKCE S256 mandatory: plain method rejected.
- Role from IdP claims: IGNORED. DB-authoritative always.
- SSRF protection: validate_oidc_issuer_url() blocks metadata endpoints.
- HTTP issuer in production: CRITICAL warning logged (B7).

Module Boundary:
    Lives in ``bootstrapper/dependencies/`` — NOT in ``shared/`` or ``modules/``.
    OIDC is an HTTP-layer authentication concern.

CONSTITUTION Priority 0: Security — OIDC provider trust, PKCE, SSRF prevention
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Phase: 81 — SSO/OIDC Integration
ADR: ADR-0067 — OIDC Integration
Review fix: B7 (HTTPS enforcement warning), F10 (remove unused client_secret param)
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from synth_engine.shared.ssrf import validate_oidc_issuer_url

_logger = logging.getLogger(__name__)

#: Regex for URL-safe base64 characters (RFC 4648 §5).
#: Used to validate state values before they are embedded in Redis key names.
_URL_SAFE_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9\-_]+$")

#: Maximum length of a state value (prevents oversized Redis key names).
_STATE_VALUE_MAX_LENGTH: int = 256

#: Timeout in seconds for IdP discovery and JWKS fetch at boot time.
_IDP_BOOT_TIMEOUT_SECONDS: float = 10.0


class OIDCProvider:
    """Cached OIDC provider configuration fetched at boot time.

    Stores the discovery document claims and JWKS data fetched from the IdP.
    Refreshing requires an application restart (ADR-0067).

    Attributes:
        issuer: The IdP issuer URL (from discovery document).
        authorization_endpoint: Authorization endpoint URL.
        token_endpoint: Token exchange endpoint URL.
        jwks_uri: JWKS endpoint URL (stored but JWKS fetched separately).
        client_id: The registered client ID.
        jwks_data: Raw JWKS JSON data for token verification.

    Args:
        issuer: The IdP issuer URL (from discovery document).
        authorization_endpoint: Authorization endpoint URL.
        token_endpoint: Token exchange endpoint URL.
        jwks_uri: JWKS endpoint URL (stored but JWKS fetched separately).
        client_id: The registered client ID.
        jwks_data: Raw JWKS JSON data for token verification.
    """

    def __init__(
        self,
        *,
        issuer: str,
        authorization_endpoint: str,
        token_endpoint: str,
        jwks_uri: str,
        client_id: str,
        jwks_data: dict[str, Any],
    ) -> None:
        self.issuer = issuer
        self.authorization_endpoint = authorization_endpoint
        self.token_endpoint = token_endpoint
        self.jwks_uri = jwks_uri
        self.client_id = client_id
        self.jwks_data = jwks_data


#: Module-level singleton. Set by initialize_oidc_provider() at boot time.
#: None when OIDC is disabled.
_OIDC_PROVIDER: OIDCProvider | None = None


def initialize_oidc_provider(
    *,
    issuer_url: str,
    client_id: str,
) -> OIDCProvider:
    """Fetch the OIDC discovery document and JWKS, cache the result.

    Called once at application startup when OIDC is enabled. Fails closed:
    any error during initialization raises an exception, preventing the
    application from starting with an unconfigured OIDC provider.

    The discovery document is fetched from ``<issuer_url>/.well-known/openid-configuration``.
    Required fields: ``issuer``, ``authorization_endpoint``, ``token_endpoint``, ``jwks_uri``.

    In production mode (``CONCLAVE_ENV=production``), if the issuer URL uses
    ``http://`` (not HTTPS), a CRITICAL warning is logged. The fetch proceeds
    because the IdP may be behind a TLS-terminating proxy (B7).

    Args:
        issuer_url: The OIDC provider issuer URL. Validated against SSRF rules
            (RFC-1918 allowed for air-gap; cloud metadata always blocked).
        client_id: The OIDC client ID registered with the IdP.

    Returns:
        :class:`OIDCProvider` instance with cached discovery document and JWKS.

    Raises:
        ValueError: If the issuer URL fails SSRF validation.
        RuntimeError: If the discovery document is unreachable, not valid JSON,
            or missing required fields. If the JWKS endpoint is unreachable or
            returns invalid data.
    """  # noqa: DOC503
    global _OIDC_PROVIDER

    # Validate the issuer URL against SSRF rules (Decision 2).
    validate_oidc_issuer_url(issuer_url)

    # B7: Warn when the issuer URL uses HTTP in production mode.
    from synth_engine.shared.settings import get_settings

    _settings = get_settings()
    if issuer_url.lower().startswith("http://") and _settings.is_production():
        _logger.critical(
            "OIDC issuer URL uses HTTP in production mode — JWKS fetch is not encrypted. "
            "Configure the IdP with HTTPS or ensure TLS is terminated by a proxy before "
            "the Conclave Engine. issuer_url=%r",
            issuer_url,
        )

    # Normalize issuer URL (strip trailing slash for consistent key construction).
    issuer_url = issuer_url.rstrip("/")
    discovery_url = f"{issuer_url}/.well-known/openid-configuration"

    _logger.info(
        "Fetching OIDC discovery document from %s",
        discovery_url,
    )

    try:
        resp = httpx.get(discovery_url, timeout=_IDP_BOOT_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"OIDC discovery document fetch failed for {discovery_url!r}: {exc}. "
            "Ensure the IdP is reachable and OIDC_ISSUER_URL is correct."
        ) from exc

    try:
        discovery = resp.json()
    except Exception as exc:
        raise RuntimeError(
            f"OIDC discovery document at {discovery_url!r} is not valid JSON: {exc}."
        ) from exc

    # Validate required fields.
    required_fields = (
        "issuer",
        "authorization_endpoint",
        "token_endpoint",
        "jwks_uri",
    )
    missing = [f for f in required_fields if not discovery.get(f)]
    if missing:
        raise RuntimeError(
            f"OIDC discovery document at {discovery_url!r} is missing required "
            f"fields: {missing!r}. "
            "Ensure the IdP returns a valid OpenID Connect discovery document."
        )

    jwks_uri = discovery["jwks_uri"]
    _logger.info("Fetching OIDC JWKS from %s", jwks_uri)

    try:
        jwks_resp = httpx.get(jwks_uri, timeout=_IDP_BOOT_TIMEOUT_SECONDS)
        jwks_resp.raise_for_status()
        jwks_data = jwks_resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"OIDC JWKS fetch failed for {jwks_uri!r}: {exc}.") from exc
    except Exception as exc:
        raise RuntimeError(f"OIDC JWKS at {jwks_uri!r} is not valid JSON: {exc}.") from exc

    provider = OIDCProvider(
        issuer=discovery["issuer"],
        authorization_endpoint=discovery["authorization_endpoint"],
        token_endpoint=discovery["token_endpoint"],
        jwks_uri=jwks_uri,
        client_id=client_id,
        jwks_data=jwks_data,
    )

    _OIDC_PROVIDER = provider

    _logger.info(
        "OIDC provider initialized: issuer=%s client_id=%s",
        provider.issuer,
        client_id,
    )

    return provider


def maybe_initialize_oidc_provider() -> None:
    """Initialize the OIDC provider at boot time if OIDC is enabled.

    Reads settings to determine whether OIDC is enabled. If not enabled,
    returns immediately without making any network calls.

    If OIDC is enabled and initialization fails, the exception propagates
    to prevent the application from starting (fail-closed).

    Raises:
        ValueError: If the issuer URL fails SSRF validation.
        RuntimeError: If the IdP is unreachable or returns invalid data.
    """  # noqa: DOC502
    from synth_engine.shared.settings import get_settings

    settings = get_settings()

    if not settings.oidc_enabled:
        _logger.debug("OIDC not enabled — skipping provider initialization.")
        return

    initialize_oidc_provider(
        issuer_url=settings.oidc_issuer_url,
        client_id=settings.oidc_client_id,
    )


def get_oidc_provider() -> OIDCProvider:
    """Return the cached OIDC provider state.

    Returns:
        The :class:`OIDCProvider` instance set at boot time.

    Raises:
        RuntimeError: If OIDC was not initialized (OIDC disabled or boot
            failed).
    """
    if _OIDC_PROVIDER is None:
        raise RuntimeError(
            "OIDC provider not initialized. "
            "Ensure OIDC_ENABLED=true and initialize_oidc_provider() was called at startup."
        )
    return _OIDC_PROVIDER


def make_state_redis_key(state_value: str) -> str:
    """Construct the Redis key for an OIDC state value.

    Key format: ``conclave:oidc:state:<state_value>``

    The state_value is pre-validated by :func:`validate_state_value` before
    being embedded in the key name.

    Args:
        state_value: The URL-safe base64 state value generated during authorize.

    Returns:
        The full Redis key string for this state value.
    """
    return f"conclave:oidc:state:{state_value}"


def validate_state_value(state_value: str) -> None:
    """Validate that a state value is safe to use as a Redis key suffix.

    Accepts URL-safe base64 characters only (A-Z, a-z, 0-9, hyphen, underscore).
    Rejects values containing colons (would allow key namespace injection) or
    any non-URL-safe characters.

    Args:
        state_value: The state value to validate.

    Raises:
        ValueError: If the state value is empty, too long, or contains
            invalid characters (colons, spaces, non-URL-safe chars).
    """
    if not state_value:
        raise ValueError("State value must not be empty.")

    if len(state_value) > _STATE_VALUE_MAX_LENGTH:
        raise ValueError(
            f"State value is too long ({len(state_value)} chars). "
            f"Maximum allowed: {_STATE_VALUE_MAX_LENGTH} chars."
        )

    if ":" in state_value:
        raise ValueError(
            f"State value {state_value!r} contains a colon character. "
            "Colon is not allowed in state values — it would allow Redis key "
            "namespace injection."
        )

    if not _URL_SAFE_PATTERN.match(state_value):
        raise ValueError(
            f"State value {state_value!r} contains non-URL-safe characters. "
            "Only A-Z, a-z, 0-9, hyphen (-), and underscore (_) are allowed."
        )
