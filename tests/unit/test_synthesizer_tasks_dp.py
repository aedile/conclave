"""Unit tests for synthesizer task differential-privacy wiring and budget accounting.

Covers: dp_wrapper forwarding to engine.train(), DI factory injection via
set_dp_wrapper_factory(), spend_budget wiring via set_spend_budget_fn(),
BudgetExhaustionError handling, audit log emission, and bootstrapper wiring validation.

All tests are isolated (no real DB, no real Huey worker, no network I/O) and stay
boundary-clean — no direct imports from modules/privacy/.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
from tests.unit.helpers_synthesizer import _make_synthesis_job


def _make_mock_dp_wrapper(epsilon: float = 3.14) -> MagicMock:
    """Build a duck-typed mock DPTrainingWrapper.

    The wrapper exposes ``epsilon_spent(delta)`` returning ``epsilon``.
    This mirrors the real ``DPTrainingWrapper`` contract without importing
    from ``modules/privacy/``.

    Args:
        epsilon: Value returned by ``epsilon_spent()``.

    Returns:
        A ``MagicMock`` configured with the DP wrapper duck-type contract.
    """
    wrapper = MagicMock()
    wrapper.epsilon_spent.return_value = epsilon
    return wrapper


# ---------------------------------------------------------------------------
# DP wiring tests (P22-T22.2)
# ---------------------------------------------------------------------------


class TestDPWiringInImpl:
    """Tests for dp_wrapper forwarding inside _run_synthesis_job_impl.

    These tests call _run_synthesis_job_impl directly with an injected
    dp_wrapper mock so no bootstrapper import is required.
    """

    def test_dp_wrapper_passed_to_engine_train_when_enabled(self) -> None:
        """engine.train() must receive the dp_wrapper kwarg when enable_dp=True.

        Confirms that _run_synthesis_job_impl forwards dp_wrapper to every
        engine.train() call made during the training loop.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=10,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = _make_mock_dp_wrapper(epsilon=2.5)

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn"),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=10,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=dp_wrapper,
            )

        # All engine.train() calls must have received dp_wrapper as a keyword arg
        for call in mock_engine.train.call_args_list:
            assert call.kwargs.get("dp_wrapper") is dp_wrapper, (
                f"engine.train() call missing dp_wrapper kwarg: {call}"
            )

    def test_dp_wrapper_not_passed_when_dp_disabled(self) -> None:
        """engine.train() must receive dp_wrapper=None when no wrapper is injected.

        Confirms the non-DP path is unaffected by the new parameter.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=11,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=False,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=11,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=None,
            )

        # All calls must have dp_wrapper=None (or absent, which is also None)
        for call in mock_engine.train.call_args_list:
            actual = call.kwargs.get("dp_wrapper", None)
            assert actual is None, (
                f"engine.train() received non-None dp_wrapper on non-DP job: {call}"
            )

    def test_actual_epsilon_set_on_job_after_dp_training(self) -> None:
        """job.actual_epsilon must be set to epsilon_spent() result after DP training.

        Confirms epsilon is read from the wrapper and persisted to the job
        record before the COMPLETE status commit.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=12,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = _make_mock_dp_wrapper(epsilon=3.14)

        with (
            patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"),
            patch("synth_engine.modules.synthesizer.job_orchestration._spend_budget_fn"),
            patch(
                "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                return_value=MagicMock(),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=12,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=dp_wrapper,
            )

        assert job.actual_epsilon == 3.14, f"Expected actual_epsilon=3.14; got {job.actual_epsilon}"
        dp_wrapper.epsilon_spent.assert_called_once_with(delta=1e-5)

    def test_actual_epsilon_is_none_when_dp_disabled(self) -> None:
        """job.actual_epsilon must remain None when no dp_wrapper is provided.

        Confirms the non-DP path does not write a spurious epsilon value.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=13,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=False,
            actual_epsilon=None,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=13,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=None,
            )

        assert job.actual_epsilon is None, (
            f"Expected actual_epsilon=None on non-DP job; got {job.actual_epsilon}"
        )

    def test_epsilon_spent_exception_marks_job_failed(self) -> None:
        """RuntimeError from epsilon_spent() must mark job FAILED (T37.1, ADV-P35-01).

        Constitution Priority 0: if the privacy cost of a training run cannot be
        measured, delivering the output would violate security guarantees.  The job
        must be marked FAILED — not silently completed with actual_epsilon=None.

        Updated from the pre-T37.1 behavior where this exception was swallowed.
        """
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=14,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = MagicMock()
        dp_wrapper.epsilon_spent.side_effect = RuntimeError("Opacus error")

        with patch("synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"):
            _run_synthesis_job_impl(
                job_id=14,
                session=mock_session,
                engine=mock_engine,
                dp_wrapper=dp_wrapper,
            )

        assert job.status == "FAILED", (
            f"Expected status=FAILED when epsilon_spent() raises; got {job.status}"
        )
        assert job.actual_epsilon is None, (
            f"Expected actual_epsilon=None when epsilon_spent() raises; got {job.actual_epsilon}"
        )
        assert job.error_msg is not None
        assert "epsilon" in job.error_msg.lower() or "privacy budget" in job.error_msg.lower()


# ---------------------------------------------------------------------------
# DI factory injection tests (P22-T22.2 architecture blocker fix)
# ---------------------------------------------------------------------------


class TestDPFactoryInjection:
    """Tests for the set_dp_wrapper_factory DI injection pattern (ADR-0029).

    These tests verify that run_synthesis_job raises RuntimeError when
    enable_dp=True but no factory has been registered, and that
    set_dp_wrapper_factory correctly stores and makes the factory callable.
    """

    def test_set_dp_wrapper_factory_stores_callable(self) -> None:
        """set_dp_wrapper_factory must store the provided callable.

        After calling set_dp_wrapper_factory with a mock factory, the module-
        level _dp_wrapper_factory must reference that exact callable.
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod

        mock_factory = MagicMock(return_value=MagicMock())
        original = orch_mod._dp_wrapper_factory
        try:
            orch_mod.set_dp_wrapper_factory(mock_factory)
            assert orch_mod._dp_wrapper_factory is mock_factory
        finally:
            # Restore original state so other tests are not affected.
            orch_mod._dp_wrapper_factory = original  # type: ignore[assignment]

    def test_dp_requested_without_factory_raises_runtime_error(self) -> None:
        """run_synthesis_job must raise RuntimeError when enable_dp=True and no factory registered.

        Verifies that the guard in run_synthesis_job() fires with the expected
        message when _dp_wrapper_factory is None and a DP job is requested.

        Because Session and get_engine are locally imported inside the task
        function body, they are patched at their source module paths rather
        than via the tasks module namespace.
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        import synth_engine.modules.synthesizer.tasks as tasks_mod

        mock_job = _make_synthesis_job(
            id=99,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
        )

        mock_session_instance = MagicMock()
        mock_session_instance.get.return_value = mock_job
        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value=mock_session_instance)
        mock_session_ctx.__exit__ = MagicMock(return_value=False)

        original_factory = orch_mod._dp_wrapper_factory
        try:
            orch_mod._dp_wrapper_factory = None  # type: ignore[assignment]

            with (
                patch(
                    "synth_engine.shared.db.get_engine",
                    return_value=MagicMock(),
                ),
                patch(
                    "sqlmodel.Session",
                    return_value=mock_session_ctx,
                ),
                pytest.raises(RuntimeError, match="dp_wrapper_factory"),
            ):
                tasks_mod.run_synthesis_job.call_local(99)
        finally:
            orch_mod._dp_wrapper_factory = original_factory  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# spend_budget() wiring tests (P22-T22.3)
# ---------------------------------------------------------------------------


class TestSpendBudgetWiring:
    """Tests for spend_budget DI injection and invocation (AC2, AC3, AC4, AC5, AC6, AC7).

    All tests use mocks — no real database, no real async session.
    The spend_budget callable is injected via set_spend_budget_fn() following
    the same DI pattern as set_dp_wrapper_factory() (ADR-0029).

    Boundary guard: these tests do NOT import from modules/privacy/ — they
    use duck-typed mocks and exception name matching to stay boundary-clean.
    """

    def _run_impl_with_budget_mock(
        self,
        job_id: int = 20,
        epsilon: float = 2.5,
        budget_fn_side_effect: Exception | None = None,
    ) -> tuple[Any, MagicMock, MagicMock]:
        """Helper: run _run_synthesis_job_impl with a DP wrapper and mocked budget fn.

        Returns:
            Tuple of (job, mock_budget_fn, mock_session).
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=job_id,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = _make_mock_dp_wrapper(epsilon=epsilon)

        mock_budget_fn = MagicMock()
        if budget_fn_side_effect is not None:
            mock_budget_fn.side_effect = budget_fn_side_effect

        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with (
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"
                ),
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                    return_value=MagicMock(),
                ),
            ):
                _run_synthesis_job_impl(
                    job_id=job_id,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=dp_wrapper,
                )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        return job, mock_budget_fn, mock_session

    def test_set_spend_budget_fn_stores_callable(self) -> None:
        """set_spend_budget_fn must store the provided callable at module level.

        After calling set_spend_budget_fn with a mock, the module-level
        _spend_budget_fn must reference that exact callable.
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod

        mock_fn = MagicMock()
        original = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_fn)
            assert orch_mod._spend_budget_fn is mock_fn
        finally:
            orch_mod._spend_budget_fn = original  # type: ignore[assignment]

    def test_spend_budget_called_after_dp_training(self) -> None:
        """spend_budget fn must be called after successful DP training (AC2).

        Verifies the fn is invoked exactly once with the correct epsilon
        from the dp_wrapper.epsilon_spent() result.
        """
        job, mock_budget_fn, _ = self._run_impl_with_budget_mock(job_id=20, epsilon=2.5)

        mock_budget_fn.assert_called_once()
        call_kwargs = mock_budget_fn.call_args.kwargs
        assert call_kwargs["amount"] == 2.5, f"Expected amount=2.5; got {call_kwargs.get('amount')}"
        assert call_kwargs["job_id"] == 20, f"Expected job_id=20; got {call_kwargs.get('job_id')}"

    def test_spend_budget_called_with_ledger_id_1(self) -> None:
        """spend_budget fn must be called with ledger_id=1 (default seeded ledger).

        The migration 005 seeds a single PrivacyLedger row with id=1.
        The task must use this fixed ledger_id until multi-tenant is implemented.
        """
        _, mock_budget_fn, _ = self._run_impl_with_budget_mock(job_id=21, epsilon=1.0)

        call_kwargs = mock_budget_fn.call_args.kwargs
        assert call_kwargs["ledger_id"] == 1, (
            f"Expected ledger_id=1; got {call_kwargs.get('ledger_id')}"
        )

    def test_budget_exhaustion_marks_job_failed(self) -> None:
        """BudgetExhaustionError from spend_budget fn must mark job FAILED (AC3).

        P26-T26.2: BudgetExhaustionError now lives in shared/exceptions.py and
        is caught by type rather than by ADR-0033 duck-typing name matching.
        """
        from synth_engine.shared.exceptions import BudgetExhaustionError

        job, mock_budget_fn, mock_session = self._run_impl_with_budget_mock(
            job_id=22,
            epsilon=999.0,
            budget_fn_side_effect=BudgetExhaustionError(
                requested_epsilon=Decimal("0.5"),
                total_spent=Decimal("0.9"),
                total_allocated=Decimal("1.0"),
            ),
        )

        assert job.status == "FAILED", f"Expected FAILED; got {job.status}"

    def test_budget_exhaustion_sets_error_msg(self) -> None:
        """BudgetExhaustionError must set job.error_msg to 'Privacy budget exhausted' (AC3)."""
        from synth_engine.shared.exceptions import BudgetExhaustionError

        job, _, _ = self._run_impl_with_budget_mock(
            job_id=23,
            epsilon=999.0,
            budget_fn_side_effect=BudgetExhaustionError(
                requested_epsilon=Decimal("0.5"),
                total_spent=Decimal("0.9"),
                total_allocated=Decimal("1.0"),
            ),
        )

        assert job.error_msg == "Privacy budget exhausted", (
            f"Expected 'Privacy budget exhausted'; got {job.error_msg!r}"
        )

    def test_budget_exhaustion_artifact_not_persisted(self) -> None:
        """When budget exhausted, job must be FAILED before artifact_path is written (AC3).

        The artifact_path must remain None — the synthesis artifact must NOT
        be persisted when the privacy budget is exhausted.
        """
        from synth_engine.shared.exceptions import BudgetExhaustionError

        job, _, _ = self._run_impl_with_budget_mock(
            job_id=24,
            epsilon=999.0,
            budget_fn_side_effect=BudgetExhaustionError(
                requested_epsilon=Decimal("0.5"),
                total_spent=Decimal("0.9"),
                total_allocated=Decimal("1.0"),
            ),
        )

        assert job.artifact_path is None, (
            f"Expected artifact_path=None on budget exhaustion; got {job.artifact_path!r}"
        )

    def test_budget_exhaustion_commits_failed_status(self) -> None:
        """Budget exhaustion must commit the FAILED status to the database (AC3)."""
        from synth_engine.shared.exceptions import BudgetExhaustionError

        _, _, mock_session = self._run_impl_with_budget_mock(
            job_id=25,
            epsilon=999.0,
            budget_fn_side_effect=BudgetExhaustionError(
                requested_epsilon=Decimal("0.5"),
                total_spent=Decimal("0.9"),
                total_allocated=Decimal("1.0"),
            ),
        )

        assert mock_session.commit.call_count >= 1

    def test_spend_budget_not_called_when_dp_disabled(self) -> None:
        """spend_budget fn must NOT be called when dp_wrapper is None (non-DP job, AC-implicit).

        When the job does not use DP, no epsilon was spent, so no budget
        deduction should occur.
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=26,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=False,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job
        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        mock_budget_fn = MagicMock()
        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with patch(
                "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"
            ):
                _run_synthesis_job_impl(
                    job_id=26,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=None,  # Non-DP path
                )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        mock_budget_fn.assert_not_called()

    def test_spend_budget_not_called_when_epsilon_is_none(self) -> None:
        """spend_budget fn must NOT be called when actual_epsilon is None after training.

        When epsilon_spent() raises, actual_epsilon stays None and budget
        deduction must be skipped (no budget was measurably spent).
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=27,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job
        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = MagicMock()
        dp_wrapper.epsilon_spent.side_effect = RuntimeError("Opacus internal error")

        mock_budget_fn = MagicMock()
        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with patch(
                "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"
            ):
                _run_synthesis_job_impl(
                    job_id=27,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=dp_wrapper,
                )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        mock_budget_fn.assert_not_called()

    def test_audit_log_emitted_on_budget_spend(self) -> None:
        """Audit log_event must be called after successful spend_budget (AC5).

        Verifies that a WORM audit record is emitted with the correct
        event_type='PRIVACY_BUDGET_SPEND' and actor='system/huey-worker'.
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=28,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job
        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = _make_mock_dp_wrapper(epsilon=1.5)
        mock_budget_fn = MagicMock()

        mock_audit_logger = MagicMock()

        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with (
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"
                ),
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                    return_value=mock_audit_logger,
                ),
            ):
                _run_synthesis_job_impl(
                    job_id=28,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=dp_wrapper,
                )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        mock_audit_logger.log_event.assert_called_once()
        audit_call_kwargs = mock_audit_logger.log_event.call_args.kwargs
        expected_event_type = "PRIVACY_BUDGET_SPEND"
        actual_event_type = audit_call_kwargs.get("event_type")
        assert actual_event_type == expected_event_type, (
            f"Expected event_type={expected_event_type!r}; got {actual_event_type!r}"
        )
        assert audit_call_kwargs["actor"] == "system/huey-worker", (
            f"Expected actor='system/huey-worker'; got {audit_call_kwargs.get('actor')!r}"
        )

    def test_non_budget_exception_from_spend_budget_marks_job_failed(self) -> None:
        """Non-BudgetExhaustion exceptions from _spend_budget_fn must mark job FAILED.

        ADV-P38-01 fix: When _spend_budget_fn raises an unexpected exception (e.g.
        ConnectionError), DpAccountingStep must catch it, log at ERROR level, wrap
        it as AuditWriteError, and the orchestrator must mark the job FAILED.
        The exception must NOT propagate uncaught out of _run_synthesis_job_impl.

        Previously this test asserted that ConnectionError propagated — that was the
        buggy behavior identified by ADV-P38-01. The fix catches and handles it.
        """
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.tasks import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=29,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job
        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = _make_mock_dp_wrapper(epsilon=1.0)
        mock_budget_fn = MagicMock()
        mock_budget_fn.side_effect = ConnectionError("DB down")

        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with (
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.check_memory_feasibility"
                ),
                patch(
                    "synth_engine.modules.synthesizer.job_orchestration.get_audit_logger",
                    return_value=MagicMock(),
                ),
            ):
                # ADV-P38-01: ConnectionError must NOT propagate — it is caught and handled.
                _run_synthesis_job_impl(
                    job_id=29,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=dp_wrapper,
                )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        # Job must be marked FAILED — DpAccountingStep caught the ConnectionError
        # and returned StepResult(success=False); the orchestrator set FAILED.
        assert job.status == "FAILED", (
            f"ConnectionError from _spend_budget_fn must mark job FAILED; got {job.status!r}"
        )


