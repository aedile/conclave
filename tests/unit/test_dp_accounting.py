"""Unit tests for the extracted dp_accounting module (T43.1).

Verifies that dp_accounting.py is a first-class module containing all DP
accounting logic: _AUDIT_RECONCILIATION_MSG constant, _handle_dp_accounting()
function, and DpAccountingStep class.

These tests import directly from dp_accounting — not through job_orchestration —
to confirm the extraction is complete and the module is independently usable.

Task: T43.1 — Extract dp_accounting.py from job_orchestration.py
Task: T49.1 — Assertion Hardening: verify failure propagation (not just logging)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.helpers_synthesizer import _make_synthesis_job

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job_context(job: Any = None, **kwargs: Any) -> Any:
    """Build a JobContext with sensible defaults for unit tests."""
    from synth_engine.modules.synthesizer.job_orchestration import JobContext

    if job is None:
        job = _make_synthesis_job()

    defaults: dict[str, Any] = {
        "job": job,
        "session": MagicMock(),
        "engine": MagicMock(),
        "dp_wrapper": None,
        "checkpoint_dir": "/tmp/ckpt",
    }
    defaults.update(kwargs)
    return JobContext(**defaults)


# ---------------------------------------------------------------------------
# AC1: dp_accounting.py is importable and exports required names
# ---------------------------------------------------------------------------


class TestDpAccountingModuleExists:
    """dp_accounting.py must exist and export all required names (T43.1 AC1)."""

    def test_module_is_importable(self) -> None:
        """dp_accounting module must be importable from the synthesizer package."""
        import synth_engine.modules.synthesizer.dp_accounting  # noqa: F401

    def test_audit_reconciliation_msg_is_exported(self) -> None:
        """_AUDIT_RECONCILIATION_MSG constant must be present in dp_accounting."""
        from synth_engine.modules.synthesizer.dp_accounting import (
            _AUDIT_RECONCILIATION_MSG,
        )

        assert isinstance(_AUDIT_RECONCILIATION_MSG, str)
        assert len(_AUDIT_RECONCILIATION_MSG) > 0

    def test_handle_dp_accounting_is_exported(self) -> None:
        """_handle_dp_accounting() function must be present in dp_accounting."""
        from synth_engine.modules.synthesizer.dp_accounting import (
            _handle_dp_accounting,
        )

        assert callable(_handle_dp_accounting)

    def test_dp_accounting_step_is_exported(self) -> None:
        """DpAccountingStep class must be present in dp_accounting."""
        from synth_engine.modules.synthesizer.dp_accounting import DpAccountingStep

        assert isinstance(DpAccountingStep(), DpAccountingStep)

    def test_dp_accounting_step_has_execute_method(self) -> None:
        """DpAccountingStep must implement the SynthesisJobStep protocol."""
        from synth_engine.modules.synthesizer.dp_accounting import DpAccountingStep

        step = DpAccountingStep()
        assert hasattr(step, "execute")
        assert callable(step.execute)


# ---------------------------------------------------------------------------
# AC1: Constants have expected values
# ---------------------------------------------------------------------------


class TestDpAccountingConstants:
    """DP accounting constants must retain their canonical values after extraction."""

    def test_audit_reconciliation_msg_content(self) -> None:
        """_AUDIT_RECONCILIATION_MSG must mention reconciliation for operators."""
        from synth_engine.modules.synthesizer.dp_accounting import (
            _AUDIT_RECONCILIATION_MSG,
        )

        assert "reconciliation" in _AUDIT_RECONCILIATION_MSG.lower()

    def test_audit_reconciliation_msg_matches_job_orchestration(self) -> None:
        """_AUDIT_RECONCILIATION_MSG in dp_accounting must equal job_orchestration's value.

        Ensures the constant is the single source of truth and was not duplicated
        with a different value.
        """
        from synth_engine.modules.synthesizer.dp_accounting import (
            _AUDIT_RECONCILIATION_MSG as DP_MSG,
        )
        from synth_engine.modules.synthesizer.job_orchestration import (
            _AUDIT_RECONCILIATION_MSG as ORCH_MSG,
        )

        assert DP_MSG == ORCH_MSG, (
            "Constant mismatch: dp_accounting and job_orchestration must agree on "
            "_AUDIT_RECONCILIATION_MSG"
        )


# ---------------------------------------------------------------------------
# AC3: DpAccountingStep imported from dp_accounting is the same class as
#       the one in job_orchestration (identity, not just equality)
# ---------------------------------------------------------------------------


class TestDpAccountingStepIdentity:
    """DpAccountingStep must be the same class object in both modules (AC3)."""

    def test_dp_accounting_step_same_class_in_job_orchestration(self) -> None:
        """DpAccountingStep from dp_accounting must be identical to job_orchestration's copy.

        After extraction, job_orchestration imports DpAccountingStep FROM
        dp_accounting — there must be exactly one class definition.
        """
        from synth_engine.modules.synthesizer.dp_accounting import (
            DpAccountingStep as DpFromDpModule,
        )
        from synth_engine.modules.synthesizer.job_orchestration import (
            DpAccountingStep as DpFromOrchModule,
        )

        assert DpFromDpModule is DpFromOrchModule, (
            "DpAccountingStep must be the same class object in both modules. "
            "job_orchestration should re-import from dp_accounting, not re-define."
        )


# ---------------------------------------------------------------------------
# Behaviour tests: _handle_dp_accounting imported from dp_accounting
# The patch targets are intentionally job_orchestration.* to confirm the
# module-level proxy approach keeps existing patch paths valid.
# ---------------------------------------------------------------------------


class TestHandleDpAccountingBehaviour:
    """_handle_dp_accounting must behave correctly when called from dp_accounting."""

    def test_sets_actual_epsilon_on_job(self) -> None:
        """_handle_dp_accounting must set job.actual_epsilon from dp_wrapper."""
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.23

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

        assert job.actual_epsilon == 1.23

    def test_raises_epsilon_measurement_error_on_epsilon_spent_failure(self) -> None:
        """_handle_dp_accounting must raise EpsilonMeasurementError when epsilon_spent() fails."""
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting
        from synth_engine.shared.exceptions import EpsilonMeasurementError

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.side_effect = RuntimeError("DP crashed")

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
            pytest.raises(EpsilonMeasurementError),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

    def test_raises_budget_exhaustion_error_when_budget_exhausted(self) -> None:
        """_handle_dp_accounting must re-raise BudgetExhaustionError from spend_budget_fn."""
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting
        from synth_engine.shared.exceptions import BudgetExhaustionError

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 999.0
        mock_budget_fn = MagicMock(
            side_effect=BudgetExhaustionError(
                requested_epsilon=Decimal("0.5"),
                total_spent=Decimal("0.9"),
                total_allocated=Decimal("1.0"),
            )
        )

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
            pytest.raises(BudgetExhaustionError),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

    def test_raises_audit_write_error_when_audit_fails_after_budget_spend(self) -> None:
        """_handle_dp_accounting must raise AuditWriteError when audit fails post-budget-spend."""
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting
        from synth_engine.shared.exceptions import AuditWriteError

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.5
        mock_budget_fn = MagicMock()  # budget spend succeeds
        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("audit write failed")

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=mock_audit,
            ),
            pytest.raises(AuditWriteError),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

    def test_early_return_when_actual_epsilon_is_none_after_measurement(self) -> None:
        """_handle_dp_accounting must skip budget spend when job.actual_epsilon is None.

        This guards the early-return branch: if epsilon_spent() returns None
        (e.g. via a custom dp_wrapper), the function must return without calling
        spend_budget_fn.
        """
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        # epsilon_spent() returns None, so job.actual_epsilon will be None after assignment
        mock_wrapper.epsilon_spent.return_value = None
        mock_budget_fn = MagicMock()

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

        # spend_budget_fn must NOT have been called when actual_epsilon is None
        mock_budget_fn.assert_not_called()

    def test_raises_audit_write_error_when_spend_budget_fn_raises_connection_error(
        self,
    ) -> None:
        """_handle_dp_accounting must raise AuditWriteError when spend_budget_fn raises.

        Covers the ``except Exception`` path (ADV-P38-01): unexpected errors from
        spend_budget_fn (e.g. ConnectionError) must be wrapped as AuditWriteError
        so the job is marked FAILED with a sentinel message — not a raw exc string.
        """
        from synth_engine.modules.synthesizer.dp_accounting import (
            _BUDGET_SPEND_FAILED_MSG,
            _handle_dp_accounting,
        )
        from synth_engine.shared.exceptions import AuditWriteError

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.8
        mock_budget_fn = MagicMock(side_effect=ConnectionError("DB unreachable"))

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
            pytest.raises(AuditWriteError) as excinfo,
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

        raised_msg = str(excinfo.value)
        # Raw exception detail must not appear in the AuditWriteError message
        assert "DB unreachable" not in raised_msg
        # The error message must be the fixed sentinel constant
        assert raised_msg == _BUDGET_SPEND_FAILED_MSG

    def test_unknown_exception_from_spend_budget_fn_does_not_propagate_raw(self) -> None:
        """Unexpected spend_budget_fn errors must NOT propagate as the raw exception type.

        If an arbitrary exception (e.g., MemoryError, OSError) escapes the budget
        spend, the caller must receive AuditWriteError — never the raw exception.
        Propagating raw exceptions would leak internal state and bypass the sentinel
        error message, making automated incident triage harder.
        """
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting
        from synth_engine.shared.exceptions import AuditWriteError

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.5

        for exc_type, exc_args in [
            (MemoryError, ("out of memory",)),
            (OSError, ("I/O error",)),
            (KeyError, ("missing key",)),
        ]:
            mock_budget_fn = MagicMock(side_effect=exc_type(*exc_args))

            with (
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                    mock_budget_fn,
                ),
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                    return_value=MagicMock(),
                ),
            ):
                with pytest.raises(AuditWriteError):
                    _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

    def test_epsilon_measurement_error_not_silently_logged(self) -> None:
        """EpsilonMeasurementError must propagate to the caller — not just be logged.

        A regression guard: if the implementation ever silently catches
        EpsilonMeasurementError and only logs it, the caller cannot react
        correctly (e.g. the job would appear successful while epsilon is unknown).
        """
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting
        from synth_engine.shared.exceptions import EpsilonMeasurementError

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.side_effect = ValueError("dp engine failure")

        # Confirm that the exception is NOT silently swallowed
        raised = False
        try:
            with (
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                    None,
                ),
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                    return_value=MagicMock(),
                ),
            ):
                _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)
        except EpsilonMeasurementError:
            raised = True

        assert raised, (
            "EpsilonMeasurementError must propagate to the caller, "
            "not be silently caught and logged"
        )

    def test_budget_exhaustion_error_not_silently_logged(self) -> None:
        """BudgetExhaustionError must propagate to the caller — not just be logged.

        A regression guard: the budget must be enforced by propagation so that
        the orchestration layer can mark the job as FAILED. Silent swallowing
        would allow the job to continue consuming budget past the limit.
        """
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting
        from synth_engine.shared.exceptions import BudgetExhaustionError

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 999.0
        mock_budget_fn = MagicMock(
            side_effect=BudgetExhaustionError(
                requested_epsilon=Decimal("1.0"),
                total_spent=Decimal("1.0"),
                total_allocated=Decimal("1.0"),
            )
        )

        raised = False
        try:
            with (
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                    mock_budget_fn,
                ),
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                    return_value=MagicMock(),
                ),
            ):
                _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)
        except BudgetExhaustionError:
            raised = True

        assert raised, (
            "BudgetExhaustionError must propagate to the caller, not be silently caught and logged"
        )


# ---------------------------------------------------------------------------
# Behaviour: DpAccountingStep imported from dp_accounting works end-to-end
# (patch targets remain job_orchestration.* to confirm proxy compatibility)
# ---------------------------------------------------------------------------


class TestDpAccountingStepFromDpAccountingModule:
    """DpAccountingStep (imported from dp_accounting) must behave correctly."""

    def test_step_returns_success_when_no_dp_wrapper(self) -> None:
        """DpAccountingStep.execute() must return success when dp_wrapper is None."""
        from synth_engine.modules.synthesizer.dp_accounting import DpAccountingStep

        ctx = _make_job_context(dp_wrapper=None)
        result = DpAccountingStep().execute(ctx)

        assert result.success is True

    def test_step_returns_success_on_normal_dp_flow(self) -> None:
        """DpAccountingStep.execute() must return success when DP accounting completes."""
        from synth_engine.modules.synthesizer.dp_accounting import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.5

        ctx = _make_job_context(dp_wrapper=mock_wrapper)

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            result = DpAccountingStep().execute(ctx)

        assert result.success is True

    def test_step_returns_failure_on_budget_exhaustion(self) -> None:
        """DpAccountingStep.execute() must return failure on BudgetExhaustionError."""
        from synth_engine.modules.synthesizer.dp_accounting import DpAccountingStep
        from synth_engine.shared.exceptions import BudgetExhaustionError

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 999.0
        mock_budget_fn = MagicMock(
            side_effect=BudgetExhaustionError(
                requested_epsilon=Decimal("0.5"),
                total_spent=Decimal("0.9"),
                total_allocated=Decimal("1.0"),
            )
        )

        ctx = _make_job_context(dp_wrapper=mock_wrapper)

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            result = DpAccountingStep().execute(ctx)

        assert result.success is False
        assert result.error_msg == "Privacy budget exhausted"

    def test_step_returns_failure_on_epsilon_measurement_error(self) -> None:
        """DpAccountingStep.execute() must return failure when epsilon_spent() raises.

        Hardened (T49.1): replaced ``is not None`` with exact sentinel match to
        confirm the error message is the expected human-readable string, not an
        accidentally swallowed value or an empty string.
        """
        from synth_engine.modules.synthesizer.dp_accounting import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.side_effect = RuntimeError("DP crash")

        ctx = _make_job_context(dp_wrapper=mock_wrapper)

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            result = DpAccountingStep().execute(ctx)

        assert result.success is False
        assert result.error_msg == (
            "DP epsilon measurement failed — privacy budget cannot be verified"
        ), f"Expected exact sentinel message; got: {result.error_msg!r}"

    def test_step_returns_failure_on_audit_write_error(self) -> None:
        """DpAccountingStep.execute() must return failure when WORM audit write fails.

        Hardened (T49.1): kept ``is not None`` check and added length check to
        confirm the error message is non-empty and contains the sentinel word.
        """
        from synth_engine.modules.synthesizer.dp_accounting import (
            _AUDIT_RECONCILIATION_MSG,
            DpAccountingStep,
        )

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.5
        mock_budget_fn = MagicMock()
        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("disk full")

        ctx = _make_job_context(dp_wrapper=mock_wrapper)

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            result = DpAccountingStep().execute(ctx)

        assert result.success is False
        assert result.error_msg == _AUDIT_RECONCILIATION_MSG, (
            f"error_msg must equal the _AUDIT_RECONCILIATION_MSG sentinel; "
            f"got: {result.error_msg!r}"
        )
