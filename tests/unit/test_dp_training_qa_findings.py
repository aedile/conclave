"""Unit tests for P30 QA review findings — three targeted fixes.

RED phase tests written before implementation fixes (TDD).

Covers:
  Finding 1: Empty DataLoader guard in _train_dp_discriminator()
    - _train_dp_discriminator raises RuntimeError when DataLoader has 0 batches
      (e.g. n_rows=1, pac=10 → batch_size capped below pac → 0 batches)

  Finding 2: Docstring/log accuracy — WGAN (not WGAN-GP) in DP mode
    - All docstrings and log messages must say "WGAN" for the DP training loop
    - No "WGAN-GP" claim in the DP training loop body, training docstring, or log

  Finding 3: _sample_from_dp_generator branch coverage
    - Branch 1: column-count mismatch → returns integer-indexed columns early
    - Branch 2: ref_df is None → returns synthetic_numeric early
    - Branch 3: non_numeric_cols empty → returns synthetic_numeric early
    - Branch 4: full non-numeric join path

  Dead imports: DataTransformer and DataSampler must be removed from dp_training.py

CONSTITUTION Priority 3: TDD Red/Green/Refactor.
Task: P30 QA Review Finding Fix
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.unit

_DP_TRAINING_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "synth_engine"
    / "modules"
    / "synthesizer"
    / "dp_training.py"
)


# ---------------------------------------------------------------------------
# Finding 1: Empty DataLoader guard in _train_dp_discriminator()
# ---------------------------------------------------------------------------


class TestEmptyDataLoaderGuard:
    """Finding 1 — _train_dp_discriminator must raise RuntimeError on 0-batch DataLoader.

    Privacy rationale: with a 0-batch DataLoader the epoch loop runs silently
    with no gradient steps.  check_budget() is then called on a budget with 0
    accounting, producing a false DP guarantee (epsilon_spent() returns 0.0).
    The guard must fail loudly instead.
    """

    def test_empty_dataloader_raises_runtime_error(self) -> None:
        """_train_dp_discriminator raises RuntimeError when DataLoader has 0 batches.

        Simulates the n_rows=1, pac=10 case: batch_size is capped to
        n_rows // 2 = 0, then floored to pac = 10, but drop_last=True means
        a single-row dataset produces 0 batches.
        """
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = MagicMock()
        mock_dp_wrapper.max_grad_norm = 1.0
        mock_dp_wrapper.noise_multiplier = 1.1

        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper)

        # 1-row DataFrame — after batch_size adjustment and drop_last=True, 0 batches.
        tiny_df = pd.DataFrame({"age": [25.0]})

        model_kwargs: dict[str, Any] = {
            "embedding_dim": 8,
            "generator_dim": (16,),
            "discriminator_dim": (16,),
            "generator_lr": 2e-4,
            "generator_decay": 1e-6,
            "discriminator_lr": 2e-4,
            "discriminator_decay": 1e-6,
            "batch_size": 500,
            "discriminator_steps": 1,
            "pac": 10,
        }

        with pytest.raises(RuntimeError, match="zero batches"):
            instance._train_dp_discriminator(tiny_df, model_kwargs)

    def test_empty_dataloader_guard_message_mentions_privacy(self) -> None:
        """RuntimeError message must explain the privacy rationale for the guard."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = MagicMock()
        mock_dp_wrapper.max_grad_norm = 1.0
        mock_dp_wrapper.noise_multiplier = 1.1

        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper)

        tiny_df = pd.DataFrame({"age": [25.0]})
        model_kwargs: dict[str, Any] = {
            "embedding_dim": 8,
            "generator_dim": (16,),
            "discriminator_dim": (16,),
            "batch_size": 500,
            "pac": 10,
        }

        with pytest.raises(RuntimeError) as exc_info:
            instance._train_dp_discriminator(tiny_df, model_kwargs)

        error_text = str(exc_info.value).lower()
        assert any(
            token in error_text for token in ["dp", "dataloader", "batch", "rows", "privacy"]
        ), f"RuntimeError message should explain the DP/DataLoader issue: {exc_info.value}"


# ---------------------------------------------------------------------------
# Finding 2: Docstring and log accuracy — WGAN not WGAN-GP in DP mode
# ---------------------------------------------------------------------------


