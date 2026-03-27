"""Unit tests for synthesizer task differential-privacy wiring and budget accounting.

Covers: dp_wrapper forwarding to engine.train(), DI factory injection via
set_dp_wrapper_factory(), spend_budget wiring via set_spend_budget_fn(),
BudgetExhaustionError handling, audit log emission, and bootstrapper wiring validation.

All tests are isolated (no real DB, no real Huey worker, no network I/O) and stay
boundary-clean — no direct imports from modules/privacy/.

ADR references:
  ADR-0029: DI factory injection pattern (set_dp_wrapper_factory, set_spend_budget_fn)
  ADR-0033: Duck-typing exception matching (superseded by P26-T26.2 typed catch)

AC references (P22-T22.2/T22.3):
  AC2: spend_budget called after successful DP training
  AC3: BudgetExhaustionError marks job FAILED with error_msg and no artifact
  AC4: bootstrapper/factories.py wires spend_budget fn at import time (Rule 8)
  AC5: audit PRIVACY_BUDGET_SPEND event emitted after spend_budget
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.helpers_synthesizer import _make_synthesis_job

# ---------------------------------------------------------------------------
# Module-level shared helpers
# ---------------------------------------------------------------------------


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


def _run_impl(
    job_id: int,
    *,
    enable_dp: bool = True,
    dp_wrapper: MagicMock | None = None,
    budget_fn: MagicMock | None = None,
    audit_logger: MagicMock | None = None,
) -> tuple[Any, MagicMock, MagicMock]:
    """Run _run_synthesis_job_impl with standard mocks.

    Configures a mock session and mock engine, wires spend_budget and audit_logger,
    then calls _run_synthesis_job_impl.  Returns (job, mock_session, mock_engine).

    Args:
        job_id: Job identifier.
        enable_dp: Whether the job uses DP training.
        dp_wrapper: Optional DP wrapper mock (None for non-DP path).
        budget_fn: Optional spend_budget mock; defaults to a no-op MagicMock.
        audit_logger: Optional audit logger mock; defaults to a no-op MagicMock.

    Returns:
        Tuple of (job, mock_session, mock_engine).
    """
    import synth_engine.modules.synthesizer.jobs.job_orchestration as orch_mod
    from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

    job = _make_synthesis_job(
        id=job_id,
        status="QUEUED",
        total_epochs=5,
        checkpoint_every_n=5,
        enable_dp=enable_dp,
        actual_epsilon=None,
    )
    mock_session = MagicMock()
    mock_session.get.return_value = job
    mock_engine = MagicMock()
    mock_engine.train.return_value = MagicMock()

    _budget_fn = budget_fn if budget_fn is not None else MagicMock()
    _audit_logger = audit_logger if audit_logger is not None else MagicMock()

    original_fn = orch_mod._spend_budget_fn
    try:
        orch_mod.set_spend_budget_fn(_budget_fn)
        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                return_value=_audit_logger,
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

    return job, mock_session, mock_engine


# ---------------------------------------------------------------------------
# DP wiring tests (P22-T22.2)
# ---------------------------------------------------------------------------


class TestDPWiringInImpl:
    """Tests for dp_wrapper forwarding inside _run_synthesis_job_impl.

    These tests call _run_synthesis_job_impl directly with an injected
    dp_wrapper mock so no bootstrapper import is required.
    """

    @pytest.mark.parametrize(
        ("enable_dp", "use_dp_wrapper", "expect_none"),
        [
            pytest.param(True, True, False, id="dp_enabled"),
            pytest.param(False, False, True, id="dp_disabled"),
        ],
    )
    def test_dp_wrapper_forwarded_to_engine_train(
        self,
        enable_dp: bool,
        use_dp_wrapper: bool,
        expect_none: bool,
    ) -> None:
        """engine.train() must receive or omit dp_wrapper based on enable_dp (P22-T22.2).

        dp_enabled: dp_wrapper kwarg on every engine.train() call must be the injected
          wrapper object.
        dp_disabled: dp_wrapper kwarg must be None on every engine.train() call.
        """
        dp_wrapper_arg = _make_mock_dp_wrapper(epsilon=2.5) if use_dp_wrapper else None
        _, _, mock_engine = _run_impl(job_id=10, enable_dp=enable_dp, dp_wrapper=dp_wrapper_arg)
        for call in mock_engine.train.call_args_list:
            actual = call.kwargs.get("dp_wrapper", None)
            if expect_none:
                assert actual is None, (
                    f"engine.train() received non-None dp_wrapper on non-DP job: {call}"
                )
            else:
                assert actual is dp_wrapper_arg, (
                    f"engine.train() call missing dp_wrapper kwarg: {call}"
                )

    @pytest.mark.parametrize(
        ("enable_dp", "epsilon_value", "expected_epsilon"),
        [
            pytest.param(True, 3.14, 3.14, id="dp_enabled"),
            pytest.param(False, None, None, id="dp_disabled"),
        ],
    )
    def test_actual_epsilon_recorded_correctly(
        self,
        enable_dp: bool,
        epsilon_value: float | None,
        expected_epsilon: float | None,
    ) -> None:
        """job.actual_epsilon must be set to epsilon_spent() result or remain None (P22-T22.2).

        dp_enabled: actual_epsilon == dp_wrapper.epsilon_spent(delta=1e-5).
        dp_disabled: actual_epsilon remains None.
        """
        dp_wrapper_arg = (
            _make_mock_dp_wrapper(epsilon=epsilon_value) if epsilon_value is not None else None
        )
        job, _, _ = _run_impl(job_id=12, enable_dp=enable_dp, dp_wrapper=dp_wrapper_arg)
        assert job.actual_epsilon == expected_epsilon, (
            f"Expected actual_epsilon={expected_epsilon!r}; got {job.actual_epsilon}"
        )
        if enable_dp and dp_wrapper_arg is not None:
            dp_wrapper_arg.epsilon_spent.assert_called_once_with(delta=1e-5)

    def test_epsilon_spent_exception_marks_job_failed(self) -> None:
        """RuntimeError from epsilon_spent() must mark job FAILED (T37.1, ADV-P35-01).

        Constitution Priority 0: if the privacy cost cannot be measured, the job
        must be FAILED — not silently completed with actual_epsilon=None.
        """
        dp_wrapper = MagicMock()
        dp_wrapper.epsilon_spent.side_effect = RuntimeError("Opacus error")
        job, _, _ = _run_impl(job_id=14, enable_dp=True, dp_wrapper=dp_wrapper)

        assert job.status == "FAILED", (
            f"Expected FAILED when epsilon_spent() raises; got {job.status}"
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
        """set_dp_wrapper_factory must store the provided callable at module level."""
        import synth_engine.modules.synthesizer.jobs.job_orchestration as orch_mod

        mock_factory = MagicMock(return_value=MagicMock())
        original = orch_mod._dp_wrapper_factory
        try:
            orch_mod.set_dp_wrapper_factory(mock_factory)
            assert orch_mod._dp_wrapper_factory is mock_factory
        finally:
            orch_mod._dp_wrapper_factory = original  # type: ignore[assignment]

    def test_dp_requested_without_factory_raises_runtime_error(self) -> None:
        """run_synthesis_job must raise RuntimeError when enable_dp=True and no factory set.

        Verifies the guard in run_synthesis_job() fires with the expected message
        when _dp_wrapper_factory is None and a DP job is requested.
        """
        import synth_engine.modules.synthesizer.jobs.job_orchestration as orch_mod
        import synth_engine.modules.synthesizer.jobs.tasks as tasks_mod

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
                patch("synth_engine.shared.db.get_engine", return_value=MagicMock()),
                patch("sqlmodel.Session", return_value=mock_session_ctx),
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

    def test_set_spend_budget_fn_stores_callable(self) -> None:
        """set_spend_budget_fn must store the provided callable at module level."""
        import synth_engine.modules.synthesizer.jobs.job_orchestration as orch_mod

        mock_fn = MagicMock()
        original = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_fn)
            assert orch_mod._spend_budget_fn is mock_fn
        finally:
            orch_mod._spend_budget_fn = original  # type: ignore[assignment]

    def test_spend_budget_called_with_correct_kwargs(self) -> None:
        """spend_budget fn must be called once with correct amount, job_id, ledger_id (AC2).

        amount == epsilon from dp_wrapper.epsilon_spent() (2.5).
        job_id == the job id passed to _run_synthesis_job_impl.
        ledger_id == 1 (migration 005 seeds a single PrivacyLedger row with id=1).
        """
        mock_budget_fn = MagicMock()
        _run_impl(
            job_id=20,
            enable_dp=True,
            dp_wrapper=_make_mock_dp_wrapper(epsilon=2.5),
            budget_fn=mock_budget_fn,
        )

        mock_budget_fn.assert_called_once()
        call_kwargs = mock_budget_fn.call_args.kwargs
        assert call_kwargs["amount"] == 2.5, f"Expected amount=2.5; got {call_kwargs['amount']}"
        assert call_kwargs["job_id"] == 20, f"Expected job_id=20; got {call_kwargs['job_id']}"
        assert call_kwargs["ledger_id"] == 1, (
            f"Expected ledger_id=1 (seeded default); got {call_kwargs['ledger_id']}"
        )

    def test_budget_exhaustion_outcomes(self) -> None:
        """BudgetExhaustionError must produce correct job state outcomes (AC3).

        When _spend_budget_fn raises BudgetExhaustionError:
        - job.status == "FAILED"
        - job.error_msg == "Privacy budget exhausted"
        - job.artifact_path is None  (synthesis artifact must NOT be persisted)
        - session.commit() called at least once (to persist FAILED status)

        P26-T26.2: BudgetExhaustionError now lives in shared/exceptions.py and
        is caught by type rather than by ADR-0033 duck-typing name matching.
        """
        from synth_engine.shared.exceptions import BudgetExhaustionError

        budget_err = BudgetExhaustionError(
            requested_epsilon=Decimal("0.5"),
            total_spent=Decimal("0.9"),
            total_allocated=Decimal("1.0"),
        )
        mock_budget_fn = MagicMock(side_effect=budget_err)
        job, mock_session, _ = _run_impl(
            job_id=22,
            enable_dp=True,
            dp_wrapper=_make_mock_dp_wrapper(epsilon=999.0),
            budget_fn=mock_budget_fn,
        )

        assert job.status == "FAILED", f"Expected FAILED; got {job.status}"
        assert job.error_msg == "Privacy budget exhausted", (
            f"Expected 'Privacy budget exhausted'; got {job.error_msg!r}"
        )
        assert job.artifact_path is None, (
            f"Expected artifact_path=None on budget exhaustion; got {job.artifact_path!r}"
        )
        assert mock_session.commit.call_count >= 1, (
            "Expected at least one session.commit() to persist FAILED status"
        )

    @pytest.mark.parametrize(
        ("enable_dp", "use_error_dp_wrapper"),
        [
            pytest.param(False, False, id="non_dp_job"),
            pytest.param(True, True, id="epsilon_measurement_failed"),
        ],
    )
    def test_spend_budget_not_called(
        self,
        enable_dp: bool,
        use_error_dp_wrapper: bool,
    ) -> None:
        """spend_budget fn must NOT be called when no epsilon was measurably spent.

        non_dp_job: no DP wrapper means no epsilon spent, so no budget deduction.
        epsilon_measurement_failed: epsilon_spent() raises, actual_epsilon stays None,
          so no budget deduction should occur.
        """
        _wrapper: MagicMock | None
        if use_error_dp_wrapper:
            _w = MagicMock()
            _w.epsilon_spent.side_effect = RuntimeError("Opacus internal error")
            _wrapper = _w
        else:
            _wrapper = None
        dp_wrapper_arg = _wrapper
        mock_budget_fn = MagicMock()
        _run_impl(
            job_id=26, enable_dp=enable_dp, dp_wrapper=dp_wrapper_arg, budget_fn=mock_budget_fn
        )
        mock_budget_fn.assert_not_called()

    def test_audit_log_emitted_on_budget_spend(self) -> None:
        """Audit log_event must be called after successful spend_budget (AC5).

        Verifies event_type='PRIVACY_BUDGET_SPEND' and actor='system/huey-worker'.
        """
        mock_audit_logger = MagicMock()
        _run_impl(
            job_id=28,
            enable_dp=True,
            dp_wrapper=_make_mock_dp_wrapper(epsilon=1.5),
            budget_fn=MagicMock(),
            audit_logger=mock_audit_logger,
        )

        mock_audit_logger.log_event.assert_called_once()
        call_kwargs = mock_audit_logger.log_event.call_args.kwargs
        assert call_kwargs.get("event_type") == "PRIVACY_BUDGET_SPEND", (
            f"Expected event_type='PRIVACY_BUDGET_SPEND'; got {call_kwargs.get('event_type')!r}"
        )
        assert call_kwargs["actor"] == "system/huey-worker", (
            f"Expected actor='system/huey-worker'; got {call_kwargs.get('actor')!r}"
        )

    def test_non_budget_exception_from_spend_budget_marks_job_failed(self) -> None:
        """Non-BudgetExhaustion exceptions from _spend_budget_fn must mark job FAILED.

        ADV-P38-01 fix: ConnectionError from _spend_budget_fn must be caught by
        DpAccountingStep, not propagate out of _run_synthesis_job_impl.
        """
        mock_budget_fn = MagicMock(side_effect=ConnectionError("DB down"))
        job, _, _ = _run_impl(
            job_id=29,
            enable_dp=True,
            dp_wrapper=_make_mock_dp_wrapper(epsilon=1.0),
            budget_fn=mock_budget_fn,
        )
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
        """build_spend_budget_fn must return a callable accepting amount, job_id, ledger_id."""
        import inspect

        from synth_engine.bootstrapper.factories import build_spend_budget_fn

        sig = inspect.signature(build_spend_budget_fn())
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

        Regression guard for F3 (review finding): the original code applied
        string.replace() unconditionally, corrupting URLs that already contained
        an async prefix (e.g., 'sqlite+aiosqlite:///:memory:').
        """
        import logging
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock as _MagicMock
        from unittest.mock import patch as _patch

        from synth_engine.bootstrapper.factories import build_spend_budget_fn
        from synth_engine.shared.settings import get_settings

        fn = build_spend_budget_fn()
        captured_urls: list[str] = []

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        def _capture(url: str, **_: object) -> MagicMock:
            captured_urls.append(url)
            return MagicMock()

        mock_settings = _MagicMock()
        mock_settings.database_url = "sqlite+aiosqlite:///:memory:"
        get_settings.cache_clear()
        with (
            _patch("synth_engine.shared.settings.get_settings", return_value=mock_settings),
            _patch("sqlalchemy.ext.asyncio.create_async_engine", side_effect=_capture),
            _patch("sqlalchemy.ext.asyncio.AsyncSession", return_value=mock_session_cm),
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
            assert captured_urls[0] == "sqlite+aiosqlite:///:memory:", (
                f"URL was corrupted: {captured_urls[0]!r}"
            )

    def test_bootstrapper_wires_spend_budget_fn_into_tasks(self) -> None:
        """bootstrapper/main.py must call set_spend_budget_fn at module import time (Rule 8)."""
        import synth_engine.bootstrapper.main  # noqa: F401 — side-effect import
        import synth_engine.modules.synthesizer.jobs.job_orchestration as orch_mod

        assert orch_mod._spend_budget_fn is not None, (
            "_spend_budget_fn must be wired by bootstrapper at import time (Rule 8)."
        )
        assert callable(orch_mod._spend_budget_fn), (
            "_spend_budget_fn must be callable after bootstrapper wiring"
        )
