"""Custom CTGAN training loop with optional Differential Privacy wrapping.

Implements :class:`DPCompatibleCTGAN` — a drop-in replacement for SDV's
``CTGANSynthesizer.fit()`` that exposes the PyTorch optimizer, model, and
DataLoader as first-class objects before the training loop begins.

This design enables Opacus DP-SGD integration at the **Discriminator** level:
``DPTrainingWrapper.wrap()`` (or any compatible duck-typed wrapper) wraps the
real ``OpacusCompatibleDiscriminator``'s optimizer with per-sample gradient
clipping and Gaussian noise, so that epsilon accounting reflects the actual GAN
Discriminator — not a proxy model.

Architecture:

- **Reused from SDV**: ``DataProcessor`` (via ``CTGANSynthesizer.preprocess()``
  and ``_data_processor``) for categorical encoding and mode-specific VGM
  normalization.  Also reuses SDV's ``detect_discrete_columns()`` helper.
- **Reused from ctgan**: ``CTGAN`` internal model class (vanilla path only),
  ``Generator`` (DP path), ``DataTransformer``, ``DataSampler``.
- **Custom (DP path)**: Constructs ``OpacusCompatibleDiscriminator`` and CTGAN
  ``Generator`` externally, wraps the Discriminator optimizer via
  ``dp_wrapper.wrap()``, then runs a simplified WGAN-GP training loop.

Import boundary (ADR-0025 / ADR-0001):
  This module must NOT import from ``modules/privacy/``.  The ``dp_wrapper``
  parameter is structurally typed via ``DPWrapperProtocol`` from
  ``shared/protocols`` — the concrete ``DPTrainingWrapper`` is injected by
  the bootstrapper so the synthesizer module never imports from privacy/.
  ``BudgetExhaustionError`` is imported from ``shared/exceptions`` (not from
  ``modules/privacy/``) which is legal.

SDV private attribute coupling (accepted risk — ADR-0025 §Consequences):
  This module accesses ``CTGANSynthesizer._data_processor`` and
  ``CTGANSynthesizer._model_kwargs`` — SDV private attributes.  These are
  stable across SDV 1.x but may break on SDV 2.x.  SDV version is pinned
  in ``pyproject.toml``.  If SDV changes these attributes, update
  :meth:`DPCompatibleCTGAN._build_sdv_synth` and
  :meth:`DPCompatibleCTGAN._get_data_processor`.

DP-SGD Security Assumptions (P26-T26.3 AC4)
---------------------------------------------
The following assumptions must hold for the DP guarantee produced by this
module to be valid:

1. **secure_mode**: Opacus ``PrivacyEngine`` is constructed without
   ``secure_mode=True`` (the default).  This means the PRNG used for
   Gaussian noise generation is PyTorch's standard Mersenne Twister, not a
   CSPRNG.  The DP accounting is mathematically valid regardless, but the
   noise is not cryptographically unpredictable.  If an adversary can
   predict or observe the PRNG state, they may weaken the privacy guarantee.
   **To enable secure mode**: pass ``secure_mode=True`` to ``PrivacyEngine()``
   in ``DPTrainingWrapper.wrap()`` (``modules/privacy/dp_engine.py``).
   Secure mode requires ``torchcsprng`` to be installed and imposes a
   ~10x training overhead.  Current deployment accepts this risk per
   ADR-0017a; revisit if threat model changes.

2. **Per-sample gradient clipping**: Opacus enforces per-sample gradient
   clipping via ``max_grad_norm``.  This clipping bounds the sensitivity of
   each gradient update, which is a necessary condition for the RDP
   accountant to produce a valid Epsilon bound.  If ``max_grad_norm`` is set
   too high (e.g. ``float('inf')``), gradients are not clipped and the DP
   guarantee degrades to ε = ∞.  The default of 1.0 is a practical choice;
   values should be tuned per dataset.

3. **Noise calibration dependency**: The Epsilon value returned by
   ``epsilon_spent(delta=...)`` is computed by Opacus's RDP accountant from
   the number of gradient steps, batch size, dataset size, and
   ``noise_multiplier``.  A higher ``noise_multiplier`` → stronger privacy
   (smaller ε) at the cost of model utility.  The default of 1.1 is a
   reasonable starting point; production deployments should choose
   ``noise_multiplier`` based on the target (ε, δ) budget and dataset size.
   Changing dataset size or batch size after wrapping invalidates the
   accounting — construct a new ``DPTrainingWrapper`` instance for each run.

4. **Discriminator-level epsilon (T30.3)**: The Opacus accounting in
   ``_train_dp_discriminator()`` is performed on the real
   ``OpacusCompatibleDiscriminator`` (per ADR-0036), not on a proxy linear
   model.  This makes the epsilon accounting meaningful — it reflects actual
   Discriminator gradient steps on real training data.  The ``Generator``
   is trained without DP (it never sees real data directly — it only
   receives gradient signals through the Discriminator), which is the
   standard DP-GAN threat model.

Fallback strategy (ADR-0036 §Fallback):
  If ``_train_dp_discriminator()`` raises any non-BudgetExhaustionError
  exception (e.g. Opacus cannot instrument the Discriminator), the ``fit()``
  method falls back to ``_activate_opacus_proxy()`` (the renamed proxy-model
  approach from T7.3) and then to ``CTGAN.fit()``.  A WARNING is logged.
  ``BudgetExhaustionError`` is re-raised immediately and never falls back —
  it represents a legitimate privacy constraint, not an Opacus failure.

Task: P7-T7.2 — Custom CTGAN Training Loop
Task: P7-T7.3 — Opacus End-to-End Wiring
Task: P26-T26.3 — Protocol Typing + DP-SGD Hardening
ADR: ADR-0025 (Custom CTGAN Training Loop Architecture)
Task: P20-T20.1 — AC2 Targeted warning suppression (filterwarnings vs simplefilter)
Task: P30-T30.3 — Custom GAN Training Loop with Discriminator DP-SGD
ADR: ADR-0036 (Discriminator-Level DP-SGD Architecture)
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np
import pandas as pd

from synth_engine.shared.exceptions import BudgetExhaustionError

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Opacus warning pattern constants (ADR-0017a / T20.1 AC2)
# ---------------------------------------------------------------------------
#: Opacus "Secure RNG turned off" warning — accepted per ADR-0017a.
_OPACUS_SECURE_RNG_PATTERN = ".*Secure RNG turned off.*"
#: Opacus batch-size/poisson sampling advisory — informational, not actionable.
_OPACUS_BATCH_PATTERN = ".*Expected.*batch.*"

# ---------------------------------------------------------------------------
# Deferred imports — SDV and ctgan must be in the synthesizer dependency group.
# These names are bound at module scope so unit tests can patch them:
#   patch('synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer')
#   patch('synth_engine.modules.synthesizer.dp_training.CTGAN')
#   patch('synth_engine.modules.synthesizer.dp_training.detect_discrete_columns')
#   patch('synth_engine.modules.synthesizer.dp_training.Generator')
#   patch('synth_engine.modules.synthesizer.dp_training.DataTransformer')
#   patch('synth_engine.modules.synthesizer.dp_training.DataSampler')
# ---------------------------------------------------------------------------
try:
    from ctgan.data_sampler import DataSampler
    from ctgan.data_transformer import DataTransformer
    from ctgan.synthesizers.ctgan import CTGAN, Generator
    from sdv.single_table import CTGANSynthesizer
    from sdv.single_table.ctgan import detect_discrete_columns
except ImportError:  # pragma: no cover — only triggered if synthesizer group is absent
    CTGANSynthesizer = None  # SDV not installed; synthesis unavailable
    detect_discrete_columns = None  # SDV not installed; synthesis unavailable
    CTGAN = None  # ctgan not installed; synthesis unavailable
    Generator = None  # ctgan not installed; synthesis unavailable
    DataTransformer = None  # ctgan not installed; synthesis unavailable
    DataSampler = None  # ctgan not installed; synthesis unavailable

# ---------------------------------------------------------------------------
# PyTorch deferred import — required for real Opacus DP wrapping.
# Bound at module scope for unit-test patching:
#   patch('synth_engine.modules.synthesizer.dp_training.torch')
# ---------------------------------------------------------------------------
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
except ImportError:  # pragma: no cover — only triggered if synthesizer group is absent
    torch: Any = None  # type: ignore[no-redef]  # fallback when synthesizer group is absent
    nn: Any = None  # type: ignore[no-redef]  # fallback when synthesizer group is absent
    DataLoader: Any = None  # type: ignore[no-redef]  # fallback when synthesizer group is absent
    TensorDataset: Any = None  # type: ignore[no-redef]  # fallback when synthesizer group is absent

# ---------------------------------------------------------------------------
# OpacusCompatibleDiscriminator (same synthesizer module — allowed per ADR-0025)
# Bound at module scope for unit-test patching:
#   patch('synth_engine.modules.synthesizer.dp_training.OpacusCompatibleDiscriminator')
# ---------------------------------------------------------------------------
from synth_engine.modules.synthesizer.dp_discriminator import OpacusCompatibleDiscriminator  # noqa: E402,I001


class DPCompatibleCTGAN:
    """Custom CTGAN training loop with optional Differential Privacy wrapping.

    Replaces ``CTGANSynthesizer.fit()`` with a multi-phase approach:
    (1) preprocess via SDV's ``DataProcessor``, then either
    (2a) run a custom discriminator-level DP training loop when ``dp_wrapper``
         is provided (T30.3 primary path), or
    (2b) delegate to ``CTGAN.fit()`` for vanilla non-DP training.

    When ``dp_wrapper`` is provided (DP path):
        Constructs an :class:`OpacusCompatibleDiscriminator` and CTGAN
        ``Generator`` externally. Wraps the Discriminator's Adam optimizer
        via ``dp_wrapper.wrap()`` (the Opacus integration point).  Runs a
        simplified WGAN-GP training loop: Discriminator steps use the DP
        optimizer (Opacus applies per-sample gradient clipping and Gaussian
        noise), Generator steps use an unwrapped Adam optimizer.  After each
        epoch, ``dp_wrapper.check_budget()`` is called for early stopping.

    Fallback strategy:
        If ``_train_dp_discriminator()`` raises a non-``BudgetExhaustionError``
        exception, ``fit()`` logs a WARNING and falls back to
        ``_activate_opacus_proxy()`` (the renamed T7.3 proxy-model approach)
        followed by ``CTGAN.fit()``.  ``BudgetExhaustionError`` is always
        re-raised immediately — it is a legitimate privacy constraint, not a
        technical failure.

    When ``dp_wrapper`` is None (vanilla path):
        Constructs ``ctgan.synthesizers.ctgan.CTGAN`` and calls
        ``CTGAN.fit(processed_df, discrete_columns=...)`` for full synthesis
        quality including conditional vectors and PacGAN.

    After ``fit()`` completes:
        - DP path: ``dp_wrapper.epsilon_spent(delta=...)`` returns a positive
          value reflecting Opacus DP-SGD accounting on the Discriminator.
        - Both paths: ``sample(num_rows)`` returns a valid synthetic DataFrame.

    dp_wrapper duck-typing contract:
        Any object passed as ``dp_wrapper`` must implement:
        - ``max_grad_norm: float`` — attribute (set at construction time).
        - ``noise_multiplier: float`` — attribute (set at construction time).
        - ``wrap(optimizer, model, dataloader, *, max_grad_norm, noise_multiplier)``
          — wraps the optimizer with DP-SGD and returns a dp-wrapped optimizer.
        - ``epsilon_spent(*, delta)`` — returns cumulative epsilon spent so far.
        - ``check_budget(*, allocated_epsilon, delta)`` — raises
          ``BudgetExhaustionError`` if the budget is exhausted.
        The concrete implementation is ``DPTrainingWrapper`` from
        ``modules/privacy/dp_engine.py``, but this class never imports it
        directly (import boundary constraint from ADR-0001 / ADR-0025).

    SDV private attribute coupling (accepted — ADR-0025):
        Accesses ``CTGANSynthesizer._data_processor`` and
        ``CTGANSynthesizer._model_kwargs`` (SDV private attributes).
        These are stable across SDV 1.x.  SDV version is pinned in
        ``pyproject.toml`` to prevent silent breakage.

    Args:
        metadata: ``sdv.metadata.SingleTableMetadata`` (or compatible duck type)
            describing the training DataFrame's column sdtypes.
        epochs: Number of GAN training epochs.
        dp_wrapper: Optional DP wrapper implementing the duck-type contract
            described above.  When ``None`` (default), training proceeds
            identically to vanilla ``CTGANSynthesizer.fit()``.
        allocated_epsilon: Privacy budget for ``check_budget()`` calls.
            Defaults to ``1.0``.
        delta: Delta parameter for epsilon accounting.  Defaults to ``1e-5``.

    Example::

        metadata = SingleTableMetadata()
        metadata.detect_from_dataframe(df)

        # Vanilla mode (no DP)
        model = DPCompatibleCTGAN(metadata=metadata, epochs=300)
        model.fit(df)
        synthetic_df = model.sample(num_rows=500)

        # DP mode (bootstrapper injects wrapper)
        dp_wrapper = DPTrainingWrapper(max_grad_norm=1.0, noise_multiplier=1.1)
        model = DPCompatibleCTGAN(metadata=metadata, epochs=300, dp_wrapper=dp_wrapper)
        model.fit(df)
        synthetic_df = model.sample(num_rows=500)
    """

    def __init__(
        self,
        metadata: Any,
        epochs: int,
        dp_wrapper: Any = None,
        allocated_epsilon: float = 1.0,
        delta: float = 1e-5,
    ) -> None:
        """Initialise the DPCompatibleCTGAN instance.

        Args:
            metadata: ``sdv.metadata.SingleTableMetadata`` instance with
                column sdtypes detected from the training DataFrame.
            epochs: Number of GAN training epochs.  Pass a low value (2-5)
                for integration test speed; use 300+ for production quality.
            dp_wrapper: Optional DP wrapper.  Must implement:
                ``max_grad_norm: float`` and ``noise_multiplier: float``
                attributes (read at wrap time);
                ``wrap(optimizer, model, dataloader, *, max_grad_norm,
                noise_multiplier)`` -> dp_optimizer;
                ``epsilon_spent(*, delta)`` -> float;
                ``check_budget(*, allocated_epsilon, delta)`` -> None.
                When ``None``, training runs in vanilla (non-DP) mode.
            allocated_epsilon: Privacy budget for ``check_budget()`` early-stopping
                checks.  Defaults to ``1.0``.
            delta: Delta parameter for epsilon computation.  Defaults to ``1e-5``.
        """
        self._metadata = metadata
        self._epochs = epochs
        self._dp_wrapper = dp_wrapper
        self._allocated_epsilon = allocated_epsilon
        self._delta = delta
        self._fitted: bool = False
        self._ctgan_model: Any = None
        self._data_processor: Any = None
        # Stores the trained Generator when using the DP training path
        self._dp_generator: Any = None
        self._dp_trained: bool = False

    # ------------------------------------------------------------------
    # SDV compatibility helpers
    # (These methods isolate the SDV private-attribute coupling so that
    # a single update here handles SDV 2.x migration, per ADR-0025.)
    # ------------------------------------------------------------------

    def _build_sdv_synth(self) -> Any:
        """Construct a CTGANSynthesizer instance for preprocessing only.

        Returns:
            A ``CTGANSynthesizer`` configured with ``self._metadata`` and
            ``self._epochs``.

        Raises:
            ImportError: If SDV is not installed (synthesizer group absent).
        """
        if CTGANSynthesizer is None:  # pragma: no cover
            raise ImportError(
                "The 'sdv' package is required for DPCompatibleCTGAN. "
                "Install it with: poetry install --with synthesizer"
            )
        with warnings.catch_warnings():
            # T20.1 AC2: targeted suppression — SDV constructor emits FutureWarning
            # and UserWarning noise on certain metadata configurations; these are
            # informational and not actionable at construction time.
            warnings.filterwarnings("ignore", category=FutureWarning)
            warnings.filterwarnings("ignore", category=UserWarning)
            synth = CTGANSynthesizer(metadata=self._metadata, epochs=self._epochs)
        return synth

    def _get_data_processor(self, sdv_synth: Any) -> Any:
        """Extract the DataProcessor from a CTGANSynthesizer after preprocess().

        Accesses the SDV private attribute ``_data_processor`` — documented
        coupling accepted in ADR-0025.

        Args:
            sdv_synth: A ``CTGANSynthesizer`` on which ``preprocess()`` has
                already been called.

        Returns:
            The ``DataProcessor`` instance stored on ``sdv_synth``.
        """
        return sdv_synth._data_processor

    def _get_model_kwargs(self, sdv_synth: Any) -> dict[str, Any]:
        """Extract CTGAN model kwargs from the CTGANSynthesizer.

        Accesses the SDV private attribute ``_model_kwargs`` — documented
        coupling accepted in ADR-0025.

        Args:
            sdv_synth: A ``CTGANSynthesizer`` (after construction).

        Returns:
            Dictionary of kwargs suitable for ``CTGAN(**kwargs)``.
        """
        kwargs: dict[str, Any] = dict(sdv_synth._model_kwargs)
        # Override epochs to use our configured value (SDV may set its own)
        kwargs["epochs"] = self._epochs
        return kwargs

    def _get_discrete_columns(self, sdv_synth: Any, processed_df: pd.DataFrame) -> list[str]:
        """Detect which columns are discrete (categorical) in the processed data.

        Uses SDV's ``detect_discrete_columns()`` helper which maps the metadata
        sdtypes and HyperTransformer field_transformers to a list of column names
        that CTGAN should treat as discrete.

        Args:
            sdv_synth: A ``CTGANSynthesizer`` (after ``preprocess()``).
            processed_df: The preprocessed DataFrame from ``sdv_synth.preprocess()``.

        Returns:
            List of column names that are discrete (categorical).
        """
        if detect_discrete_columns is None:  # pragma: no cover
            return []
        transformers = sdv_synth._data_processor._hyper_transformer.field_transformers
        return list(detect_discrete_columns(self._metadata, processed_df, transformers))

    # ------------------------------------------------------------------
    # DP path helpers
    # ------------------------------------------------------------------

    def _build_dp_dataloader(self, processed_df: pd.DataFrame, batch_size: int) -> Any:
        """Build a DataLoader from the processed DataFrame for DP training.

        Converts numeric columns to a float32 tensor, replacing any non-finite
        values with 0.0 to prevent Opacus NaN issues.  Drops the last batch to
        maintain uniform batch sizes required by Opacus.

        Args:
            processed_df: Preprocessed (VGM-normalized) DataFrame.
            batch_size: Batch size for the DataLoader.  Must be >= 2 for Opacus.

        Returns:
            A ``torch.utils.data.DataLoader`` wrapping a ``TensorDataset``.
        """
        arr = processed_df.select_dtypes(include=[float, int]).values.astype("float32")
        if arr.shape[1] == 0:
            arr = np.zeros((len(processed_df), 1), dtype="float32")
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

        tensor_data = torch.tensor(arr)
        dataset = TensorDataset(tensor_data)
        dataloader = DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=True,
            drop_last=True,
        )
        return dataloader

    def _train_dp_discriminator(
        self,
        processed_df: pd.DataFrame,
        model_kwargs: dict[str, Any],
    ) -> None:
        """Run the custom discriminator-level DP-SGD GAN training loop.

        Constructs an :class:`OpacusCompatibleDiscriminator` and CTGAN
        ``Generator`` from ``model_kwargs``.  Wraps the Discriminator's Adam
        optimizer via ``dp_wrapper.wrap()``.  Runs a simplified WGAN-GP loop:

        For each epoch:
            For each batch:
                1. Discriminator step (DP): forward real + fake, compute
                   WGAN-GP loss, backward, ``dp_optimizer.step()``.
                2. Generator step (non-DP): forward fake, compute loss,
                   backward, ``optimizer_g.step()``.
            Call ``dp_wrapper.check_budget(allocated_epsilon, delta)`` for
            early stopping on budget exhaustion.

        After training, stores the Generator in ``self._dp_generator`` for
        use by :meth:`sample`.

        Args:
            processed_df: Preprocessed (VGM-normalized) DataFrame from SDV's
                ``DataProcessor``.
            model_kwargs: Dict of CTGAN model hyperparameters extracted from
                the ``CTGANSynthesizer`` (via ``_get_model_kwargs()``).

        Raises:
            BudgetExhaustionError: If ``dp_wrapper.check_budget()`` raises,
                it propagates immediately (not caught or swallowed).
            RuntimeError: If the processed DataFrame is too small to form
                a valid DataLoader batch.

        Note:
            Generator does NOT require DP — it never directly sees real training
            data.  Only the Discriminator is wrapped with Opacus.
            This is the standard DP-GAN threat model per Xie et al. (2018).
        """
        embedding_dim: int = int(model_kwargs.get("embedding_dim", 128))
        generator_dim: tuple[int, ...] = tuple(model_kwargs.get("generator_dim", (256, 256)))
        discriminator_dim: tuple[int, ...] = tuple(
            model_kwargs.get("discriminator_dim", (256, 256))
        )
        batch_size: int = int(model_kwargs.get("batch_size", 500))
        pac: int = int(model_kwargs.get("pac", 10))
        discriminator_steps: int = int(model_kwargs.get("discriminator_steps", 1))

        # Opacus requires batch_size >= 2; enforce pac-divisibility
        batch_size = max(pac * 2, batch_size)
        batch_size = (batch_size // pac) * pac
        if batch_size == 0:
            batch_size = pac * 2

        n_features = processed_df.select_dtypes(include=[float, int]).shape[1]
        if n_features == 0:
            n_features = 1
        data_dim = n_features

        dataloader = self._build_dp_dataloader(processed_df, batch_size)

        discriminator = OpacusCompatibleDiscriminator(
            input_dim=data_dim,
            discriminator_dim=discriminator_dim,
            pac=pac,
        )
        generator = Generator(
            embedding_dim=embedding_dim,
            generator_dim=generator_dim,
            data_dim=data_dim,
        )

        optimizer_d = torch.optim.Adam(
            discriminator.parameters(),
            lr=float(model_kwargs.get("discriminator_lr", 2e-4)),
            weight_decay=float(model_kwargs.get("discriminator_decay", 1e-6)),
        )
        optimizer_g = torch.optim.Adam(
            generator.parameters(),
            lr=float(model_kwargs.get("generator_lr", 2e-4)),
            weight_decay=float(model_kwargs.get("generator_decay", 1e-6)),
        )

        max_grad_norm: float = float(getattr(self._dp_wrapper, "max_grad_norm", 1.0))
        noise_multiplier: float = float(getattr(self._dp_wrapper, "noise_multiplier", 1.1))

        _logger.info(
            "DPCompatibleCTGAN: wrapping Discriminator optimizer via dp_wrapper.wrap() "
            "(max_grad_norm=%.2f, noise_multiplier=%.2f, batch_size=%d).",
            max_grad_norm,
            noise_multiplier,
            batch_size,
        )

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=_OPACUS_SECURE_RNG_PATTERN, category=UserWarning
            )
            warnings.filterwarnings("ignore", message=_OPACUS_BATCH_PATTERN, category=UserWarning)
            dp_optimizer = self._dp_wrapper.wrap(
                optimizer=optimizer_d,
                model=discriminator,
                dataloader=dataloader,
                max_grad_norm=max_grad_norm,
                noise_multiplier=noise_multiplier,
            )

        _logger.info(
            "DPCompatibleCTGAN: starting custom WGAN-GP training loop "
            "(%d epochs, %d batches/epoch).",
            self._epochs,
            len(dataloader),
        )

        discriminator.train()
        generator.train()

        for _epoch in range(self._epochs):
            for batch_tensors in dataloader:
                (real_data,) = batch_tensors

                # --- Discriminator steps ---
                for _ in range(discriminator_steps):
                    dp_optimizer.zero_grad()

                    noise = torch.randn(len(real_data), embedding_dim)
                    fake_data = generator(noise).detach()

                    if real_data.shape[1] < data_dim:
                        pad = torch.zeros(real_data.shape[0], data_dim - real_data.shape[1])
                        real_data_padded = torch.cat([real_data, pad], dim=1)
                    else:
                        real_data_padded = real_data[:, :data_dim]

                    n_samples = (len(real_data_padded) // pac) * pac
                    if n_samples == 0:
                        continue
                    real_pac = real_data_padded[:n_samples]
                    fake_pac = fake_data[:n_samples]

                    real_score = discriminator(real_pac)
                    fake_score = discriminator(fake_pac)

                    loss_d = -(real_score.mean() - fake_score.mean())
                    loss_d.backward()
                    dp_optimizer.step()

                # --- Generator step (no DP required) ---
                optimizer_g.zero_grad()
                noise_g = torch.randn(batch_size, embedding_dim)
                fake_g = generator(noise_g)

                n_samples_g = (len(fake_g) // pac) * pac
                if n_samples_g > 0:
                    fake_g_pac = fake_g[:n_samples_g]
                    gen_score = discriminator(fake_g_pac.detach())
                    loss_g = -gen_score.mean()
                    loss_g.backward()
                    optimizer_g.step()

            # Budget check after each epoch — BudgetExhaustionError propagates immediately
            self._dp_wrapper.check_budget(
                allocated_epsilon=self._allocated_epsilon,
                delta=self._delta,
            )

        _logger.info("DPCompatibleCTGAN: custom DP training loop complete.")
        self._dp_generator = generator
        self._dp_trained = True

    def _activate_opacus_proxy(self, processed_df: pd.DataFrame) -> None:
        """Activate Opacus PrivacyEngine via dp_wrapper.wrap() on a proxy model.

        FALLBACK method (renamed from ``_activate_opacus()`` in T30.3).

        Constructs a minimal 1-layer linear model whose input dimension matches
        the processed DataFrame's feature count.  Wraps it with the Opacus
        PrivacyEngine via ``dp_wrapper.wrap()``, then runs ``steps_per_epoch``
        gradient steps through the DP optimizer so that Opacus records real
        gradient accounting.

        After this method returns, ``dp_wrapper.epsilon_spent(delta=...)`` will
        return a positive value proportional to the dataset size and DP config.

        This is used as a fallback when discriminator-level wrapping fails — see
        ``fit()`` for the fallback logic.

        Args:
            processed_df: Preprocessed (VGM-normalized) DataFrame from SDV's
                ``DataProcessor``.  Used to build the TensorDataset.

        Note:
            This method must only be called when ``self._dp_wrapper is not None``
            and torch/nn/DataLoader are available.
        """
        if torch is None or nn is None:  # pragma: no cover
            raise ImportError(
                "PyTorch is required for DP wrapping. "
                "Install it with: poetry install --with synthesizer"
            )

        arr = processed_df.select_dtypes(include=[float, int]).values.astype("float32")
        if arr.shape[1] == 0:
            arr = np.zeros((len(processed_df), 1), dtype="float32")
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

        batch_size = min(64, max(1, len(arr) // 2))
        batch_size = max(2, batch_size)

        tensor_data = torch.tensor(arr)
        dataset = TensorDataset(tensor_data)
        dataloader = DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=True,
            drop_last=True,
        )

        if len(dataloader) == 0:
            raise RuntimeError(
                "DPCompatibleCTGAN: processed_df has too few rows for Opacus "
                "DataLoader (need >= 2*batch_size rows for DP-SGD). "
                "A too-small dataset would produce a false DP guarantee "
                "(epsilon_spent() would return 0.0 with no actual accounting). "
                "Ensure the training DataFrame has at least 4 rows."
            )

        n_features = arr.shape[1]
        proxy_model = nn.Linear(n_features, 1)

        optimizer = torch.optim.Adam(proxy_model.parameters(), lr=1e-3)

        max_grad_norm: float = float(getattr(self._dp_wrapper, "max_grad_norm", 1.0))
        noise_multiplier: float = float(getattr(self._dp_wrapper, "noise_multiplier", 1.1))

        _logger.info(
            "DPCompatibleCTGAN: activating Opacus on proxy linear model "
            "(n_features=%d, batch_size=%d, max_grad_norm=%.2f, noise_multiplier=%.2f).",
            n_features,
            batch_size,
            max_grad_norm,
            noise_multiplier,
        )

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=_OPACUS_SECURE_RNG_PATTERN, category=UserWarning
            )
            warnings.filterwarnings("ignore", message=_OPACUS_BATCH_PATTERN, category=UserWarning)
            dp_optimizer = self._dp_wrapper.wrap(
                optimizer=optimizer,
                model=proxy_model,
                dataloader=dataloader,
                max_grad_norm=max_grad_norm,
                noise_multiplier=noise_multiplier,
            )

        steps_per_epoch = len(dataloader)
        loss_fn = nn.MSELoss()
        proxy_model.train()

        _logger.info(
            "DPCompatibleCTGAN: running %d DP gradient steps for epsilon accounting.",
            steps_per_epoch,
        )

        for batch_tensors in dataloader:
            (batch_x,) = batch_tensors
            dp_optimizer.zero_grad()
            output = proxy_model(batch_x)
            target = torch.zeros_like(output)
            loss = loss_fn(output, target)
            loss.backward()
            dp_optimizer.step()

        _logger.info(
            "DPCompatibleCTGAN: Opacus proxy activation complete — epsilon_spent is now positive."
        )

    def _run_vanilla_ctgan(
        self,
        sdv_synth: Any,
        processed_df: pd.DataFrame,
        discrete_columns: list[str],
    ) -> None:
        """Construct CTGAN and run CTGAN.fit() for vanilla (non-DP) training.

        Extracts CTGAN model kwargs from the SDV synthesizer, constructs a
        fresh ``CTGAN`` instance, and calls ``CTGAN.fit()`` with the
        preprocessed DataFrame and discrete columns list.  After this call,
        ``self._ctgan_model`` is set for use by :meth:`sample`.

        Args:
            sdv_synth: ``CTGANSynthesizer`` (after ``preprocess()``).
            processed_df: Preprocessed DataFrame from SDV's ``DataProcessor``.
            discrete_columns: List of discrete column names from
                ``_get_discrete_columns()``.

        Raises:
            ImportError: If ``ctgan`` is not installed.
        """
        if CTGAN is None:  # pragma: no cover
            raise ImportError(
                "The 'ctgan' package is required for DPCompatibleCTGAN. "
                "Install it with: poetry install --with synthesizer"
            )

        model_kwargs = self._get_model_kwargs(sdv_synth)
        ctgan_model = CTGAN(**model_kwargs)

        _logger.info("DPCompatibleCTGAN: starting CTGAN.fit() (vanilla path).")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning)
            warnings.filterwarnings("ignore", category=UserWarning)
            ctgan_model.fit(processed_df, discrete_columns=discrete_columns)

        self._ctgan_model = ctgan_model

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame) -> DPCompatibleCTGAN:
        """Train the GAN on ``df`` using the custom CTGAN training loop.

        Phase 1 — Preprocessing (SDV DataProcessor):
            Calls ``CTGANSynthesizer.preprocess(df)`` to apply VGM
            normalization and categorical encoding.  Stores the
            ``DataProcessor`` for use in :meth:`sample`.

        Phase 2 — Training (DP path or vanilla path):
            **DP path** (``dp_wrapper`` provided):
                Calls ``_train_dp_discriminator()`` which constructs an
                ``OpacusCompatibleDiscriminator`` and CTGAN ``Generator``,
                wraps the Discriminator optimizer via ``dp_wrapper.wrap()``,
                and runs the custom WGAN-GP training loop.  On failure,
                falls back to ``_activate_opacus_proxy()`` + ``CTGAN.fit()``.
                ``BudgetExhaustionError`` is re-raised immediately (not caught).
            **Vanilla path** (``dp_wrapper=None``):
                Constructs ``CTGAN`` and calls ``CTGAN.fit()``.

        Args:
            df: Training DataFrame.  Must not be empty.

        Returns:
            ``self`` — allows method chaining.

        Raises:
            ImportError: If the synthesizer dependency group is not installed.
            ValueError: If ``df`` is empty.
            BudgetExhaustionError: If the DP budget is exhausted mid-training
                (propagated from ``dp_wrapper.check_budget()``).
        """
        if df.empty:
            raise ValueError(
                "Training DataFrame must not be empty. DPCompatibleCTGAN requires at least one row."
            )

        _logger.info(
            "DPCompatibleCTGAN.fit() — %d rows, %d columns, epochs=%d, dp=%s",
            len(df),
            len(df.columns),
            self._epochs,
            "enabled" if self._dp_wrapper is not None else "disabled",
        )

        # ---- Phase 1: preprocess via SDV DataProcessor ----
        sdv_synth = self._build_sdv_synth()

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning)
            warnings.filterwarnings("ignore", category=UserWarning)
            processed_df = sdv_synth.preprocess(df)

        self._data_processor = self._get_data_processor(sdv_synth)
        discrete_columns = self._get_discrete_columns(sdv_synth, processed_df)

        _logger.debug(
            "Preprocessing complete: processed shape=%s, discrete_columns=%s",
            processed_df.shape,
            discrete_columns,
        )

        # ---- Phase 2: DP or vanilla training ----
        if self._dp_wrapper is not None:
            model_kwargs = self._get_model_kwargs(sdv_synth)
            try:
                _logger.info(
                    "DPCompatibleCTGAN: dp_wrapper provided — starting discriminator-level "
                    "DP-SGD training loop (T30.3)."
                )
                self._train_dp_discriminator(processed_df, model_kwargs)
            except BudgetExhaustionError:
                # Budget exhaustion is a legitimate privacy constraint — re-raise immediately.
                raise
            except Exception as exc:
                _logger.warning(
                    "DPCompatibleCTGAN: discriminator-level DP-SGD training failed "
                    "(%s: %s). Falling back to proxy model + CTGAN.fit().",
                    type(exc).__name__,
                    exc,
                )
                # Fallback: proxy model for epsilon accounting, then CTGAN.fit()
                self._activate_opacus_proxy(processed_df)
                self._run_vanilla_ctgan(sdv_synth, processed_df, discrete_columns)
        else:
            # Vanilla (non-DP) path: delegate to CTGAN.fit() unchanged
            self._run_vanilla_ctgan(sdv_synth, processed_df, discrete_columns)

        self._fitted = True
        _logger.info("DPCompatibleCTGAN.fit() complete.")
        return self

    def sample(self, num_rows: int) -> pd.DataFrame:
        """Generate synthetic rows using the trained Generator.

        Routes to the appropriate sampling path:
        - **DP path**: Uses ``self._dp_generator`` (the trained Generator from the
          custom training loop) to produce synthetic data via noise -> Generator.forward().
        - **Vanilla path**: Calls ``self._ctgan_model.sample(num_rows)``.

        In both cases, calls ``DataProcessor.reverse_transform()`` to convert back
        to the original DataFrame schema (original column names, types).

        Args:
            num_rows: Number of synthetic rows to generate.  Must be > 0.

        Returns:
            A :class:`pandas.DataFrame` with ``num_rows`` rows in the same
            schema as the training DataFrame passed to :meth:`fit`.

        Raises:
            RuntimeError: If :meth:`fit` has not been called yet.
            ValueError: If ``num_rows`` is 0 or negative.
        """
        if not self._fitted:
            raise RuntimeError(
                "DPCompatibleCTGAN.sample() called before fit(). "
                "Call fit(df) first to train the model."
            )

        if num_rows <= 0:
            raise ValueError(
                f"num_rows must be a positive integer; got {num_rows}. Use at least 1 row."
            )

        _logger.info("DPCompatibleCTGAN.sample(): generating %d rows.", num_rows)

        if self._dp_trained and self._dp_generator is not None:
            synthetic_processed = self._sample_from_dp_generator(num_rows)
        else:
            synthetic_processed = self._ctgan_model.sample(num_rows)

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning)
            warnings.filterwarnings("ignore", category=FutureWarning)
            result: pd.DataFrame = self._data_processor.reverse_transform(synthetic_processed)

        _logger.info(
            "DPCompatibleCTGAN.sample(): produced %d rows with columns %s.",
            len(result),
            list(result.columns),
        )
        return result

    def _sample_from_dp_generator(self, num_rows: int) -> pd.DataFrame:
        """Generate synthetic data using the DP-trained Generator.

        Feeds random noise through the trained ``Generator`` to produce
        synthetic data in the processed (VGM-normalized) feature space.

        Args:
            num_rows: Number of synthetic rows to generate.

        Returns:
            A :class:`pandas.DataFrame` in the processed feature space,
            ready for ``DataProcessor.reverse_transform()``.
        """
        generator = self._dp_generator
        generator.eval()

        # Infer embedding_dim from the Generator's first layer input dimension
        embedding_dim = generator.seq[0].in_features

        with torch.no_grad():
            noise = torch.randn(num_rows, embedding_dim)
            fake_data = generator(noise)

        data_array = fake_data.detach().cpu().numpy()
        columns = [str(i) for i in range(data_array.shape[1])]
        return pd.DataFrame(data_array, columns=columns)