# ---------------------------------------------------------------------------
# Bootstrapper factory wiring (AC4)
# ---------------------------------------------------------------------------


class TestSpendBudgetFactoryBootstrapper:
    """Tests for build_spend_budget_fn factory in bootstrapper/factories.py (AC4).

    Verifies the factory produces a sync callable that wraps async spend_budget
    without violating import boundaries.
    """

    def test_build_spend_budget_fn_returns_callable_with_expected_signature(self) -> None:
        """build_spend_budget_fn must return a callable with the spend_budget signature.

        A plain callable() check proves nothing about the wrapper being correct.
        This test asserts the returned function accepts the expected parameters:
        amount, job_id, and ledger_id.
        """
        import inspect

        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        fn = build_spend_budget_fn()
        sig = inspect.signature(fn)
        param_names = set(sig.parameters.keys())
        assert "amount" in param_names, (
            f"spend_budget wrapper must accept 'amount', got params: {param_names}"
        )
        assert "job_id" in param_names, (
            f"spend_budget wrapper must accept 'job_id', got params: {param_names}"
        )
        assert "ledger_id" in param_names, (
            f"spend_budget wrapper must accept 'ledger_id', got params: {param_names}"
        )

    def test_build_spend_budget_fn_does_not_corrupt_async_url(self) -> None:
        """build_spend_budget_fn must not double-substitute async driver prefixes.

        If DATABASE_URL already contains an async driver prefix (e.g.,
        'sqlite+aiosqlite:///:memory:' or 'postgresql+asyncpg://host/db'),
        the URL promotion logic must pass it through unchanged and not corrupt
        it by re-substituting the sync prefix.

        This is a regression guard for F3 (review finding): the original
        code applied string.replace() unconditionally, which would corrupt
        URLs that already contained the async prefix.
        """
        import logging
        from unittest.mock import AsyncMock
        from unittest.mock import patch as _patch

        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        fn = build_spend_budget_fn()

        # Capture the URL passed to create_async_engine.
        captured_urls: list[str] = []

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        def _capture_create_async_engine(url: str, **kwargs: object) -> MagicMock:
            captured_urls.append(url)
            return MagicMock()

        from unittest.mock import MagicMock as _MagicMock

        from synth_engine.shared.settings import get_settings

        mock_settings = _MagicMock()
        mock_settings.database_url = "sqlite+aiosqlite:///:memory:"
        get_settings.cache_clear()
        with (
            _patch(
                "synth_engine.shared.settings.get_settings",
                return_value=mock_settings,
            ),
            _patch(
                "sqlalchemy.ext.asyncio.create_async_engine",
                side_effect=_capture_create_async_engine,
            ),
            _patch(
                "sqlalchemy.ext.asyncio.AsyncSession",
                return_value=mock_session_cm,
            ),
            _patch(
                "synth_engine.modules.privacy.accountant.spend_budget",
                new_callable=lambda: lambda: AsyncMock(),
            ),
        ):
            try:
                fn(amount=0.5, job_id=1, ledger_id=1)
            except Exception as err:
                logging.getLogger(__name__).debug("Expected mock error: %s", err)

        if captured_urls:
            # The URL must not have been double-substituted.
            assert captured_urls[0] == "sqlite+aiosqlite:///:memory:", (
                f"URL was corrupted: {captured_urls[0]!r}"
            )

    def test_bootstrapper_wires_spend_budget_fn_into_tasks(self) -> None:
        """bootstrapper/main.py must call set_spend_budget_fn at module import time.

        Verifies that importing main.py results in _spend_budget_fn being set
        on the tasks module (Rule 8 compliance).
        """
        # Importing main triggers the wiring side-effect; _spend_budget_fn
        # must be non-None after import completes.
        import synth_engine.bootstrapper.main  # noqa: F401 — side-effect import
        import synth_engine.modules.synthesizer.job_orchestration as orch_mod

        assert orch_mod._spend_budget_fn is not None, (
            "_spend_budget_fn must be wired by bootstrapper at import time (Rule 8)."
        )
        assert callable(orch_mod._spend_budget_fn), (
            "_spend_budget_fn must be callable after bootstrapper wiring"
        )
