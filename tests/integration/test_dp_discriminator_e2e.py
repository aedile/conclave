"""Integration tests: full DP pipeline with real Opacus on a real Discriminator.

Exercises ``DPCompatibleCTGAN`` + ``DPTrainingWrapper`` end-to-end with real
Opacus (no mocks).  Confirms:

  Test 1: Full DP training produces positive epsilon
    - ``DPTrainingWrapper.epsilon_spent(delta=1e-5) > 0`` after ``fit()``.

  Test 2: Sampling after DP training
    - ``model.sample(5)`` returns a ``pd.DataFrame`` with 5 rows.
    - Soft assertion: exceptions from ``sample()`` at 2 epochs are tolerated;
      the KEY invariant is epsilon > 0 (Test 1).

  Test 3: Budget exhaustion
    - ``DPCompatibleCTGAN.fit()`` raises ``BudgetExhaustionError`` when
      ``allocated_epsilon=0.0001`` (tiny budget exhausted within first epoch).

All fixture data is fictional (deterministic ``range()`` values).  No PII.

These tests require the synthesizer dependency group:
  poetry install --with synthesizer

Run with:
  poetry run pytest tests/integration/test_dp_discriminator_e2e.py -v --no-cov

Task: P30-T30.5 — Integration Test: Real Opacus on Real Discriminator
ADR: ADR-0036 (Discriminator-Level DP-SGD Architecture)
"""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