class TestWGANDocstringAccuracy:
    """Finding 2 — dp_training.py must not claim WGAN-GP in the DP training loop.

    The discriminator step uses plain WGAN loss: -(real.mean() - fake.mean()).
    calc_gradient_penalty() is NOT called in the Opacus DP training path
    (torch.autograd.grad() conflicts with Opacus per-sample gradient hooks).

    All references in the DP loop, its docstring, module docstring, and log
    messages must say 'WGAN' — not 'WGAN-GP'.
    """

    def _read_source(self) -> str:
        """Read the dp_training.py source file.

        Returns:
            Source text of dp_training.py.
        """
        return _DP_TRAINING_PATH.read_text()

    def test_train_dp_discriminator_docstring_says_wgan_not_wgan_gp(self) -> None:
        """_train_dp_discriminator docstring must not claim WGAN-GP for the DP loop."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        doc = DPCompatibleCTGAN._train_dp_discriminator.__doc__ or ""
        # The docstring must not claim WGAN-GP (gradient penalty is not applied in DP mode)
        assert "WGAN-GP" not in doc, (
            "_train_dp_discriminator docstring claims 'WGAN-GP' but the DP training "
            "loop does not call calc_gradient_penalty(). "
            "Correct the docstring to say 'WGAN' and explain why GP is omitted."
        )

    def test_no_wgan_gp_in_training_log_messages(self) -> None:
        """Log messages in the training loop must say WGAN, not WGAN-GP."""
        source = self._read_source()
        # Find all log message strings that contain "WGAN-GP"
        # The training loop log: 'starting custom WGAN-GP training loop' → must be 'WGAN'
        # Look specifically in the _train_dp_discriminator body (lines after its def)
        lines = source.splitlines()
        in_train_dp = False
        wgan_gp_log_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if "def _train_dp_discriminator" in stripped:
                in_train_dp = True
            elif (
                in_train_dp
                and stripped.startswith("def ")
                and "train_dp_discriminator" not in stripped
            ):
                in_train_dp = False
            if in_train_dp and "WGAN-GP" in line and "_logger" in line:
                wgan_gp_log_lines.append(line.strip())

        assert not wgan_gp_log_lines, (
            "Found WGAN-GP in training log message(s) — should say WGAN:\n"
            + "\n".join(wgan_gp_log_lines)
        )

    def test_module_docstring_dp_section_says_wgan_not_wgan_gp(self) -> None:
        """Module docstring's DP-path description must say 'WGAN' not 'WGAN-GP'."""
        source = self._read_source()
        # Extract the module docstring (between the first triple-quotes)
        # Check the 'simplified WGAN-GP training loop' claim specifically
        # The pattern is: 'runs a simplified WGAN-GP' should become 'WGAN'
        assert "simplified WGAN-GP" not in source, (
            "Module/class docstring still says 'simplified WGAN-GP training loop'. "
            "Correct to 'simplified WGAN training loop' since gradient penalty "
            "is not applied in DP mode (Opacus incompatibility)."
        )

    def test_dp_training_loop_does_not_call_calc_gradient_penalty(self) -> None:
        """The DP training loop body must not call calc_gradient_penalty().

        If WGAN-GP were actually implemented in DP mode, calc_gradient_penalty()
        would be called. Since it is NOT called, docstrings must not claim WGAN-GP.
        This test verifies the docstring/code consistency: code does NOT call it,
        therefore docs must NOT claim it.
        """
        source = self._read_source()
        lines = source.splitlines()
        in_train_dp = False
        penalty_calls: list[str] = []
        for line in lines:
            stripped = line.strip()
            if "def _train_dp_discriminator" in stripped:
                in_train_dp = True
            elif in_train_dp:
                # Detect method boundary by indentation
                if stripped.startswith("def ") and "train_dp_discriminator" not in stripped:
                    if not line.startswith("    "):
                        in_train_dp = False
                if in_train_dp and "calc_gradient_penalty" in line and not stripped.startswith("#"):
                    penalty_calls.append(line.strip())

        # There should be NO active (non-commented) calc_gradient_penalty calls
        # in _train_dp_discriminator since it's incompatible with Opacus hooks.
        assert not penalty_calls, (
            "calc_gradient_penalty() is called in _train_dp_discriminator but "
            "this is incompatible with Opacus per-sample gradient hooks.\n"
            f"Found: {penalty_calls}"
        )


# ---------------------------------------------------------------------------
# Finding 3: _sample_from_dp_generator branch coverage
# ---------------------------------------------------------------------------


