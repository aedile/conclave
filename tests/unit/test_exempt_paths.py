"""Unit tests for the shared exempt-path constants (ADV-T39.1-01).

Verifies that the extracted ``COMMON_INFRA_EXEMPT_PATHS`` constant is correctly
composed in ``_exempt_paths.py`` and that each consumer module (vault, licensing,
auth) imports and uses it without diverging from the canonical set.

Updated in T48.3: ``/ready`` added to ``COMMON_INFRA_EXEMPT_PATHS`` so the
Kubernetes readiness probe bypasses SealGateMiddleware and AuthenticationGateMiddleware.
Count increased from 10 to 11.  AUTH_EXEMPT_PATHS count increased from 11 to 12.

CONSTITUTION Priority 0: Security
CONSTITUTION Priority 3: TDD
Advisory: ADV-T39.1-01 — Extract EXEMPT_PATHS to shared module
Task: T48.3 — Readiness Probe & External Dependency Health Checks
"""

from __future__ import annotations


class TestCommonInfraExemptPaths:
    """Tests for the shared COMMON_INFRA_EXEMPT_PATHS constant."""

    def test_common_infra_exempt_paths_has_exactly_eleven_paths(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must contain exactly 11 paths.

        Count increased from 10 to 11 in T48.3 when /ready was added as a
        Kubernetes readiness probe exempt from vault and auth gates.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert len(COMMON_INFRA_EXEMPT_PATHS) == 11

    def test_common_infra_exempt_paths_is_frozenset(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must be an immutable frozenset."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert isinstance(COMMON_INFRA_EXEMPT_PATHS, frozenset)

    def test_common_infra_exempt_paths_contains_expected_paths(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must contain all 11 expected paths."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        expected = frozenset(
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
        assert COMMON_INFRA_EXEMPT_PATHS == expected

    def test_common_infra_exempt_paths_contains_ready(self) -> None:
        """/ready must be in COMMON_INFRA_EXEMPT_PATHS (T48.3 -- readiness probe)."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert "/ready" in COMMON_INFRA_EXEMPT_PATHS


class TestAuthExemptPaths:
    """Tests for AUTH_EXEMPT_PATHS in auth.py (superset of common paths)."""

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

    def test_auth_exempt_paths_has_exactly_twelve_paths(self) -> None:
        """AUTH_EXEMPT_PATHS must have exactly 12 paths (11 common + /auth/token).

        Count increased from 11 to 12 in T48.3 when /ready was added to
        COMMON_INFRA_EXEMPT_PATHS.
        """
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert len(AUTH_EXEMPT_PATHS) == 12

    def test_auth_exempt_paths_contains_ready(self) -> None:
        """/ready must be in AUTH_EXEMPT_PATHS (T48.3 -- readiness probe)."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/ready" in AUTH_EXEMPT_PATHS


class TestVaultExemptPaths:
    """Tests for EXEMPT_PATHS in vault.py (equals COMMON_INFRA_EXEMPT_PATHS)."""

    def test_vault_exempt_paths_equals_common(self) -> None:
        """EXEMPT_PATHS in vault.py must equal COMMON_INFRA_EXEMPT_PATHS."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )
        from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

        assert EXEMPT_PATHS == COMMON_INFRA_EXEMPT_PATHS

    def test_vault_exempt_paths_is_frozenset(self) -> None:
        """EXEMPT_PATHS from vault.py must be a frozenset."""
        from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

        assert isinstance(EXEMPT_PATHS, frozenset)

    def test_vault_exempt_paths_contains_ready(self) -> None:
        """/ready must be in vault EXEMPT_PATHS (T48.3 -- readiness probe)."""
        from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

        assert "/ready" in EXEMPT_PATHS


class TestLicenseExemptPaths:
    """Tests for LICENSE_EXEMPT_PATHS in licensing.py (equals COMMON_INFRA_EXEMPT_PATHS)."""

    def test_license_exempt_paths_equals_common(self) -> None:
        """LICENSE_EXEMPT_PATHS must equal COMMON_INFRA_EXEMPT_PATHS."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )
        from synth_engine.bootstrapper.dependencies.licensing import (
            LICENSE_EXEMPT_PATHS,
        )

        assert LICENSE_EXEMPT_PATHS == COMMON_INFRA_EXEMPT_PATHS

    def test_license_exempt_paths_is_frozenset(self) -> None:
        """LICENSE_EXEMPT_PATHS must be a frozenset."""
        from synth_engine.bootstrapper.dependencies.licensing import (
            LICENSE_EXEMPT_PATHS,
        )

        assert isinstance(LICENSE_EXEMPT_PATHS, frozenset)

    def test_license_exempt_paths_contains_ready(self) -> None:
        """/ready must be in LICENSE_EXEMPT_PATHS (T48.3 -- readiness probe)."""
        from synth_engine.bootstrapper.dependencies.licensing import (
            LICENSE_EXEMPT_PATHS,
        )

        assert "/ready" in LICENSE_EXEMPT_PATHS
