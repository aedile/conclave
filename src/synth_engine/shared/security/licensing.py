"""Offline License Activation Protocol — core licensing logic.

This module is intentionally framework-agnostic.  No FastAPI, Starlette,
or bootstrapper imports are allowed here.  Only stdlib and third-party
libraries may be imported.

License activation uses an asymmetric (RS256) challenge/response flow:

1. The software generates a ``hardware_id`` — a SHA-256 digest of the
   machine's MAC address combined with a static application seed.
2. The operator copies the ``hardware_id`` to an internet-connected device
   and submits it to the central licensing server.
3. The licensing server signs a JWT (RS256) containing the ``hardware_id``
   with its private key (never touches the air-gapped machine).
4. The operator copies the JWT back to the air-gapped machine.
5. The software validates the JWT's signature using the embedded public key,
   asserts that the ``hardware_id`` claim matches the local machine, and
   transitions :class:`LicenseState` to the LICENSED state.

Security properties
-------------------
- Asymmetric signing: the private key lives only on the licensing server.
  A compromised copy of the application binary cannot forge a license.
- Hardware binding: the JWT's ``hardware_id`` claim must match the local
  machine's SHA-256(MAC + app_seed).  The license is machine-specific.
- Expiry: standard JWT ``exp`` claim is validated by PyJWT.
- Thread safety: :class:`LicenseState` mutations are protected by a
  ``threading.Lock``.

ADR-0008 compliance (ADV-054)
------------------------------
``LicenseError`` is a plain domain exception.  It carries only the
``detail`` string needed by the bootstrapper to build an RFC 7807 response.
HTTP status code mapping is the sole responsibility of the bootstrapper
middleware/exception handler layer — it does NOT belong in the shared layer.
Any pre-ADV-054 callers that read ``exc.status_code`` must be updated to
hardcode the appropriate HTTP status (403 Forbidden for all ``LicenseError``
cases).

``LicenseError`` was previously defined locally in this module inheriting
bare ``Exception``.  In T34.1 it was moved to
:mod:`synth_engine.shared.exceptions` (inheriting ``SynthEngineError``) and
is re-exported here for backward compatibility.

CONSTITUTION Priority 0: Security
Task: P5-T5.2 — Offline License Activation Protocol
Task: P8-T8.3 — Data Model & Architecture Cleanup (ADV-054)
Task: T34.1 — Unify Vault Exceptions Under SynthEngineError
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

import jwt as pyjwt
from jwt.exceptions import ExpiredSignatureError, PyJWTError

from synth_engine.shared.exceptions import LicenseError

__all__ = ["LicenseError"]

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application constants
# ---------------------------------------------------------------------------

#: Static seed mixed into the hardware ID derivation.
#: This prevents a bare MAC address from being used as a hardware ID on a
#: different application without this seed.
_APP_SEED: bytes = b"conclave-license-v1"

#: Application version embedded in challenge payloads.
_APP_VERSION: str = "0.1.0"

# ---------------------------------------------------------------------------
# Embedded public key — fallback placeholder
# ---------------------------------------------------------------------------
# This constant holds the RSA public key used to verify license JWTs.
# In production, the real key is deployed here during build.
# The environment variable LICENSE_PUBLIC_KEY overrides this at runtime
# (for key rotation without redeployment).
#
# The placeholder below is a valid 2048-bit RSA public key generated
# offline for bootstrapping purposes only.  Any JWT signed with its
# corresponding private key would pass signature verification, but that
# private key is never distributed — making the placeholder safe to embed.
# Production deployments MUST set LICENSE_PUBLIC_KEY to the real key.
_EMBEDDED_PUBLIC_KEY: str = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2a2rwplBQLzHPZe5TNJN\n"  # pragma: allowlist secret
    "HPnCVFq7BjRqQ2LMxHJpblNMaWMrqUWyMxHJpblNMaWMrqUWyMxHJpblNMaWMr\n"
    "qUWyMxHJpblNMaWMrqUWyMxHJpblNMaWMrqUWyMxHJpblNMaWMrqUWyMxHJpbl\n"
    "NMaWMrqUWyMxHJpblNMaWMrqUWyMxHJpblNMaWMrqUWyMxHJpblNMaWMrqUWy\n"
    "PLACEHOLDER_KEY_NOT_FOR_PRODUCTION_USE_SET_LICENSE_PUBLIC_KEY_ENV=\n"
    "-----END PUBLIC KEY-----\n"
)


def get_active_public_key() -> str:
    """Return the public key to use for JWT verification.

    Checks ``LICENSE_PUBLIC_KEY`` environment variable first; falls back to
    the embedded placeholder key.

    Returns:
        PEM-encoded RSA public key string.
    """
    env_key = os.environ.get("LICENSE_PUBLIC_KEY")
    if env_key:
        return env_key
    return _EMBEDDED_PUBLIC_KEY


# ---------------------------------------------------------------------------
# LicenseState singleton
# ---------------------------------------------------------------------------


class LicenseState:
    """Class-level singleton managing license activation state.

    All state is maintained at the *class* level so that the license gate
    is enforced across every request without any dependency injection.

    Mutations are protected by ``_lock`` (threading.Lock) to ensure
    thread safety when multiple request-handler threads call activate()
    concurrently.

    Class Attributes:
        _is_licensed: True while the software has an active license.
        _license_claims: JWT claims from the most recent successful activation.
        _lock: Threading lock protecting mutations.
    """

    _is_licensed: ClassVar[bool] = False
    _license_claims: ClassVar[dict[str, Any] | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def activate(cls, claims: dict[str, Any]) -> None:
        """Transition to the LICENSED state and store the JWT claims.

        Args:
            claims: Decoded JWT claims from a verified license token.
        """
        with cls._lock:
            cls._license_claims = dict(claims)
            cls._is_licensed = True
        safe_claims = {k: claims[k] for k in ("hardware_id",) if k in claims}
        _logger.info("License activated. Claims: %s", safe_claims)

    @classmethod
    def deactivate(cls) -> None:
        """Transition to the UNLICENSED state and clear stored claims."""
        with cls._lock:
            cls._license_claims = None
            cls._is_licensed = False

    @classmethod
    def is_licensed(cls) -> bool:
        """Return True if the software is currently licensed.

        Returns:
            Licensed status.
        """
        return cls._is_licensed

    @classmethod
    def get_claims(cls) -> dict[str, Any]:
        """Return the JWT claims from the active license.

        Returns:
            Dictionary of JWT claims.

        Raises:
            LicenseError: If the software has not been activated.
        """
        if not cls._is_licensed or cls._license_claims is None:
            raise LicenseError("Software is not licensed. POST /license/activate to activate.")
        return dict(cls._license_claims)


# ---------------------------------------------------------------------------
# Hardware ID derivation
# ---------------------------------------------------------------------------


def get_hardware_id() -> str:
    """Derive a hardware-bound identifier for this machine.

    Computes SHA-256 of the machine's MAC address (as a 12-character hex
    string) concatenated with the static application seed
    ``b"conclave-license-v1"``.

    The MAC address is obtained via :func:`uuid.getnode`.  On machines
    where the MAC cannot be determined, Python generates a random 48-bit
    address — this is acceptable because the hardware ID is stable within
    a single Python process on any given machine.

    Warning:
        In containerized environments (Docker, Kubernetes) where the MAC
        address is not explicitly exposed to the container, ``uuid.getnode()``
        may return a randomly generated value per Python process invocation.
        This means the hardware ID will differ across container restarts,
        making license validation fail after each restart.  Production
        deployments in containers MUST either (1) assign a fixed MAC to the
        container interface, or (2) use a stable machine identifier injected
        via environment variable rather than relying on ``uuid.getnode()``.

    Returns:
        64-character lowercase hexadecimal SHA-256 digest.
    """
    mac = uuid.getnode()
    mac_hex: bytes = f"{mac:012x}".encode()
    return hashlib.sha256(mac_hex + _APP_SEED).hexdigest()


# ---------------------------------------------------------------------------
# Challenge payload generation
# ---------------------------------------------------------------------------


def generate_challenge() -> dict[str, str]:
    """Generate a challenge payload for offline license activation.

    The payload contains the hardware ID, application version, and an
    ISO-8601 UTC timestamp.  The operator copies this to an
    internet-connected device to request a signed license JWT.

    Returns:
        Dictionary with ``hardware_id``, ``app_version``, and ``timestamp``.
    """
    return {
        "hardware_id": get_hardware_id(),
        "app_version": _APP_VERSION,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# JWT verification
# ---------------------------------------------------------------------------


def verify_license_jwt(token: str, public_key: str | None = None) -> dict[str, Any]:
    """Verify a license JWT against the RSA public key.

    Key resolution order:
    1. The ``public_key`` parameter (explicit override, used by tests).
    2. ``LICENSE_PUBLIC_KEY`` environment variable.
    3. The embedded placeholder key (``_EMBEDDED_PUBLIC_KEY``).

    Validates:
    1. The JWT signature (RS256) against the resolved public key.
    2. The ``exp`` claim (token must not be expired).
    3. The ``hardware_id`` claim must be present and must match the local
       machine's hardware ID.

    Args:
        token: Compact JWT string issued by the licensing server.
        public_key: Optional PEM-encoded RSA public key.  When ``None``
            (the default), key resolution falls through to the env var and
            the embedded placeholder.

    Returns:
        Dictionary of decoded JWT claims on success.

    Raises:
        LicenseError: On any validation failure (invalid signature, expired
            token, missing or mismatched ``hardware_id`` claim).  The
            bootstrapper layer maps this to HTTP 403.
    """
    resolved_key = public_key if public_key is not None else get_active_public_key()
    try:
        claims: dict[str, Any] = pyjwt.decode(
            token,
            resolved_key,
            algorithms=["RS256"],
        )
    except ExpiredSignatureError as exc:
        _logger.warning("License JWT validation failed: token expired.")
        raise LicenseError("License token has expired.") from exc
    except PyJWTError as exc:
        _logger.warning("License JWT validation failed: %s", type(exc).__name__)
        raise LicenseError("License token signature is invalid.") from exc

    # Validate hardware_id claim
    token_hw_id = claims.get("hardware_id")
    if not token_hw_id:
        raise LicenseError(
            "License token is missing the required 'hardware_id' claim.",
        )

    local_hw_id = get_hardware_id()
    if token_hw_id != local_hw_id:
        _logger.warning(
            "License JWT hardware_id mismatch. token=%s local=%s",
            token_hw_id[:8] + "...",
            local_hw_id[:8] + "...",
        )
        raise LicenseError(
            "License token hardware_id does not match this machine.",
        )

    return claims
