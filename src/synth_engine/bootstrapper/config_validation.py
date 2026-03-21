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

Multi-key signing consistency (T42.1):
  When ``ARTIFACT_SIGNING_KEYS`` is non-empty, ``ARTIFACT_SIGNING_KEY_ACTIVE`` must
  be set and must exist as a key within the map.  This is validated in all deployment
  modes to prevent silent misconfiguration during rotation.

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
Task: T42.1 — Artifact Signing Key Versioning (multi-key consistency validation)
"""

from __future__ import annotations

import logging

from synth_engine.shared.settings import get_settings

_ALWAYS_REQUIRED: tuple[str, ...] = (
    "DATABASE_URL",
    "AUDIT_KEY",
)

_PRODUCTION_REQUIRED: tuple[str, ...] = (
    "ARTIFACT_SIGNING_KEY",
    "MASKING_SALT",
)

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


def validate_config() -> None:
    """Validate required environment variables at application startup.

    Checks that all required environment variables are set and non-empty.
    In production mode (``ENV=production`` or ``CONCLAVE_ENV=production``),
    also validates that ``ARTIFACT_SIGNING_KEY`` and ``MASKING_SALT`` are present.

    Additionally, validates multi-key signing consistency (T42.1): if
    ``ARTIFACT_SIGNING_KEYS`` is non-empty, ``ARTIFACT_SIGNING_KEY_ACTIVE``
    must be set and present as a key within the map.  This check applies in
    all deployment modes.

    Also emits a security warning when ``CONCLAVE_SSL_REQUIRED=false``
    is detected in production mode, as this disables SSL enforcement for
    PostgreSQL connections.

    Collects ALL missing variables before raising so that the operator
    receives a complete list in a single error message — not just the first
    missing variable.

    All environment variable access goes through the :func:`get_settings`
    singleton rather than ``os.environ`` directly, ensuring a single source
    of truth consistent with the T36.1 centralization goal (ADV-P36-01).

    Returns:
        ``None`` when all required variables are present and consistent.

    Raises:
        SystemExit: If any required environment variable is missing or if the
            multi-key signing configuration is inconsistent.  The exit message
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
