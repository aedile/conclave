"""Attack/negative tests for the layered exempt-path security model.

Verifies that the layered exemption design correctly prevents bypasses:
- Security routes MUST NOT bypass AuthenticationGateMiddleware.
- /security/keys/rotate MUST NOT bypass SealGateMiddleware (returns 423 internally
  when sealed, but must not be excluded from the seal gate entirely).
- /security/shred MUST bypass SealGateMiddleware (emergency protocol requires
  this to work even when the vault is sealed).
- /security/shred MUST bypass LicenseGateMiddleware (emergency shred works
  without a license).
- No caller can fake a sealed-vault bypass for /security/keys/rotate.

CONSTITUTION Priority 0: Security
Task: P50 review fix — restore /security/shred vault-layer bypass (layered model)
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# ATTACK: /security/shred and /security/keys/rotate must NOT be in
# COMMON_INFRA_EXEMPT_PATHS — they require JWT auth
# ---------------------------------------------------------------------------


class TestSecurityRoutesNotInCommonExemptPaths:
    """Security routes must NOT be in COMMON_INFRA_EXEMPT_PATHS.

    COMMON_INFRA_EXEMPT_PATHS is the auth baseline — anything in it
    bypasses AuthenticationGateMiddleware entirely.  Security routes
    must require JWT auth (ADV-P47-04), so they must NOT appear here.
    """

    def test_security_shred_not_in_common_infra_exempt_paths(self) -> None:
        """/security/shred must NOT be in COMMON_INFRA_EXEMPT_PATHS.

        /security/shred requires JWT auth with security:admin scope.
        Including it in COMMON_INFRA_EXEMPT_PATHS would bypass
        AuthenticationGateMiddleware entirely — a security regression.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert "/security/shred" not in COMMON_INFRA_EXEMPT_PATHS, (
            "/security/shred must NOT bypass AuthenticationGateMiddleware. "
            "It requires JWT auth with security:admin scope."
        )

    def test_security_keys_rotate_not_in_common_infra_exempt_paths(self) -> None:
        """/security/keys/rotate must NOT be in COMMON_INFRA_EXEMPT_PATHS.

        /security/keys/rotate requires JWT auth with security:admin scope.
        Including it in COMMON_INFRA_EXEMPT_PATHS would bypass
        AuthenticationGateMiddleware entirely — a security regression.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert "/security/keys/rotate" not in COMMON_INFRA_EXEMPT_PATHS, (
            "/security/keys/rotate must NOT bypass AuthenticationGateMiddleware. "
            "It requires JWT auth with security:admin scope."
        )

    def test_common_infra_exempt_paths_has_exactly_ten_paths(self) -> None:
        """COMMON_INFRA_EXEMPT_PATHS must have exactly 10 paths.

        9 original + /health/vault added in T55.1.

        After the layered exemption model is in place, security routes are NOT
        in COMMON_INFRA_EXEMPT_PATHS (auth baseline).  They were removed to
        enforce JWT auth on all security operations (ADV-P47-04).
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
        )

        assert len(COMMON_INFRA_EXEMPT_PATHS) == 10, (
            f"Expected 10 paths in COMMON_INFRA_EXEMPT_PATHS, "
            f"got {len(COMMON_INFRA_EXEMPT_PATHS)}. See T55.1."
        )


# ---------------------------------------------------------------------------
# ATTACK: /security/keys/rotate must NOT be in SEAL_EXEMPT_PATHS
# Rotation requires an unsealed vault — bypassing SealGateMiddleware
# would silently return 423 from route logic instead of being gated.
# ---------------------------------------------------------------------------


