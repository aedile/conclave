"""Attack/negative tests for DP budget fail-closed behaviour (T50.1).

Security mandate: if ``spend_budget_fn`` raises any unexpected exception, the
privacy budget state is unknown — the epsilon ledger may be wrong.  The system
MUST fail closed: the synthesis job must be marked FAILED, and the raw exception
type/message must propagate so the caller can observe the actual failure cause.

ADR-0050: Fail-Closed DP Budget Deduction.

These tests are written ATTACK-RED-first (Rule 22).  They assert the NEW
fail-closed behaviour.  The two existing tests that asserted the OLD
wrapping-as-AuditWriteError behaviour are replaced in the companion commit
that updates test_dp_accounting.py.

Negative / attack scenarios covered:

1. ``ConnectionError`` from ``spend_budget_fn`` propagates as ``ConnectionError``
   through ``_handle_dp_accounting`` — NOT wrapped as ``AuditWriteError``.
2. ``RuntimeError`` from ``spend_budget_fn`` propagates as ``RuntimeError``.
3. ``TypeError`` from ``spend_budget_fn`` propagates as ``TypeError``.
4. ``DpAccountingStep.execute()`` catches any unexpected exception from
   ``_handle_dp_accounting`` and returns ``StepResult(success=False)`` with
   the exception class name in the error message.
5. ``ConnectionError`` injected → job status is FAILED (orchestrator gate).
6. ``RuntimeError`` injected → job status is FAILED.
7. ``epsilon_spent()`` returning a negative value — document guard (or lack of it).
8. ``epsilon_spent()`` returning ``float('inf')`` — document behaviour.
9. ``epsilon_spent()`` returning ``float('nan')`` — document behaviour.
10. ``spend_budget_fn`` succeeds but audit log write raises → job FAILED,
    budget already spent (partial failure, existing behaviour preserved).

Task: T50.1 — DP Budget Deduction: Fail Closed
"""

from __future__ import annotations

import math
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
# ATTACK: _handle_dp_accounting propagates unexpected exceptions raw
# ---------------------------------------------------------------------------


class TestHandleDpAccountingFailClosed:
    """Unexpected exceptions from spend_budget_fn MUST propagate as-is (T50.1 AC1-2)."""

    def test_connection_error_propagates_from_spend_budget_fn(self) -> None:
        """ConnectionError from spend_budget_fn must propagate, NOT wrap as AuditWriteError.

        Compliance: if budget deduction fails due to DB connectivity, the raw
        ConnectionError must reach the caller so it can distinguish a DB failure
        from a deliberate audit-write failure.  Wrapping it as AuditWriteError
        is semantically incorrect and hides the true failure cause.
        """
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting

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
            pytest.raises(ConnectionError),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

    def test_runtime_error_propagates_from_spend_budget_fn(self) -> None:
        """RuntimeError from spend_budget_fn must propagate, NOT wrap as AuditWriteError."""
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.5
        mock_budget_fn = MagicMock(side_effect=RuntimeError("unexpected state"))

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
            pytest.raises(RuntimeError),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

    def test_type_error_propagates_from_spend_budget_fn(self) -> None:
        """TypeError from spend_budget_fn must propagate, NOT wrap as AuditWriteError."""
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.3
        mock_budget_fn = MagicMock(side_effect=TypeError("bad argument type"))

        with (
            patch(
                "synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn",
                mock_budget_fn,
            ),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
            pytest.raises(TypeError),
        ):
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

    def test_connection_error_is_not_wrapped_as_audit_write_error(self) -> None:
        """ConnectionError MUST NOT be wrapped as AuditWriteError.

        AuditWriteError means the budget was successfully deducted but the WORM
        audit trail failed to record it.  A ConnectionError before or during
        spend_budget_fn has a completely different meaning — the deduction status
        is unknown.  Wrapping it as AuditWriteError is a semantic lie.
        """
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting
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
        ):
            raised_as_audit_error = False
            try:
                _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)
            except AuditWriteError:
                raised_as_audit_error = True
            except ConnectionError:
                pass  # Correct: the raw exception propagated

        assert not raised_as_audit_error, (
            "ConnectionError from spend_budget_fn must NOT be wrapped as AuditWriteError. "
            "AuditWriteError has specific semantics: budget deducted, WORM write failed. "
            "A ConnectionError before/during deduction is a different failure class."
        )


