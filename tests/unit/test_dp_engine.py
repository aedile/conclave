"""Unit tests for DPTrainingWrapper and BudgetExhaustionError.

Tests follow TDD Red/Green/Refactor.  All tests are isolated (no real Opacus
calls, no real PyTorch model) and assert return values — not just absence of
exceptions.

Pattern guards applied:
- Return-value assertion pattern: every test asserts the return value of the
  function under test, not just absence of exceptions.
- Import boundary: modules/privacy must NOT import from modules/synthesizer.
  DPTrainingWrapper accepts generic PyTorch objects — no synthesizer import.
- Silent except blocks: None permitted — all errors logged or re-raised.
- Version-pin hallucination: opacus pinned to verified 1.5.4 from PyPI query.

Task: P4-T4.3b — DP Engine Wiring
ADR: ADR-0017 (CTGAN + Opacus; RDP accountant)
ADV-046: parameter validation guards for degenerate inputs added.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestBudgetExhaustionError:
    """Unit tests for BudgetExhaustionError exception class."""

    def test_budget_exhaustion_error_is_exception(self) -> None:
        """BudgetExhaustionError must be importable and be an Exception subclass."""
        from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError

        assert issubclass(BudgetExhaustionError, Exception)

    def test_budget_exhaustion_error_carries_message(self) -> None:
        """BudgetExhaustionError must carry a human-readable message."""
        from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError

        err = BudgetExhaustionError("epsilon 1.1 >= allocated 1.0")
        assert "1.1" in str(err)

    def test_budget_exhaustion_error_is_raiseable(self) -> None:
        """BudgetExhaustionError must be raiseable via raise statement."""
        from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError

        with pytest.raises(BudgetExhaustionError, match="budget"):
            raise BudgetExhaustionError("budget exhausted")


class TestDPTrainingWrapperInit:
    """Unit tests for DPTrainingWrapper construction."""

    def test_wrapper_instantiates(self) -> None:
        """DPTrainingWrapper must instantiate without error."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper()
        assert wrapper is not None

    def test_wrapper_is_not_wrapped_initially(self) -> None:
        """A newly created DPTrainingWrapper must not be in a wrapped state."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper()
        # epsilon_spent before wrapping should return 0.0 (no Opacus engine yet)
        epsilon = wrapper.epsilon_spent(delta=1e-5)
        assert epsilon == 0.0


class TestDPTrainingWrapperWrap:
    """Unit tests for DPTrainingWrapper.wrap() method.

    All Opacus components are mocked — these are pure unit tests.
    """

    def _make_mock_optimizer(self) -> MagicMock:
        """Return a minimal mock that satisfies Opacus's optimizer interface."""
        mock_opt = MagicMock()
        mock_opt.param_groups = [{"params": [MagicMock()]}]
        return mock_opt

    def _make_mock_model(self) -> MagicMock:
        """Return a minimal mock nn.Module."""
        mock_model = MagicMock()
        mock_model.parameters.return_value = iter([MagicMock()])
        return mock_model

    def _make_mock_dataloader(self) -> MagicMock:
        """Return a minimal mock DataLoader."""
        mock_dl = MagicMock()
        mock_dl.dataset = MagicMock()
        mock_dl.batch_size = 32
        return mock_dl

    def test_wrap_returns_dp_optimizer(self) -> None:
        """wrap() must return the DP-wrapped optimizer from PrivacyEngine.make_private."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        mock_dp_optimizer = MagicMock()
        mock_engine_instance = MagicMock()
        mock_engine_instance.make_private.return_value = (
            MagicMock(),  # dp_model
            mock_dp_optimizer,
            MagicMock(),  # dp_dataloader
        )

        wrapper = DPTrainingWrapper()
        with patch(
            "synth_engine.modules.privacy.dp_engine.PrivacyEngine",
            return_value=mock_engine_instance,
        ):
            result = wrapper.wrap(
                optimizer=self._make_mock_optimizer(),
                model=self._make_mock_model(),
                dataloader=self._make_mock_dataloader(),
                max_grad_norm=1.0,
                noise_multiplier=1.1,
            )

        assert result is mock_dp_optimizer

    def test_wrap_calls_make_private_with_correct_params(self) -> None:
        """wrap() must call PrivacyEngine.make_private() with correct parameters."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        mock_engine_instance = MagicMock()
        mock_dp_optimizer = MagicMock()
        mock_engine_instance.make_private.return_value = (
            MagicMock(),
            mock_dp_optimizer,
            MagicMock(),
        )

        optimizer = self._make_mock_optimizer()
        model = self._make_mock_model()
        dataloader = self._make_mock_dataloader()

        wrapper = DPTrainingWrapper()
        with patch(
            "synth_engine.modules.privacy.dp_engine.PrivacyEngine",
            return_value=mock_engine_instance,
        ):
            wrapper.wrap(
                optimizer=optimizer,
                model=model,
                dataloader=dataloader,
                max_grad_norm=1.2,
                noise_multiplier=0.8,
            )

        mock_engine_instance.make_private.assert_called_once_with(
            module=model,
            optimizer=optimizer,
            data_loader=dataloader,
            max_grad_norm=1.2,
            noise_multiplier=0.8,
        )

    def test_wrap_stores_privacy_engine(self) -> None:
        """wrap() must store the PrivacyEngine instance for later epsilon queries."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        mock_engine_instance = MagicMock()
        mock_engine_instance.make_private.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )

        wrapper = DPTrainingWrapper()
        with patch(
            "synth_engine.modules.privacy.dp_engine.PrivacyEngine",
            return_value=mock_engine_instance,
        ) as mock_privacy_engine_cls:
            wrapper.wrap(
                optimizer=self._make_mock_optimizer(),
                model=self._make_mock_model(),
                dataloader=self._make_mock_dataloader(),
                max_grad_norm=1.0,
                noise_multiplier=1.1,
            )

        # PrivacyEngine was constructed once
        mock_privacy_engine_cls.assert_called_once()

    def test_wrap_second_call_raises_runtime_error(self) -> None:
        """wrap() called twice on the same wrapper must raise RuntimeError.

        A DPTrainingWrapper is single-use — wrapping twice would create a
        second PrivacyEngine and corrupt epsilon tracking.
        """
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        mock_engine_instance = MagicMock()
        mock_engine_instance.make_private.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )

        wrapper = DPTrainingWrapper()
        with patch(
            "synth_engine.modules.privacy.dp_engine.PrivacyEngine",
            return_value=mock_engine_instance,
        ):
            wrapper.wrap(
                optimizer=self._make_mock_optimizer(),
                model=self._make_mock_model(),
                dataloader=self._make_mock_dataloader(),
                max_grad_norm=1.0,
                noise_multiplier=1.1,
            )
            # Second call must raise
            with pytest.raises(RuntimeError, match="already wrapped"):
                wrapper.wrap(
                    optimizer=self._make_mock_optimizer(),
                    model=self._make_mock_model(),
                    dataloader=self._make_mock_dataloader(),
                    max_grad_norm=1.0,
                    noise_multiplier=1.1,
                )

    # --- ADV-046: degenerate input guards for wrap() ---

    def test_wrap_raises_for_max_grad_norm_zero(self) -> None:
        """wrap() must raise ValueError when max_grad_norm is zero.

        A zero gradient norm bound would clip all gradients to zero, producing
        no useful training signal and silently corrupting the DP guarantee.
        """
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper()
        with pytest.raises(ValueError, match="max_grad_norm must be positive"):
            wrapper.wrap(
                optimizer=self._make_mock_optimizer(),
                model=self._make_mock_model(),
                dataloader=self._make_mock_dataloader(),
                max_grad_norm=0.0,
                noise_multiplier=1.1,
            )

    def test_wrap_raises_for_max_grad_norm_negative(self) -> None:
        """wrap() must raise ValueError when max_grad_norm is negative."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper()
        with pytest.raises(ValueError, match="max_grad_norm must be positive"):
            wrapper.wrap(
                optimizer=self._make_mock_optimizer(),
                model=self._make_mock_model(),
                dataloader=self._make_mock_dataloader(),
                max_grad_norm=-1.0,
                noise_multiplier=1.1,
            )

    def test_wrap_raises_for_noise_multiplier_zero(self) -> None:
        """wrap() must raise ValueError when noise_multiplier is zero.

        A zero noise multiplier adds no Gaussian noise, providing no privacy
        protection while appearing to perform DP training.
        """
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper()
        with pytest.raises(ValueError, match="noise_multiplier must be positive"):
            wrapper.wrap(
                optimizer=self._make_mock_optimizer(),
                model=self._make_mock_model(),
                dataloader=self._make_mock_dataloader(),
                max_grad_norm=1.0,
                noise_multiplier=0.0,
            )

    def test_wrap_raises_for_noise_multiplier_negative(self) -> None:
        """wrap() must raise ValueError when noise_multiplier is negative."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper()
        with pytest.raises(ValueError, match="noise_multiplier must be positive"):
            wrapper.wrap(
                optimizer=self._make_mock_optimizer(),
                model=self._make_mock_model(),
                dataloader=self._make_mock_dataloader(),
                max_grad_norm=1.0,
                noise_multiplier=-0.5,
            )


class TestDPTrainingWrapperEpsilonSpent:
    """Unit tests for DPTrainingWrapper.epsilon_spent()."""

    def test_epsilon_spent_delegates_to_opacus_engine(self) -> None:
        """epsilon_spent() must call PrivacyEngine.get_epsilon() and return its value."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        mock_engine_instance = MagicMock()
        mock_engine_instance.make_private.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        # get_epsilon returns 0.73 for any delta
        mock_engine_instance.get_epsilon.return_value = 0.73

        wrapper = DPTrainingWrapper()
        with patch(
            "synth_engine.modules.privacy.dp_engine.PrivacyEngine",
            return_value=mock_engine_instance,
        ):
            wrapper.wrap(
                optimizer=MagicMock(),
                model=MagicMock(),
                dataloader=MagicMock(),
                max_grad_norm=1.0,
                noise_multiplier=1.1,
            )

        result = wrapper.epsilon_spent(delta=1e-5)

        assert result == 0.73
        mock_engine_instance.get_epsilon.assert_called_once_with(delta=1e-5)

    def test_epsilon_spent_returns_float(self) -> None:
        """epsilon_spent() must return a float value."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        mock_engine_instance = MagicMock()
        mock_engine_instance.make_private.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        mock_engine_instance.get_epsilon.return_value = 0.5

        wrapper = DPTrainingWrapper()
        with patch(
            "synth_engine.modules.privacy.dp_engine.PrivacyEngine",
            return_value=mock_engine_instance,
        ):
            wrapper.wrap(
                optimizer=MagicMock(),
                model=MagicMock(),
                dataloader=MagicMock(),
                max_grad_norm=1.0,
                noise_multiplier=1.1,
            )

        result = wrapper.epsilon_spent(delta=1e-5)
        assert isinstance(result, float)

    def test_epsilon_spent_returns_strict_python_float_not_numpy_float64(self) -> None:
        """epsilon_spent() must return a strict Python float, not np.float64.

        Opacus PrivacyEngine.get_epsilon() returns np.float64.  psycopg2 cannot
        serialize np.float64 as a PostgreSQL NUMERIC/FLOAT column — it emits
        'schema "np" does not exist'.  This test guards against regression by
        verifying type(result) is float, not just isinstance(result, float) which
        np.float64 passes due to numpy's float subclassing.

        F6 regression guard.
        """
        import numpy as np

        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        mock_engine_instance = MagicMock()
        mock_engine_instance.make_private.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        mock_engine_instance.get_epsilon.return_value = np.float64(3.14159)

        wrapper = DPTrainingWrapper()
        with patch(
            "synth_engine.modules.privacy.dp_engine.PrivacyEngine",
            return_value=mock_engine_instance,
        ):
            wrapper.wrap(
                optimizer=MagicMock(),
                model=MagicMock(),
                dataloader=MagicMock(),
                max_grad_norm=1.0,
                noise_multiplier=1.1,
            )

        result = wrapper.epsilon_spent(delta=1e-5)
        assert type(result) is float, (
            f"epsilon_spent() must return strict Python float, got {type(result).__name__}. "
            "np.float64 breaks psycopg2 serialization (F6)."
        )


class TestDPTrainingWrapperCheckBudget:
    """Unit tests for DPTrainingWrapper.check_budget().

    Core acceptance criteria tests:
    - check_budget raises BudgetExhaustionError when epsilon_spent >= allocated
    - check_budget does NOT raise when epsilon_spent < allocated
    """

    def _make_wrapper_with_epsilon(self, epsilon_value: float) -> object:
        """Build a DPTrainingWrapper whose epsilon_spent() returns epsilon_value."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        mock_engine_instance = MagicMock()
        mock_engine_instance.make_private.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        mock_engine_instance.get_epsilon.return_value = epsilon_value

        wrapper = DPTrainingWrapper()
        with patch(
            "synth_engine.modules.privacy.dp_engine.PrivacyEngine",
            return_value=mock_engine_instance,
        ):
            wrapper.wrap(
                optimizer=MagicMock(),
                model=MagicMock(),
                dataloader=MagicMock(),
                max_grad_norm=1.0,
                noise_multiplier=1.1,
            )
        return wrapper

    def test_check_budget_raises_when_epsilon_exceeds_allocated(self) -> None:
        """check_budget(allocated=1.0) must raise BudgetExhaustionError when epsilon_spent==1.1."""
        from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError

        wrapper = self._make_wrapper_with_epsilon(1.1)

        with pytest.raises(BudgetExhaustionError):
            wrapper.check_budget(allocated_epsilon=1.0, delta=1e-5)  # type: ignore[union-attr]

    def test_check_budget_raises_when_epsilon_equals_allocated(self) -> None:
        """check_budget must raise BudgetExhaustionError when epsilon_spent==allocated.

        Boundary condition: epsilon_spent exactly equals allocated_epsilon.
        """
        from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError

        wrapper = self._make_wrapper_with_epsilon(1.0)

        with pytest.raises(BudgetExhaustionError):
            wrapper.check_budget(allocated_epsilon=1.0, delta=1e-5)  # type: ignore[union-attr]

    def test_check_budget_does_not_raise_when_under_budget(self) -> None:
        """check_budget(allocated=1.0) must NOT raise when epsilon_spent==0.8."""
        wrapper = self._make_wrapper_with_epsilon(0.8)

        # Must not raise — returns None
        result = wrapper.check_budget(allocated_epsilon=1.0, delta=1e-5)  # type: ignore[union-attr]
        assert result is None

    def test_check_budget_returns_none_on_success(self) -> None:
        """check_budget must return None when budget is not exhausted."""
        wrapper = self._make_wrapper_with_epsilon(0.5)

        result = wrapper.check_budget(allocated_epsilon=1.0, delta=1e-5)  # type: ignore[union-attr]
        assert result is None

    def test_check_budget_error_message_contains_epsilon_values(self) -> None:
        """BudgetExhaustionError message must contain both spent and allocated epsilon."""
        from synth_engine.modules.privacy.dp_engine import BudgetExhaustionError

        wrapper = self._make_wrapper_with_epsilon(1.1)

        with pytest.raises(BudgetExhaustionError, match=r"1\.1.*1\.0"):
            wrapper.check_budget(allocated_epsilon=1.0, delta=1e-5)  # type: ignore[union-attr]

    def test_check_budget_before_wrap_raises_runtime_error(self) -> None:
        """check_budget on an unwrapped wrapper must raise RuntimeError (no engine yet)."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper()

        with pytest.raises(RuntimeError, match="not wrapped"):
            wrapper.check_budget(allocated_epsilon=1.0, delta=1e-5)

    # --- ADV-046: degenerate input guards for check_budget() ---

    def test_check_budget_raises_for_allocated_epsilon_zero(self) -> None:
        """check_budget() must raise ValueError when allocated_epsilon is zero.

        An allocation of zero would immediately exhaust the budget on any
        training step — this is a degenerate configuration, not a valid
        privacy budget.
        """
        wrapper = self._make_wrapper_with_epsilon(0.5)

        with pytest.raises(ValueError, match="allocated_epsilon must be positive"):
            wrapper.check_budget(allocated_epsilon=0.0, delta=1e-5)  # type: ignore[union-attr]

    def test_check_budget_raises_for_allocated_epsilon_negative(self) -> None:
        """check_budget() must raise ValueError when allocated_epsilon is negative."""
        wrapper = self._make_wrapper_with_epsilon(0.5)

        with pytest.raises(ValueError, match="allocated_epsilon must be positive"):
            wrapper.check_budget(allocated_epsilon=-1.0, delta=1e-5)  # type: ignore[union-attr]

    def test_check_budget_raises_for_delta_zero(self) -> None:
        """check_budget() must raise ValueError when delta is zero.

        Delta=0 would request a pure epsilon-DP guarantee that Opacus's RDP
        accountant cannot compute, resulting in a division-by-zero or infinite
        epsilon rather than a controlled error.
        """
        wrapper = self._make_wrapper_with_epsilon(0.5)

        with pytest.raises(ValueError, match="delta must be positive"):
            wrapper.check_budget(allocated_epsilon=1.0, delta=0.0)  # type: ignore[union-attr]

    def test_check_budget_raises_for_delta_negative(self) -> None:
        """check_budget() must raise ValueError when delta is negative."""
        wrapper = self._make_wrapper_with_epsilon(0.5)

        with pytest.raises(ValueError, match="delta must be positive"):
            wrapper.check_budget(allocated_epsilon=1.0, delta=-1e-5)  # type: ignore[union-attr]


class TestPrivacyModuleExports:
    """Verify that BudgetExhaustionError and DPTrainingWrapper are exported from the module."""

    def test_budget_exhaustion_error_importable_from_privacy_init(self) -> None:
        """BudgetExhaustionError must be importable from synth_engine.modules.privacy."""
        from synth_engine.modules.privacy import BudgetExhaustionError

        assert BudgetExhaustionError is not None

    def test_dp_training_wrapper_importable_from_privacy_init(self) -> None:
        """DPTrainingWrapper must be importable from synth_engine.modules.privacy."""
        from synth_engine.modules.privacy import DPTrainingWrapper

        assert DPTrainingWrapper is not None
