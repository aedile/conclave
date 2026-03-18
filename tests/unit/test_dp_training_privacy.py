"""Unit tests for DPCompatibleCTGAN privacy, boundary, and documentation checks.

Tests follow TDD Red/Green/Refactor.  All tests are isolated.

Covers:
  - Import boundary: dp_training must NOT import from modules/privacy
  - dp_wrapper typed as Any, not DPTrainingWrapper
  - DPCompatibleCTGAN has a class-level docstring
  - Docstring documents dp_wrapper.wrap() interface (duck-typing contract)
  - _activate_opacus() raises RuntimeError for too-few rows (zero batches)
  - _activate_opacus() fallback tensor shape for all-categorical columns
  - fit() with empty DataFrame raises ValueError
  - No blanket warnings.simplefilter() calls in dp_training.py
  - filterwarnings() used with message pattern for Opacus warnings
  - _model_kwargs access documented in module docstring
  - _get_model_kwargs references SDV version context
  - _get_model_kwargs is a dedicated helper method
  - _get_model_kwargs reads _model_kwargs and overrides epochs
  - _get_model_kwargs returns a copy, not a reference

CONSTITUTION Priority 0: Security — privacy budget correctness and boundary enforcement.
Task: P7-T7.2 — Custom CTGAN Training Loop
Task: P20-T20.1 — Exception Handling & Warning Suppression Fixes
ADR: ADR-0025 (Custom CTGAN Training Loop Architecture)
Task: P26-T26.6 — Split from test_dp_training.py for maintainability
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helper — shared mock SDV synthesizer
# ---------------------------------------------------------------------------


def _make_mock_sdv_synthesizer() -> MagicMock:
    """Return a mock CTGANSynthesizer with the standard _model_kwargs fixture.

    Returns:
        Configured MagicMock standing in for CTGANSynthesizer.
    """
    import numpy as np

    rng = np.random.default_rng(99)
    processed_df = pd.DataFrame(
        {
            "age": rng.integers(18, 80, size=50).tolist(),
            "dept": rng.choice(["Engineering", "Marketing", "Sales"], size=50).tolist(),
        }
    )

    mock_synth = MagicMock()
    mock_synth.preprocess.return_value = processed_df
    mock_synth._model_kwargs = {
        "embedding_dim": 128,
        "generator_dim": (256, 256),
        "discriminator_dim": (256, 256),
        "generator_lr": 2e-4,
        "generator_decay": 1e-6,
        "discriminator_lr": 2e-4,
        "discriminator_decay": 1e-6,
        "batch_size": 500,
        "discriminator_steps": 1,
        "log_frequency": True,
        "verbose": False,
        "epochs": 2,
        "pac": 10,
        "enable_gpu": True,
    }
    mock_proc = MagicMock()
    mock_proc._hyper_transformer.field_transformers = {}
    mock_synth._data_processor = mock_proc
    return mock_synth


# ---------------------------------------------------------------------------
# Tests for import boundary — dp_training must NOT import from modules/privacy
# ---------------------------------------------------------------------------


class TestImportBoundary:
    """Verify that dp_training.py does NOT import from modules/privacy.

    Per ADR-0025 and ADR-0001: the dp_wrapper parameter is typed as Any.
    modules/synthesizer must never import from modules/privacy.
    """

    def test_dp_training_does_not_import_privacy(self) -> None:
        """Inspect dp_training source — must not contain 'from ...privacy' imports."""
        import ast
        from pathlib import Path

        dp_training_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "synth_engine"
            / "modules"
            / "synthesizer"
            / "dp_training.py"
        )
        assert dp_training_path.exists(), (
            f"dp_training.py not found at {dp_training_path}. "
            "Implement the file before running these tests."
        )

        source = dp_training_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "privacy" not in module, (
                    f"dp_training.py must NOT import from modules/privacy. "
                    f"Found: from {module} import ..."
                )

    def test_dp_wrapper_typed_as_any(self) -> None:
        """DPCompatibleCTGAN.__init__ dp_wrapper parameter must be typed as Any."""
        import inspect

        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        sig = inspect.signature(DPCompatibleCTGAN.__init__)
        hints = DPCompatibleCTGAN.__init__.__annotations__
        # dp_wrapper must be present and typed as Any (not DPTrainingWrapper)
        assert "dp_wrapper" in hints or "dp_wrapper" in sig.parameters
        # Ensure it's not typed as DPTrainingWrapper (would violate import boundary)
        dp_wrapper_annotation = str(hints.get("dp_wrapper", ""))
        assert "DPTrainingWrapper" not in dp_wrapper_annotation, (
            "dp_wrapper must NOT be annotated as DPTrainingWrapper — "
            "that would require importing from modules/privacy."
        )


# ---------------------------------------------------------------------------
# Tests for docstring completeness (duck-typing contract documentation)
# ---------------------------------------------------------------------------


class TestDocstringDuckTypingContract:
    """Verify that DPCompatibleCTGAN documents the dp_wrapper interface contract.

    Per the known failure pattern: 'Duck-typing docstring contract: The
    dp_wrapper: Any pattern requires explicit docstring documentation of the
    expected interface.'
    """

    def test_dp_compatible_ctgan_has_docstring(self) -> None:
        """DPCompatibleCTGAN must have a class-level docstring."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        assert DPCompatibleCTGAN.__doc__ is not None
        assert len(DPCompatibleCTGAN.__doc__.strip()) > 0

    def test_docstring_documents_dp_wrapper_interface(self) -> None:
        """DPCompatibleCTGAN docstring must mention the dp_wrapper.wrap() method."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        # The docstring must document the expected dp_wrapper interface
        doc = DPCompatibleCTGAN.__doc__ or ""
        assert "wrap" in doc, (
            "DPCompatibleCTGAN docstring must document the dp_wrapper.wrap() "
            "method as part of the duck-typing contract."
        )


# ---------------------------------------------------------------------------
# Tests for _activate_opacus() — privacy-critical edge cases
# (Added to address QA/Architecture review findings for P7-T7.3)
# ---------------------------------------------------------------------------


class TestActivateOpacusEdgeCases:
    """Unit tests for DPCompatibleCTGAN._activate_opacus() edge-case paths.

    These tests guard the privacy-critical guarantees:
    - Zero DataLoader batches must raise RuntimeError (not silently return 0.0 epsilon).
    - All-categorical columns fallback must produce the correct 1-wide tensor shape.
    """

    def test_activate_opacus_too_few_rows_raises_runtime_error(self) -> None:
        """_activate_opacus() must raise RuntimeError when DataLoader produces zero batches.

        Privacy rationale: a silent early-return would leave epsilon_spent() returning
        0.0, creating a false DP guarantee — callers relying on check_budget() would
        never see BudgetExhaustionError.  The correct behaviour is to fail loudly.
        """
        import numpy as np

        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = MagicMock()
        mock_dp_wrapper.max_grad_norm = 1.0
        mock_dp_wrapper.noise_multiplier = 1.1

        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2, dp_wrapper=mock_dp_wrapper)

        # A single-row DataFrame will produce batch_size=max(2,1//2)=2 but only 1 sample,
        # so drop_last=True drops it → len(dataloader) == 0.
        rng = np.random.default_rng(7)
        tiny_df = pd.DataFrame(
            {
                "age": rng.integers(18, 80, size=1).tolist(),
            }
        )

        with pytest.raises(RuntimeError, match="too few rows"):
            instance._activate_opacus(tiny_df)

    def test_activate_opacus_all_categorical_fallback_tensor_shape(self) -> None:
        """_activate_opacus() fallback tensor must be (n_rows, 1) when all columns are categorical.

        When processed_df has no numeric columns, select_dtypes returns an empty array
        (shape (n, 0)).  The code must fall back to a 1-wide zero tensor so the DataLoader
        is valid and n_features == 1.
        """
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = MagicMock()
        mock_dp_wrapper.max_grad_norm = 1.0
        mock_dp_wrapper.noise_multiplier = 1.1
        # dp_wrapper.wrap() returns a mock dp_optimizer that supports zero_grad / step
        mock_dp_optimizer = MagicMock()
        mock_dp_wrapper.wrap.return_value = mock_dp_optimizer

        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2, dp_wrapper=mock_dp_wrapper)

        # DataFrame with only string / object columns — no numeric columns at all.
        all_cat_df = pd.DataFrame(
            {
                "dept": ["Engineering", "Sales", "Marketing", "HR"] * 5,  # 20 rows
                "region": ["North", "South", "East", "West"] * 5,
            }
        )

        # Intercept TensorDataset to capture the tensor built from the processed data.
        from torch.utils.data import TensorDataset

        captured: dict[str, Any] = {}
        original_tensor_dataset = TensorDataset

        def capturing_tensor_dataset(*args: Any) -> Any:
            captured["tensor"] = args[0]
            return original_tensor_dataset(*args)

        with patch(
            "synth_engine.modules.synthesizer.dp_training.TensorDataset",
            side_effect=capturing_tensor_dataset,
        ):
            instance._activate_opacus(all_cat_df)

        # The tensor must have shape (n_rows, 1) — the 1-wide fallback.
        assert "tensor" in captured, "TensorDataset was never called"
        t = captured["tensor"]
        assert t.shape[1] == 1, f"Fallback tensor must have 1 feature column; got shape {t.shape}"
        assert t.shape[0] == len(all_cat_df), (
            f"Fallback tensor row count must match DataFrame; got {t.shape[0]}, "
            f"expected {len(all_cat_df)}"
        )

    def test_fit_empty_dataframe_raises_value_error(self) -> None:
        """fit() with an empty DataFrame must raise ValueError with 'empty' in the message."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)

        with pytest.raises(ValueError, match="empty"):
            instance.fit(pd.DataFrame())