class TestSampleFromDPGeneratorBranches:
    """Finding 3 — unit tests for all 4 branches of _sample_from_dp_generator().

    The method has 4 distinct return paths:
      1. column-count mismatch → integer-indexed columns, returns early
      2. ref_df is None → returns synthetic_numeric early
      3. non_numeric_cols empty → returns synthetic_numeric early
      4. full non-numeric join path

    These tests exercise each branch in isolation using a directly-configured
    DPCompatibleCTGAN instance (no fit() call required).
    """

    def _make_instance_with_dp_generator(
        self,
        numeric_cols: list[str],
        ref_df: pd.DataFrame | None,
        embedding_dim: int = 4,
        data_cols: int = 2,
    ) -> Any:
        """Build a DPCompatibleCTGAN instance pre-configured for _sample_from_dp_generator.

        Injects a mock Generator and populates the internal state attributes
        that _sample_from_dp_generator reads, without running fit().

        Args:
            numeric_cols: Value for _dp_numeric_columns.
            ref_df: Value for _dp_processed_df_sample (or None).
            embedding_dim: Noise embedding dimension.
            data_cols: Number of output columns from mock Generator.

        Returns:
            Configured DPCompatibleCTGAN instance.
        """
        import torch

        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1)
        instance._fitted = True
        instance._dp_trained = True
        instance._dp_embedding_dim = embedding_dim
        instance._dp_numeric_columns = numeric_cols
        instance._dp_processed_df_sample = ref_df

        # Mock generator: returns fixed-size tensor
        mock_gen = MagicMock()
        rng = np.random.default_rng(42)

        def fake_forward(noise: Any) -> Any:
            n = noise.shape[0] if hasattr(noise, "shape") else 5
            return torch.tensor(rng.standard_normal((n, data_cols)).astype("float32"))

        mock_gen.side_effect = fake_forward
        mock_gen.eval = MagicMock()
        instance._dp_generator = mock_gen
        return instance

    def test_branch_1_column_count_mismatch_returns_integer_indexed(self) -> None:
        """Branch 1: column-count mismatch → returns DataFrame with integer-indexed columns.

        When len(numeric_cols) != data_array.shape[1], the method falls back to
        integer column indices and returns early (before attempting the ref_df join).
        """
        # numeric_cols has 3 entries but generator outputs 2 columns → mismatch
        numeric_cols = ["col_a", "col_b", "col_c"]
        ref_df = pd.DataFrame({"col_a": [1.0, 2.0], "col_b": [3.0, 4.0], "col_c": [5.0, 6.0]})

        instance = self._make_instance_with_dp_generator(
            numeric_cols=numeric_cols,
            ref_df=ref_df,
            embedding_dim=4,
            data_cols=2,  # mismatch: 3 numeric_cols but 2 output columns
        )

        result = instance._sample_from_dp_generator(num_rows=3)

        assert isinstance(result, pd.DataFrame)
        # Columns must be integer-indexed (fallback path)
        assert list(result.columns) == ["0", "1"], (
            f"Column-count mismatch path must use integer column names; got {list(result.columns)}"
        )

    def test_branch_2_ref_df_none_returns_synthetic_numeric(self) -> None:
        """Branch 2: ref_df is None → returns synthetic_numeric DataFrame early.

        When _dp_processed_df_sample is None, there is no reference DataFrame
        for non-numeric column sampling — return synthetic_numeric directly.
        """
        numeric_cols = ["feat_a", "feat_b"]
        # ref_df is None → branch 2

        instance = self._make_instance_with_dp_generator(
            numeric_cols=numeric_cols,
            ref_df=None,
            embedding_dim=4,
            data_cols=2,  # matches numeric_cols length
        )

        result = instance._sample_from_dp_generator(num_rows=3)

        assert isinstance(result, pd.DataFrame)
        # Must have the numeric column names (not integer indices)
        assert "feat_a" in result.columns
        assert "feat_b" in result.columns

    def test_branch_3_no_non_numeric_cols_returns_synthetic_numeric(self) -> None:
        """Branch 3: all ref_df columns are numeric → returns synthetic_numeric early.

        When non_numeric_cols is empty (all columns in ref_df appear in numeric_cols),
        there is nothing to join — return synthetic_numeric directly.
        """
        numeric_cols = ["x", "y"]
        # ref_df has only numeric columns (both are in numeric_cols)
        ref_df = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [4.0, 5.0, 6.0]})

        instance = self._make_instance_with_dp_generator(
            numeric_cols=numeric_cols,
            ref_df=ref_df,
            embedding_dim=4,
            data_cols=2,
        )

        result = instance._sample_from_dp_generator(num_rows=3)

        assert isinstance(result, pd.DataFrame)
        assert "x" in result.columns
        assert "y" in result.columns
        # No extra non-numeric columns should appear
        assert set(result.columns) == {"x", "y"}

    def test_branch_4_non_numeric_join_path(self) -> None:
        """Branch 4: ref_df has non-numeric columns → joins them into the result.

        When non_numeric_cols is non-empty, the method samples from ref_df's
        non-numeric columns and concatenates them with the synthetic numeric data.
        The result columns are reordered to match ref_df's column order.
        """
        numeric_cols = ["age", "score"]
        # ref_df has both numeric and non-numeric columns
        ref_df = pd.DataFrame(
            {
                "age": [25.0, 30.0, 35.0, 40.0, 45.0],
                "dept": ["Eng", "Sales", "HR", "Eng", "Sales"],  # non-numeric
                "score": [0.5, 0.7, 0.3, 0.9, 0.1],
                "region": ["N", "S", "E", "W", "N"],  # non-numeric
            }
        )

        instance = self._make_instance_with_dp_generator(
            numeric_cols=numeric_cols,
            ref_df=ref_df,
            embedding_dim=4,
            data_cols=2,
        )

        result = instance._sample_from_dp_generator(num_rows=4)

        assert isinstance(result, pd.DataFrame)
        # Must include both numeric AND non-numeric columns
        assert "age" in result.columns
        assert "score" in result.columns
        assert "dept" in result.columns
        assert "region" in result.columns
        # Row count must match num_rows
        assert len(result) == 4

    def test_branch_4_result_columns_ordered_as_ref_df(self) -> None:
        """Branch 4: result column order must match ref_df column order."""
        numeric_cols = ["val1", "val2"]
        ref_df = pd.DataFrame(
            {
                "val1": [1.0, 2.0, 3.0],
                "label": ["a", "b", "c"],  # non-numeric
                "val2": [4.0, 5.0, 6.0],
            }
        )

        instance = self._make_instance_with_dp_generator(
            numeric_cols=numeric_cols,
            ref_df=ref_df,
            embedding_dim=4,
            data_cols=2,
        )

        result = instance._sample_from_dp_generator(num_rows=3)

        # Columns must appear in the same order as ref_df
        ref_order = [c for c in ref_df.columns if c in result.columns]
        result_order = list(result.columns)
        assert result_order == ref_order, (
            f"Column order must match ref_df. Expected {ref_order}, got {result_order}"
        )


