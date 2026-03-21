"""Unit tests for the shared exempt-path constants (ADV-T39.1-01).

Verifies that the extracted ``COMMON_INFRA_EXEMPT_PATHS`` constant is correctly
composed in ``_exempt_paths.py`` and that each consumer module (vault, licensing,
auth) imports and uses it without diverging from the canonical set.

CONSTITUTION Priority 0: Security
CONSTITUTION Priority 3: TDD
Advisory: ADV-T39.1-01 — Extract EXEMPT_PATHS to shared module
"""

from __future__ import annotations


class TestCommonInfraExemptPaths:
    """Tests for the shared COMMON_INFRA_EXEMPT_PATHS constant."""

    def test_common_infra_exempt_paths_has_exactly_ten_paths(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must contain exactly 10 paths."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert len(COMMON_INFRA_EXEMPT_PATHS) == 10

    def test_common_infra_exempt_paths_is_frozenset(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must be an immutable frozenset."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert isinstance(COMMON_INFRA_EXEMPT_PATHS, frozenset)

    def test_common_infra_exempt_paths_contains_expected_paths(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must contain all 10 expected paths."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        expected = frozenset(
            {
                "/unseal",
                "/health",
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

    def test_auth_exempt_paths_has_exactly_eleven_paths(self) -> None:
        """AUTH_EXEMPT_PATHS must have exactly 11 paths (10 common + /auth/token)."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert len(AUTH_EXEMPT_PATHS) == 11


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