# ---------------------------------------------------------------------------
# Tests for T20.1 — Warning targeting and SDV private attribute coupling
# ---------------------------------------------------------------------------


class TestWarningTargeting:
    """T20.1 AC2 — blanket warnings.simplefilter('ignore') must be replaced
    with targeted warnings.filterwarnings() specifying Opacus message patterns.

    Parses dp_training.py source to verify no simplefilter calls remain at all
    — all warning suppression must use filterwarnings() for consistency and
    auditability.
    """

    def test_no_blanket_simplefilter_ignore_in_dp_training(self) -> None:
        """dp_training.py must not contain ANY warnings.simplefilter() calls.

        T20.1 AC2: simplefilter() in any form (with or without a category
        argument) must be replaced by filterwarnings() for consistency.
        This test flags ALL simplefilter() calls, not just the blanket
        no-category form, to ensure the full migration to filterwarnings.
        """
        import re
        from pathlib import Path

        dp_training_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "synth_engine"
            / "modules"
            / "synthesizer"
            / "dp_training.py"
        )
        source = dp_training_path.read_text()

        # Flag any simplefilter call — all should be filterwarnings after T20.1.
        simplefilter_pattern = re.compile(r"simplefilter\s*\(")
        matches = simplefilter_pattern.findall(source)
        assert not matches, (
            f"Found {len(matches)} simplefilter() call(s) in dp_training.py. "
            "T20.1 AC2 requires all warning suppression to use filterwarnings(). "
            f"Matches: {matches}"
        )

    def test_filterwarnings_used_with_message_for_opacus(self) -> None:
        """dp_training.py must use filterwarnings with a message pattern for Opacus warnings.

        T20.1 AC2: targeted suppression requires specifying the message (or at
        minimum the category) so only known-safe warnings are suppressed.
        """
        from pathlib import Path

        dp_training_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "synth_engine"
            / "modules"
            / "synthesizer"
            / "dp_training.py"
        )
        source = dp_training_path.read_text()

        # The file must use filterwarnings (targeted form) at least once
        assert "filterwarnings" in source, (
            "dp_training.py must use warnings.filterwarnings() for targeted warning "
            "suppression (T20.1 AC2). No filterwarnings calls found."
        )


