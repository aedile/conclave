"""Shared test helper functions for synthesizer module unit tests.

These are plain callable functions (not pytest fixtures) shared across
``test_dp_training_init.py``, ``test_dp_training_sample.py``,
``test_dp_accounting.py``, ``test_synthesizer_tasks_lifecycle.py``,
``test_synthesizer_tasks_errors.py``, ``test_synthesizer_tasks_dp.py``,
``test_t20_2_caplog_assertions.py``, and ``test_job_steps.py``.  They are
extracted here to eliminate copy-paste duplication without elevating their
scope.

Usage::

    from tests.unit.helpers_synthesizer import make_training_df, make_mock_ctgan_model
    from tests.unit.helpers_synthesizer import _make_synthesis_job

All helpers use seeded NumPy RNGs — deterministic, no PII.

T49.3: extracted from test_dp_training_init.py and test_dp_training_sample.py.
T49.QA: _make_synthesis_job extracted from 6 test files (P49 QA review finding).
"""

from __future__ import annotations

from typing import Any
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


def _make_synthesis_job(**kwargs: Any) -> Any:
    """Create a SynthesisJob instance with default values overridden by kwargs.

    Canonical shared helper — extracted from 6 test files to eliminate
    copy-paste duplication (P49 QA review finding).  All DP-specific fields
    (``enable_dp``, ``noise_multiplier``, ``max_grad_norm``, ``actual_epsilon``)
    inherit the model's own defaults unless overridden via ``kwargs``.

    Args:
        **kwargs: Field overrides applied on top of the defaults below.

    Returns:
        A SynthesisJob instance suitable for unit-test isolation.
    """
    from synth_engine.modules.synthesizer.job_models import SynthesisJob

    defaults: dict[str, Any] = {
        "id": 1,
        "status": "QUEUED",
        "current_epoch": 0,
        "total_epochs": 10,
        "num_rows": 100,
        "artifact_path": None,
        "output_path": None,
        "error_msg": None,
        "table_name": "persons",
        "parquet_path": "/data/persons.parquet",
        "checkpoint_every_n": 5,
    }
    defaults.update(kwargs)
    return SynthesisJob(**defaults)
