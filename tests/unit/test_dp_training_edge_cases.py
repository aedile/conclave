"""Unit tests for P30 review follow-up — edge case guard paths in _train_dp_discriminator().

RED phase tests written before implementation changes (TDD).

Covers four previously untested guard branches:
  Guard 1: batch_size == 0 after pac alignment → reset to pac before building DataLoader.
  Guard 2: n_features == 0 (all-categorical input) → set to 1 to avoid zero-dim tensors.
  Guard 3: real_data.shape[1] < data_dim → pad batch tensor before Discriminator forward.
  Guard 4: n_samples == 0 in discriminator step → skip (continue) when pac > batch rows.

Class naming (T40.2 AC4):
  All four classes end in *Wiring because each test patches 3+ targets in the
  module under test (OpacusCompatibleDiscriminator, Generator, torch).  These
  tests verify that guard logic wires the correct paths — they do NOT test that
  PyTorch or Opacus work correctly.

  For behavioral tests that exercise real tensor arithmetic without mocking
  torch, see test_dp_training_behavioral.py (cap_batch_size, parse_gan_hyperparams)
  and the @pytest.mark.synthesizer suite.

CONSTITUTION Priority 3: TDD Red/Green/Refactor.
Task: P30 review follow-up — edge case branch coverage
Task: P40-T40.2 — Replace Mock-Heavy Tests With Behavioral Tests (wiring labeling)
ADR: ADR-0036 (Discriminator-Level DP-SGD Architecture)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import torch

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers shared across this module
# ---------------------------------------------------------------------------


def _make_mock_dp_wrapper() -> MagicMock:
    """Return a MagicMock DP wrapper with standard attributes.

    Boundary mock: replaces the external Opacus/privacy wrapper at the
    dp_wrapper interface.  Used in all *Wiring tests in this module.

    Returns:
        MagicMock with max_grad_norm, noise_multiplier, wrap, epsilon_spent,
        check_budget configured.
    """
    wrapper = MagicMock()
    wrapper.max_grad_norm = 1.0
    wrapper.noise_multiplier = 1.1
    mock_dp_opt = MagicMock()
    mock_dp_opt.zero_grad = MagicMock()
    mock_dp_opt.step = MagicMock()
    wrapper.wrap.return_value = mock_dp_opt
    wrapper.epsilon_spent.return_value = 0.5
    wrapper.check_budget.return_value = None
    return wrapper


def _minimal_model_kwargs(
    *,
    batch_size: int = 10,
    pac: int = 2,
    embedding_dim: int = 4,
    discriminator_dim: tuple[int, ...] = (8,),
    generator_dim: tuple[int, ...] = (8,),
    discriminator_steps: int = 1,
) -> dict[str, Any]:
    """Return a minimal model_kwargs dict for _train_dp_discriminator.

    Args:
        batch_size: Batch size hint (adjusted by the method internally).
        pac: PacGAN factor.
        embedding_dim: Generator noise embedding dimension.
        discriminator_dim: Hidden layer sizes for Discriminator.
        generator_dim: Hidden layer sizes for Generator.
        discriminator_steps: Number of Discriminator updates per batch.

    Returns:
        Dictionary of CTGAN model hyperparameters.
    """
    return {
        "embedding_dim": embedding_dim,
        "generator_dim": generator_dim,
        "discriminator_dim": discriminator_dim,
        "generator_lr": 2e-4,
        "generator_decay": 1e-6,
        "discriminator_lr": 2e-4,
        "discriminator_decay": 1e-6,
        "batch_size": batch_size,
        "discriminator_steps": discriminator_steps,
        "pac": pac,
        "enable_gpu": False,
    }


def _make_one_batch_dataloader(n_rows: int, n_cols: int) -> MagicMock:
    """Return a mock DataLoader that yields a single batch of real tensors.

    Uses real torch.zeros() for the tensor so tensor operations in the training
    loop work without mocking torch.

    Args:
        n_rows: Number of rows in the single batch tensor.
        n_cols: Number of columns in the batch tensor.

    Returns:
        MagicMock behaving like a DataLoader with one batch.
    """
    batch = (torch.zeros(n_rows, n_cols),)
    mock_dl = MagicMock()
    mock_dl.__len__ = MagicMock(return_value=1)
    mock_dl.__iter__ = MagicMock(return_value=iter([batch]))
    return mock_dl


# ---------------------------------------------------------------------------
# Guard 1: batch_size == 0 after pac alignment → reset to pac
# ---------------------------------------------------------------------------


class TestBatchSizeZeroGuardWiring:
    """Guard 1 — wiring tests: when pac alignment produces batch_size == 0 it is reset to pac.

    SCOPE (T40.2 AC4): These are *Wiring tests because they patch
    OpacusCompatibleDiscriminator, Generator, and torch in the module under
    test to isolate the batch_size alignment guard path.

    The guard at line ~474:
        ``if batch_size == 0: batch_size = pac``
    ensures that a batch_size of 0 never reaches ``_build_dp_dataloader``,
    which would cause a PyTorch DataLoader error.

    In normal operation ``batch_size = max(pac, prev_bs)`` ensures batch_size >= pac,
    so ``(batch_size // pac) * pac`` is always >= pac.  The guard is a safety net
    for callers that pass batch_size=0 or extremely small values.  We verify the
    invariant: ``_build_dp_dataloader`` is never called with batch_size=0.
    """

    def test_batch_size_zero_never_passed_to_build_dataloader(self) -> None:
        """batch_size=0 must not reach _build_dp_dataloader after pac alignment.

        Passes model_kwargs with batch_size=0.  After the guard logic:
          - ``min(0, n_rows//2)`` → still 0 (or small)
          - ``max(pac, 0) = pac``
          - ``(pac // pac) * pac = pac``
          - guard line ``if batch_size == 0`` is NOT triggered here (pac > 0)
        The important invariant: batch_size arriving at _build_dp_dataloader is >= 1.
        """
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper)

        rng = np.random.default_rng(7)
        processed_df = pd.DataFrame(
            {
                "a": rng.standard_normal(20).astype(float),
                "b": rng.standard_normal(20).astype(float),
            }
        )

        pac = 2
        n_data_cols = 2
        received_batch_sizes: list[int] = []

        def spy_build(df: Any, batch_size: int) -> Any:
            received_batch_sizes.append(batch_size)
            return _make_one_batch_dataloader(pac * 2, n_data_cols)

        instance._build_dp_dataloader = spy_build  # type: ignore[method-assign]

        model_kwargs = _minimal_model_kwargs(batch_size=0, pac=pac)

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.OpacusCompatibleDiscriminator"
            ) as mock_disc_cls,
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.Generator"
            ) as mock_gen_cls,
            patch("synth_engine.modules.synthesizer.training.dp_training.torch") as mock_torch,
        ):
            mock_disc_instance = MagicMock()
            mock_disc_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_disc_instance.train = MagicMock()
            mock_disc_instance.return_value = torch.zeros(pac, 1)
            mock_disc_cls.return_value = mock_disc_instance

            mock_gen_instance = MagicMock()
            mock_gen_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_gen_instance.train = MagicMock()
            mock_gen_instance.return_value = torch.zeros(pac * 2, n_data_cols)
            mock_gen_cls.return_value = mock_gen_instance

            mock_torch.optim = MagicMock()
            mock_torch.optim.Adam.return_value = MagicMock()
            mock_torch.device.return_value = "cpu"
            mock_torch.cuda.is_available.return_value = False
            mock_torch.zeros.return_value = torch.zeros(1)
            mock_torch.randn.return_value = torch.zeros(pac * 2, 4)
            mock_torch.cat = torch.cat
            mock_torch.tensor = torch.tensor

            instance._train_dp_discriminator(processed_df, model_kwargs)

        assert received_batch_sizes, "_build_dp_dataloader was never called"
        assert received_batch_sizes[0] != 0, (
            f"batch_size=0 must never reach _build_dp_dataloader after guard; "
            f"got batch_size={received_batch_sizes[0]}"
        )

    def test_batch_size_guard_result_is_pac_multiple(self) -> None:
        """After pac alignment, batch_size must always be a multiple of pac.

        Tests the invariant: ``batch_size = (batch_size // pac) * pac`` always
        produces a pac-multiple.  When initial batch_size is very small the
        ``max(pac, batch_size)`` ensures it is at least pac.
        """
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper)

        rng = np.random.default_rng(9)
        processed_df = pd.DataFrame({"x": rng.standard_normal(20).astype(float)})

        pac = 3
        received_batch_sizes: list[int] = []

        def spy_build(df: Any, batch_size: int) -> Any:
            received_batch_sizes.append(batch_size)
            return _make_one_batch_dataloader(pac * 2, 1)

        instance._build_dp_dataloader = spy_build  # type: ignore[method-assign]

        model_kwargs = _minimal_model_kwargs(batch_size=1, pac=pac)  # 1 < pac

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.OpacusCompatibleDiscriminator"
            ) as mock_disc_cls,
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.Generator"
            ) as mock_gen_cls,
            patch("synth_engine.modules.synthesizer.training.dp_training.torch") as mock_torch,
        ):
            mock_disc_instance = MagicMock()
            mock_disc_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_disc_instance.train = MagicMock()
            mock_disc_instance.return_value = torch.zeros(pac, 1)
            mock_disc_cls.return_value = mock_disc_instance

            mock_gen_instance = MagicMock()
            mock_gen_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_gen_instance.train = MagicMock()
            mock_gen_instance.return_value = torch.zeros(pac * 2, 1)
            mock_gen_cls.return_value = mock_gen_instance

            mock_torch.optim = MagicMock()
            mock_torch.optim.Adam.return_value = MagicMock()
            mock_torch.device.return_value = "cpu"
            mock_torch.cuda.is_available.return_value = False
            mock_torch.zeros.return_value = torch.zeros(1)
            mock_torch.randn.return_value = torch.zeros(pac * 2, 4)
            mock_torch.cat = torch.cat
            mock_torch.tensor = torch.tensor

            instance._train_dp_discriminator(processed_df, model_kwargs)

        assert received_batch_sizes, "_build_dp_dataloader was never called"
        bs = received_batch_sizes[0]
        assert bs % pac == 0, f"batch_size={bs} must be a multiple of pac={pac} after alignment"
        assert bs >= pac, f"batch_size={bs} must be >= pac={pac} after max() guard"


# ---------------------------------------------------------------------------
# Guard 2: n_features == 0 (all-categorical input) → set to 1
# ---------------------------------------------------------------------------


class TestNFeatureZeroGuardWiring:
    """Guard 2 — wiring tests: all-categorical input triggers n_features=1 guard.

    SCOPE (T40.2 AC4): *Wiring because patches OpacusCompatibleDiscriminator,
    Generator, and torch.  Verifies guard routing, not PyTorch correctness.

    All-categorical data (no float/int columns) produces n_features=0 from
    ``select_dtypes(include=[float, int])``.  The guard sets n_features=1 to
    avoid constructing a zero-dimensional tensor and a zero-input Discriminator.
    """

    def test_n_features_zero_guard_uses_input_dim_one_for_discriminator(self) -> None:
        """Discriminator must be constructed with input_dim >= 1 for all-categorical data.

        An all-categorical DataFrame triggers the n_features==0 guard, which
        sets ``n_features=1`` before the Discriminator is constructed.  We verify
        by spying on the Discriminator constructor's ``input_dim`` argument.
        """
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper)

        # All-categorical DataFrame — select_dtypes(include=[float, int]) returns 0 cols
        all_cat_df = pd.DataFrame(
            {
                "dept": ["Eng", "Sales", "HR", "Mkt", "Ops"] * 4,
                "region": ["N", "S", "E", "W", "N"] * 4,
            }
        )

        pac = 2
        received_input_dims: list[int] = []

        def spy_disc_cls(
            *,
            input_dim: int,
            discriminator_dim: Any,
            pac: int,
        ) -> MagicMock:
            received_input_dims.append(input_dim)
            mock_disc = MagicMock()
            mock_disc.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_disc.train = MagicMock()
            mock_disc.return_value = torch.zeros(pac, 1)
            return mock_disc

        # Use a real DataLoader with 1 batch so training runs past the zero-batch guard
        instance._build_dp_dataloader = MagicMock(  # type: ignore[method-assign]
            return_value=_make_one_batch_dataloader(pac * 2, 1)
        )

        model_kwargs = _minimal_model_kwargs(batch_size=4, pac=pac)

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.OpacusCompatibleDiscriminator",
                side_effect=spy_disc_cls,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.Generator"
            ) as mock_gen_cls,
            patch("synth_engine.modules.synthesizer.training.dp_training.torch") as mock_torch,
        ):
            mock_gen_instance = MagicMock()
            mock_gen_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_gen_instance.train = MagicMock()
            mock_gen_instance.return_value = torch.zeros(pac * 2, 1)
            mock_gen_cls.return_value = mock_gen_instance

            mock_torch.optim = MagicMock()
            mock_torch.optim.Adam.return_value = MagicMock()
            mock_torch.device.return_value = "cpu"
            mock_torch.cuda.is_available.return_value = False
            mock_torch.zeros.return_value = torch.zeros(1)
            mock_torch.randn.return_value = torch.zeros(pac * 2, 4)
            mock_torch.cat = torch.cat
            mock_torch.tensor = torch.tensor

            instance._train_dp_discriminator(all_cat_df, model_kwargs)

        assert received_input_dims, "OpacusCompatibleDiscriminator was never constructed"
        assert received_input_dims[0] >= 1, (
            f"n_features==0 guard must set input_dim to at least 1; "
            f"got input_dim={received_input_dims[0]}"
        )

    def test_n_features_zero_guard_builds_dataloader_for_all_categorical(self) -> None:
        """_build_dp_dataloader must be called even when all columns are categorical.

        ``_build_dp_dataloader`` has its own guard for zero numeric columns
        (it pads with a zeros column).  The key invariant: the DataLoader
        construction is not skipped when n_features==0.
        """
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper)

        all_cat_df = pd.DataFrame({"category": ["a", "b", "c"] * 6})
        pac = 2

        build_called: list[bool] = []

        def spy_build(df: Any, batch_size: int) -> Any:
            build_called.append(True)
            return _make_one_batch_dataloader(pac * 2, 1)

        instance._build_dp_dataloader = spy_build  # type: ignore[method-assign]

        model_kwargs = _minimal_model_kwargs(batch_size=4, pac=pac)

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.OpacusCompatibleDiscriminator"
            ) as mock_disc_cls,
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.Generator"
            ) as mock_gen_cls,
            patch("synth_engine.modules.synthesizer.training.dp_training.torch") as mock_torch,
        ):
            mock_disc_instance = MagicMock()
            mock_disc_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_disc_instance.train = MagicMock()
            mock_disc_instance.return_value = torch.zeros(pac, 1)
            mock_disc_cls.return_value = mock_disc_instance

            mock_gen_instance = MagicMock()
            mock_gen_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_gen_instance.train = MagicMock()
            mock_gen_instance.return_value = torch.zeros(pac * 2, 1)
            mock_gen_cls.return_value = mock_gen_instance

            mock_torch.optim = MagicMock()
            mock_torch.optim.Adam.return_value = MagicMock()
            mock_torch.device.return_value = "cpu"
            mock_torch.cuda.is_available.return_value = False
            mock_torch.zeros.return_value = torch.zeros(1)
            mock_torch.randn.return_value = torch.zeros(pac * 2, 4)
            mock_torch.cat = torch.cat
            mock_torch.tensor = torch.tensor

            instance._train_dp_discriminator(all_cat_df, model_kwargs)

        assert build_called == [True], (
            "_build_dp_dataloader must be called even for all-categorical data"
        )


# ---------------------------------------------------------------------------
# Guard 3: real_data.shape[1] < data_dim → pad real_data before forward pass
# ---------------------------------------------------------------------------


class TestRealDataPaddingGuardWiring:
    """Guard 3 — wiring tests: narrow batch is padded before Discriminator forward pass.

    SCOPE (T40.2 AC4): *Wiring because patches OpacusCompatibleDiscriminator,
    Generator, and torch.  Uses real torch.cat/torch.zeros for the padding spy
    so that shape assertions are real, not mocked.

    ``data_dim`` is computed from ``processed_df``'s numeric column count.
    Normally the DataLoader batch has the same number of columns.  When a narrower
    batch arrives (simulated by injecting a DataLoader with fewer columns than
    data_dim), the padding guard at:
        ``if real_data.shape[1] < data_dim:``
            ``pad = torch.zeros(...)``
            ``real_data_padded = torch.cat([real_data, pad], dim=1)``
    prevents a shape mismatch in the Discriminator forward call.
    """

    def test_padding_guard_applied_when_batch_narrower_than_data_dim(self) -> None:
        """Training loop must pad real_data when shape[1] < data_dim.

        Injects a DataLoader that yields 1-column batches while data_dim=2
        (processed_df has 2 numeric columns).  The loop must not raise a
        shape mismatch — the padding guard handles it gracefully.
        """
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper)

        rng = np.random.default_rng(11)
        # 2-column DataFrame → data_dim = 2
        processed_df = pd.DataFrame(
            {
                "x": rng.standard_normal(20).astype(float),
                "y": rng.standard_normal(20).astype(float),
            }
        )

        pac = 2
        # DataLoader yields 1-column batches (narrower than data_dim=2)
        narrow_dl = _make_one_batch_dataloader(pac * 2, 1)
        instance._build_dp_dataloader = MagicMock(return_value=narrow_dl)  # type: ignore[method-assign]

        model_kwargs = _minimal_model_kwargs(batch_size=4, pac=pac)

        cat_calls: list[tuple[int, ...]] = []

        def spy_cat(tensors: Any, *, dim: int = 0, **kwargs: Any) -> Any:
            result = torch.cat(tensors, dim=dim, **kwargs)
            cat_calls.append(tuple(result.shape))
            return result

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.OpacusCompatibleDiscriminator"
            ) as mock_disc_cls,
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.Generator"
            ) as mock_gen_cls,
            patch("synth_engine.modules.synthesizer.training.dp_training.torch") as mock_torch,
        ):
            mock_disc_instance = MagicMock()
            mock_disc_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_disc_instance.train = MagicMock()
            mock_disc_instance.return_value = torch.zeros(pac, 1)
            mock_disc_cls.return_value = mock_disc_instance

            mock_gen_instance = MagicMock()
            mock_gen_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_gen_instance.train = MagicMock()
            mock_gen_instance.return_value = torch.zeros(pac * 2, 2)
            mock_gen_cls.return_value = mock_gen_instance

            mock_torch.optim = MagicMock()
            mock_torch.optim.Adam.return_value = MagicMock()
            mock_torch.device.return_value = "cpu"
            mock_torch.cuda.is_available.return_value = False
            mock_torch.randn.return_value = torch.zeros(pac * 2, 4)
            mock_torch.zeros = torch.zeros
            # Route torch.cat through our spy so we can observe the padding
            mock_torch.cat = spy_cat
            mock_torch.tensor = torch.tensor

            # Must not raise — padding guard prevents shape mismatch
            instance._train_dp_discriminator(processed_df, model_kwargs)

        # The padding path calls torch.cat — verify it was invoked for the padding
        assert len(cat_calls) > 0, (
            "torch.cat must be called in the padding guard branch (real_data.shape[1] < data_dim)"
        )

    def test_padding_guard_not_invoked_when_shapes_match(self) -> None:
        """When batch columns == data_dim, the padding path must NOT be taken.

        Injects a 2-column DataLoader matching a 2-column processed_df.
        The ``if real_data.shape[1] < data_dim`` branch must not fire,
        and ``real_data_padded = real_data[:, :data_dim]`` is used instead.
        """
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper)

        rng = np.random.default_rng(12)
        processed_df = pd.DataFrame(
            {
                "x": rng.standard_normal(20).astype(float),
                "y": rng.standard_normal(20).astype(float),
            }
        )

        pac = 2
        # Matching DataLoader: 2 columns = data_dim
        matching_dl = _make_one_batch_dataloader(pac * 2, 2)
        instance._build_dp_dataloader = MagicMock(return_value=matching_dl)  # type: ignore[method-assign]

        model_kwargs = _minimal_model_kwargs(batch_size=4, pac=pac)

        padding_cat_calls: list[bool] = []

        def spy_cat_check(tensors: Any, *, dim: int = 0, **kwargs: Any) -> Any:
            # Discriminate padding cats from any other 2-tensor cat (e.g., pac grouping).
            # The padding guard in the production code always:
            #   (1) concatenates along dim=1 (column-wise),
            #   (2) uses a second operand that is a 2D all-zeros tensor with shape[1] > 0.
            # Only record the call when ALL three conditions hold.
            if (
                dim == 1
                and isinstance(tensors, list | tuple)
                and len(tensors) == 2
                and hasattr(tensors[1], "shape")
                and tensors[1].ndim == 2
                and tensors[1].shape[1] > 0
                and torch.allclose(tensors[1], torch.zeros_like(tensors[1]))
            ):
                padding_cat_calls.append(True)
            return torch.cat(tensors, dim=dim, **kwargs)

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.OpacusCompatibleDiscriminator"
            ) as mock_disc_cls,
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.Generator"
            ) as mock_gen_cls,
            patch("synth_engine.modules.synthesizer.training.dp_training.torch") as mock_torch,
        ):
            mock_disc_instance = MagicMock()
            mock_disc_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_disc_instance.train = MagicMock()
            mock_disc_instance.return_value = torch.zeros(pac, 1)
            mock_disc_cls.return_value = mock_disc_instance

            mock_gen_instance = MagicMock()
            mock_gen_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_gen_instance.train = MagicMock()
            mock_gen_instance.return_value = torch.zeros(pac * 2, 2)
            mock_gen_cls.return_value = mock_gen_instance

            mock_torch.optim = MagicMock()
            mock_torch.optim.Adam.return_value = MagicMock()
            mock_torch.device.return_value = "cpu"
            mock_torch.cuda.is_available.return_value = False
            mock_torch.randn.return_value = torch.zeros(pac * 2, 4)
            mock_torch.zeros = torch.zeros
            mock_torch.cat = spy_cat_check
            mock_torch.tensor = torch.tensor

            instance._train_dp_discriminator(processed_df, model_kwargs)

        # When shapes match, padding cat must NOT be called
        assert not padding_cat_calls, (
            "torch.cat must NOT be called for padding when batch columns == data_dim"
        )


# ---------------------------------------------------------------------------
# Guard 4: n_samples == 0 in discriminator step → continue (skip batch)
# ---------------------------------------------------------------------------


class TestNSamplesZeroSkipGuardWiring:
    """Guard 4 — wiring tests: n_samples == 0 triggers continue (skip discriminator step).

    SCOPE (T40.2 AC4): *Wiring because patches OpacusCompatibleDiscriminator,
    Generator, and torch.  Uses a spy on the discriminator forward call to
    verify the skip guard fires.

    ``n_samples = (len(real_data_padded) // pac) * pac`` is 0 when the batch
    has fewer rows than pac.  The ``if n_samples == 0: continue`` guard skips
    the Discriminator forward pass, which would otherwise fail with a reshape
    error (Discriminator concatenates ``pac`` samples per group).
    """

    def test_n_samples_zero_skips_discriminator_forward_call(self) -> None:
        """When a batch is smaller than pac, the discriminator forward pass is skipped.

        Injects a DataLoader that yields a single 1-row batch with pac=4.
        ``n_samples = (1 // 4) * 4 = 0`` → the ``continue`` guard fires.
        The Discriminator's forward pass must NOT be called for this batch.
        """
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper)

        rng = np.random.default_rng(13)
        processed_df = pd.DataFrame(
            {
                "a": rng.standard_normal(20).astype(float),
                "b": rng.standard_normal(20).astype(float),
            }
        )

        pac = 4
        # Batch has only 1 row → n_samples = (1 // 4)*4 = 0
        tiny_dl = _make_one_batch_dataloader(1, 2)
        instance._build_dp_dataloader = MagicMock(return_value=tiny_dl)  # type: ignore[method-assign]

        model_kwargs = _minimal_model_kwargs(batch_size=pac, pac=pac)

        discriminator_forward_calls: list[int] = []

        def disc_forward(x: Any) -> Any:
            discriminator_forward_calls.append(x.shape[0] if hasattr(x, "shape") else 0)
            return torch.zeros(1, 1)

        mock_disc_instance = MagicMock()
        mock_disc_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
        mock_disc_instance.train = MagicMock()
        mock_disc_instance.side_effect = disc_forward

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.OpacusCompatibleDiscriminator",
                return_value=mock_disc_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.Generator"
            ) as mock_gen_cls,
            patch("synth_engine.modules.synthesizer.training.dp_training.torch") as mock_torch,
        ):
            mock_gen_instance = MagicMock()
            mock_gen_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_gen_instance.train = MagicMock()
            mock_gen_instance.return_value = torch.zeros(pac, 2)
            mock_gen_cls.return_value = mock_gen_instance

            mock_torch.optim = MagicMock()
            mock_torch.optim.Adam.return_value = MagicMock()
            mock_torch.device.return_value = "cpu"
            mock_torch.cuda.is_available.return_value = False
            mock_torch.zeros = torch.zeros
            mock_torch.randn.return_value = torch.zeros(pac, 4)
            mock_torch.cat = torch.cat
            mock_torch.tensor = torch.tensor

            # Must not raise — n_samples==0 guard skips the discriminator step
            instance._train_dp_discriminator(processed_df, model_kwargs)

        # Discriminator must not have been called for the discriminator step
        # (n_samples==0 fired the continue guard)
        assert discriminator_forward_calls == [], (
            f"Discriminator must not be called when n_samples==0 (pac={pac} > batch_rows=1). "
            f"Got {len(discriminator_forward_calls)} forward call(s): {discriminator_forward_calls}"
        )

    def test_n_samples_zero_does_not_raise(self) -> None:
        """_train_dp_discriminator must complete without error when n_samples == 0.

        The ``continue`` guard for ``n_samples == 0`` must silently skip the
        batch — no exception should propagate.
        """
        from synth_engine.modules.synthesizer.training.dp_training import DPCompatibleCTGAN

        mock_metadata = MagicMock()
        mock_dp_wrapper = _make_mock_dp_wrapper()
        instance = DPCompatibleCTGAN(metadata=mock_metadata, epochs=1, dp_wrapper=mock_dp_wrapper)

        rng = np.random.default_rng(17)
        processed_df = pd.DataFrame({"val": rng.standard_normal(30).astype(float)})

        pac = 8
        # Batch of 3 rows with pac=8 → n_samples = (3 // 8)*8 = 0
        skip_dl = _make_one_batch_dataloader(3, 1)
        instance._build_dp_dataloader = MagicMock(return_value=skip_dl)  # type: ignore[method-assign]

        model_kwargs = _minimal_model_kwargs(batch_size=8, pac=pac)

        mock_disc_instance = MagicMock()
        mock_disc_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
        mock_disc_instance.train = MagicMock()

        with (
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.OpacusCompatibleDiscriminator",
                return_value=mock_disc_instance,
            ),
            patch(
                "synth_engine.modules.synthesizer.training.dp_training.Generator"
            ) as mock_gen_cls,
            patch("synth_engine.modules.synthesizer.training.dp_training.torch") as mock_torch,
        ):
            mock_gen_instance = MagicMock()
            mock_gen_instance.parameters = MagicMock(return_value=iter([torch.zeros(1)]))
            mock_gen_instance.train = MagicMock()
            mock_gen_instance.return_value = torch.zeros(pac, 1)
            mock_gen_cls.return_value = mock_gen_instance

            mock_torch.optim = MagicMock()
            mock_torch.optim.Adam.return_value = MagicMock()
            mock_torch.device.return_value = "cpu"
            mock_torch.cuda.is_available.return_value = False
            mock_torch.zeros = torch.zeros
            mock_torch.randn.return_value = torch.zeros(pac, 4)
            mock_torch.cat = torch.cat
            mock_torch.tensor = torch.tensor

            # No exception expected — the guard skips the step gracefully
            instance._train_dp_discriminator(processed_df, model_kwargs)

        # If we get here without an exception, the guard worked correctly
        mock_dp_wrapper.check_budget.assert_called_once()
        assert mock_dp_wrapper.check_budget.call_count == 1
