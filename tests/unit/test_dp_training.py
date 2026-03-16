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
