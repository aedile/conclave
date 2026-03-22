"""Unit tests for the shared exception hierarchy (AC1–AC5, P26-T26.2; T34.1).

Tests follow TDD RED phase — written before implementation.

Key goals:
- Verify exception hierarchy inheritance relationships.
- Verify BudgetExhaustionError can be caught without duck-typing.
- Verify safe_error_msg strips Python module paths from error messages.
- Verify error sanitization for HTTP exposure.
- Verify vault and license exceptions are unified under SynthEngineError (T34.1).

CONSTITUTION Priority 3: TDD — RED phase
Task: P26-T26.2 — Exception Hierarchy + Error Sanitization + Type Tightening
Task: T34.1 — Unify Vault Exceptions Under SynthEngineError
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.unit


class TestExceptionHierarchy:
    """AC1: SynthEngineError base hierarchy lives in shared/exceptions.py."""

    def test_synth_engine_error_is_importable(self) -> None:
        """SynthEngineError must be importable from shared.exceptions."""
        from synth_engine.shared.exceptions import SynthEngineError

        assert issubclass(SynthEngineError, Exception)

    def test_budget_exhaustion_error_inherits_synth_engine_error(self) -> None:
        """BudgetExhaustionError must inherit from SynthEngineError."""
        from synth_engine.shared.exceptions import BudgetExhaustionError, SynthEngineError

        assert issubclass(BudgetExhaustionError, SynthEngineError)

    def test_oom_guardrail_error_inherits_synth_engine_error(self) -> None:
        """OOMGuardrailError must inherit from SynthEngineError."""
        from synth_engine.shared.exceptions import OOMGuardrailError, SynthEngineError

        assert issubclass(OOMGuardrailError, SynthEngineError)

    def test_privilege_escalation_error_inherits_synth_engine_error(self) -> None:
        """PrivilegeEscalationError must inherit from SynthEngineError."""
        from synth_engine.shared.exceptions import PrivilegeEscalationError, SynthEngineError

        assert issubclass(PrivilegeEscalationError, SynthEngineError)

    def test_artifact_tampering_error_inherits_synth_engine_error(self) -> None:
        """ArtifactTamperingError must inherit from SynthEngineError."""
        from synth_engine.shared.exceptions import ArtifactTamperingError, SynthEngineError

        assert issubclass(ArtifactTamperingError, SynthEngineError)

    def test_vault_sealed_error_inherits_synth_engine_error(self) -> None:
        """VaultSealedError must inherit from SynthEngineError."""
        from synth_engine.shared.exceptions import SynthEngineError, VaultSealedError

        assert issubclass(VaultSealedError, SynthEngineError)

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
        from synth_engine.modules.synthesizer.guardrails import OOMGuardrailError as GuardOom
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

        caught = False
        try:
            _fake_spend_budget()
        except BudgetExhaustionError:
            caught = True

        assert caught, "BudgetExhaustionError must be catchable by type"

    def test_privacy_module_budget_exhaustion_catchable_as_shared_type(self) -> None:
        """Raising from modules/privacy must be catchable via shared.exceptions type."""
        from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError as DpBee
        from synth_engine.shared.exceptions import BudgetExhaustionError as SharedBee

        caught_as_shared = False
        try:
            raise DpBee(
                requested_epsilon=Decimal("0.5"),
                total_spent=Decimal("0.9"),
                total_allocated=Decimal("1.0"),
            )
        except SharedBee:
            caught_as_shared = True

        assert caught_as_shared, (
            "dp_engine.BudgetExhaustionError raised must be catchable as "
            "shared BudgetExhaustionError"
        )


class TestSafeErrorMsgModulePaths:
    """AC5: safe_error_msg must strip Python module paths from error messages."""

    def test_strips_synth_engine_module_path(self) -> None:
        """safe_error_msg must strip 'synth_engine.modules.*' prefixes."""
        from synth_engine.shared.errors import safe_error_msg

        msg = "synth_engine.modules.privacy.dp_engine.BudgetExhaustionError: budget gone"
        result = safe_error_msg(msg)
        assert "synth_engine.modules" not in result

    def test_strips_synth_engine_shared_path(self) -> None:
        """safe_error_msg must strip 'synth_engine.shared.*' class name paths."""
        from synth_engine.shared.errors import safe_error_msg

        msg = "synth_engine.shared.exceptions.SynthEngineError: something failed"
        result = safe_error_msg(msg)
        assert "synth_engine.shared" not in result

    def test_strips_synth_engine_bootstrapper_path(self) -> None:
        """safe_error_msg must strip 'synth_engine.bootstrapper.*' paths."""
        from synth_engine.shared.errors import safe_error_msg

        msg = "synth_engine.bootstrapper.errors.RFC7807Middleware raised"
        result = safe_error_msg(msg)
        assert "synth_engine.bootstrapper" not in result

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
    """T34.1 AC1: Vault exceptions must inherit SynthEngineError (not ValueError).

    These tests verify the three vault exceptions that previously inherited
    ValueError are now unified under the domain hierarchy.
    """

    def test_vault_empty_passphrase_error_is_synth_engine_error(self) -> None:
        """VaultEmptyPassphraseError instance must satisfy isinstance(exc, SynthEngineError)."""
        from synth_engine.shared.exceptions import SynthEngineError, VaultEmptyPassphraseError

        exc = VaultEmptyPassphraseError("Passphrase must not be empty.")
        assert isinstance(exc, SynthEngineError)

    def test_vault_already_unsealed_error_is_synth_engine_error(self) -> None:
        """VaultAlreadyUnsealedError instance must satisfy isinstance(exc, SynthEngineError)."""
        from synth_engine.shared.exceptions import SynthEngineError, VaultAlreadyUnsealedError

        exc = VaultAlreadyUnsealedError("Vault is already unsealed.")
        assert isinstance(exc, SynthEngineError)

    def test_vault_config_error_is_synth_engine_error(self) -> None:
        """VaultConfigError instance must satisfy isinstance(exc, SynthEngineError)."""
        from synth_engine.shared.exceptions import SynthEngineError, VaultConfigError

        exc = VaultConfigError("VAULT_SEAL_SALT is not set.")
        assert isinstance(exc, SynthEngineError)

    def test_vault_empty_passphrase_error_not_value_error(self) -> None:
        """VaultEmptyPassphraseError must NOT inherit ValueError after T34.1.

        Changing the base class away from ValueError is the whole point of the task.
        A VaultEmptyPassphraseError must NOT be caught by a bare 'except ValueError'.
        """
        from synth_engine.shared.exceptions import VaultEmptyPassphraseError

        assert not issubclass(VaultEmptyPassphraseError, ValueError)

    def test_vault_already_unsealed_error_not_value_error(self) -> None:
        """VaultAlreadyUnsealedError must NOT inherit ValueError after T34.1."""
        from synth_engine.shared.exceptions import VaultAlreadyUnsealedError

        assert not issubclass(VaultAlreadyUnsealedError, ValueError)

    def test_vault_config_error_not_value_error(self) -> None:
        """VaultConfigError must NOT inherit ValueError after T34.1."""
        from synth_engine.shared.exceptions import VaultConfigError

        assert not issubclass(VaultConfigError, ValueError)

    def test_vault_exceptions_importable_from_shared_exceptions(self) -> None:
        """All three vault exceptions must be importable from shared.exceptions directly."""
        from synth_engine.shared.exceptions import (  # noqa: F401
            VaultAlreadyUnsealedError,
            VaultConfigError,
            VaultEmptyPassphraseError,
        )

    def test_vault_exceptions_re_exported_from_vault_module(self) -> None:
        """vault.py must still export the three exceptions for backward compatibility."""
        from synth_engine.shared.security.vault import (  # noqa: F401
            VaultAlreadyUnsealedError,
            VaultConfigError,
            VaultEmptyPassphraseError,
        )


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
        from synth_engine.shared.exceptions import LicenseError  # noqa: F401

    def test_license_error_re_exported_from_licensing_module(self) -> None:
        """licensing.py must still export LicenseError for backward compatibility."""
        from synth_engine.shared.security.licensing import LicenseError  # noqa: F401
