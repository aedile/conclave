"""Unit tests for build_dp_wrapper() bootstrapper factory.

Tests follow TDD Red/Green/Refactor.  All tests use mocks — no Opacus calls,
no real PyTorch objects.

Pattern guards applied:
- Return-value assertion pattern: every test asserts the return value of the
  function under test, not just absence of exceptions.
- Version-pin hallucination: no version constraints touched in pyproject.toml.
- File placement: build_dp_wrapper() lives in bootstrapper/main.py only.
- Import boundaries: bootstrapper CAN import from modules/privacy/ and
  modules/synthesizer/; modules/synthesizer/ must NOT import from modules/privacy/.

Task: P7-T7.3 — Opacus End-to-End Wiring
ADV-048 drain: build_dp_wrapper() factory wired through bootstrapper.
"""

from __future__ import annotations

import ast
import pathlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


class TestBuildDpWrapper:
    """Unit tests for the build_dp_wrapper() bootstrapper factory.

    The factory constructs a DPTrainingWrapper with configurable
    max_grad_norm and noise_multiplier.  These tests verify:
    - The factory returns a DPTrainingWrapper instance.
    - Default parameter values are sensible.
    - Custom parameters are passed through.
    - A new instance is returned on each call.
    """

    def test_build_dp_wrapper_returns_dp_training_wrapper(self) -> None:
        """build_dp_wrapper() must return a DPTrainingWrapper instance."""
        from synth_engine.bootstrapper.main import build_dp_wrapper
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        result = build_dp_wrapper()

        assert isinstance(result, DPTrainingWrapper), (
            f"build_dp_wrapper() must return DPTrainingWrapper, got {type(result)}"
        )

    def test_build_dp_wrapper_default_max_grad_norm(self) -> None:
        """build_dp_wrapper() default must have max_grad_norm=1.0 stored."""
        from synth_engine.bootstrapper.main import build_dp_wrapper

        wrapper = build_dp_wrapper()

        assert wrapper.max_grad_norm == 1.0, (
            f"Default max_grad_norm must be 1.0, got {wrapper.max_grad_norm}"
        )

    def test_build_dp_wrapper_default_noise_multiplier(self) -> None:
        """build_dp_wrapper() default must have noise_multiplier=1.1 stored."""
        from synth_engine.bootstrapper.main import build_dp_wrapper

        wrapper = build_dp_wrapper()

        assert wrapper.noise_multiplier == 1.1, (
            f"Default noise_multiplier must be 1.1, got {wrapper.noise_multiplier}"
        )

    def test_build_dp_wrapper_custom_max_grad_norm(self) -> None:
        """build_dp_wrapper(max_grad_norm=0.5) must store max_grad_norm=0.5."""
        from synth_engine.bootstrapper.main import build_dp_wrapper

        wrapper = build_dp_wrapper(max_grad_norm=0.5)

        assert wrapper.max_grad_norm == 0.5, (
            f"build_dp_wrapper(max_grad_norm=0.5) must store 0.5, got {wrapper.max_grad_norm}"
        )

    def test_build_dp_wrapper_custom_noise_multiplier(self) -> None:
        """build_dp_wrapper(noise_multiplier=2.0) must store noise_multiplier=2.0."""
        from synth_engine.bootstrapper.main import build_dp_wrapper

        wrapper = build_dp_wrapper(noise_multiplier=2.0)

        assert wrapper.noise_multiplier == 2.0, (
            f"build_dp_wrapper(noise_multiplier=2.0) must store 2.0, got {wrapper.noise_multiplier}"
        )

    def test_build_dp_wrapper_returns_fresh_instance_each_call(self) -> None:
        """build_dp_wrapper() must return a new instance on each call."""
        from synth_engine.bootstrapper.main import build_dp_wrapper

        wrapper1 = build_dp_wrapper()
        wrapper2 = build_dp_wrapper()

        assert wrapper1 is not wrapper2, (
            "build_dp_wrapper() must return a new DPTrainingWrapper on each call."
        )

    def test_build_dp_wrapper_invalid_max_grad_norm_raises(self) -> None:
        """build_dp_wrapper(max_grad_norm=0) must raise ValueError."""
        from synth_engine.bootstrapper.main import build_dp_wrapper

        with pytest.raises(ValueError, match="max_grad_norm"):
            build_dp_wrapper(max_grad_norm=0.0)

    def test_build_dp_wrapper_invalid_noise_multiplier_raises(self) -> None:
        """build_dp_wrapper(noise_multiplier=-1) must raise ValueError."""
        from synth_engine.bootstrapper.main import build_dp_wrapper

        with pytest.raises(ValueError, match="noise_multiplier"):
            build_dp_wrapper(noise_multiplier=-1.0)


