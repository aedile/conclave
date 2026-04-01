"""Startup configuration validation for the Conclave Engine bootstrapper.

Provides :func:`validate_config`, a fail-fast guard that inspects environment
variables at application startup and raises :exc:`SystemExit` with a clear,
actionable error message if any required configuration is absent.

Required in all deployment modes:
  - ``DATABASE_URL``  — async-compatible PostgreSQL DSN (e.g. ``postgresql+asyncpg://...``).
  - ``AUDIT_KEY``     — hex-encoded HMAC key for the audit logger.

Required additionally in production mode (``CONCLAVE_ENV=production`` — ``ENV=`` is deprecated):
  - ``ARTIFACT_SIGNING_KEY`` — hex-encoded HMAC key for ModelArtifact pickle signing.
  - ``MASKING_SALT``         — secret salt for deterministic HMAC masking.  Without
    this, production masking falls back to a hardcoded development salt, making
    masked values reversible by anyone with access to the source code.
  - ``JWT_SECRET_KEY``       — HMAC secret for JWT signing and verification.  An
    empty or whitespace-only value silently disables all authenticated routes.
  - ``OPERATOR_CREDENTIALS_HASH`` — bcrypt hash of the operator passphrase.  Must
    start with ``$2b$`` and be at least 59 characters.  Without a valid hash,
    token issuance always fails at runtime.

Multi-key signing consistency (T42.1):
  When ``ARTIFACT_SIGNING_KEYS`` is non-empty, ``ARTIFACT_SIGNING_KEY_ACTIVE`` must
  be set and must exist as a key within the map.  This is validated in all deployment
  modes to prevent silent misconfiguration during rotation.

mTLS cert file validation (T46.2):
  When ``MTLS_ENABLED=true``, the three cert path settings
  (``MTLS_CA_CERT_PATH``, ``MTLS_CLIENT_CERT_PATH``, ``MTLS_CLIENT_KEY_PATH``)
  must each point to an existing, readable file.  All three are checked before
  raising so that the operator receives a complete error in one pass.

  The readability check uses an atomic ``open()`` attempt (ADV-P46-03) rather than
  a separate ``os.access()`` call, to avoid a TOCTOU race between the access check
  and the actual open.

  When ``MTLS_ENABLED=true`` and ``CONCLAVE_SSL_REQUIRED=false``, a WARNING
  is logged noting that mTLS implies SSL is required — the setting is effectively
  overridden.

Design rationale (ADV-077):
  Without a startup check, a misconfigured production instance will start
  silently and then fail at runtime — potentially mid-synthesis, after PII
  has already been processed.  Fail-fast at boot time is the correct pattern
  for security-critical configuration.

  The function collects ALL missing variables before raising so that an
  operator receives a complete picture in a single error, rather than having
  to fix one variable at a time.

CONSTITUTION Priority 0: Security — fail-fast prevents silent misconfiguration
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: P9-T9.1 — Advisory Drain + Startup Validation (ADV-077)
Task: P19-T19.2 — Security Hardening: MASKING_SALT production enforcement
Task: P20-T20.4 — Architecture Tightening (ADV-020: SSL override warning)
Task: T36.1 — Centralize Configuration Into Pydantic Settings Model
Task: P36 review — Delegate _is_production() to get_settings().is_production() (QA Finding 1)
Task: T37.2 — Drain ADV-P36-01: replace remaining os.environ.get() with get_settings()
Task: T42.2 — Add HTTPS Enforcement & Deployment Safety Checks
Task: T42.1 — Artifact Signing Key Versioning (multi-key consistency validation)
Task: T46.2 — Wire mTLS on All Container-to-Container Connections
Task: T47.4 — Add JWT_SECRET_KEY to production-required validation
Task: T47.5 — Add OPERATOR_CREDENTIALS_HASH to production-required validation
Task: ADV-P46-03 — Fix cert readability check (existence + open())
Task: T48.5 — ALE Vault Dependency Enforcement (vault-sealed startup warning)
Task: T50.3 — Default to Production Mode (dev-mode startup warning)
Task: T63.3 — Rate Limiter Fail-Closed (warn fail-open in production)
Task: P63 QA review — Fix stale docstrings in _validate_jwt_secret_key and
    _validate_operator_credentials_hash after T63.1 moved production validation
    to Pydantic model_validator; remove dead production-branch code.
Advisory: ADV-P47-04 — Security route removal from exempt paths (verified here)
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from synth_engine.bootstrapper.dependencies.https_enforcement import warn_if_ssl_misconfigured
from synth_engine.shared.errors import safe_error_msg
from synth_engine.shared.settings import get_settings

# T63.1: _ALWAYS_REQUIRED and _PRODUCTION_REQUIRED removed — these field-level
# checks are now enforced by ConclaveSettings._validate_production_required_fields()
# at Pydantic model construction time.  This eliminates the duplicate check and
# ensures validation always fires at settings construction, not just at startup.

# Minimum structural length for a bcrypt hash ($2b$NN$<22-char salt><31-char hash>).
# A full bcrypt output is 60 characters; 59 is the minimum we accept to guard against
# truncation without calling bcrypt.checkpw() (which is intentionally slow).
_BCRYPT_PREFIX = "$2b$"
_BCRYPT_MIN_LENGTH = 59

_logger = logging.getLogger(__name__)


def _is_production() -> bool:
    """Return ``True`` if the current deployment mode is production.

    Delegates to :meth:`synth_engine.shared.settings.ConclaveSettings.is_production`
    via the :func:`get_settings` singleton, ensuring a single source of truth for
    production-mode detection and eliminating the duplicate ``os.environ.get()``
    calls that contradicted T36.1's centralization goal (QA Finding 1, P36 review).

    Returns:
        ``True`` when the deployment mode is production, ``False`` otherwise.
    """
    return get_settings().is_production()


def _validate_jwt_secret_key() -> None:
    """Emit a development-mode WARNING when JWT_SECRET_KEY is absent or empty.

    An absent or whitespace-only key silently disables all authenticated routes
    at runtime.  This function emits a WARNING in non-production environments
    to make the developer aware before deploying.

    Production-mode validation for this field has moved to the Pydantic
    ``model_validator`` in ``settings.py``.  This function now only emits
    non-production development-mode warnings.

    The warning is NOT emitted in production mode because production failures
    are caught at settings construction time by
    :meth:`~synth_engine.shared.settings.ConclaveSettings._validate_production_required_fields`.
    """
    settings = get_settings()
    key_value = settings.jwt_secret_key.get_secret_value().strip()

    if not key_value and not _is_production():
        _logger.warning(
            "JWT_SECRET_KEY is not set — JWT authentication will not function. "
            "Set a cryptographically random value before deploying to production."
        )


def _is_valid_bcrypt_hash(value: str) -> bool:
    """Return ``True`` when ``value`` is structurally valid as a bcrypt hash.

    Uses a fast structural check only (prefix + length) — does NOT call
    ``bcrypt.checkpw()`` which is intentionally CPU-intensive.

    Args:
        value: The string to check.

    Returns:
        ``True`` when ``value`` starts with ``$2b$`` and is at least
        :data:`_BCRYPT_MIN_LENGTH` characters long.
    """
    return value.startswith(_BCRYPT_PREFIX) and len(value) >= _BCRYPT_MIN_LENGTH


def _validate_operator_credentials_hash() -> None:
    """Emit development-mode WARNINGs when OPERATOR_CREDENTIALS_HASH is absent or invalid.

    Two checks are applied in non-production environments:

    1. **Presence**: the value must be non-empty.
    2. **Format**: the value must start with ``$2b$`` and be at least 59 characters
       (structural bcrypt validity check — no cryptographic verification).

    Production-mode validation for this field has moved to the Pydantic
    ``model_validator`` in ``settings.py``.  This function now only emits
    non-production development-mode warnings.

    Production failures are caught at settings construction time by
    :meth:`~synth_engine.shared.settings.ConclaveSettings._validate_production_required_fields`.
    The warning message always names the variable but NEVER includes the hash
    value itself, to prevent hash oracle attacks via logs.
    """
    if _is_production():
        return

    settings = get_settings()
    hash_value = settings.operator_credentials_hash

    if not hash_value:
        _logger.warning(
            "OPERATOR_CREDENTIALS_HASH is not set — "
            "POST /auth/token will always fail. "
            "Set a bcrypt hash of the operator passphrase before deploying to production."
        )
        return

    if not _is_valid_bcrypt_hash(hash_value):
        _logger.warning(
            "OPERATOR_CREDENTIALS_HASH does not appear to be a valid bcrypt hash — "
            f"expected prefix '{_BCRYPT_PREFIX}' and minimum length {_BCRYPT_MIN_LENGTH}. "
            "POST /auth/token may fail at runtime."
        )


def _check_cert_path_readable(env_var: str, path_str: str, errors: list[str]) -> None:
    """Check that a single mTLS cert path exists and is readable (ADV-P46-03).

    Uses an atomic open() attempt to avoid TOCTOU race between existence check
    and actual read.  Appends an error string to ``errors`` on failure.

    Args:
        env_var: The environment variable name (for error messages).
        path_str: The configured path string.
        errors: Mutable list; errors appended in-place.
    """
    if not path_str:
        errors.append(f"{env_var} is empty — set it to the path of the mTLS certificate file")
        return
    path = Path(path_str)
    if not path.exists():
        errors.append(
            f"{env_var}={path_str!r} does not exist — "
            f"ensure the certificate file is present before starting with MTLS_ENABLED=true"
        )
        return
    # Atomic readability check: open() catches permission errors and
    # path-is-a-directory cases without a separate os.access() call (ADV-P46-03).
    try:
        with open(path, "rb"):
            pass
    except OSError as exc:
        sanitized = safe_error_msg(str(exc))
        errors.append(
            f"{env_var}={path_str!r} exists but cannot be read — "
            f"check file permissions and ensure the process has read access: {sanitized}"
        )


def _validate_mtls_cert_files(errors: list[str]) -> None:
    """Validate mTLS cert files exist and are readable when MTLS_ENABLED=true.

    No-op when ``MTLS_ENABLED=false``.  All three cert paths are checked
    before returning so the operator receives a complete error list.

    Args:
        errors: Mutable list of error strings; appended in-place.
    """
    settings = get_settings()
    if not settings.mtls_enabled:
        return

    cert_paths: dict[str, str] = {
        "MTLS_CA_CERT_PATH": settings.mtls_ca_cert_path,
        "MTLS_CLIENT_CERT_PATH": settings.mtls_client_cert_path,
        "MTLS_CLIENT_KEY_PATH": settings.mtls_client_key_path,
    }
    for env_var, path_str in cert_paths.items():
        _check_cert_path_readable(env_var, path_str, errors)


def _warn_if_vault_sealed() -> None:
    """Emit a WARNING if the vault is sealed at startup.

    ALE operations (EncryptedString TypeDecorator) require an unsealed vault
    (T48.5).  When the vault is sealed at startup, all ALE reads and writes
    will raise VaultSealedError until ``POST /unseal`` is called.  Emitting
    a WARNING here gives operators a clear signal rather than a cryptic error
    at first DB access.

    This function does NOT raise SystemExit — a sealed vault at startup is
    expected (the application boots sealed; /unseal is called by the operator
    as a post-startup step).
    """
    from synth_engine.shared.security.vault import VaultState

    if VaultState.is_sealed():
        _logger.warning(
            "Vault is sealed at startup — ALE (Application-Level Encryption) "
            "operations will fail until the vault is unsealed. "
            "Call POST /unseal with the operator passphrase to enable ALE. "
            "See docs/OPERATIONAL_RUNBOOK.md for unseal instructions."
        )


def _warn_if_development_mode() -> None:
    """Emit a WARNING when the engine boots in development mode.

    Development mode disables authentication (JWT_SECRET_KEY is not required).
    In containerized environments, a port may be inadvertently exposed, making
    a silent dev-mode boot a security risk.  This WARNING gives operators a
    visible, unambiguous signal that authentication is disabled.

    This function does NOT raise SystemExit — development mode is a deliberate
    choice; the warning is advisory only.

    T50.3: Called by :func:`validate_config` after all error checks pass.
    Only fires when :func:`_is_production` returns ``False``.
    """
    _logger.warning(
        "Authentication disabled — development mode active. "
        "Set CONCLAVE_ENV=production for production use."
    )


def _check_always_required_fields(errors: list[str]) -> None:
    """Append errors for DATABASE_URL and AUDIT_KEY when empty in any mode.

    These fields are required in ALL modes.  In production mode, Pydantic
    already caught them at settings construction time.  Here we enforce them
    in development mode too — an empty AUDIT_KEY means all audit event HMAC
    signatures will be computed with an empty key, which is a security
    vulnerability in any mode.

    Args:
        errors: Mutable list; errors are appended in-place.
    """
    settings = get_settings()
    if not settings.database_url or not settings.database_url.strip():
        errors.append(
            "DATABASE_URL is not set or is empty — "
            "set it to a valid async-compatible PostgreSQL DSN (e.g. postgresql+asyncpg://...)"
        )
    audit_key_value = settings.audit_key.get_secret_value()
    if not audit_key_value or not audit_key_value.strip():
        errors.append(
            "AUDIT_KEY is not set or is empty — "
            "set it to a hex-encoded 32-byte HMAC key for audit event signing"
        )


def _check_production_security_settings() -> None:
    """Emit warnings and block forbidden production settings (T68.7, T46.2, T42.2).

    Handles three production-mode security checks:
    1. Block CONCLAVE_RATE_LIMIT_FAIL_OPEN=true (raises SystemExit).
    2. Warn when CONCLAVE_SSL_REQUIRED=false.
    3. Warn when MTLS_ENABLED=true but CONCLAVE_SSL_REQUIRED=false.
    4. Warn when ssl_required=True but no TLS cert path is configured.

    Raises:
        SystemExit: When CONCLAVE_RATE_LIMIT_FAIL_OPEN=true in production mode.
    """
    settings = get_settings()

    # T68.7: Block rate_limit_fail_open=True in production (security misconfiguration).
    # ADV-P67-02 resolution.
    if _is_production() and settings.conclave_rate_limit_fail_open:
        raise SystemExit(
            "Startup configuration error: CONCLAVE_RATE_LIMIT_FAIL_OPEN=true is not allowed "
            "in production mode (CONCLAVE_ENV=production). "
            "This setting disables distributed rate limiting during Redis outages, "
            "allowing brute-force and DoS attacks to bypass per-IP limits. "
            "Set CONCLAVE_RATE_LIMIT_FAIL_OPEN=false (the default) for production deployments. "
            "If you are testing fail-open behavior, set CONCLAVE_ENV=development first."
        )

    if _is_production() and not settings.conclave_ssl_required:
        _logger.warning(
            "CONCLAVE_SSL_REQUIRED=false in production mode — "
            "SSL enforcement for PostgreSQL connections is disabled. "
            "This is a security misconfiguration for production."
        )

    # T46.2: Warn when mTLS is enabled but CONCLAVE_SSL_REQUIRED is false.
    if settings.mtls_enabled and not settings.conclave_ssl_required:
        _logger.warning(
            "MTLS_ENABLED=true overrides CONCLAVE_SSL_REQUIRED=false — "
            "ssl is implicitly required when mTLS is active. "
            "Set CONCLAVE_SSL_REQUIRED=true to silence this warning."
        )

    # TLS cert misconfiguration check (T42.2).
    tls_cert_configured: bool = bool(settings.conclave_tls_cert_path)
    warn_if_ssl_misconfigured(
        ssl_required=settings.conclave_ssl_required,
        tls_cert_configured=tls_cert_configured,
    )


def validate_config() -> None:
    """Validate required environment variables at application startup.

    Fail-fast guard that inspects settings at boot and raises SystemExit
    with a clear, actionable message if any required configuration is absent
    or misconfigured.  See module docstring for full details on required
    variables by deployment mode.

    Collects ALL errors before raising so operators receive a complete list
    in a single message rather than one error at a time.

    Returns:
        ``None`` when all required variables are present and consistent.

    Raises:
        SystemExit: On missing required variables, mTLS cert errors, or
            CONCLAVE_RATE_LIMIT_FAIL_OPEN=true in production mode.
    """
    try:
        get_settings()
    except (ValidationError, ValueError) as exc:
        # T63.1: ConclaveSettings validates all required fields at construction
        # time via @model_validator.  A ValidationError/ValueError here means
        # a required field is missing or misconfigured.
        raise SystemExit(
            f"Startup configuration error: the following required environment "
            f"variable(s) are not set or are misconfigured: {exc}. "
            f"Set them before starting the Conclave Engine."
        ) from exc

    errors: list[str] = []
    _check_always_required_fields(errors)
    # T46.2 / ADV-P46-03: validate mTLS cert files exist and are readable.
    _validate_mtls_cert_files(errors)
    if errors:
        raise SystemExit(
            f"Startup configuration error: the following required environment "
            f"variable(s) are not set or are misconfigured: {', '.join(errors)}. "
            f"Set them before starting the Conclave Engine."
        )

    _validate_jwt_secret_key()  # T47.4 — dev-mode WARNING only
    _validate_operator_credentials_hash()  # T47.5 — dev-mode WARNING only
    _check_production_security_settings()  # T68.7, T46.2, T42.2 — warns / blocks
    _warn_if_vault_sealed()  # T48.5 — vault sealed at startup
    if not _is_production():
        _warn_if_development_mode()  # T50.3 — dev-mode safety warning