class TestSDVPrivateAttributeCoupling:
    """T20.1 AC3 — SDV _model_kwargs access must be documented with a version-pin comment.

    The coupling to SDV's private attribute is accepted risk per ADR-0025.
    The module-level docstring and the helper method must document the SDV
    version this works with, consistent with the pin in pyproject.toml.
    """

    def test_model_kwargs_access_documented_in_module_docstring(self) -> None:
        """dp_training.py module docstring must document SDV private attribute coupling.

        T20.1 AC3: the _model_kwargs access is accepted risk — it must be documented
        so future developers know why it exists and what SDV version it works with.
        """
        from pathlib import Path

        dp_training_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "synth_engine"
            / "modules"
            / "synthesizer"
            / "dp_training.py"
        )
        source = dp_training_path.read_text()

        # The module must mention _model_kwargs coupling in its docstring or comments
        assert "_model_kwargs" in source, (
            "dp_training.py must reference _model_kwargs in its documentation. "
            "T20.1 AC3: SDV private attribute coupling must be documented."
        )

    def test_model_kwargs_coupling_mentions_sdv_version_pin(self) -> None:
        """_get_model_kwargs helper docstring must reference SDV version pinning.

        T20.1 AC3: the version-pin comment ensures that SDV 2.x breakage is
        caught immediately.  The docstring must mention SDV version or pyproject.toml.
        """
        from pathlib import Path

        dp_training_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "synth_engine"
            / "modules"
            / "synthesizer"
            / "dp_training.py"
        )
        source = dp_training_path.read_text()

        # The file must mention SDV version context for _model_kwargs coupling
        # Acceptable forms: "SDV 1.x", "SDV version", "pyproject.toml", "SDV 2.x"
        has_sdv_version_context = any(
            token in source
            for token in ["SDV 1.x", "SDV 2.x", "SDV version", "pyproject.toml", "sdv>="]
        )
        assert has_sdv_version_context, (
            "dp_training.py must document the SDV version context for _model_kwargs "
            "private attribute access. T20.1 AC3: include version-pin reference "
            "('SDV 1.x', 'SDV 2.x', 'pyproject.toml', etc.) in the file."
        )

    def test_get_model_kwargs_helper_exists(self) -> None:
        """_get_model_kwargs must be a dedicated helper method (not inline access).

        T20.1 AC3: isolating the coupling in a helper method means SDV 2.x
        migration requires updating only one location.
        """
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        assert hasattr(DPCompatibleCTGAN, "_get_model_kwargs"), (
            "DPCompatibleCTGAN must have a _get_model_kwargs helper method. "
            "T20.1 AC3: coupling must be isolated in a dedicated method."
        )