class TestBuildDpWrapperImportBoundary:
    """Verify that build_dp_wrapper is only in bootstrapper/main.py.

    The bootstrapper is the sole layer that imports from both
    modules/privacy/ and modules/synthesizer/.
    """

    def test_build_dp_wrapper_importable_from_bootstrapper(self) -> None:
        """build_dp_wrapper must be importable from bootstrapper.main."""
        from synth_engine.bootstrapper import main as bootstrapper_main

        assert hasattr(bootstrapper_main, "build_dp_wrapper"), (
            "build_dp_wrapper must be defined in synth_engine.bootstrapper.main"
        )

    def test_modules_synthesizer_engine_does_not_import_privacy(self) -> None:
        """modules/synthesizer/engine.py must NOT import from modules/privacy/.

        Import boundary: synthesizer must not know the concrete DPTrainingWrapper type.
        The dp_wrapper parameter is typed as Any to enforce this boundary.
        """
        engine_path = pathlib.Path(
            "src/synth_engine/modules/synthesizer/training/engine.py"
        ).resolve()
        source = engine_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "modules.privacy" not in node.module, (
                        f"engine.py imports from modules.privacy: {node.module}. "
                        "This violates the import boundary. "
                        "Use Any typing for dp_wrapper parameter."
                    )


class TestSynthesisEngineRoutes:
    """Unit tests for SynthesisEngine.train() routing to DPCompatibleCTGAN.

    When dp_wrapper is not None, train() must use DPCompatibleCTGAN instead
    of CTGANSynthesizer.
    """

    def _make_persons_df(self, n: int = 10) -> pd.DataFrame:
        """Build a minimal persons DataFrame for training tests."""
        rng = np.random.default_rng(42)
        return pd.DataFrame(
            {
                "id": range(1, n + 1),
                "age": rng.integers(18, 80, size=n).tolist(),
                "salary": rng.integers(30000, 100000, size=n).tolist(),
            }
        )

    def test_train_without_dp_wrapper_uses_ctgan_synthesizer(self) -> None:
        """train() without dp_wrapper must use CTGANSynthesizer (vanilla path)."""
        from synth_engine.modules.synthesizer.training.engine import SynthesisEngine

        df = self._make_persons_df()

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "synth_engine.modules.synthesizer.training.engine.CTGANSynthesizer"
            ) as mock_ctgan,
            patch(
                "synth_engine.modules.synthesizer.training.engine.DPCompatibleCTGAN"
            ) as mock_dp_ctgan,
        ):
            mock_instance = MagicMock()
            mock_ctgan.return_value = mock_instance

            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            engine.train(table_name="persons", parquet_path=parquet_path)

        # vanilla path: CTGANSynthesizer used, DPCompatibleCTGAN NOT used
        assert mock_ctgan.called, "CTGANSynthesizer must be called in vanilla mode."
        assert not mock_dp_ctgan.called, (
            "DPCompatibleCTGAN must NOT be called when dp_wrapper is None."
        )

    def test_train_with_dp_wrapper_uses_dp_compatible_ctgan(self) -> None:
        """train() with dp_wrapper must use DPCompatibleCTGAN (DP path)."""
        from synth_engine.modules.synthesizer.storage.artifact import ModelArtifact
        from synth_engine.modules.synthesizer.training.engine import SynthesisEngine

        df = self._make_persons_df()
        mock_dp_wrapper = MagicMock()

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.training.engine.CTGANSynthesizer"),
            patch(
                "synth_engine.modules.synthesizer.training.engine.DPCompatibleCTGAN"
            ) as mock_dp_ctgan,
        ):
            mock_dp_instance = MagicMock()
            mock_dp_instance.fit.return_value = mock_dp_instance
            mock_dp_ctgan.return_value = mock_dp_instance

            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            result = engine.train(
                table_name="persons",
                parquet_path=parquet_path,
                dp_wrapper=mock_dp_wrapper,
            )

        # DP path: DPCompatibleCTGAN used
        assert mock_dp_ctgan.called, "DPCompatibleCTGAN must be used when dp_wrapper is not None."
        assert isinstance(result, ModelArtifact), (
            f"train() must return ModelArtifact, got {type(result)}"
        )

    def test_train_with_dp_wrapper_returns_model_artifact(self) -> None:
        """train() with dp_wrapper must return a ModelArtifact."""
        from synth_engine.modules.synthesizer.storage.artifact import ModelArtifact
        from synth_engine.modules.synthesizer.training.engine import SynthesisEngine

        df = self._make_persons_df()
        mock_dp_wrapper = MagicMock()

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.training.engine.CTGANSynthesizer"),
            patch(
                "synth_engine.modules.synthesizer.training.engine.DPCompatibleCTGAN"
            ) as mock_dp_ctgan,
        ):
            mock_dp_instance = MagicMock()
            mock_dp_instance.fit.return_value = mock_dp_instance
            mock_dp_ctgan.return_value = mock_dp_instance

            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            result = engine.train(
                table_name="persons",
                parquet_path=parquet_path,
                dp_wrapper=mock_dp_wrapper,
            )

        assert isinstance(result, ModelArtifact), (
            f"train() with dp_wrapper must return ModelArtifact, got {type(result)}"
        )

    def test_train_with_dp_wrapper_no_deferral_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """train() with dp_wrapper must NOT log the old T4.3b deferral warning.

        In T4.3b, a WARNING was logged with text "SDV's CTGANSynthesizer.fit()
        does not expose its optimizer".  T7.3 replaces this warning path with
        actual routing to DPCompatibleCTGAN.  The deferral warning must be gone.
        """
        import logging

        from synth_engine.modules.synthesizer.training.engine import SynthesisEngine

        df = self._make_persons_df()
        mock_dp_wrapper = MagicMock()

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.training.engine.CTGANSynthesizer"),
            patch(
                "synth_engine.modules.synthesizer.training.engine.DPCompatibleCTGAN"
            ) as mock_dp_ctgan,
            caplog.at_level(
                logging.WARNING, logger="synth_engine.modules.synthesizer.training.engine"
            ),
        ):
            mock_dp_instance = MagicMock()
            mock_dp_instance.fit.return_value = mock_dp_instance
            mock_dp_ctgan.return_value = mock_dp_instance

            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            engine.train(
                table_name="persons",
                parquet_path=parquet_path,
                dp_wrapper=mock_dp_wrapper,
            )

        # No deferral warning should be present
        deferral_logged = any(
            "does not expose" in record.message or "deferred" in record.message.lower()
            for record in caplog.records
        )
        assert not deferral_logged, (
            "The T4.3b deferral warning must not appear when dp_wrapper is provided. "
            "T7.3 routes to DPCompatibleCTGAN instead. "
            f"Logged warnings: {[r.message for r in caplog.records]}"
        )

    def test_dp_compatible_ctgan_receives_dp_wrapper(self) -> None:
        """DPCompatibleCTGAN must receive the dp_wrapper when train() routes to it."""
        from synth_engine.modules.synthesizer.training.engine import SynthesisEngine

        df = self._make_persons_df()
        mock_dp_wrapper = MagicMock()

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("synth_engine.modules.synthesizer.training.engine.CTGANSynthesizer"),
            patch(
                "synth_engine.modules.synthesizer.training.engine.DPCompatibleCTGAN"
            ) as mock_dp_ctgan,
        ):
            mock_dp_instance = MagicMock()
            mock_dp_instance.fit.return_value = mock_dp_instance
            mock_dp_ctgan.return_value = mock_dp_instance

            parquet_path = str(Path(tmpdir) / "persons.parquet")
            df.to_parquet(parquet_path, index=False, engine="pyarrow")

            engine = SynthesisEngine()
            engine.train(
                table_name="persons",
                parquet_path=parquet_path,
                dp_wrapper=mock_dp_wrapper,
            )

        # Verify DPCompatibleCTGAN constructor received dp_wrapper
        _, kwargs = mock_dp_ctgan.call_args
        assert kwargs.get("dp_wrapper") is mock_dp_wrapper, (
            "DPCompatibleCTGAN must be constructed with the dp_wrapper argument."
        )


