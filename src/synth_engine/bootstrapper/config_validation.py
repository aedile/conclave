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
"""

from __future__ import annotations

import logging

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

    Additionally:
    - Emits a security warning when ``CONCLAVE_SSL_REQUIRED=false`` is detected
      in production mode, as this disables SSL enforcement for PostgreSQL
      connections.
    - Calls :func:`warn_if_ssl_misconfigured` to warn when
      ``CONCLAVE_SSL_REQUIRED=true`` but no TLS certificate path is configured
      in the environment — indicating a potential misconfiguration where the
      application expects TLS but no cert is wired.

    Collects ALL missing variables before raising so that the operator
    receives a complete list in a single error message — not just the first
    missing variable.

    All environment variable access goes through the :func:`get_settings`
    singleton rather than ``os.environ`` directly, ensuring a single source
    of truth consistent with the T36.1 centralization goal (ADV-P36-01).

    Returns:
        ``None`` when all required variables are present.

    Raises:
        SystemExit: If any required environment variable is missing.  The
            exit message lists every missing variable by name.

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
    missing = [var for var in required if not getattr(settings, var.lower(), None)]

    if missing:
        missing_list = ", ".join(missing)
        raise SystemExit(
            f"Startup configuration error: the following required environment "
            f"variable(s) are not set: {missing_list}. "
            f"Set them before starting the Conclave Engine."
        )

    if _is_production() and not settings.conclave_ssl_required:
        _logger.warning(
            "CONCLAVE_SSL_REQUIRED=false in production mode — "
            "SSL enforcement for PostgreSQL connections is disabled. "
            "This is a security misconfiguration for production."
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
