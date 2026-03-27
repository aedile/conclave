"""Dummy ML Synthesizer — a test fixture that mirrors the SynthesisEngine API.

This module provides :class:`DummyMLSynthesizer`, a lightweight stand-in for
:class:`~synth_engine.modules.synthesizer.training.engine.SynthesisEngine` that:

- Implements the exact same ``train()`` / ``generate()`` interface.
- Returns a valid :class:`~synth_engine.modules.synthesizer.storage.models.ModelArtifact`
  immediately, without performing any real ML training.
- Generates deterministic random DataFrames (seeded NumPy RNG — no unseeded PRNG).
- Does NOT require PyTorch, SDV, or Opacus to be installed.

Usage in integration tests::

    from tests.fixtures.dummy_ml_synthesizer import DummyMLSynthesizer

    synthesizer = DummyMLSynthesizer(seed=42)
    artifact = synthesizer.train("customers", "/fake/customers.parquet")
    df = synthesizer.generate(artifact, n_rows=500)

Design rationale (P6-T6.1 Context & Constraints):
    Running real PyTorch models in CI/CD takes too long.  The dummy synthesizer
    exercises the same API, Storage, and Privacy Ledger pathways without the
    compute cost.  It is intentionally placed in ``tests/fixtures/`` — it is
    test infrastructure, not production code.

PRNG seeding note (CLAUDE.md Spike-to-Production Rule 2):
    All random number generation uses ``np.random.default_rng(seed)`` with an
    explicit seed.  Unseeded PRNG is forbidden in production code; for test
    fixtures the seed defaults to 0 and can be overridden by the caller.

CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: P6-T6.1 — E2E Generative Synthesis Subsystem Tests
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from synth_engine.modules.synthesizer.storage.artifact import ModelArtifact

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dummy model — duck-types CTGANSynthesizer's sample() method
# ---------------------------------------------------------------------------

#: Default schema used by the dummy synthesizer when no real Parquet is read.
_DEFAULT_COLUMNS: list[str] = ["id", "value", "category", "amount"]
_DEFAULT_DTYPES: dict[str, str] = {
    "id": "int64",
    "value": "float64",
    "category": "object",
    "amount": "float64",
}
_DEFAULT_NULLABLES: dict[str, bool] = {
    "id": False,
    "value": False,
    "category": False,
    "amount": True,
}


class _DummyModel:
    """Internal duck-typed model that implements CTGANSynthesizer's sample() API.

    Attributes:
        _column_names: Column names for the generated DataFrame.
        _rng: Seeded NumPy random number generator for reproducible output.
    """

    def __init__(self, column_names: list[str], seed: int) -> None:
        """Initialise the dummy model with column schema and RNG seed.

        Args:
            column_names: The columns to include in generated DataFrames.
            seed: Seed for the NumPy RNG to ensure reproducible output.
        """
        self._column_names = column_names
        self._rng = np.random.default_rng(seed)

    def sample(self, num_rows: int) -> pd.DataFrame:
        """Generate a DataFrame with ``num_rows`` rows of random data.

        Each column is filled with uniformly distributed floats in [0, 1).
        The exact values depend on the RNG seed passed at construction time.

        Args:
            num_rows: Number of rows to generate.  Must be positive.

        Returns:
            A :class:`pandas.DataFrame` with ``num_rows`` rows and one
            column per entry in ``_column_names``.
        """
        data = {col: self._rng.uniform(0.0, 1.0, size=num_rows) for col in self._column_names}
        return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# DummyMLSynthesizer — public API
# ---------------------------------------------------------------------------


class DummyMLSynthesizer:
    """Lightweight stand-in for SynthesisEngine — no real ML training required.

    Implements the same ``train()`` / ``generate()`` interface as
    :class:`~synth_engine.modules.synthesizer.training.engine.SynthesisEngine` so that
    integration and E2E tests can exercise the full job pipeline without
    incurring the cost of real CTGAN training.

    Attributes:
        _epochs: Stored for interface parity with SynthesisEngine (unused).
        _seed: Base seed for the internal NumPy RNG.

    Example::

        synthesizer = DummyMLSynthesizer(seed=42)
        artifact = synthesizer.train("customers", "/data/customers.parquet")
        df = synthesizer.generate(artifact, n_rows=500)
        assert len(df) == 500
    """

    def __init__(self, epochs: int = 1, seed: int = 0) -> None:
        """Initialise the dummy synthesizer.

        Args:
            epochs: Accepted for interface parity; not used.
            seed: Base seed for the internal NumPy RNG.  Defaults to 0.
                Override for reproducible but distinct test datasets.
        """
        self._epochs = epochs
        self._seed = seed

    def train(
        self,
        table_name: str,
        parquet_path: str,
        *,
        dp_wrapper: Any = None,
    ) -> ModelArtifact:
        """Return a ModelArtifact immediately without real ML training.

        Does NOT read from ``parquet_path`` (the file need not exist).
        Uses the default column schema defined in this module.  The
        ``dp_wrapper`` parameter is accepted for interface parity but ignored.

        Args:
            table_name: Stored verbatim in the returned artifact.
            parquet_path: Accepted for interface parity; not read.
            dp_wrapper: Accepted for interface parity; ignored.

        Returns:
            A :class:`ModelArtifact` whose ``model`` is a :class:`_DummyModel`
            that generates random DataFrames via ``sample(num_rows)``.
        """
        if dp_wrapper is not None:
            _logger.debug(
                "DummyMLSynthesizer: dp_wrapper provided for table '%s' — "
                "ignored (dummy synthesizer does not apply DP-SGD).",
                table_name,
            )

        _logger.info(
            "DummyMLSynthesizer: returning pre-built artifact for table '%s' "
            "(no real training performed).",
            table_name,
        )

        dummy_model = _DummyModel(
            column_names=_DEFAULT_COLUMNS,
            seed=self._seed,
        )

        return ModelArtifact(
            table_name=table_name,
            model=dummy_model,
            column_names=_DEFAULT_COLUMNS,
            column_dtypes=_DEFAULT_DTYPES,
            column_nullables=_DEFAULT_NULLABLES,
        )

    def generate(
        self,
        artifact: ModelArtifact,
        n_rows: int,
    ) -> pd.DataFrame:
        """Generate synthetic rows using the artifact's dummy model.

        Delegates to ``artifact.model.sample(num_rows=n_rows)``, mirroring
        the exact same delegation pattern used by SynthesisEngine.generate().

        Args:
            artifact: A :class:`ModelArtifact` from :meth:`train`.
            n_rows: Number of synthetic rows to generate.  Must be > 0.

        Returns:
            A :class:`pandas.DataFrame` with ``n_rows`` rows.

        Raises:
            ValueError: If ``n_rows`` is 0 or negative (interface parity with
                :meth:`~synth_engine.modules.synthesizer.training.engine.SynthesisEngine.generate`).
        """
        if n_rows <= 0:
            raise ValueError(
                f"n_rows must be a positive integer; got {n_rows}. Use at least 1 row."
            )

        _logger.info(
            "DummyMLSynthesizer: generating %d rows for table '%s'.",
            n_rows,
            artifact.table_name,
        )
        result: pd.DataFrame = artifact.model.sample(num_rows=n_rows)
        return result
