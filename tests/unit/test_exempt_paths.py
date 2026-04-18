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
- T73: Parametrize cross-set membership tests to eliminate 11 near-duplicate
  functions (contains_ready, excludes_keys_rotate, contains_shred, is_frozenset
  for SEAL/VAULT/LICENSE).

CONSTITUTION Priority 0: Security
CONSTITUTION Priority 3: TDD
Advisory: ADV-T39.1-01 — Extract EXEMPT_PATHS to shared module
Task: T48.3 — Readiness Probe & External Dependency Health Checks
Task: P50 review fix — restore /security/shred vault-layer bypass (layered model)
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers: lazy-import each exempt-path set to avoid module-scope side effects
# ---------------------------------------------------------------------------


def _get_common() -> frozenset[str]:
    from synth_engine.bootstrapper.dependencies._exempt_paths import (
        COMMON_INFRA_EXEMPT_PATHS,
    )

    return COMMON_INFRA_EXEMPT_PATHS


def _get_seal() -> frozenset[str]:
    from synth_engine.bootstrapper.dependencies._exempt_paths import SEAL_EXEMPT_PATHS

    return SEAL_EXEMPT_PATHS


def _get_auth() -> frozenset[str]:
    from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

    return AUTH_EXEMPT_PATHS


def _get_vault() -> frozenset[str]:
    from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

    return EXEMPT_PATHS


def _get_license() -> frozenset[str]:
    from synth_engine.bootstrapper.dependencies.licensing import LICENSE_EXEMPT_PATHS

    return LICENSE_EXEMPT_PATHS


# ---------------------------------------------------------------------------
# Cross-set parametrized membership tests (T73 — replaces 11 per-class copies)
# ---------------------------------------------------------------------------

_ALL_EXEMPT_SETS = [
    pytest.param(_get_common, id="COMMON_INFRA"),
    pytest.param(_get_seal, id="SEAL"),
    pytest.param(_get_auth, id="AUTH"),
    pytest.param(_get_vault, id="VAULT"),
    pytest.param(_get_license, id="LICENSE"),
]

_SEAL_AUTH_VAULT_LICENSE_SETS = [
    pytest.param(_get_seal, id="SEAL"),
    pytest.param(_get_auth, id="AUTH"),
    pytest.param(_get_vault, id="VAULT"),
    pytest.param(_get_license, id="LICENSE"),
]

_SEAL_VAULT_LICENSE_SETS = [
    pytest.param(_get_seal, id="SEAL"),
    pytest.param(_get_vault, id="VAULT"),
    pytest.param(_get_license, id="LICENSE"),
]


@pytest.mark.parametrize("get_set", _ALL_EXEMPT_SETS)
def test_every_exempt_set_contains_ready(get_set: object) -> None:
    """/ready must appear in every exempt-path set (T48.3 Kubernetes readiness probe).

    All middleware layers must let /ready through — it is the k8s liveness
    probe and must never be blocked regardless of seal / license / auth state.

    Args:
        get_set: Callable that returns the frozenset under test.
    """
    assert callable(get_set)
    path_set = get_set()  # type: ignore[operator]
    assert "/ready" in path_set, f"/ready must be in {get_set.__name__!r}, got {path_set!r}"


@pytest.mark.parametrize("get_set", _ALL_EXEMPT_SETS)
def test_every_exempt_set_excludes_keys_rotate(get_set: object) -> None:
    """/security/keys/rotate must NOT appear in ANY exempt-path set.

    Key rotation requires the vault to be unsealed and the operator to be
    authenticated.  No middleware layer should ever bypass auth for this path.

    Args:
        get_set: Callable that returns the frozenset under test.
    """
    assert callable(get_set)
    path_set = get_set()  # type: ignore[operator]
    assert "/security/keys/rotate" not in path_set, (
        f"/security/keys/rotate must NOT be in {get_set.__name__!r}"
    )


@pytest.mark.parametrize("get_set", _SEAL_VAULT_LICENSE_SETS)
def test_seal_vault_license_sets_contain_security_shred(get_set: object) -> None:
    """/security/shred must be in SEAL, VAULT, and LICENSE exempt-path sets.

    Emergency shred must bypass the seal gate, vault gate, and license gate
    so that operators can shred data even when those subsystems are degraded.
    AUTH is intentionally excluded (shred DOES require JWT auth).

    Args:
        get_set: Callable that returns the frozenset under test.
    """
    assert callable(get_set)
    path_set = get_set()  # type: ignore[operator]
    assert "/security/shred" in path_set, (
        f"/security/shred must be in {get_set.__name__!r} (emergency shred protocol)"
    )


@pytest.mark.parametrize("get_set", _SEAL_AUTH_VAULT_LICENSE_SETS)
def test_non_common_exempt_sets_are_frozensets(get_set: object) -> None:
    """SEAL, AUTH, VAULT, and LICENSE exempt-path sets must each be immutable frozensets.

    Mutable sets could be accidentally modified at runtime, breaking the security
    invariant that these sets are fixed at application start.

    Args:
        get_set: Callable that returns the frozenset under test.
    """
    assert callable(get_set)
    path_set = get_set()  # type: ignore[operator]
    assert isinstance(path_set, frozenset), (
        f"Expected frozenset from {get_set.__name__!r}, got {type(path_set).__name__!r}"
    )


