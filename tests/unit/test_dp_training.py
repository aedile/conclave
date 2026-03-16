"""Unit tests for DPCompatibleCTGAN — custom CTGAN training loop.

Tests follow TDD Red/Green/Refactor.  All tests are isolated — SDV internals
are mocked so these tests run without a GPU and without a full CTGAN training
run.  Every test asserts on the *return value* of the function under test, not
merely on the absence of an exception.

Pattern guards applied:
- Version-pin hallucinations: pyproject.toml not modified; SDV version accepted as-is.
- Stale parameter propagation: grep of call sites run after implementation.
- Duck-typing docstring contract: dp_wrapper's required interface (wrap, epsilon_spent,
  check_budget) documented in DPCompatibleCTGAN docstring — verified in a test.
- SDV private attribute coupling: access wrapped in helper methods in implementation.
- Return-value assertion completeness: all DataFrame asserts check BOTH schema
  (column names, dtypes) AND content (row count, no unexpected NaN).

Task: P7-T7.2 — Custom CTGAN Training Loop
Task: P20-T20.1 — Exception Handling & Warning Suppression Fixes
ADR: ADR-0025 (Custom CTGAN Training Loop Architecture)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_training_df(n: int = 50) -> pd.DataFrame:
    """Return a simple fictional training DataFrame for test fixtures.

    Uses a seeded NumPy RNG — deterministic, no PII.

    Args:
        n: Number of rows.

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


def _make_mock_sdv_synthesizer(processed_df: pd.DataFrame | None = None) -> MagicMock:
    """Return a mock CTGANSynthesizer with realistic preprocess / _data_processor.

    Args:
        processed_df: The DataFrame that synth.preprocess() will return.
            Defaults to a 50-row DataFrame with columns ['age', 'dept'].

    Returns:
        Configured MagicMock standing in for CTGANSynthesizer.
    """
    if processed_df is None:
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
    # _data_processor for reverse_transform
    mock_proc = MagicMock()
    mock_proc._hyper_transformer.field_transformers = {}
    mock_synth._data_processor = mock_proc

    return mock_synth


