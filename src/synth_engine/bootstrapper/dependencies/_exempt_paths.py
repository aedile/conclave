"""Shared exempt-path constants for middleware gates.

Centralizes the path exemption sets that were previously duplicated across
``vault.py``, ``licensing.py``, and ``auth.py`` (ADV-T39.1-01).

Each middleware that requires a path allowlist imports from this module and
composes its specific set from :data:`COMMON_INFRA_EXEMPT_PATHS`.

CONSTITUTION Priority 0: Security
Advisory: ADV-T39.1-01 — Extract EXEMPT_PATHS to shared module
Task: T48.3 — Readiness Probe & External Dependency Health Checks
"""

from __future__ import annotations

#: Paths that are accessible to all middleware gates regardless of system state.
#:
#: These 11 paths cover pre-auth bootstrapping and infrastructure concerns
#: that must remain reachable before the vault is unsealed, a license is
#: activated, or an operator has authenticated:
#:
#: - ``/unseal`` — vault unsealing (pre-boot)
#: - ``/health`` — liveness probe (infra)
#: - ``/ready`` — readiness probe (infra; Kubernetes readiness gate)
#: - ``/metrics`` — Prometheus scrape (infra)
#: - ``/docs``, ``/redoc``, ``/openapi.json`` — API documentation
#: - ``/license/challenge``, ``/license/activate`` — offline license activation
#: - ``/security/shred`` — emergency key destruction (must survive sealed state)
#: - ``/security/keys/rotate`` — key rotation (returns 423 internally if sealed)
COMMON_INFRA_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/unseal",
        "/health",
        "/ready",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/license/challenge",
        "/license/activate",
        "/security/shred",
        "/security/keys/rotate",
    }
)