class TestDPTrainingWrapperConstructorParams:
    """Unit tests verifying DPTrainingWrapper stores constructor params.

    T7.3 adds max_grad_norm and noise_multiplier as constructor arguments
    so the bootstrapper factory can configure them and DPCompatibleCTGAN
    can read them via duck-typing.
    """

    def test_dp_training_wrapper_stores_max_grad_norm(self) -> None:
        """DPTrainingWrapper(max_grad_norm=0.8) must store it as an attribute."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper(max_grad_norm=0.8, noise_multiplier=1.0)

        assert wrapper.max_grad_norm == 0.8, (
            f"DPTrainingWrapper.max_grad_norm must be 0.8, got {wrapper.max_grad_norm}"
        )

    def test_dp_training_wrapper_stores_noise_multiplier(self) -> None:
        """DPTrainingWrapper(noise_multiplier=1.5) must store it as an attribute."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper(max_grad_norm=1.0, noise_multiplier=1.5)

        assert wrapper.noise_multiplier == 1.5, (
            f"DPTrainingWrapper.noise_multiplier must be 1.5, got {wrapper.noise_multiplier}"
        )

    def test_dp_training_wrapper_constructor_validates_max_grad_norm(self) -> None:
        """DPTrainingWrapper(max_grad_norm=0) must raise ValueError."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        with pytest.raises(ValueError, match="max_grad_norm"):
            DPTrainingWrapper(max_grad_norm=0.0, noise_multiplier=1.1)

    def test_dp_training_wrapper_constructor_validates_noise_multiplier(self) -> None:
        """DPTrainingWrapper(noise_multiplier=-1) must raise ValueError."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        with pytest.raises(ValueError, match="noise_multiplier"):
            DPTrainingWrapper(max_grad_norm=1.0, noise_multiplier=-1.0)

    def test_dp_training_wrapper_backward_compat_no_args(self) -> None:
        """DPTrainingWrapper() with no args must use default values."""
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper()

        assert wrapper.max_grad_norm == 1.0, (
            f"Default max_grad_norm must be 1.0, got {wrapper.max_grad_norm}"
        )
        assert wrapper.noise_multiplier == 1.1, (
            f"Default noise_multiplier must be 1.1, got {wrapper.noise_multiplier}"
        )


pytestmark = pytest.mark.unit
