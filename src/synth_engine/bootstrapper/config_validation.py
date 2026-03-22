"""Startup configuration validation for the Conclave Engine bootstrapper.

Provides :func:`validate_config`, a fail-fast guard that inspects environment
variables at application startup and raises :exc:`SystemExit` with a clear,
actionable error message if any required configuration is absent.

Required in all deployment modes:
  - ``DATABASE_URL``  — async-compatible PostgreSQL DSN (e.g. ``postgresql+asyncpg://...``).
  - ``AUDIT_KEY``     — hex-encoded HMAC key for the audit logger.

Required additionally in production mode (``ENV=production`` or
``CONCLAVE_ENV=production``):
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
"""

from __future__ import annotations

import logging
from pathlib import Path

from synth_engine.bootstrapper.dependencies.https_enforcement import warn_if_ssl_misconfigured
from synth_engine.shared.settings import get_settings

_ALWAYS_REQUIRED: tuple[str, ...] = (
    "DATABASE_URL",
    "AUDIT_KEY",
)

_PRODUCTION_REQUIRED: tuple[str, ...] = (
    "ARTIFACT_SIGNING_KEY",
    "MASKING_SALT",
)

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


def _validate_jwt_secret_key(errors: list[str]) -> None:
    """Validate that JWT_SECRET_KEY is present and non-empty in production.

    An absent or whitespace-only key silently disables all authenticated routes
    at runtime.  In production the key is fatal-if-absent; in development a
    WARNING is emitted so the developer is aware.

    The warning is NOT emitted in production mode — there it is a fatal error
    already collected into ``errors``.

    Args:
        errors: Mutable list of error strings.  Any new errors found are
            appended in-place.
    """
    settings = get_settings()
    key_value = settings.jwt_secret_key.strip()

    if not key_value:
        if _is_production():
            errors.append(
                "JWT_SECRET_KEY is not set or is empty — "
                "a non-empty HMAC secret is required to sign and verify JWT tokens in production"
            )
        else:
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


def _validate_operator_credentials_hash(errors: list[str]) -> None:
    """Validate that OPERATOR_CREDENTIALS_HASH is present and structurally valid.

    Two checks are applied:

    1. **Presence**: the value must be non-empty.
    2. **Format**: the value must start with ``$2b$`` and be at least 59 characters
       (structural bcrypt validity check — no cryptographic verification).

    In production, a failed check appends an error that will cause
    :exc:`SystemExit` at the end of :func:`validate_config`.  The error message
    always names the variable but NEVER includes the hash value itself, to prevent
    hash oracle attacks via logs.

    In development, a failed check emits a WARNING so the developer is aware
    without blocking startup.

    Args:
        errors: Mutable list of error strings.  Any new errors found are
            appended in-place.
    """
    settings = get_settings()
    hash_value = settings.operator_credentials_hash
    production = _is_production()

    if not hash_value:
        if production:
            errors.append(
                "OPERATOR_CREDENTIALS_HASH is not set — "
                "a bcrypt hash of the operator passphrase is required in production. "
                "Generate one with: python -c \"import bcrypt; print(bcrypt.hashpw(b'<passphrase>', bcrypt.gensalt()).decode())\""
            )
        else:
            _logger.warning(
                "OPERATOR_CREDENTIALS_HASH is not set — "
                "POST /auth/token will always fail. "
                "Set a bcrypt hash of the operator passphrase before deploying to production."
            )
        return

    if not _is_valid_bcrypt_hash(hash_value):
        if production:
            errors.append(
                "OPERATOR_CREDENTIALS_HASH has an invalid format — "
                f"expected a bcrypt hash starting with '{_BCRYPT_PREFIX}' "
                f"and at least {_BCRYPT_MIN_LENGTH} characters long. "
                "Generate a valid hash before starting the Conclave Engine in production."
            )
        else:
            _logger.warning(
                "OPERATOR_CREDENTIALS_HASH does not appear to be a valid bcrypt hash — "
                f"expected prefix '{_BCRYPT_PREFIX}' and minimum length {_BCRYPT_MIN_LENGTH}. "
                "POST /auth/token may fail at runtime."
            )


