"""Unit tests for the shared exempt-path constants (ADV-T39.1-01).

Verifies that the extracted ``COMMON_INFRA_EXEMPT_PATHS`` and ``SEAL_EXEMPT_PATHS``
constants are correctly composed in ``_exempt_paths.py`` and that each consumer
module (vault, licensing, auth) imports and uses the correct set.

Layered exemption model (P50 review fix)
----------------------------------------
Three middleware layers use different exemption sets to implement fine-grained
access control:

1. ``SealGateMiddleware`` (vault.py) uses ``SEAL_EXEMPT_PATHS``:
   ``COMMON_INFRA_EXEMPT_PATHS | {"/security/shred"}``.
   Emergency shred must be reachable even when the vault is sealed.

2. ``LicenseGateMiddleware`` (licensing.py) uses ``SEAL_EXEMPT_PATHS``:
   Emergency shred must be reachable even without a license.

3. ``AuthenticationGateMiddleware`` (auth.py) uses
   ``COMMON_INFRA_EXEMPT_PATHS | {"/auth/token"}``.
   Both security routes require JWT auth — they must NOT bypass the auth gate.

History
-------
- T39.1: Initial extraction of EXEMPT_PATHS to shared module (ADV-T39.1-01).
- T48.3: ``/ready`` added to ``COMMON_INFRA_EXEMPT_PATHS`` (Kubernetes readiness
  probe exempt from all gates).  Count increased from 10→11 in COMMON,
  11→12 in AUTH_EXEMPT_PATHS.
- P50 review fix: Security routes removed from ``COMMON_INFRA_EXEMPT_PATHS``
  (auth baseline, now 9 paths).  ``SEAL_EXEMPT_PATHS`` introduced (10 paths)
  for vault and license gates.  AUTH_EXEMPT_PATHS reduced from 12→10 paths.

CONSTITUTION Priority 0: Security
CONSTITUTION Priority 3: TDD
Advisory: ADV-T39.1-01 — Extract EXEMPT_PATHS to shared module
Task: T48.3 — Readiness Probe & External Dependency Health Checks
Task: P50 review fix — restore /security/shred vault-layer bypass (layered model)
"""

from __future__ import annotations


