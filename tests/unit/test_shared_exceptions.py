"""Unit tests for the shared exception hierarchy (AC1–AC5, P26-T26.2; T34.1).

Tests follow TDD RED phase — written before implementation.

Key goals:
- Verify exception hierarchy inheritance relationships.
- Verify BudgetExhaustionError can be caught without duck-typing.
- Verify safe_error_msg strips Python module paths from error messages.
- Verify error sanitization for HTTP exposure.
- Verify vault and license exceptions are unified under SynthEngineError (T34.1).

T73: Parametrize 4 groups of near-duplicate tests:
  - 5 inherits-SynthEngineError tests → 1 parametrized
  - 3 safe_error_msg strip tests → 1 parametrized
  - 3 vault-is-SynthEngineError tests → 1 parametrized
  - 3 vault-not-ValueError tests → 1 parametrized

CONSTITUTION Priority 3: TDD — RED phase
Task: P26-T26.2 — Exception Hierarchy + Error Sanitization + Type Tightening
Task: T34.1 — Unify Vault Exceptions Under SynthEngineError
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Parametrized: domain exception subclass inheritance (AC1)
# ---------------------------------------------------------------------------

_EXCEPTION_SUBCLASSES = [
    pytest.param("BudgetExhaustionError", id="BudgetExhaustion"),
    pytest.param("OOMGuardrailError", id="OOMGuardrail"),
    pytest.param("PrivilegeEscalationError", id="PrivilegeEscalation"),
    pytest.param("ArtifactTamperingError", id="ArtifactTampering"),
    pytest.param("VaultSealedError", id="VaultSealed"),
]


@pytest.mark.parametrize("exc_name", _EXCEPTION_SUBCLASSES)
def test_domain_exception_inherits_synth_engine_error(exc_name: str) -> None:
    """Each domain exception must inherit from SynthEngineError (AC1).

    All five domain exceptions must be catchable at the SynthEngineError boundary
    so orchestration code can handle them with a single except clause.

    Args:
        exc_name: Name of the exception class to verify.
    """
    import synth_engine.shared.exceptions as mod

    synth_engine_error = mod.SynthEngineError
    exc_class = getattr(mod, exc_name)
    assert issubclass(exc_class, synth_engine_error), (
        f"{exc_name} must be a subclass of SynthEngineError, got bases: {exc_class.__bases__!r}"
    )


# ---------------------------------------------------------------------------
# Parametrized: safe_error_msg strips module paths (AC5)
# ---------------------------------------------------------------------------

_STRIP_CASES = [
    pytest.param(
        "synth_engine.modules.privacy.dp_engine.BudgetExhaustionError: budget gone",
        "synth_engine.modules",
        id="modules_path",
    ),
    pytest.param(
        "synth_engine.shared.exceptions.SynthEngineError: something failed",
        "synth_engine.shared",
        id="shared_path",
    ),
    pytest.param(
        "synth_engine.bootstrapper.errors.RFC7807Middleware raised",
        "synth_engine.bootstrapper",
        id="bootstrapper_path",
    ),
]


@pytest.mark.parametrize(("input_msg", "forbidden_fragment"), _STRIP_CASES)
def test_safe_error_msg_strips_module_path(input_msg: str, forbidden_fragment: str) -> None:
    """safe_error_msg must strip Python module-path prefixes from error messages (AC5).

    Module paths in error messages expose internal class names and module
    structure.  safe_error_msg must sanitize them before the string is
    returned to callers or included in HTTP responses.

    Args:
        input_msg: Raw error message that contains a module-path prefix.
        forbidden_fragment: Fragment that must NOT appear in the sanitized output.
    """
    from synth_engine.shared.errors import safe_error_msg

    result = safe_error_msg(input_msg)
    assert forbidden_fragment not in result, (
        f"safe_error_msg must strip {forbidden_fragment!r} from {input_msg!r}, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# Parametrized: vault exceptions — SynthEngineError membership + not ValueError (T34.1)
# ---------------------------------------------------------------------------

_VAULT_EXCEPTION_CASES = [
    pytest.param(
        "VaultEmptyPassphraseError",
        "Passphrase must not be empty.",
        id="VaultEmptyPassphrase",
    ),
    pytest.param(
        "VaultAlreadyUnsealedError",
        "Vault is already unsealed.",
        id="VaultAlreadyUnsealed",
    ),
    pytest.param(
        "VaultConfigError",
        "VAULT_SEAL_SALT is not set.",
        id="VaultConfig",
    ),
]


@pytest.mark.parametrize(("exc_name", "message"), _VAULT_EXCEPTION_CASES)
def test_vault_exception_is_synth_engine_error_not_value_error(exc_name: str, message: str) -> None:
    """Vault exceptions must inherit SynthEngineError and NOT ValueError (T34.1).

    T34.1 moved vault exceptions from ValueError to SynthEngineError.  Both
    conditions are tested together: is-SynthEngineError and is-NOT-ValueError,
    because they are two sides of the same migration — if one passes but the
    other fails, the migration is incomplete.

    Args:
        exc_name: Name of the vault exception class.
        message: Test message to pass when constructing the exception.
    """
    import synth_engine.shared.exceptions as mod

    synth_engine_error = mod.SynthEngineError
    exc_class = getattr(mod, exc_name)

    # Must be a SynthEngineError (post-T34.1)
    exc = exc_class(message)
    assert isinstance(exc, synth_engine_error), (
        f"{exc_name} instance must satisfy isinstance(exc, SynthEngineError)"
    )
    assert str(exc) == message, f"{exc_name} must preserve the message string, got: {str(exc)!r}"
    # Must NOT be a ValueError (pre-T34.1 base class)
    assert not issubclass(exc_class, ValueError), (
        f"{exc_name} must NOT inherit ValueError after T34.1 — "
        "it must only be catchable at SynthEngineError"
    )


# ---------------------------------------------------------------------------
# Class-based tests: exception hierarchy (unique assertions kept standalone)
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    """AC1: SynthEngineError base hierarchy lives in shared/exceptions.py."""

    def test_synth_engine_error_is_importable(self) -> None:
        """SynthEngineError must be importable from shared.exceptions."""
        from synth_engine.shared.exceptions import SynthEngineError

        assert issubclass(SynthEngineError, Exception)

    def test_all_exception_classes_exported(self) -> None:
        """All hierarchy members must appear in shared.exceptions.__all__."""
        import synth_engine.shared.exceptions as mod

        for name in (
            "SynthEngineError",
            "BudgetExhaustionError",
            "OOMGuardrailError",
            "PrivilegeEscalationError",
            "ArtifactTamperingError",
            "VaultSealedError",
            "VaultEmptyPassphraseError",
            "VaultAlreadyUnsealedError",
            "VaultConfigError",
            "LicenseError",
        ):
            assert name in mod.__all__, f"{name} missing from __all__"

    def test_synth_engine_error_is_catchable_as_exception(self) -> None:
        """SynthEngineError must be catchable as a plain Exception."""
        from synth_engine.shared.exceptions import SynthEngineError

        with pytest.raises(SynthEngineError, match="test"):
            raise SynthEngineError("test")

    def test_budget_exhaustion_carries_generic_message(self) -> None:
        """BudgetExhaustionError must return the generic scrubbed message.

        T47.9: The exception message must not contain epsilon values.
        The generic message must describe the error without leaking budget state.
        """
        from synth_engine.shared.exceptions import BudgetExhaustionError

        exc = BudgetExhaustionError(
            requested_epsilon=Decimal("0.5"),
            total_spent=Decimal("0.9"),
            total_allocated=Decimal("1.0"),
        )
        # The message must be the generic safe constant
        assert "budget exhausted" in str(exc).lower(), (
            f"Generic budget exhausted message expected; got: {str(exc)!r}"
        )
        # Must NOT contain any epsilon values
        assert "0.5" not in str(exc), "Message must not contain epsilon values"

    def test_oom_guardrail_carries_message(self) -> None:
        """OOMGuardrailError must preserve the error message."""
        from synth_engine.shared.exceptions import OOMGuardrailError

        exc = OOMGuardrailError("not enough RAM")
        assert "not enough RAM" in str(exc)


class TestBackwardCompatImports:
    """AC1 sub-goal: modules must re-export from shared so existing catch sites work."""

    def test_dp_engine_budget_exhaustion_is_same_class(self) -> None:
        """dp_engine.BudgetExhaustionError must be the shared class (or its subclass)."""
        from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError as DpBee
        from synth_engine.shared.exceptions import BudgetExhaustionError as SharedBee

        # After migration, they must be the same class or one must be a subclass.
        assert issubclass(DpBee, SharedBee) or DpBee is SharedBee

    def test_guardrails_oom_is_same_class(self) -> None:
        """guardrails.OOMGuardrailError must be the shared class (or its subclass)."""
        from synth_engine.modules.synthesizer.training.guardrails import (
            OOMGuardrailError as GuardOom,
        )
        from synth_engine.shared.exceptions import OOMGuardrailError as SharedOom

        assert issubclass(GuardOom, SharedOom) or GuardOom is SharedOom

    def test_postgres_adapter_privilege_escalation_is_same_class(self) -> None:
        """postgres_adapter.PrivilegeEscalationError must be the shared class."""
        from synth_engine.modules.ingestion.postgres_adapter import (
            PrivilegeEscalationError as AdapterPee,
        )
        from synth_engine.shared.exceptions import PrivilegeEscalationError as SharedPee

        assert issubclass(AdapterPee, SharedPee) or AdapterPee is SharedPee

    def test_hmac_signing_security_error_is_same_as_artifact_tampering(self) -> None:
        """hmac_signing.SecurityError must be ArtifactTamperingError or its subclass."""
        from synth_engine.shared.exceptions import ArtifactTamperingError
        from synth_engine.shared.security.hmac_signing import SecurityError

        assert (
            issubclass(SecurityError, ArtifactTamperingError)
            or SecurityError is ArtifactTamperingError
        )

    def test_vault_sealed_error_import_from_vault_is_same_class(self) -> None:
        """vault.VaultSealedError must be the shared class (or its subclass)."""
        from synth_engine.shared.exceptions import VaultSealedError as SharedVse
        from synth_engine.shared.security.vault import VaultSealedError as VaultVse

        assert issubclass(VaultVse, SharedVse) or VaultVse is SharedVse


class TestBudgetExhaustionCatchByType:
    """AC1 primary goal: replace string-based duck-typing with type-based catch."""

    def test_budget_exhaustion_catchable_from_shared_at_orchestration_boundary(self) -> None:
        """job_orchestration must be able to catch BudgetExhaustionError by type.

        This replaces the ADR-0033 duck-typing pattern:
            if "BudgetExhaustion" in type(exc).__name__
        with:
            except BudgetExhaustionError
        """
        from synth_engine.shared.exceptions import BudgetExhaustionError

        # Simulate what job_orchestration does: spend_budget() raises
        # BudgetExhaustionError (from shared, via privacy module).
        def _fake_spend_budget() -> None:
            raise BudgetExhaustionError(
                requested_epsilon=Decimal("0.5"),
                total_spent=Decimal("0.9"),
                total_allocated=Decimal("1.0"),
            )

        exc_captured: BudgetExhaustionError | None = None
        try:
            _fake_spend_budget()
        except BudgetExhaustionError as exc:
            exc_captured = exc

        assert exc_captured is not None, "BudgetExhaustionError must be catchable by type"
        # Verify the exception carries the expected epsilon attributes
        assert exc_captured.requested_epsilon == Decimal("0.5")
        assert exc_captured.remaining_epsilon == Decimal("0.1")

    def test_privacy_module_budget_exhaustion_catchable_as_shared_type(self) -> None:
        """Raising from modules/privacy must be catchable via shared.exceptions type."""
        from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError as DpBee
        from synth_engine.shared.exceptions import BudgetExhaustionError as SharedBee

        exc_captured: SharedBee | None = None
        try:
            raise DpBee(
                requested_epsilon=Decimal("0.5"),
                total_spent=Decimal("0.9"),
                total_allocated=Decimal("1.0"),
            )
        except SharedBee as exc:
            exc_captured = exc

        assert exc_captured is not None, (
            "dp_engine.BudgetExhaustionError raised must be catchable as "
            "shared BudgetExhaustionError"
        )
        # Verify the exception carries the expected epsilon attributes
        assert exc_captured.requested_epsilon == Decimal("0.5")
        assert exc_captured.total_allocated == Decimal("1.0")


class TestSafeErrorMsgModulePaths:
    """AC5: safe_error_msg must handle non-module messages and combined messages."""

    def test_preserves_user_facing_message_after_stripping_module_path(self) -> None:
        """Stripping module paths must not destroy the human-readable portion."""
        from synth_engine.shared.errors import safe_error_msg

        msg = "synth_engine.modules.privacy.dp_engine.BudgetExhaustionError: budget gone"
        result = safe_error_msg(msg)
        assert "budget gone" in result

    def test_non_module_path_message_unchanged(self) -> None:
        """Messages without module paths must pass through unchanged (modulo existing rules)."""
        from synth_engine.shared.errors import safe_error_msg

        msg = "Privacy budget exhausted for job 42"
        result = safe_error_msg(msg)
        assert result == msg

    def test_module_path_stripped_in_combined_message(self) -> None:
        """Combined messages with module path + filesystem path must strip both."""
        from synth_engine.shared.errors import safe_error_msg

        msg = (
            "synth_engine.modules.ingestion.postgres_adapter.PrivilegeEscalationError at /etc/conf"
        )
        result = safe_error_msg(msg)
        assert "synth_engine.modules" not in result
        assert "/etc/conf" not in result


class TestVaultExceptionHierarchyT34:
    """T34.1: Vault exception backward-compat and importability (non-parametrized unique tests)."""

    def test_vault_exceptions_importable_from_shared_exceptions(self) -> None:
        """All three vault exceptions must be importable from shared.exceptions directly."""
        from synth_engine.shared.exceptions import (
            VaultAlreadyUnsealedError,
            VaultConfigError,
            VaultEmptyPassphraseError,
        )

        assert VaultAlreadyUnsealedError.__name__ == "VaultAlreadyUnsealedError"
        assert VaultConfigError.__name__ == "VaultConfigError"
        assert VaultEmptyPassphraseError.__name__ == "VaultEmptyPassphraseError"

    def test_vault_exceptions_re_exported_from_vault_module(self) -> None:
        """vault.py must still export the three exceptions for backward compatibility."""
        from synth_engine.shared.security.vault import (  # noqa: F401
            VaultAlreadyUnsealedError,
            VaultConfigError,
            VaultEmptyPassphraseError,
        )

        assert VaultAlreadyUnsealedError.__name__ == "VaultAlreadyUnsealedError"


class TestLicenseExceptionHierarchyT34:
    """T34.1 AC2: LicenseError must inherit SynthEngineError (not bare Exception).

    Previously LicenseError(Exception) bypassed the domain exception middleware.
    After T34.1 it must be LicenseError(SynthEngineError).
    """

    def test_license_error_is_synth_engine_error(self) -> None:
        """LicenseError instance must satisfy isinstance(exc, SynthEngineError)."""
        from synth_engine.shared.exceptions import LicenseError, SynthEngineError

        exc = LicenseError("License token has expired.")
        assert isinstance(exc, SynthEngineError)
        assert str(exc) == "License token has expired."

    def test_license_error_not_bare_exception(self) -> None:
        """LicenseError must not directly inherit bare Exception after T34.1.

        It should only be an Exception through SynthEngineError.
        """
        from synth_engine.shared.exceptions import LicenseError, SynthEngineError

        assert issubclass(LicenseError, SynthEngineError)
        # Its immediate base must be SynthEngineError, not Exception directly
        assert LicenseError.__bases__ == (SynthEngineError,)

    def test_license_error_preserves_detail_attribute(self) -> None:
        """LicenseError.detail must carry the message string after hierarchy change."""
        from synth_engine.shared.exceptions import LicenseError

        exc = LicenseError("License token has expired.")
        assert exc.detail == "License token has expired."

    def test_license_error_importable_from_shared_exceptions(self) -> None:
        """LicenseError must be importable directly from shared.exceptions."""
        from synth_engine.shared.exceptions import LicenseError

        assert LicenseError.__name__ == "LicenseError"

    def test_license_error_re_exported_from_licensing_module(self) -> None:
        """licensing.py must still export LicenseError for backward compatibility."""
        from synth_engine.shared.security.licensing import LicenseError

        assert LicenseError.__name__ == "LicenseError"