class TestKeyRotateNotInSealExemptPaths:
    """/security/keys/rotate must NOT bypass SealGateMiddleware.

    Key rotation requires an unsealed vault to access the KEK.
    Adding it to SEAL_EXEMPT_PATHS would let the request reach the
    route handler when sealed, which internally returns 423 — but
    that is the correct design: the route handler owns the 423 logic,
    not the middleware.  SEAL_EXEMPT_PATHS should only contain paths
    that are explicitly designed to work from a sealed state.
    """

    def test_security_keys_rotate_not_in_seal_exempt_paths(self) -> None:
        """/security/keys/rotate must NOT be in SEAL_EXEMPT_PATHS.

        Only /security/shred bypasses the seal gate (emergency protocol).
        Key rotation cannot work when sealed — middleware should block it
        at the 423 level rather than passing to the route handler.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )

        assert "/security/keys/rotate" not in SEAL_EXEMPT_PATHS, (
            "/security/keys/rotate must NOT be in SEAL_EXEMPT_PATHS. "
            "Key rotation requires an unsealed vault."
        )


# ---------------------------------------------------------------------------
# ATTACK: SEAL_EXEMPT_PATHS must be a strict superset of COMMON_INFRA_EXEMPT_PATHS
# and must NOT include /security/keys/rotate
# ---------------------------------------------------------------------------


class TestSealExemptPathsComposition:
    """SEAL_EXEMPT_PATHS must be COMMON_INFRA_EXEMPT_PATHS + /security/shred only."""

    def test_seal_exempt_paths_is_strict_superset_of_common(self) -> None:
        """SEAL_EXEMPT_PATHS must be a strict superset of COMMON_INFRA_EXEMPT_PATHS.

        SEAL_EXEMPT_PATHS extends the auth baseline with /security/shred only —
        it cannot be equal to COMMON_INFRA_EXEMPT_PATHS (it adds emergency shred)
        and cannot be smaller than it.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
            SEAL_EXEMPT_PATHS,
        )

        assert COMMON_INFRA_EXEMPT_PATHS < SEAL_EXEMPT_PATHS, (
            "SEAL_EXEMPT_PATHS must be a strict superset of COMMON_INFRA_EXEMPT_PATHS "
            "(adds /security/shred for emergency vault bypass)."
        )

    def test_seal_exempt_paths_has_exactly_eleven_paths(self) -> None:
        """SEAL_EXEMPT_PATHS must have exactly 11 paths (10 common + /security/shred)."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )

        assert len(SEAL_EXEMPT_PATHS) == 11, (
            f"Expected 11 paths in SEAL_EXEMPT_PATHS, got {len(SEAL_EXEMPT_PATHS)}. "
            "SEAL_EXEMPT_PATHS = COMMON_INFRA_EXEMPT_PATHS (10) + /security/shred (1)."
        )

    def test_seal_exempt_paths_contains_security_shred(self) -> None:
        """/security/shred must be in SEAL_EXEMPT_PATHS (emergency protocol).

        Emergency shred must work even when the vault is sealed — it IS
        the mechanism by which an operator can emergency-seal in response
        to a key compromise.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )

        assert "/security/shred" in SEAL_EXEMPT_PATHS

    def test_seal_exempt_paths_is_frozenset(self) -> None:
        """SEAL_EXEMPT_PATHS must be an immutable frozenset."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )

        assert isinstance(SEAL_EXEMPT_PATHS, frozenset)

    def test_seal_exempt_paths_difference_is_exactly_shred(self) -> None:
        """SEAL_EXEMPT_PATHS - COMMON_INFRA_EXEMPT_PATHS must be exactly {/security/shred}.

        No additional paths should slip into SEAL_EXEMPT_PATHS beyond the
        single emergency shred path.
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            COMMON_INFRA_EXEMPT_PATHS,
            SEAL_EXEMPT_PATHS,
        )

        delta = SEAL_EXEMPT_PATHS - COMMON_INFRA_EXEMPT_PATHS
        assert delta == frozenset({"/security/shred"}), (
            f"SEAL_EXEMPT_PATHS must differ from COMMON_INFRA_EXEMPT_PATHS by "
            f"exactly {{/security/shred}}, but got: {delta}"
        )


# ---------------------------------------------------------------------------
# ATTACK: vault.py must use SEAL_EXEMPT_PATHS, not COMMON_INFRA_EXEMPT_PATHS
# An attacker who removes /security/shred from COMMON_INFRA_EXEMPT_PATHS
# must NOT accidentally lock out the emergency shred path.
# ---------------------------------------------------------------------------