class TestCommonInfraExemptPaths:
    """Tests for the shared COMMON_INFRA_EXEMPT_PATHS constant.

    COMMON_INFRA_EXEMPT_PATHS is the auth baseline — paths that bypass
    AuthenticationGateMiddleware entirely (pre-auth bootstrapping, infra).
    Security routes are NOT included: they require JWT auth.
    """

    def test_common_infra_exempt_paths_has_exactly_seven_paths(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must contain exactly 7 paths.

        After the T66.2 security fix (ADV-P62-01):
        - /docs, /redoc, /openapi.json removed from exempt set.
        Count: 11 (T48.3) → 9 (P50 fix) → 10 (T55.1) → 7 (T66.2).
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert len(COMMON_INFRA_EXEMPT_PATHS) == 7

    def test_common_infra_exempt_paths_is_frozenset(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must be an immutable frozenset."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert isinstance(COMMON_INFRA_EXEMPT_PATHS, frozenset)
        # frozenset must contain the health endpoint (structural safety check)
        assert "/health" in COMMON_INFRA_EXEMPT_PATHS

    def test_common_infra_exempt_paths_contains_expected_paths(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must contain exactly the 7 expected paths.

        Security routes are excluded from this set — they require JWT auth
        and are handled by the SEAL_EXEMPT_PATHS for vault/license bypass only.

        T66.2 (ADV-P62-01): /docs, /redoc, /openapi.json removed.  In production
        these endpoints return 404 (FastAPI docs disabled).  In development they
        require a Bearer token like any other GET endpoint.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        expected = frozenset(
            {
                "/unseal",
                "/health",
                "/ready",
                "/health/vault",
                "/metrics",
                "/license/challenge",
                "/license/activate",
            }
        )
        assert COMMON_INFRA_EXEMPT_PATHS == expected

    def test_common_infra_exempt_paths_excludes_doc_paths(self) -> None:
        """/docs, /redoc, /openapi.json must NOT be in COMMON_INFRA_EXEMPT_PATHS (T66.2).

        Removing these paths from the auth-bypass set prevents unauthenticated
        API schema reconnaissance (ADV-P62-01).
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert "/docs" not in COMMON_INFRA_EXEMPT_PATHS, (
            "/docs must NOT be in COMMON_INFRA_EXEMPT_PATHS (T66.2 — ADV-P62-01)"
        )
        assert "/redoc" not in COMMON_INFRA_EXEMPT_PATHS, (
            "/redoc must NOT be in COMMON_INFRA_EXEMPT_PATHS (T66.2 — ADV-P62-01)"
        )
        assert "/openapi.json" not in COMMON_INFRA_EXEMPT_PATHS, (
            "/openapi.json must NOT be in COMMON_INFRA_EXEMPT_PATHS (T66.2 — ADV-P62-01)"
        )

    def test_common_infra_exempt_paths_contains_ready(self) -> None:
        """/ready must be in COMMON_INFRA_EXEMPT_PATHS (T48.3 -- readiness probe)."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert "/ready" in COMMON_INFRA_EXEMPT_PATHS

    def test_common_infra_exempt_paths_excludes_security_shred(self) -> None:
        """/security/shred must NOT be in COMMON_INFRA_EXEMPT_PATHS (requires JWT auth)."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert "/security/shred" not in COMMON_INFRA_EXEMPT_PATHS

    def test_common_infra_exempt_paths_excludes_security_keys_rotate(self) -> None:
        """/security/keys/rotate must NOT be in COMMON_INFRA_EXEMPT_PATHS (requires JWT auth)."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert "/security/keys/rotate" not in COMMON_INFRA_EXEMPT_PATHS


class TestSealExemptPaths:
    """Tests for SEAL_EXEMPT_PATHS — the vault and license gate exemption set.

    SEAL_EXEMPT_PATHS = COMMON_INFRA_EXEMPT_PATHS | {"/security/shred"}.
    It extends the auth baseline with the emergency shred path so that
    SealGateMiddleware and LicenseGateMiddleware allow emergency shred through.
    """

    def test_seal_exempt_paths_is_frozenset(self) -> None:
        """SEAL_EXEMPT_PATHS must be an immutable frozenset."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )

        assert isinstance(SEAL_EXEMPT_PATHS, frozenset)
        # SEAL_EXEMPT_PATHS must include the emergency shred path
        assert "/security/shred" in SEAL_EXEMPT_PATHS

    def test_seal_exempt_paths_has_exactly_eight_paths(self) -> None:
        """SEAL_EXEMPT_PATHS must have exactly 8 paths (7 common + /security/shred).

        T66.2 reduced COMMON_INFRA_EXEMPT_PATHS from 10 to 7 paths.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )

        assert len(SEAL_EXEMPT_PATHS) == 8

    def test_seal_exempt_paths_contains_security_shred(self) -> None:
        """/security/shred must be in SEAL_EXEMPT_PATHS (emergency vault bypass)."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )

        assert "/security/shred" in SEAL_EXEMPT_PATHS

    def test_seal_exempt_paths_excludes_security_keys_rotate(self) -> None:
        """/security/keys/rotate must NOT be in SEAL_EXEMPT_PATHS.

        Key rotation cannot work when the vault is sealed (requires the current
        KEK).  The route handler returns 423 internally.  SealGateMiddleware
        correctly returns 423 for this path when sealed.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )

        assert "/security/keys/rotate" not in SEAL_EXEMPT_PATHS

    def test_seal_exempt_paths_is_strict_superset_of_common(self) -> None:
        """SEAL_EXEMPT_PATHS must be a strict superset of COMMON_INFRA_EXEMPT_PATHS."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
            SEAL_EXEMPT_PATHS,
        )

        assert COMMON_INFRA_EXEMPT_PATHS < SEAL_EXEMPT_PATHS

    def test_seal_exempt_paths_delta_is_exactly_shred(self) -> None:
        """SEAL_EXEMPT_PATHS - COMMON_INFRA_EXEMPT_PATHS must equal {/security/shred}."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
            SEAL_EXEMPT_PATHS,
        )

        assert SEAL_EXEMPT_PATHS - COMMON_INFRA_EXEMPT_PATHS == frozenset({"/security/shred"})


