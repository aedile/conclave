"""Unit tests for the extracted dp_accounting module (T43.1, T50.1).

Verifies that dp_accounting.py is a first-class module containing all DP
accounting logic: _AUDIT_RECONCILIATION_MSG constant, _handle_dp_accounting()
function, and DpAccountingStep class.

These tests import directly from dp_accounting — not through job_orchestration —
to confirm the extraction is complete and the module is independently usable.

T50.1 (fail-closed): The broad ``except Exception`` block that previously wrapped
unexpected exceptions from ``spend_budget_fn`` as ``AuditWriteError`` has been
removed.  Those tests have been replaced with tests documenting the new behaviour:
unexpected exceptions now propagate naturally so the caller can observe the true
failure cause.  See also ``test_dp_budget_fail_closed.py`` for the attack tests.

Task: T43.1 — Extract dp_accounting.py from job_orchestration.py
Task: T49.1 — Assertion Hardening: verify failure propagation (not just logging)
Task: T50.1 — DP Budget Deduction: Fail Closed
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
    from synth_engine.modules.synthesizer.jobs.job_orchestration import JobContext

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
        import synth_engine.modules.synthesizer.training.dp_accounting  # noqa: F401
        import synth_engine.modules.synthesizer.training.dp_accounting as dp_acct

        assert dp_acct.__name__ == "synth_engine.modules.synthesizer.training.dp_accounting"

    def test_audit_reconciliation_msg_is_exported(self) -> None:
        """_AUDIT_RECONCILIATION_MSG constant must be present in dp_accounting."""
        from synth_engine.modules.synthesizer.training.dp_accounting import (
            _AUDIT_RECONCILIATION_MSG,
        )

        assert isinstance(_AUDIT_RECONCILIATION_MSG, str)
        assert len(_AUDIT_RECONCILIATION_MSG) > 0

    def test_handle_dp_accounting_is_exported(self) -> None:
        """_handle_dp_accounting() function must be present in dp_accounting."""
        from synth_engine.modules.synthesizer.training.dp_accounting import (
            _handle_dp_accounting,
        )

        assert callable(_handle_dp_accounting)

    def test_dp_accounting_step_is_exported(self) -> None:
        """DpAccountingStep class must be present in dp_accounting."""
        from synth_engine.modules.synthesizer.training.dp_accounting import DpAccountingStep

        step = DpAccountingStep()
        assert isinstance(step, DpAccountingStep)
        # DpAccountingStep must implement the execute() method from the protocol
        assert callable(step.execute)

    def test_dp_accounting_step_has_execute_method(self) -> None:
        """DpAccountingStep must implement the SynthesisJobStep protocol."""
        from synth_engine.modules.synthesizer.training.dp_accounting import DpAccountingStep

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
        from synth_engine.modules.synthesizer.training.dp_accounting import (
            _AUDIT_RECONCILIATION_MSG,
        )

        assert "reconciliation" in _AUDIT_RECONCILIATION_MSG.lower()

    def test_audit_reconciliation_msg_matches_job_orchestration(self) -> None:
        """_AUDIT_RECONCILIATION_MSG in dp_accounting must equal job_orchestration's value.

        Ensures the constant is the single source of truth and was not duplicated
        with a different value.
        """
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            _AUDIT_RECONCILIATION_MSG as ORCH_MSG,
        )
        from synth_engine.modules.synthesizer.training.dp_accounting import (
            _AUDIT_RECONCILIATION_MSG as DP_MSG,
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
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            DpAccountingStep as DpFromOrchModule,
        )
        from synth_engine.modules.synthesizer.training.dp_accounting import (
            DpAccountingStep as DpFromDpModule,
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
        from synth_engine.modules.synthesizer.training.dp_accounting import _handle_dp_accounting

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.23

        with (
            patch("synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

        assert job.actual_epsilon == 1.23

    def test_raises_epsilon_measurement_error_on_epsilon_spent_failure(self) -> None:
        """_handle_dp_accounting must raise EpsilonMeasurementError when epsilon_spent() fails."""
        from synth_engine.modules.synthesizer.training.dp_accounting import _handle_dp_accounting
        from synth_engine.shared.exceptions import EpsilonMeasurementError

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.side_effect = RuntimeError("DP crashed")

        with (
            patch("synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
            pytest.raises(EpsilonMeasurementError),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

    def test_raises_budget_exhaustion_error_when_budget_exhausted(self) -> None:
        """_handle_dp_accounting must re-raise BudgetExhaustionError from spend_budget_fn."""
        from synth_engine.modules.synthesizer.training.dp_accounting import _handle_dp_accounting
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
                "synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
            pytest.raises(BudgetExhaustionError),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

    def test_raises_audit_write_error_when_audit_fails_after_budget_spend(self) -> None:
        """_handle_dp_accounting must raise AuditWriteError when audit fails post-budget-spend."""
        from synth_engine.modules.synthesizer.training.dp_accounting import _handle_dp_accounting
        from synth_engine.shared.exceptions import AuditWriteError

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.5
        mock_budget_fn = MagicMock()  # budget spend succeeds
        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("audit write failed")

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
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
        from synth_engine.modules.synthesizer.training.dp_accounting import _handle_dp_accounting

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        # epsilon_spent() returns None, so job.actual_epsilon will be None after assignment
        mock_wrapper.epsilon_spent.return_value = None
        mock_budget_fn = MagicMock()

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

        # spend_budget_fn must NOT have been called when actual_epsilon is None
        mock_budget_fn.assert_not_called()
        assert mock_budget_fn.call_count == 0

    def test_connection_error_from_spend_budget_fn_propagates_as_connection_error(
        self,
    ) -> None:
        """_handle_dp_accounting must NOT catch ConnectionError from spend_budget_fn.

        T50.1 (fail-closed): The broad ``except Exception`` block has been removed.
        Unexpected exceptions from ``spend_budget_fn`` propagate to the caller so
        the orchestration layer can observe the true failure cause and fail the job.

        Replaces: test_raises_audit_write_error_when_spend_budget_fn_raises_connection_error
        (which documented the OLD, incorrect wrapping behaviour).
        """
        from synth_engine.modules.synthesizer.training.dp_accounting import _handle_dp_accounting

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.8
        mock_budget_fn = MagicMock(side_effect=ConnectionError("DB unreachable"))

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
            pytest.raises(ConnectionError, match="DB unreachable"),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

    def test_unexpected_exceptions_from_spend_budget_fn_propagate_as_their_own_type(
        self,
    ) -> None:
        """Unexpected spend_budget_fn exceptions must propagate as their original type.

        T50.1 (fail-closed): each exception type must propagate as-is so the
        caller can identify the true failure cause.

        Replaces: test_unknown_exception_from_spend_budget_fn_does_not_propagate_raw
        (which documented the OLD, incorrect wrapping behaviour).
        """
        from synth_engine.modules.synthesizer.training.dp_accounting import _handle_dp_accounting

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
                    "synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn",
                    mock_budget_fn,
                ),
                patch(
                    "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                    return_value=MagicMock(),
                ),
                pytest.raises(exc_type),
            ):
                _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

    def test_epsilon_measurement_error_not_silently_logged(self) -> None:
        """EpsilonMeasurementError must propagate to the caller — not just be logged.

        A regression guard: if the implementation ever silently catches
        EpsilonMeasurementError and only logs it, the caller cannot react
        correctly (e.g. the job would appear successful while epsilon is unknown).
        """
        from synth_engine.modules.synthesizer.training.dp_accounting import _handle_dp_accounting
        from synth_engine.shared.exceptions import EpsilonMeasurementError

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.side_effect = ValueError("dp engine failure")

        # Confirm that the exception is NOT silently swallowed
        raised = False
        try:
            with (
                patch(
                    "synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn",
                    None,
                ),
                patch(
                    "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                    return_value=MagicMock(),
                ),
            ):
                _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)
        except EpsilonMeasurementError:
            raised = True

        assert raised == True, (
            "EpsilonMeasurementError must propagate to the caller, "
            "not be silently caught and logged"
        )
        assert raised

    def test_budget_exhaustion_error_not_silently_logged(self) -> None:
        """BudgetExhaustionError must propagate to the caller — not just be logged.

        A regression guard: the budget must be enforced by propagation so that
        the orchestration layer can mark the job as FAILED. Silent swallowing
        would allow the job to continue consuming budget past the limit.
        """
        from synth_engine.modules.synthesizer.training.dp_accounting import _handle_dp_accounting
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
                    "synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn",
                    mock_budget_fn,
                ),
                patch(
                    "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                    return_value=MagicMock(),
                ),
            ):
                _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)
        except BudgetExhaustionError:
            raised = True

        assert raised == True, (
            "BudgetExhaustionError must propagate to the caller, not be silently caught and logged"
        )
        assert raised


# ---------------------------------------------------------------------------
# Behaviour: DpAccountingStep imported from dp_accounting works end-to-end
# (patch targets remain job_orchestration.* to confirm proxy compatibility)
# ---------------------------------------------------------------------------


class TestDpAccountingStepFromDpAccountingModule:
    """DpAccountingStep (imported from dp_accounting) must behave correctly."""

    def test_step_returns_success_when_no_dp_wrapper(self) -> None:
        """DpAccountingStep.execute() must return success when dp_wrapper is None."""
        from synth_engine.modules.synthesizer.training.dp_accounting import DpAccountingStep

        ctx = _make_job_context(dp_wrapper=None)
        result = DpAccountingStep().execute(ctx)

        assert result.success is True
        assert result.success

    def test_step_returns_success_on_normal_dp_flow(self) -> None:
        """DpAccountingStep.execute() must return success when DP accounting completes."""
        from synth_engine.modules.synthesizer.training.dp_accounting import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.5

        ctx = _make_job_context(dp_wrapper=mock_wrapper)

        with (
            patch("synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            result = DpAccountingStep().execute(ctx)

        assert result.success is True
        assert result.success

    def test_step_returns_failure_on_budget_exhaustion(self) -> None:
        """DpAccountingStep.execute() must return failure on BudgetExhaustionError."""
        from synth_engine.modules.synthesizer.training.dp_accounting import DpAccountingStep
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
                "synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
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
        from synth_engine.modules.synthesizer.training.dp_accounting import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.side_effect = RuntimeError("DP crash")

        ctx = _make_job_context(dp_wrapper=mock_wrapper)

        with (
            patch("synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn", None),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
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
        from synth_engine.modules.synthesizer.training.dp_accounting import (
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
                "synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                return_value=mock_audit,
            ),
        ):
            result = DpAccountingStep().execute(ctx)

        assert result.success is False
        assert result.error_msg == _AUDIT_RECONCILIATION_MSG, (
            f"error_msg must equal the _AUDIT_RECONCILIATION_MSG sentinel; "
            f"got: {result.error_msg!r}"
        )

    def test_step_returns_failure_on_unexpected_exception_from_spend_budget_fn(
        self,
    ) -> None:
        """DpAccountingStep.execute() must return failure on any unexpected exception.

        T50.1 (fail-closed): When spend_budget_fn raises an unexpected exception
        (e.g. ConnectionError, RuntimeError), it propagates through
        _handle_dp_accounting and must be caught by DpAccountingStep.execute()'s
        catch-all, which returns StepResult(success=False) with a sanitised message.

        The catch-all must NOT re-raise, must NOT return success=True, and the
        error_msg must be a safe sentinel (not the raw exception message).
        """
        from synth_engine.modules.synthesizer.training.dp_accounting import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.7
        mock_budget_fn = MagicMock(side_effect=ConnectionError("connection refused"))

        ctx = _make_job_context(dp_wrapper=mock_wrapper)

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            result = DpAccountingStep().execute(ctx)

        assert result.success is False, (
            "DpAccountingStep must return success=False when spend_budget_fn raises"
        )
        assert result.error_msg is not None
        assert len(result.error_msg) > 0
        # Raw exception detail must not appear in the sanitised error message
        assert "connection refused" not in (result.error_msg or ""), (
            "Raw exception message must not appear in StepResult.error_msg"
        )
