"""Shared test helper functions for synthesizer module unit tests.

These are plain callable functions (not pytest fixtures) shared across
``test_dp_training_init.py``, ``test_dp_training_sample.py``, and
``test_dp_training_loop.py``.  They are extracted here to eliminate
copy-paste duplication without elevating their scope.

Usage::

    from tests.unit.helpers_synthesizer import make_training_df, make_mock_ctgan_model

All helpers use seeded NumPy RNGs — deterministic, no PII.

T49.3: extracted from test_dp_training_init.py and test_dp_training_sample.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd


def make_training_df(n: int = 50) -> pd.DataFrame:
    """Return a simple fictional training DataFrame for test fixtures.

    Uses a seeded NumPy RNG — deterministic, no PII.

    Args:
        n: Number of rows.  Defaults to 50.

    Returns:
        DataFrame with columns: id (int), age (int), dept (str).
    """
    import numpy as np

    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "id": range(1, n + 1),
            "age": rng.integers(18, 80, size=n).tolist(),
            "dept": rng.choice(["Engineering", "Marketing", "Sales"], size=n).tolist(),
        }
    )


def make_mock_ctgan_model(n_rows: int = 50) -> MagicMock:
    """Return a mock CTGAN model that produces a known sample DataFrame.

    Boundary mock: replaces the external ctgan library at its interface boundary.
    Used in wiring tests that verify DPCompatibleCTGAN's orchestration of CTGAN,
    not CTGAN's own correctness.

    Args:
        n_rows: Number of rows CTGAN.sample() will return.  Defaults to 50.

    Returns:
        MagicMock standing in for ctgan.synthesizers.ctgan.CTGAN.
    """
    mock_ctgan = MagicMock()
    mock_ctgan.sample.return_value = pd.DataFrame(
        {
            "age": list(range(n_rows)),
            "dept": ["Engineering"] * n_rows,
        }
    )
    return mock_ctgan