class TestVaultUsesSeaExemptPaths:
    """vault.py EXEMPT_PATHS must equal SEAL_EXEMPT_PATHS, not COMMON_INFRA_EXEMPT_PATHS."""

    def test_vault_exempt_paths_equals_seal_exempt_paths(self) -> None:
        """EXEMPT_PATHS in vault.py must equal SEAL_EXEMPT_PATHS.

        SealGateMiddleware uses EXEMPT_PATHS.  Emergency shred must bypass
        the seal gate — so EXEMPT_PATHS must be SEAL_EXEMPT_PATHS (which
        includes /security/shred), not COMMON_INFRA_EXEMPT_PATHS (which
        does not include it after the layered model is in place).
        """
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )
        from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

        assert EXEMPT_PATHS == SEAL_EXEMPT_PATHS, (
            "vault.EXEMPT_PATHS must equal SEAL_EXEMPT_PATHS to ensure "
            "/security/shred bypasses SealGateMiddleware."
        )

    def test_vault_exempt_paths_contains_security_shred(self) -> None:
        """/security/shred must bypass SealGateMiddleware (vault EXEMPT_PATHS)."""
        from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

        assert "/security/shred" in EXEMPT_PATHS

    def test_vault_exempt_paths_does_not_contain_security_keys_rotate(self) -> None:
        """/security/keys/rotate must NOT bypass SealGateMiddleware."""
        from synth_engine.bootstrapper.dependencies.vault import EXEMPT_PATHS

        assert "/security/keys/rotate" not in EXEMPT_PATHS


# ---------------------------------------------------------------------------
# ATTACK: licensing.py must use SEAL_EXEMPT_PATHS (emergency shred
# must work without a license).
# ---------------------------------------------------------------------------


class TestLicenseUsesSeaExemptPaths:
    """LICENSE_EXEMPT_PATHS must equal SEAL_EXEMPT_PATHS.

    Emergency shred must work without a license — if /security/shred is not
    in LICENSE_EXEMPT_PATHS, LicenseGateMiddleware would block it with 402
    when the software is unlicensed, defeating the emergency protocol.
    """

    def test_license_exempt_paths_equals_seal_exempt_paths(self) -> None:
        """LICENSE_EXEMPT_PATHS must equal SEAL_EXEMPT_PATHS."""
        from synth_engine.bootstrapper.dependencies._exempt_paths import (
            SEAL_EXEMPT_PATHS,
        )
        from synth_engine.bootstrapper.dependencies.licensing import (
            LICENSE_EXEMPT_PATHS,
        )

        assert LICENSE_EXEMPT_PATHS == SEAL_EXEMPT_PATHS, (
            "LICENSE_EXEMPT_PATHS must equal SEAL_EXEMPT_PATHS to ensure "
            "/security/shred works without a license (emergency protocol)."
        )

    def test_license_exempt_paths_contains_security_shred(self) -> None:
        """/security/shred must bypass LicenseGateMiddleware (emergency shred without license)."""
        from synth_engine.bootstrapper.dependencies.licensing import (
            LICENSE_EXEMPT_PATHS,
        )

        assert "/security/shred" in LICENSE_EXEMPT_PATHS

    def test_license_exempt_paths_does_not_contain_security_keys_rotate(self) -> None:
        """/security/keys/rotate must NOT bypass LicenseGateMiddleware."""
        from synth_engine.bootstrapper.dependencies.licensing import (
            LICENSE_EXEMPT_PATHS,
        )

        assert "/security/keys/rotate" not in LICENSE_EXEMPT_PATHS


# ---------------------------------------------------------------------------
# ATTACK: AuthenticationGateMiddleware must NOT exempt security routes
# ---------------------------------------------------------------------------


class TestAuthGateDoesNotExemptSecurityRoutes:
    """AUTH_EXEMPT_PATHS must NOT contain /security/shred or /security/keys/rotate.

    Both security routes require JWT auth with security:admin scope.
    They must not bypass AuthenticationGateMiddleware.
    """

    def test_auth_exempt_paths_does_not_contain_security_shred(self) -> None:
        """/security/shred must NOT be in AUTH_EXEMPT_PATHS."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/security/shred" not in AUTH_EXEMPT_PATHS, (
            "/security/shred must require JWT auth. It must NOT be in AUTH_EXEMPT_PATHS."
        )

    def test_auth_exempt_paths_does_not_contain_security_keys_rotate(self) -> None:
        """/security/keys/rotate must NOT be in AUTH_EXEMPT_PATHS."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/security/keys/rotate" not in AUTH_EXEMPT_PATHS, (
            "/security/keys/rotate must require JWT auth. It must NOT be in AUTH_EXEMPT_PATHS."
        )
