"""Shared exempt-path constants for middleware gates.

Centralizes the path exemption sets that were previously duplicated across
``vault.py``, ``licensing.py``, and ``auth.py`` (ADV-T39.1-01).

Layered exemption model (P50 review fix)
-----------------------------------------
Different middleware layers enforce access control at different levels.
Each layer imports the appropriate constant for its security posture:

:data:`COMMON_INFRA_EXEMPT_PATHS` — **auth baseline** (10 paths)
    Pre-auth bootstrapping and infrastructure paths that must be reachable
    before any credential is issued or the vault is unsealed.  This is the
    most restrictive set — anything in it bypasses all three gates.
    ``AuthenticationGateMiddleware`` (``auth.py``) composes from this set.
    Security routes are **NOT** included here: they require JWT auth.

:data:`SEAL_EXEMPT_PATHS` — **vault/license gate set** (11 paths)
    Extends ``COMMON_INFRA_EXEMPT_PATHS`` with ``/security/shred`` so that
    the emergency cryptographic shred protocol remains reachable even when
    the vault is sealed (or unlicensed).  ``SealGateMiddleware`` (``vault.py``)
    and ``LicenseGateMiddleware`` (``licensing.py``) use this set.

    ``/security/keys/rotate`` is deliberately **excluded**: key rotation
    requires an unsealed vault to access the KEK.  When sealed, the request
    reaches the route handler (via ``SEAL_EXEMPT_PATHS`` not containing it,
    meaning the seal gate blocks it with 423), which is the correct behaviour.

Path summary
------------
.. list-table::
   :header-rows: 1
   :widths: 30 15 15 15

   * - Path
     - COMMON
     - SEAL
     - AUTH
   * - /unseal, /health, /ready, /metrics, /docs, /redoc, /openapi.json,
       /license/challenge, /license/activate, /health/vault
     - Yes
     - Yes
     - Yes (via composition)
   * - /security/shred
     - **No**
     - **Yes**
     - No (requires JWT auth)
   * - /security/keys/rotate
     - **No**
     - **No**
     - No (requires JWT auth + unsealed vault)
   * - /auth/token
     - No
     - No
     - Yes (operator login)

Why ``/health/vault`` is in COMMON_INFRA_EXEMPT_PATHS
------------------------------------------------------
``/health/vault`` reports the vault seal state of this specific worker process.
It must be reachable when the vault is sealed (otherwise operators cannot
observe *which* workers are sealed before issuing unseal commands).  The seal
gate cannot block the endpoint that reports on the seal state — that would
create a deadlock for operators diagnosing multi-worker unseal issues.

CONSTITUTION Priority 0: Security
Advisory: ADV-T39.1-01 — Extract EXEMPT_PATHS to shared module
Advisory: ADV-P47-04 — Enforce JWT auth on security endpoints
Task: T48.3 — Readiness Probe & External Dependency Health Checks
Task: P50 review fix — restore /security/shred vault-layer bypass (layered model)
Task: T55.1 — Vault State Health Endpoint & Multi-Worker Coordination
"""

from __future__ import annotations

#: Auth baseline: paths accessible to all middleware gates regardless of system state.
#:
#: These 10 paths cover pre-auth bootstrapping and infrastructure concerns that must
#: remain reachable before the vault is unsealed, a license is activated, or an
#: operator has authenticated:
#:
#: - ``/unseal`` — vault unsealing (pre-boot)
#: - ``/health`` — liveness probe (infra)
#: - ``/ready`` — readiness probe (infra; Kubernetes readiness gate)
#: - ``/health/vault`` — per-worker vault seal status (ops; must not be gate-locked)
#: - ``/metrics`` — Prometheus scrape (infra)
#: - ``/docs``, ``/redoc``, ``/openapi.json`` — API documentation
#: - ``/license/challenge``, ``/license/activate`` — offline license activation
#:
#: Security routes (``/security/shred``, ``/security/keys/rotate``) are **not**
#: included here — they require JWT authentication (ADV-P47-04).
#: Use :data:`SEAL_EXEMPT_PATHS` for the vault and license gate exemption set.
COMMON_INFRA_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/unseal",
        "/health",
        "/ready",
        "/health/vault",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/license/challenge",
        "/license/activate",
    }
)

#: Vault and license gate exemption set.
#:
#: Extends :data:`COMMON_INFRA_EXEMPT_PATHS` with ``/security/shred`` so that
#: the emergency cryptographic shred protocol remains reachable when the vault
#: is sealed (SealGateMiddleware) or when the software is unlicensed
#: (LicenseGateMiddleware).
#:
#: ``/security/keys/rotate`` is **excluded**: key rotation cannot work without
#: the current vault KEK.  SealGateMiddleware correctly returns 423 for that
#: path when the vault is sealed.
#:
#: Used by:
#: - :class:`~synth_engine.bootstrapper.dependencies.vault.SealGateMiddleware`
#: - :class:`~synth_engine.bootstrapper.dependencies.licensing.LicenseGateMiddleware`
SEAL_EXEMPT_PATHS: frozenset[str] = COMMON_INFRA_EXEMPT_PATHS | frozenset({"/security/shred"})