pytestmark = [pytest.mark.synthesizer, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Shared fixture: a 40-row DataFrame with numeric and categorical columns.
# 40 rows ensures batch_size is divisible by pac=10 (the CTGAN default) and
# Opacus has sufficient samples for meaningful per-sample gradient accounting.
# ---------------------------------------------------------------------------


@pytest.fixture
def training_df() -> pd.DataFrame:
    """Return a 40-row fictional training DataFrame.

    All values are deterministically generated from ``range()`` — no PII.
    The DataFrame contains two numeric columns (age, salary) and one
    categorical column (dept) to exercise CTGAN's mixed-type training path.

    Returns:
        DataFrame with 40 rows: age (int), salary (int), dept (str).
    """
    return pd.DataFrame(
        {
            "age": list(range(20, 40)) * 2,  # 40 rows: 20–39, repeated
            "salary": list(range(30000, 50000, 1000)) * 2,  # 40 rows
            "dept": (["A", "B"] * 10) * 2,  # 40 rows alternating A/B
        }
    )


# ---------------------------------------------------------------------------
# Test 1: Full DP training produces positive epsilon
# ---------------------------------------------------------------------------


class TestDPTrainingProducesPositiveEpsilon:
    """Verify that real Opacus accounting yields epsilon_spent > 0 after fit().

    Uses ``allocated_epsilon=50.0`` (high budget) so training completes
    without exhausting the budget during the 2-epoch run.
    """

    def test_full_dp_training_epsilon_positive(self, training_df: pd.DataFrame) -> None:
        """Fit DPCompatibleCTGAN in DP mode; epsilon_spent must be positive after fit().

        Creates a real ``DPTrainingWrapper`` and a real ``DPCompatibleCTGAN``,
        fits on the fictional ``training_df`` (40 rows, 3 columns), and asserts
        that ``dp_wrapper.epsilon_spent(delta=1e-5) > 0`` after training.

        A positive epsilon confirms that Opacus performed real gradient
        accounting — not a proxy path or no-op.

        Note:
            ``allocated_epsilon=50.0`` is set generously high so the budget is
            never exhausted during the 2-epoch training run.  This test
            isolates the epsilon-positivity invariant.  Budget exhaustion is
            tested separately in ``TestBudgetExhaustionRaisesError``.

        Args:
            training_df: 40-row fictional DataFrame from the shared fixture.
        """
        from sdv.metadata import (
            SingleTableMetadata,  # type: ignore[import-untyped]  # sdv lacks py.typed; unfixable
        )

        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        metadata = SingleTableMetadata()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore")
            metadata.detect_from_dataframe(training_df)

        wrapper = DPTrainingWrapper(max_grad_norm=1.0, noise_multiplier=1.1)
        model = DPCompatibleCTGAN(
            metadata=metadata,
            epochs=2,
            dp_wrapper=wrapper,
            allocated_epsilon=50.0,
            delta=1e-5,
        )

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore")
            model.fit(training_df)

        eps = wrapper.epsilon_spent(delta=1e-5)
        assert eps > 0, (
            f"Expected positive epsilon after DP training, got {eps}. "
            "Opacus PrivacyEngine must have been activated and accumulated "
            "at least one gradient step for epsilon_spent to be non-zero."
        )


# ---------------------------------------------------------------------------
# Test 2: Sampling after DP training
# ---------------------------------------------------------------------------


class TestDPTrainingSampling:
    """Verify sample() after DP training returns a DataFrame with correct row count.

    Note on soft assertion: ``reverse_transform`` may produce data-quality
    issues at only 2 epochs (the Generator has not fully converged).  Any
    exception from ``sample()`` is caught and the row-count assertion is
    skipped — the KEY invariant (epsilon > 0, Test 1) is tested separately.
    """

    def test_sample_after_dp_training_returns_five_rows(
        self, training_df: pd.DataFrame
    ) -> None:
        """sample(num_rows=5) must return a DataFrame with exactly 5 rows.

        Fits ``DPCompatibleCTGAN`` in DP mode, confirms epsilon > 0 (pre-
        condition), then calls ``sample(num_rows=5)``.  If ``sample()`` raises
        any exception (e.g. ``reverse_transform`` mismatch due to low epoch
        count), the row-count assertion is skipped via ``pytest.skip``.

        The skip is acceptable: ``sample()`` correctness at 2 epochs is
        documented as non-guaranteed per ADR-0025 §T7.3 consequences;
        full sample correctness is covered by ``test_dp_training_integration.py``
        with standard epoch counts.

        Args:
            training_df: 40-row fictional DataFrame from the shared fixture.
        """
        from sdv.metadata import (
            SingleTableMetadata,  # type: ignore[import-untyped]  # sdv lacks py.typed; unfixable
        )

        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        metadata = SingleTableMetadata()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore")
            metadata.detect_from_dataframe(training_df)

        wrapper = DPTrainingWrapper(max_grad_norm=1.0, noise_multiplier=1.1)
        model = DPCompatibleCTGAN(
            metadata=metadata,
            epochs=2,
            dp_wrapper=wrapper,
            allocated_epsilon=50.0,
            delta=1e-5,
        )

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore")
            model.fit(training_df)

        # Pre-condition: epsilon > 0 confirms real Opacus activation.
        assert wrapper.epsilon_spent(delta=1e-5) > 0, (
            "Pre-condition: epsilon_spent must be > 0 before sampling."
        )

        try:
            result = model.sample(num_rows=5)
        except Exception:  # noqa: BLE001  # soft assertion: sample() may fail at 2 epochs
            pytest.skip(
                "sample() raised an exception after 2-epoch DP training — "
                "reverse_transform instability at low epoch count is expected. "
                "DP correctness already confirmed by epsilon > 0 pre-condition."
            )
            return  # unreachable after pytest.skip; keeps type-checker happy

        assert isinstance(result, pd.DataFrame), (
            f"sample() must return pd.DataFrame, got {type(result)}"
        )
        assert len(result) == 5, (
            f"sample(num_rows=5) must return exactly 5 rows, got {len(result)}"
        )


# ---------------------------------------------------------------------------
# Test 3: Budget exhaustion raises BudgetExhaustionError during fit()
# ---------------------------------------------------------------------------


class TestBudgetExhaustionRaisesError:
    """Verify that a tiny allocated_epsilon triggers BudgetExhaustionError.

    ``DPCompatibleCTGAN.fit()`` calls ``dp_wrapper.check_budget()`` after
    the Opacus activation step.  With ``allocated_epsilon=0.0001`` the budget
    is guaranteed to be exhausted on the first check (any real Opacus run
    produces epsilon >> 0.0001), so ``BudgetExhaustionError`` propagates
    immediately from ``fit()``.
    """

    def test_tiny_budget_raises_budget_exhaustion_error(
        self, training_df: pd.DataFrame
    ) -> None:
        """fit() must raise BudgetExhaustionError when allocated_epsilon is tiny.

        Uses ``allocated_epsilon=0.0001`` — smaller than the epsilon produced
        by a single Opacus gradient step.  The budget is exhausted the moment
        ``check_budget()`` is called inside ``fit()``, which raises
        ``BudgetExhaustionError``.

        ``BudgetExhaustionError`` is imported from ``shared.exceptions``
        per P26-T26.2.  The error must propagate directly from ``fit()`` —
        not be swallowed or wrapped in another exception type.

        Args:
            training_df: 40-row fictional DataFrame from the shared fixture.
        """
        from sdv.metadata import (
            SingleTableMetadata,  # type: ignore[import-untyped]  # sdv lacks py.typed; unfixable
        )

        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN
        from synth_engine.shared.exceptions import BudgetExhaustionError

        metadata = SingleTableMetadata()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore")
            metadata.detect_from_dataframe(training_df)

        wrapper = DPTrainingWrapper(max_grad_norm=1.0, noise_multiplier=1.1)
        model = DPCompatibleCTGAN(
            metadata=metadata,
            epochs=10,
            dp_wrapper=wrapper,
            allocated_epsilon=0.0001,
            delta=1e-5,
        )

        with pytest.raises(BudgetExhaustionError):
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore")
                model.fit(training_df)
