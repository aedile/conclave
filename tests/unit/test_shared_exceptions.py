"""Unit tests for the shared exception hierarchy (AC1–AC5, P26-T26.2).

Tests follow TDD RED phase — written before implementation.

Key goals:
- Verify exception hierarchy inheritance relationships.
- Verify BudgetExhaustionError can be caught without duck-typing.
- Verify safe_error_msg strips Python module paths from error messages.
- Verify error sanitization for HTTP exposure.

CONSTITUTION Priority 3: TDD — RED phase
Task: P26-T26.2 — Exception Hierarchy + Error Sanitization + Type Tightening
"""

from __future__ import annotations

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
        ):
            assert name in mod.__all__, f"{name} missing from __all__"

    def test_synth_engine_error_is_catchable_as_exception(self) -> None:
        """SynthEngineError must be catchable as a plain Exception."""
        from synth_engine.shared.exceptions import SynthEngineError

        with pytest.raises(Exception):
            raise SynthEngineError("test")

    def test_budget_exhaustion_carries_message(self) -> None:
        """BudgetExhaustionError must preserve the error message."""
        from synth_engine.shared.exceptions import BudgetExhaustionError

        exc = BudgetExhaustionError("budget gone")
        assert "budget gone" in str(exc)

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

        assert issubclass(SecurityError, ArtifactTamperingError) or SecurityError is ArtifactTamperingError

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
            raise BudgetExhaustionError("epsilon_spent=1.1 >= allocated=1.0")

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
            raise DpBee("test from dp_engine")
        except SharedBee:
            caught_as_shared = True

        assert caught_as_shared, (
            "dp_engine.BudgetExhaustionError raised must be catchable as shared BudgetExhaustionError"
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

        msg = "synth_engine.modules.ingestion.postgres_adapter.PrivilegeEscalationError at /etc/conf"
        result = safe_error_msg(msg)
        assert "synth_engine.modules" not in result
        assert "/etc/conf" not in result