def _validate_mtls_cert_files(errors: list[str]) -> None:
    """Validate that mTLS cert files exist and are readable.

    Uses an atomic existence-plus-readability check: after verifying the path
    exists, the function attempts to ``open()`` the file for reading.  This
    avoids the TOCTOU race that a separate ``os.access()`` call would introduce
    (ADV-P46-03).

    Appends error messages to ``errors`` for each missing or unreadable
    cert file.  All three cert paths are checked before returning so that
    the operator receives a complete list of problems in one pass.

    This function is a no-op when ``MTLS_ENABLED=false``.

    Args:
        errors: Mutable list of error strings.  Any new errors found are
            appended in-place.
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
        if not path_str:
            errors.append(f"{env_var} is empty — set it to the path of the mTLS certificate file")
            continue
        path = Path(path_str)
        if not path.exists():
            errors.append(
                f"{env_var}={path_str!r} does not exist — "
                f"ensure the certificate file is present before starting with MTLS_ENABLED=true"
            )
            continue
        # Atomic readability check: attempt to open the file.  This catches
        # permission errors and path-is-a-directory cases without a separate
        # os.access() call that would introduce a TOCTOU race (ADV-P46-03).
        try:
            with open(path, "rb"):
                pass
        except OSError as exc:
            errors.append(
                f"{env_var}={path_str!r} exists but cannot be read — "
                f"check file permissions and ensure the process has read access: {exc}"
            )


def validate_config() -> None:
    """Validate required environment variables at application startup.

    Checks that all required environment variables are set and non-empty.
    In production mode (``ENV=production`` or ``CONCLAVE_ENV=production``),
    also validates that ``ARTIFACT_SIGNING_KEY``, ``MASKING_SALT``,
    ``JWT_SECRET_KEY``, and ``OPERATOR_CREDENTIALS_HASH`` are present and
    correctly formed.

    Additionally:
    - Emits a security warning when ``CONCLAVE_SSL_REQUIRED=false`` is detected
      in production mode, as this disables SSL enforcement for PostgreSQL
      connections.
    - Calls :func:`warn_if_ssl_misconfigured` to warn when
      ``CONCLAVE_SSL_REQUIRED=true`` but no TLS certificate path is configured
      in the environment — indicating a potential misconfiguration where the
      application expects TLS but no cert is wired.
    - Validates multi-key signing consistency (T42.1): if
      ``ARTIFACT_SIGNING_KEYS`` is non-empty, ``ARTIFACT_SIGNING_KEY_ACTIVE``
      must be set and present as a key within the map.  This check applies in
      all deployment modes.
    - When ``MTLS_ENABLED=true``, validates that all three mTLS cert paths
      (``MTLS_CA_CERT_PATH``, ``MTLS_CLIENT_CERT_PATH``, ``MTLS_CLIENT_KEY_PATH``)
      point to existing, readable files (T46.2, ADV-P46-03).
    - When ``MTLS_ENABLED=true`` and ``CONCLAVE_SSL_REQUIRED=false``, emits a
      WARNING that mTLS implies SSL is required (T46.2).
    - Validates ``JWT_SECRET_KEY`` presence in production (T47.4).
    - Validates ``OPERATOR_CREDENTIALS_HASH`` presence and bcrypt format in
      production (T47.5).

    Collects ALL missing variables and cert errors before raising so that the
    operator receives a complete list in a single error message — not just the
    first missing variable.

    All environment variable access goes through the :func:`get_settings`
    singleton rather than ``os.environ`` directly, ensuring a single source
    of truth consistent with the T36.1 centralization goal (ADV-P36-01).

    Returns:
        ``None`` when all required variables are present and consistent.

    Raises:
        SystemExit: If any required environment variable is missing or if the
            multi-key signing configuration is inconsistent, or if any mTLS
            cert file is missing when MTLS_ENABLED=true.  The exit message
            lists every error.

    Example::

        # Call at application startup before any other initialisation:
        from synth_engine.bootstrapper.config_validation import validate_config
        validate_config()
    """
    settings = get_settings()
    required = list(_ALWAYS_REQUIRED)
    if _is_production():
        required.extend(_PRODUCTION_REQUIRED)

    # Access each required variable via the settings model rather than os.environ.
    # Settings field names are the lowercase equivalents of the env var names
    # (e.g. DATABASE_URL -> settings.database_url).
    errors: list[str] = [var for var in required if not getattr(settings, var.lower(), None)]

    # T42.1: validate multi-key signing consistency in all deployment modes.
    # If artifact_signing_keys is non-empty, artifact_signing_key_active must be
    # set and present as a key in the map.
    if settings.artifact_signing_keys:
        if not settings.artifact_signing_key_active:
            errors.append(
                "ARTIFACT_SIGNING_KEY_ACTIVE must be set when ARTIFACT_SIGNING_KEYS is non-empty"
            )
        elif settings.artifact_signing_key_active not in settings.artifact_signing_keys:
            errors.append(
                f"ARTIFACT_SIGNING_KEY_ACTIVE '{settings.artifact_signing_key_active}' "
                f"is not present in ARTIFACT_SIGNING_KEYS"
            )

    # T47.4: validate JWT_SECRET_KEY presence in production.
    _validate_jwt_secret_key(errors)

    # T47.5: validate OPERATOR_CREDENTIALS_HASH presence and bcrypt format in production.
    _validate_operator_credentials_hash(errors)

    # T46.2 / ADV-P46-03: validate mTLS cert files exist and are readable.
    _validate_mtls_cert_files(errors)

    if errors:
        error_list = ", ".join(errors)
        raise SystemExit(
            f"Startup configuration error: the following required environment "
            f"variable(s) are not set or are misconfigured: {error_list}. "
            f"Set them before starting the Conclave Engine."
        )

    if _is_production() and not settings.conclave_ssl_required:
        _logger.warning(
            "CONCLAVE_SSL_REQUIRED=false in production mode — "
            "SSL enforcement for PostgreSQL connections is disabled. "
            "This is a security misconfiguration for production."
        )

    # T46.2: Warn when mTLS is enabled but CONCLAVE_SSL_REQUIRED is false.
    # mTLS implies SSL — the explicit flag is redundant but its absence is
    # a misconfiguration signal worth surfacing.
    if settings.mtls_enabled and not settings.conclave_ssl_required:
        _logger.warning(
            "MTLS_ENABLED=true overrides CONCLAVE_SSL_REQUIRED=false — "
            "ssl is implicitly required when mTLS is active. "
            "Set CONCLAVE_SSL_REQUIRED=true to silence this warning."
        )

    # TLS cert misconfiguration check (T42.2): warn if ssl_required=True but no
    # TLS certificate path is configured.  A TLS cert path is considered
    # "configured" when CONCLAVE_TLS_CERT_PATH is set and non-empty.  This is a
    # heuristic advisory — the definitive TLS enforcement is handled by the
    # reverse proxy (nginx/Caddy) per docs/PRODUCTION_DEPLOYMENT.md §2.1.
    tls_cert_configured: bool = bool(settings.conclave_tls_cert_path)
    warn_if_ssl_misconfigured(
        ssl_required=settings.conclave_ssl_required,
        tls_cert_configured=tls_cert_configured,
    )
