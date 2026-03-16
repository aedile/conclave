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
"""

from __future__ import annotations

import os

_ALWAYS_REQUIRED: tuple[str, ...] = (
    "DATABASE_URL",
    "AUDIT_KEY",
)

_PRODUCTION_REQUIRED: tuple[str, ...] = (
    "ARTIFACT_SIGNING_KEY",
    "MASKING_SALT",
)


def _is_production() -> bool:
    """Return ``True`` if the current deployment mode is production.

    Production mode is indicated by either of:
      - ``ENV=production``
      - ``CONCLAVE_ENV=production``

    Both env var names are checked for maximum compatibility with deployment
    tooling that may use either convention.

    Returns:
        ``True`` when the deployment mode is production, ``False`` otherwise.
    """
    return (
        os.environ.get("ENV", "").lower() == "production"
        or os.environ.get("CONCLAVE_ENV", "").lower() == "production"
    )


def validate_config() -> None:
    """Validate required environment variables at application startup.

    Checks that all required environment variables are set and non-empty.
    In production mode (``ENV=production`` or ``CONCLAVE_ENV=production``),
    also validates that ``ARTIFACT_SIGNING_KEY`` and ``MASKING_SALT`` are present.

    Collects ALL missing variables before raising so that the operator
    receives a complete list in a single error message — not just the first
    missing variable.

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
    required = list(_ALWAYS_REQUIRED)
    if _is_production():
        required.extend(_PRODUCTION_REQUIRED)

    missing = [var for var in required if not os.environ.get(var)]

    if missing:
        missing_list = ", ".join(missing)
        raise SystemExit(
            f"Startup configuration error: the following required environment "
            f"variable(s) are not set: {missing_list}. "
            f"Set them before starting the Conclave Engine."
        )