# ---------------------------------------------------------------------------
# Finding 3 (continued): DataTransformer and DataSampler must be removed
# ---------------------------------------------------------------------------


class TestDeadImportsRemoved:
    """Finding 3 — DataTransformer and DataSampler must not be imported in dp_training.py.

    These were imported 'for unit-test patching' but no production path uses them
    and no test actually patches them. They are dead imports that add noise to the
    module-scope comment and the vulture whitelist.
    """

    def test_data_transformer_not_imported_in_dp_training(self) -> None:
        """dp_training.py must NOT import DataTransformer from ctgan."""
        import ast

        source = _DP_TRAINING_PATH.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [alias.name for alias in node.names]
                assert "DataTransformer" not in names, (
                    "DataTransformer is a dead import in dp_training.py — "
                    "no production code uses it and no test patches it. "
                    "Remove it and its vulture whitelist entry."
                )

    def test_data_sampler_not_imported_in_dp_training(self) -> None:
        """dp_training.py must NOT import DataSampler from ctgan."""
        import ast

        source = _DP_TRAINING_PATH.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [alias.name for alias in node.names]
                assert "DataSampler" not in names, (
                    "DataSampler is a dead import in dp_training.py — "
                    "no production code uses it and no test patches it. "
                    "Remove it and its vulture whitelist entry."
                )

    def test_data_transformer_not_in_vulture_whitelist(self) -> None:
        """DataTransformer must not have a whitelist entry once the import is removed."""
        whitelist_path = Path(__file__).parent.parent.parent / ".vulture_whitelist.py"
        source = whitelist_path.read_text()
        # After removing the dead import, the whitelist entry should also be gone
        assert "DataTransformer  # unused import" not in source, (
            "DataTransformer whitelist entry must be removed when the dead import is removed."
        )

    def test_data_sampler_not_in_vulture_whitelist(self) -> None:
        """DataSampler must not have a whitelist entry once the import is removed."""
        whitelist_path = Path(__file__).parent.parent.parent / ".vulture_whitelist.py"
        source = whitelist_path.read_text()
        assert "DataSampler  # unused import" not in source, (
            "DataSampler whitelist entry must be removed when the dead import is removed."
        )