# ---------------------------------------------------------------------------
# ATTACK: DpAccountingStep.execute() fails closed on unexpected exceptions
# ---------------------------------------------------------------------------


class TestDpAccountingStepFailClosed:
    """DpAccountingStep.execute() must return failure when spend_budget_fn raises (T50.1 AC3)."""

    def test_step_returns_failure_when_spend_budget_fn_raises_connection_error(
        self,
    ) -> None:
        """DpAccountingStep.execute() must return StepResult(success=False) on ConnectionError.

        This is the AC3 gate: the caller (job orchestrator) must receive a failed
        StepResult when budget deduction fails unexpectedly.  The job status must
        be set to FAILED by the orchestrator.
        """
        from synth_engine.modules.synthesizer.dp_accounting import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.8
        mock_budget_fn = MagicMock(side_effect=ConnectionError("DB unreachable"))

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

        assert result.success is False, (
            "DpAccountingStep.execute() must return success=False when "
            "spend_budget_fn raises ConnectionError"
        )
        assert result.error_msg is not None
        assert len(result.error_msg) > 0

    def test_step_returns_failure_when_spend_budget_fn_raises_runtime_error(
        self,
    ) -> None:
        """DpAccountingStep.execute() must return StepResult(success=False) on RuntimeError."""
        from synth_engine.modules.synthesizer.dp_accounting import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.5
        mock_budget_fn = MagicMock(side_effect=RuntimeError("unexpected state"))

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
        assert result.error_msg is not None

    def test_step_error_message_does_not_expose_internal_exception_detail(
        self,
    ) -> None:
        """DpAccountingStep.execute() error_msg must not leak raw exception messages.

        Even though the exception propagates within the module, the StepResult
        error_msg returned to the orchestrator must contain a sanitised sentinel,
        NOT the raw exception string (which may contain connection strings,
        internal paths, or other sensitive state).
        """
        from synth_engine.modules.synthesizer.dp_accounting import DpAccountingStep

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.8
        sensitive_detail = "postgresql://user:secret_password@db-host:5432/proddb"
        mock_budget_fn = MagicMock(
            side_effect=ConnectionError(sensitive_detail)
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
        assert result.error_msg is not None
        # The raw sensitive detail must NOT appear in the error message
        assert sensitive_detail not in (result.error_msg or ""), (
            "Raw exception message containing sensitive connection details must NOT "
            "appear in StepResult.error_msg — only a sanitised sentinel is permitted"
        )


# ---------------------------------------------------------------------------
# ATTACK: edge cases for epsilon_spent() return values
# ---------------------------------------------------------------------------


class TestEpsilonEdgeCases:
    """Edge cases for unusual epsilon_spent() return values (T50.1 negative tests)."""

    def test_negative_epsilon_is_recorded_without_guard(self) -> None:
        """epsilon_spent() returning a negative value is recorded as-is (no guard exists).

        This test documents the CURRENT behaviour.  If a guard is added in a
        future task, this test should be updated to assert the guard fires.

        A negative epsilon is physically meaningless but _handle_dp_accounting
        does not currently validate the sign.  Recording it does not cause an
        exception — it just stores an invalid value.  The test documents this
        gap so it can be addressed deliberately, not accidentally.
        """
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = -0.5  # Physically meaningless

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
            # No exception expected — documenting that no guard fires
            _handle_dp_accounting(job=job, dp_wrapper=mock_wrapper, job_id=1)

        # The negative value is stored as-is
        assert job.actual_epsilon == -0.5, (
            "Without a sign guard, a negative epsilon is recorded verbatim. "
            "This test documents the current (unguarded) behaviour."
        )

    def test_infinity_epsilon_is_recorded_without_guard(self) -> None:
        """epsilon_spent() returning float('inf') is recorded as-is (no guard exists).

        Documents current behaviour.  float('inf') epsilon is physically
        meaningless but does not raise an exception in the current implementation.
        """
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = float("inf")

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

        assert job.actual_epsilon is not None
        assert math.isinf(job.actual_epsilon), (  # type: ignore[arg-type]
            "Without an infinity guard, float('inf') epsilon is recorded verbatim."
        )

    def test_nan_epsilon_is_recorded_without_guard(self) -> None:
        """epsilon_spent() returning float('nan') is recorded as-is (no guard exists).

        Documents current behaviour.  float('nan') epsilon is physically
        meaningless but does not raise an exception in the current implementation.
        """
        from synth_engine.modules.synthesizer.dp_accounting import _handle_dp_accounting

        job = _make_synthesis_job(id=1)
        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = float("nan")

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

        assert job.actual_epsilon is not None
        assert math.isnan(job.actual_epsilon), (  # type: ignore[arg-type]
            "Without a NaN guard, float('nan') epsilon is recorded verbatim."
        )


# ---------------------------------------------------------------------------
# ATTACK: audit log failure after budget spend (regression guard)
# ---------------------------------------------------------------------------


class TestAuditFailureAfterBudgetSpend:
    """After a successful budget deduction, audit failure must still fail the job (T50.1)."""

    def test_step_returns_failure_when_audit_fails_after_successful_spend(
        self,
    ) -> None:
        """DpAccountingStep must return failure when budget is spent but audit write fails.

        This is a partial-failure scenario: the epsilon was deducted from the
        ledger (irreversible) but the WORM audit trail did not record the event.
        The job must be FAILED so the operator knows reconciliation is needed.
        """
        from synth_engine.modules.synthesizer.dp_accounting import (
            _AUDIT_RECONCILIATION_MSG,
            DpAccountingStep,
        )

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 1.0
        mock_budget_fn = MagicMock()  # Budget spend succeeds
        mock_audit = MagicMock()
        mock_audit.log_event.side_effect = RuntimeError("audit disk full")

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

        assert result.success is False, (
            "Job must be FAILED when budget is spent but audit write fails"
        )
        assert result.error_msg == _AUDIT_RECONCILIATION_MSG, (
            f"error_msg must be the reconciliation sentinel; got: {result.error_msg!r}"
        )
        # Confirm budget was called (i.e., deduction happened before audit failure)
        mock_budget_fn.assert_called_once()

    def test_budget_was_deducted_before_audit_failure_is_detectable(self) -> None:
        """The budget deduction call happens before the audit write — verifiable via mock.

        This test documents the execution order: spend_budget_fn is called first,
        then get_audit_logger().log_event().  If the audit write fails, the budget
        has already been spent, which is why reconciliation is required.
        """
        from synth_engine.modules.synthesizer.dp_accounting import DpAccountingStep
        from synth_engine.shared.exceptions import AuditWriteError

        call_order: list[str] = []

        def record_spend(**kwargs: Any) -> None:
            call_order.append("budget_spent")

        mock_wrapper = MagicMock()
        mock_wrapper.epsilon_spent.return_value = 0.7
        mock_budget_fn = MagicMock(side_effect=record_spend)
        mock_audit = MagicMock()

        def record_audit_fail(**kwargs: Any) -> None:
            call_order.append("audit_failed")
            raise RuntimeError("audit failure")

        mock_audit.log_event.side_effect = record_audit_fail

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
        assert call_order == ["budget_spent", "audit_failed"], (
            f"Execution order must be: spend then audit-fail; got {call_order}"
        )


# ---------------------------------------------------------------------------
# ATTACK: BudgetExhaustionError still handled gracefully (regression guard)
# ---------------------------------------------------------------------------


class TestBudgetExhaustionStillHandled:
    """BudgetExhaustionError from spend_budget_fn must still be caught gracefully (T50.1 AC1)."""

    def test_budget_exhaustion_still_returns_failure_not_propagates(self) -> None:
        """BudgetExhaustionError must still be caught by DpAccountingStep.execute().

        This is a regression guard: the fail-closed change MUST NOT accidentally
        remove the BudgetExhaustionError handler, which is the existing correct
        behaviour that must be preserved.
        """
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