class TestAuthExemptPaths:
    """Tests for AUTH_EXEMPT_PATHS in auth.py (superset of COMMON_INFRA_EXEMPT_PATHS).

    AUTH_EXEMPT_PATHS = COMMON_INFRA_EXEMPT_PATHS | {"/auth/token"}.
    Security routes are NOT included — they require JWT auth.
    """

    def test_auth_exempt_paths_is_superset_of_common(self) -> None:
        """AUTH_EXEMPT_PATHS must be a strict superset of COMMON_INFRA_EXEMPT_PATHS."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert COMMON_INFRA_EXEMPT_PATHS < AUTH_EXEMPT_PATHS

    def test_auth_exempt_paths_contains_auth_token(self) -> None:
        """AUTH_EXEMPT_PATHS must include /auth/token."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/auth/token" in AUTH_EXEMPT_PATHS

    def test_auth_exempt_paths_has_exactly_eight_paths(self) -> None:
        """AUTH_EXEMPT_PATHS must have exactly 8 paths (7 common + /auth/token).

        After T66.2 removed /docs, /redoc, /openapi.json from COMMON_INFRA_EXEMPT_PATHS:
        - COMMON_INFRA_EXEMPT_PATHS has 7 paths
        - AUTH_EXEMPT_PATHS = 7 + /auth/token = 8 paths
        Count: 12 (T48.3) → 10 (P50) → 11 (T55.1) → 8 (T66.2, ADV-P62-01).
        """
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert len(AUTH_EXEMPT_PATHS) == 8

    def test_auth_exempt_paths_contains_ready(self) -> None:
        """/ready must be in AUTH_EXEMPT_PATHS (T48.3 -- readiness probe)."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/ready" in AUTH_EXEMPT_PATHS

    def test_auth_exempt_paths_excludes_security_shred(self) -> None:
        """/security/shred must NOT be in AUTH_EXEMPT_PATHS (requires JWT auth)."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/security/shred" not in AUTH_EXEMPT_PATHS

    def test_auth_exempt_paths_excludes_security_keys_rotate(self) -> None:
        """/security/keys/rotate must NOT be in AUTH_EXEMPT_PATHS (requires JWT auth)."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/security/keys/rotate" not in AUTH_EXEMPT_PATHS


class TestVaultExemptPaths:
    """Tests for EXEMPT_PATHS in vault.py (must equal SEAL_EXEMPT_PATHS).

    After the P50 layered exemption model, SealGateMiddleware uses
    SEAL_EXEMPT_PATHS (not COMMON_INFRA_EXEMPT_PATHS) so that emergency
    shred bypasses the seal gate.
    """

    def test_vault_exempt_paths_equals_seal_exempt_paths(self) -> None:
        """EXEMPT_PATHS in vault.py must equal SEAL_EXEMPT_PATHS.

        Emergency shred must bypass SealGateMiddleware — so EXEMPT_PATHS
        must be SEAL_EXEMPT_PATHS, which includes /security/shred.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )
        from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

        assert EXEMPT_PATHS == SEAL_EXEMPT_PATHS

    def test_vault_exempt_paths_is_frozenset(self) -> None:
        """EXEMPT_PATHS from vault.py must be a frozenset."""
        from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

        assert isinstance(EXEMPT_PATHS, frozenset)
        # frozenset must include /unseal (the primary vault path)
        assert "/unseal" in EXEMPT_PATHS

    def test_vault_exempt_paths_contains_ready(self) -> None:
        """/ready must be in vault EXEMPT_PATHS (T48.3 -- readiness probe)."""
        from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

        assert "/ready" in EXEMPT_PATHS

    def test_vault_exempt_paths_contains_security_shred(self) -> None:
        """/security/shred must be in vault EXEMPT_PATHS (emergency shred protocol)."""
        from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

        assert "/security/shred" in EXEMPT_PATHS

    def test_vault_exempt_paths_excludes_security_keys_rotate(self) -> None:
        """/security/keys/rotate must NOT be in vault EXEMPT_PATHS."""
        from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

        assert "/security/keys/rotate" not in EXEMPT_PATHS


class TestLicenseExemptPaths:
    """Tests for LICENSE_EXEMPT_PATHS in licensing.py (must equal SEAL_EXEMPT_PATHS).

    After the P50 layered exemption model, LicenseGateMiddleware uses
    SEAL_EXEMPT_PATHS so that emergency shred works without a license.
    """

    def test_license_exempt_paths_equals_seal_exempt_paths(self) -> None:
        """LICENSE_EXEMPT_PATHS must equal SEAL_EXEMPT_PATHS.

        Emergency shred must work without a license — so LICENSE_EXEMPT_PATHS
        must be SEAL_EXEMPT_PATHS, which includes /security/shred.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )
        from synth_engine.bootstrapper.dependencies.licensing import (
            LICENSE_EXEMPT_PATHS,
        )

        assert LICENSE_EXEMPT_PATHS == SEAL_EXEMPT_PATHS

    def test_license_exempt_paths_is_frozenset(self) -> None:
        """LICENSE_EXEMPT_PATHS must be a frozenset."""
        from synth_engine.bootstrapper.dependencies.licensing import (
            LICENSE_EXEMPT_PATHS,
        )

        assert isinstance(LICENSE_EXEMPT_PATHS, frozenset)
        # frozenset must include the license activation endpoint
        assert "/license/activate" in LICENSE_EXEMPT_PATHS

    def test_license_exempt_paths_contains_ready(self) -> None:
        """/ready must be in LICENSE_EXEMPT_PATHS (T48.3 -- readiness probe)."""
        from synth_engine.bootstrapper.dependencies.licensing import (
            LICENSE_EXEMPT_PATHS,
        )

        assert "/ready" in LICENSE_EXEMPT_PATHS

    def test_license_exempt_paths_contains_security_shred(self) -> None:
        """/security/shred must be in LICENSE_EXEMPT_PATHS (emergency shred without license)."""
        from synth_engine.bootstrapper.dependencies.licensing import (
            LICENSE_EXEMPT_PATHS,
        )

        assert "/security/shred" in LICENSE_EXEMPT_PATHS

    def test_license_exempt_paths_excludes_security_keys_rotate(self) -> None:
        """/security/keys/rotate must NOT be in LICENSE_EXEMPT_PATHS."""
        from synth_engine.bootstrapper.dependencies.licensing import (
            LICENSE_EXEMPT_PATHS,
        )

        assert "/security/keys/rotate" not in LICENSE_EXEMPT_PATHS