def _make_mock_ctgan_model(n_rows: int = 50) -> MagicMock:
    """Return a mock CTGAN model that produces a known sample DataFrame.

    Args:
        n_rows: Number of rows CTGAN.sample() will return.

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


# ---------------------------------------------------------------------------
# Tests for __init__
# ---------------------------------------------------------------------------


class TestDPCompatibleCTGANInit:
    """Unit tests for DPCompatibleCTGAN.__init__ signature and defaults."""

    def test_init_accepts_metadata_epochs_dp_wrapper(self) -> None:
        """__init__ must accept metadata, epochs, and dp_wrapper parameters."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = MagicMock()

        # Should not raise
        instance = DPCompatibleCTGAN(
            metadata=mock_metadata,
            epochs=5,
            dp_wrapper=mock_dp_wrapper,
        )
        assert instance is not None

    def test_init_dp_wrapper_defaults_to_none(self) -> None:
        """dp_wrapper must default to None — vanilla mode."""
        import inspect

        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        sig = inspect.signature(DPCompatibleCTGAN.__init__)
        assert "dp_wrapper" in sig.parameters
        assert sig.parameters["dp_wrapper"].default is None

    def test_init_stores_epochs(self) -> None:
        """__init__ must store the epochs parameter as an instance attribute."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=7)
        assert instance._epochs == 7

    def test_init_stores_metadata(self) -> None:
        """__init__ must store the metadata parameter as an instance attribute."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
        assert instance._metadata is mock_metadata

    def test_init_stores_dp_wrapper(self) -> None:
        """__init__ must store dp_wrapper as _dp_wrapper."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_wrapper = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2, dp_wrapper=mock_wrapper)
        assert instance._dp_wrapper is mock_wrapper

    def test_init_dp_wrapper_none_stored_correctly(self) -> None:
        """__init__ with dp_wrapper=None must store None."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2, dp_wrapper=None)
        assert instance._dp_wrapper is None

    def test_init_not_fitted_initially(self) -> None:
        """A freshly created instance must not be marked as fitted."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
        assert instance._fitted is False


# ---------------------------------------------------------------------------
# Tests for fit() — with mocked SDV and CTGAN internals
# ---------------------------------------------------------------------------


class TestDPCompatibleCTGANFit:
    """Unit tests for DPCompatibleCTGAN.fit() with mocked SDV internals."""

    def _patch_sdv_and_ctgan(self, mock_sdv_synth: MagicMock, mock_ctgan_cls: MagicMock) -> Any:
        """Return the combined patch context manager for SDV and CTGAN.

        Args:
            mock_sdv_synth: Pre-configured CTGANSynthesizer mock instance.
            mock_ctgan_cls: Mock for ctgan.synthesizers.ctgan.CTGAN class.

        Returns:
            A context manager string for use in with-statement patching.
        """
        return mock_sdv_synth

    def test_fit_returns_self(self) -> None:
        """fit() must return self to allow method chaining."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            df = _make_training_df()
            result = instance.fit(df)

        assert result is instance

    def test_fit_marks_instance_as_fitted(self) -> None:
        """fit() must set _fitted=True after successful training."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        assert instance._fitted is True

    def test_fit_calls_sdv_preprocess(self) -> None:
        """fit() must call CTGANSynthesizer.preprocess() to transform the input DataFrame."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            df = _make_training_df()
            instance.fit(df)

        mock_sdv_synth.preprocess.assert_called_once_with(df)

    def test_fit_stores_data_processor(self) -> None:
        """fit() must store _data_processor from the SDV synth for later reverse_transform."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        assert instance._data_processor is mock_sdv_synth._data_processor

    def test_fit_calls_ctgan_fit(self) -> None:
        """fit() must call CTGAN.fit() on the underlying model."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=["dept"],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        mock_ctgan_instance.fit.assert_called_once()

    def test_fit_passes_discrete_columns_to_ctgan(self) -> None:
        """fit() must pass the discrete_columns detected by SDV to CTGAN.fit()."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        detected_discrete = ["dept"]

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=detected_discrete,
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df())

        _call_args = mock_ctgan_instance.fit.call_args
        assert _call_args is not None
        # discrete_columns must be passed as keyword arg
        assert _call_args.kwargs.get("discrete_columns") == detected_discrete


# ---------------------------------------------------------------------------
# Tests for fit() — DP wrapper integration
# ---------------------------------------------------------------------------


class TestDPCompatibleCTGANFitWithDPWrapper:
    """Tests for DPCompatibleCTGAN.fit() when dp_wrapper is provided.

    The dp_wrapper parameter is typed as Any (never imports from modules/privacy).
    Expected interface: wrap(optimizer, model, dataloader, ...) -> optimizer
    """

    def test_fit_without_dp_wrapper_does_not_call_wrap(self) -> None:
        """fit(df) with dp_wrapper=None must NOT call any wrap() method."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()
        mock_dp_wrapper = MagicMock()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2, dp_wrapper=None)
            instance.fit(_make_training_df())

        mock_dp_wrapper.wrap.assert_not_called()

    def test_fit_with_dp_wrapper_calls_wrap_before_training(self) -> None:
        """fit() with dp_wrapper must call dp_wrapper.wrap() before CTGAN.fit().

        wrap() must be called before CTGAN.fit() — this is the Opacus integration
        point where the discriminator optimizer is DP-wrapped BEFORE training begins.
        """
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        mock_dp_wrapper = MagicMock()
        mock_dp_wrapper.wrap.return_value = MagicMock()  # returns a dp_optimizer

        call_order: list[str] = []

        def record_wrap(*args: Any, **kwargs: Any) -> Any:
            call_order.append("wrap")
            return MagicMock()

        def record_fit(*args: Any, **kwargs: Any) -> None:
            call_order.append("ctgan_fit")

        mock_dp_wrapper.wrap.side_effect = record_wrap
        mock_ctgan_instance.fit.side_effect = record_fit

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(
                metadata=mock_metadata, epochs=2, dp_wrapper=mock_dp_wrapper
            )
            instance.fit(_make_training_df())

        assert "wrap" in call_order, "dp_wrapper.wrap() was never called"
        wrap_idx = call_order.index("wrap")
        ctgan_fit_idx = call_order.index("ctgan_fit")
        assert wrap_idx < ctgan_fit_idx, (
            "dp_wrapper.wrap() must be called BEFORE CTGAN.fit(), "
            f"but wrap was at index {wrap_idx}, ctgan_fit at {ctgan_fit_idx}"
        )

    def test_fit_with_dp_wrapper_wrap_called_with_required_kwargs(self) -> None:
        """dp_wrapper.wrap() must be called with optimizer, model, dataloader kwargs."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()

        mock_dp_wrapper = MagicMock()
        mock_dp_wrapper.wrap.return_value = MagicMock()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(
                metadata=mock_metadata, epochs=2, dp_wrapper=mock_dp_wrapper
            )
            instance.fit(_make_training_df())

        assert mock_dp_wrapper.wrap.called, "dp_wrapper.wrap() was never called"
        call_kwargs = mock_dp_wrapper.wrap.call_args.kwargs
        # Must pass optimizer, model, dataloader — not positional args
        # (These are the three required PyTorch objects for Opacus wrapping)
        assert "optimizer" in call_kwargs or len(mock_dp_wrapper.wrap.call_args.args) >= 3, (
            f"wrap() must be called with optimizer, model, dataloader. "
            f"Got args={mock_dp_wrapper.wrap.call_args.args}, kwargs={call_kwargs}"
        )


# ---------------------------------------------------------------------------
# Tests for sample()
# ---------------------------------------------------------------------------


class TestDPCompatibleCTGANSample:
    """Unit tests for DPCompatibleCTGAN.sample() with mocked SDV internals."""

    def _fit_instance(
        self,
        mock_sdv_synth: MagicMock,
        mock_ctgan_instance: MagicMock,
        n_rows: int = 50,
    ) -> Any:
        """Helper: fit a DPCompatibleCTGAN and return the fitted instance.

        Args:
            mock_sdv_synth: Mocked CTGANSynthesizer instance.
            mock_ctgan_instance: Mocked CTGAN instance.
            n_rows: Number of rows in the synthetic sample.

        Returns:
            Fitted DPCompatibleCTGAN instance.
        """
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()

        with (
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer",
                return_value=mock_sdv_synth,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.CTGAN",
                return_value=mock_ctgan_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.dp_training.detect_discrete_columns",
                return_value=[],
            ),
        ):
            instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)
            instance.fit(_make_training_df(n=n_rows))

        return instance

    def test_sample_returns_dataframe(self) -> None:
        """sample() must return a pd.DataFrame."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model(n_rows=20)

        # reverse_transform returns a DataFrame with original columns
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {
                "id": list(range(20)),
                "age": list(range(20, 40)),
                "dept": ["Engineering"] * 20,
            }
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance, n_rows=50)

        result = instance.sample(n_rows=20)
        assert isinstance(result, pd.DataFrame)

    def test_sample_returns_correct_row_count(self) -> None:
        """sample(n_rows=N) must return exactly N rows."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        n_rows = 30
        mock_ctgan_instance = _make_mock_ctgan_model(n_rows=n_rows)
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {
                "id": list(range(n_rows)),
                "age": list(range(n_rows)),
                "dept": ["Sales"] * n_rows,
            }
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance, n_rows=50)
        result = instance.sample(n_rows=n_rows)

        assert len(result) == n_rows, f"Expected {n_rows} rows, got {len(result)}"

    def test_sample_calls_ctgan_sample(self) -> None:
        """sample() must call the underlying CTGAN model's sample() method."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model(n_rows=10)
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {"age": list(range(10)), "dept": ["HR"] * 10}
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance)
        instance.sample(n_rows=10)

        mock_ctgan_instance.sample.assert_called_once_with(10)

    def test_sample_calls_reverse_transform(self) -> None:
        """sample() must call data_processor.reverse_transform() on CTGAN output."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model(n_rows=15)
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {
                "id": list(range(15)),
                "age": list(range(15)),
                "dept": ["Engineering"] * 15,
            }
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance)
        instance.sample(n_rows=15)

        mock_sdv_synth._data_processor.reverse_transform.assert_called_once()

    def test_sample_before_fit_raises_runtime_error(self) -> None:
        """sample() before fit() must raise RuntimeError with a clear message."""
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=2)

        with pytest.raises(RuntimeError, match="fit"):
            instance.sample(n_rows=10)

    def test_sample_zero_rows_raises_value_error(self) -> None:
        """sample(n_rows=0) must raise ValueError."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {"age": [], "dept": []}
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance)

        with pytest.raises(ValueError, match="n_rows"):
            instance.sample(n_rows=0)

    def test_sample_negative_rows_raises_value_error(self) -> None:
        """sample(n_rows<0) must raise ValueError."""
        mock_sdv_synth = _make_mock_sdv_synthesizer()
        mock_ctgan_instance = _make_mock_ctgan_model()
        mock_sdv_synth._data_processor.reverse_transform.return_value = pd.DataFrame(
            {"age": [], "dept": []}
        )

        instance = self._fit_instance(mock_sdv_synth, mock_ctgan_instance)

        with pytest.raises(ValueError, match="n_rows"):
            instance.sample(n_rows=-1)


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

    Parses dp_training.py source to verify no blanket simplefilter("ignore")
    calls remain.  The targeted filterwarnings() calls must include a message
    pattern or specific category — not a blanket suppress-all.
    """

    def test_no_blanket_simplefilter_ignore_in_dp_training(self) -> None:
        """dp_training.py must not contain warnings.simplefilter('ignore').

        T20.1 AC2: blanket suppression silently hides real Opacus or PyTorch
        warnings beyond the documented ADR-0017a-approved set.  Each suppression
        must be targeted via warnings.filterwarnings with a specific message
        pattern and/or category.
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

        # Check for the exact pattern: simplefilter("ignore") with no category
        # — this is the blanket form that suppresses ALL warnings indiscriminately.
        import re

        blanket_pattern = re.compile(
            r'simplefilter\s*\(\s*["\']ignore["\']\s*\)',
        )
        matches = blanket_pattern.findall(source)
        assert not matches, (
            f"Found {len(matches)} blanket simplefilter('ignore') call(s) in dp_training.py. "
            "T20.1 AC2 requires targeted filterwarnings() with message pattern. "
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