# ---------------------------------------------------------------------------
# Per-set unique tests: COMMON_INFRA_EXEMPT_PATHS
# ---------------------------------------------------------------------------


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
        assert len(_get_common()) == 7

    def test_common_infra_exempt_paths_is_frozenset(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must be an immutable frozenset."""
        path_set = _get_common()
        assert isinstance(path_set, frozenset)
        # frozenset must contain the health endpoint (structural safety check)
        assert "/health" in path_set

    def test_common_infra_exempt_paths_contains_expected_paths(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must contain exactly the 7 expected paths.

        Security routes are excluded from this set — they require JWT auth
        and are handled by the SEAL_EXEMPT_PATHS for vault/license bypass only.

        T66.2 (ADV-P62-01): /docs, /redoc, /openapi.json removed.  In production
        these endpoints return 404 (FastAPI docs disabled).  In development they
        require a Bearer token like any other GET endpoint.
        """
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
        assert _get_common() == expected

    def test_common_infra_exempt_paths_excludes_doc_paths(self) -> None:
        """/docs, /redoc, /openapi.json must NOT be in COMMON_INFRA_EXEMPT_PATHS (T66.2).

        Removing these paths from the auth-bypass set prevents unauthenticated
        API schema reconnaissance (ADV-P62-01).
        """
        path_set = _get_common()
        assert "/docs" not in path_set, (
            "/docs must NOT be in COMMON_INFRA_EXEMPT_PATHS (T66.2 — ADV-P62-01)"
        )
        assert "/redoc" not in path_set, (
            "/redoc must NOT be in COMMON_INFRA_EXEMPT_PATHS (T66.2 — ADV-P62-01)"
        )
        assert "/openapi.json" not in path_set, (
            "/openapi.json must NOT be in COMMON_INFRA_EXEMPT_PATHS (T66.2 — ADV-P62-01)"
        )

    def test_common_infra_exempt_paths_excludes_security_shred(self) -> None:
        """/security/shred must NOT be in COMMON_INFRA_EXEMPT_PATHS (requires JWT auth)."""
        assert "/security/shred" not in _get_common()


# ---------------------------------------------------------------------------
# Per-set unique tests: SEAL_EXEMPT_PATHS
# ---------------------------------------------------------------------------


class TestSealExemptPaths:
    """Tests for SEAL_EXEMPT_PATHS — the vault and license gate exemption set.

    SEAL_EXEMPT_PATHS = COMMON_INFRA_EXEMPT_PATHS | {"/security/shred"}.
    It extends the auth baseline with the emergency shred path so that
    SealGateMiddleware and LicenseGateMiddleware allow emergency shred through.
    """

    def test_seal_exempt_paths_has_exactly_eight_paths(self) -> None:
        """SEAL_EXEMPT_PATHS must have exactly 8 paths (7 common + /security/shred).

        T66.2 reduced COMMON_INFRA_EXEMPT_PATHS from 10 to 7 paths.
        """
        assert len(_get_seal()) == 8

    def test_seal_exempt_paths_is_strict_superset_of_common(self) -> None:
        """SEAL_EXEMPT_PATHS must be a strict superset of COMMON_INFRA_EXEMPT_PATHS."""
        assert _get_common() < _get_seal()

    def test_seal_exempt_paths_delta_is_exactly_shred(self) -> None:
        """SEAL_EXEMPT_PATHS - COMMON_INFRA_EXEMPT_PATHS must equal {/security/shred}."""
        assert _get_seal() - _get_common() == frozenset({"/security/shred"})


# ---------------------------------------------------------------------------
# Per-set unique tests: AUTH_EXEMPT_PATHS
# ---------------------------------------------------------------------------


class TestAuthExemptPaths:
    """Tests for AUTH_EXEMPT_PATHS in auth.py (superset of COMMON_INFRA_EXEMPT_PATHS).

    AUTH_EXEMPT_PATHS = COMMON_INFRA_EXEMPT_PATHS | {"/auth/token"}.
    Security routes are NOT included — they require JWT auth.
    """

    def test_auth_exempt_paths_is_superset_of_common(self) -> None:
        """AUTH_EXEMPT_PATHS must be a strict superset of COMMON_INFRA_EXEMPT_PATHS."""
        assert _get_common() < _get_auth()

    def test_auth_exempt_paths_contains_auth_token(self) -> None:
        """AUTH_EXEMPT_PATHS must include /auth/token."""
        assert "/auth/token" in _get_auth()

    def test_auth_exempt_paths_has_exactly_eight_paths(self) -> None:
        """AUTH_EXEMPT_PATHS must have exactly 10 paths (7 common + 3 auth paths).

        After T66.2 removed /docs, /redoc, /openapi.json from COMMON_INFRA_EXEMPT_PATHS:
        - COMMON_INFRA_EXEMPT_PATHS has 7 paths
        - AUTH_EXEMPT_PATHS = 7 + /auth/token + /auth/oidc/authorize
          + /auth/oidc/callback = 10 paths
        Count: 12 (T48.3) → 10 (P50) → 11 (T55.1) → 8 (T66.2) → 10 (P81 OIDC endpoints).
        """
        assert len(_get_auth()) == 10

    def test_auth_exempt_paths_excludes_security_shred(self) -> None:
        """/security/shred must NOT be in AUTH_EXEMPT_PATHS (requires JWT auth)."""
        assert "/security/shred" not in _get_auth()


# ---------------------------------------------------------------------------
# Per-set unique tests: VAULT EXEMPT_PATHS
# ---------------------------------------------------------------------------


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
        assert _get_vault() == _get_seal()


# ---------------------------------------------------------------------------
# Per-set unique tests: LICENSE_EXEMPT_PATHS
# ---------------------------------------------------------------------------


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
        assert _get_license() == _get_seal()

    def test_license_exempt_paths_contains_license_activate(self) -> None:
        """/license/activate must be in LICENSE_EXEMPT_PATHS (license bootstrap)."""
        assert "/license/activate" in _get_license()