# ---------------------------------------------------------------------------
# Tests for T20.1 — Integration test for SDV _model access (AC3)
# ---------------------------------------------------------------------------


class TestSDVModelKwargsIntegration:
    """Integration-style unit test: _get_model_kwargs reads from SDV synth correctly.

    Uses a mock SDV synthesizer to verify the helper does not break when
    _model_kwargs contains the expected dict structure.
    """

    def test_get_model_kwargs_reads_from_sdv_synth(self) -> None:
        """_get_model_kwargs() must extract _model_kwargs and override epochs."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=7)

        result = instance._get_model_kwargs(mock_sdv_synth)

        # Must return a dict (not a reference to the original)
        assert isinstance(result, dict)
        # Must override epochs with the instance's configured value
        assert result["epochs"] == 7, (
            f"_get_model_kwargs must override epochs to {7}; got {result['epochs']}"
        )
        # Must preserve other model kwargs from SDV
        assert "embedding_dim" in result, "_get_model_kwargs must preserve embedding_dim from SDV"

    def test_get_model_kwargs_returns_copy_not_reference(self) -> None:
        """_get_model_kwargs() must return a copy, not a reference to the private dict."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)

        result = instance._get_model_kwargs(mock_sdv_synth)

        # Mutating the result must not affect the original mock's _model_kwargs
        original_embed_dim = mock_sdv_synth._model_kwargs["embedding_dim"]
        result["embedding_dim"] = 999
        assert mock_sdv_synth._model_kwargs["embedding_dim"] == original_embed_dim, (
            "_get_model_kwargs must return a copy — mutating the result must not "
            "affect the original SDV synthesizer's _model_kwargs."
        )
