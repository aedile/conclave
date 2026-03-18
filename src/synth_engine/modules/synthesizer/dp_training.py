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
  ``Generator`` (DP path).
- **Custom (DP path)**: Constructs ``OpacusCompatibleDiscriminator`` and CTGAN
  ``Generator`` externally, wraps the Discriminator optimizer via
  ``dp_wrapper.wrap()``, then runs a simplified WGAN training loop.
  Note: gradient penalty (WGAN-GP) is omitted in DP mode — ``torch.autograd.grad()``
  conflicts with Opacus per-sample gradient hooks.

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
Task: P31-T31.3 — dp_training.py Decomposition (code health)
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np
import pandas as pd

from synth_engine.shared.exceptions import BudgetExhaustionError
from synth_engine.shared.protocols import DPWrapperProtocol

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
# ---------------------------------------------------------------------------
try:
    from ctgan.synthesizers.ctgan import CTGAN, Generator
    from sdv.single_table import CTGANSynthesizer
    from sdv.single_table.ctgan import detect_discrete_columns
except ImportError:  # pragma: no cover — only triggered if synthesizer group is absent
    CTGANSynthesizer = None  # SDV not installed; synthesis unavailable
    detect_discrete_columns = None  # SDV not installed; synthesis unavailable
    CTGAN = None  # ctgan not installed; synthesis unavailable
    Generator = None  # ctgan not installed; synthesis unavailable

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
        simplified WGAN training loop: Discriminator steps use the DP
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
        dp_wrapper: DPWrapperProtocol | None = None,
        allocated_epsilon: float = 50.0,
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
                checks.  Defaults to ``50.0``.  This is intentionally generous:
                discriminator-level DP-SGD typically spends 1-10 epsilon per epoch;
                callers that need tighter budgets must pass an explicit value.
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
        self._dp_embedding_dim: int = 128  # updated by _train_dp_discriminator
        self._dp_numeric_columns: list[str] = []  # numeric cols from processed_df
        self._dp_processed_df_sample: pd.DataFrame | None = None  # for non-numeric defaults

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

    def _prepare_dp_dataloader_checked(
        self, processed_df: pd.DataFrame, batch_size: int
    ) -> tuple[Any, int]:
        """Build the DP DataLoader and resolve ``data_dim``, raising on zero batches.

        Delegates DataLoader construction to :meth:`_build_dp_dataloader`.
        Resolves ``data_dim`` as the number of numeric features (minimum 1).
        Raises ``RuntimeError`` if the resulting DataLoader has zero batches,
        which would produce a false DP guarantee.

        Args:
            processed_df: Preprocessed (VGM-normalized) DataFrame.
            batch_size: Pac-divisible, Opacus-compatible batch size.

        Returns:
            A 2-tuple ``(dataloader, data_dim)``.

        Raises:
            RuntimeError: If the DataLoader has zero batches (dataset too small).
        """
        n_features = processed_df.select_dtypes(include=[float, int]).shape[1]
        data_dim = max(n_features, 1)

        dataloader = self._build_dp_dataloader(processed_df, batch_size)

        # Guard: zero batches means no gradient steps — false DP guarantee (Privacy P0).
        if len(dataloader) == 0:
            raise RuntimeError(
                "DPCompatibleCTGAN._train_dp_discriminator: DataLoader has zero batches. "
                "The dataset is too small for the configured batch_size and pac factor. "
                "No DP gradient steps would occur, producing a false DP guarantee "
                "(epsilon_spent() returns 0.0 with no actual accounting). "
                "Ensure the training DataFrame has enough rows for at least one batch."
            )
        return dataloader, data_dim

    def _parse_gan_hyperparams(
        self, model_kwargs: dict[str, Any]
    ) -> tuple[int, tuple[int, ...], tuple[int, ...], int, int, int]:
        """Extract GAN architecture hyperparameters from CTGAN model kwargs.

        Centralises ``model_kwargs`` extraction to keep
        :meth:`_train_dp_discriminator` focused on orchestration.

        Args:
            model_kwargs: CTGAN hyperparameter dict from ``_get_model_kwargs()``.

        Returns:
            A 6-tuple ``(embedding_dim, generator_dim, discriminator_dim,
            pac, discriminator_steps, batch_size)``.
        """
        embedding_dim = int(model_kwargs.get("embedding_dim", 128))
        generator_dim: tuple[int, ...] = tuple(model_kwargs.get("generator_dim", (256, 256)))
        discriminator_dim: tuple[int, ...] = tuple(
            model_kwargs.get("discriminator_dim", (256, 256))
        )
        pac = int(model_kwargs.get("pac", 10))
        discriminator_steps = int(model_kwargs.get("discriminator_steps", 1))
        batch_size = int(model_kwargs.get("batch_size", 500))
        return embedding_dim, generator_dim, discriminator_dim, pac, discriminator_steps, batch_size

    def _cap_batch_size(self, n_samples: int, requested_batch_size: int, pac: int) -> int:
        """Clamp batch size to satisfy Opacus sample-rate and pac-divisibility constraints.

        Opacus's ``DPDataLoader.from_data_loader`` sets
        ``sample_rate = 1 / len(data_loader)``.  When ``len(data_loader) == 1``
        (i.e. ``batch_size >= n_rows``), ``sample_rate == 1.0`` and Opacus's PRV
        accountant raises a ``RuntimeWarning`` on ``log(1 - q)`` for ``q = 1.0``
        (promoted to an error under ``-W error`` in tests).

        Fix: cap ``batch_size <= n_samples // 2`` so ``len(data_loader) >= 2``
        and ``q <= 0.5``.  Then enforce pac-divisibility (pac is the minimum
        grouping for one Discriminator pass).

        Args:
            n_samples: Number of rows in the training DataFrame.
            requested_batch_size: Raw batch size from CTGAN model kwargs.
            pac: PacGAN grouping factor from model kwargs.

        Returns:
            A valid batch size that is pac-divisible and satisfies the
            Opacus minimum-batch constraint.
        """
        batch_size = min(requested_batch_size, n_samples // 2)
        # Opacus requires batch_size >= 2; enforce pac-divisibility.
        # Use max(pac, batch_size) — pac is the minimum for one discriminator group.
        batch_size = max(pac, batch_size)
        batch_size = (batch_size // pac) * pac
        if batch_size == 0:
            batch_size = pac
        return batch_size

    def _build_gan_models(
        self,
        data_dim: int,
        embedding_dim: int,
        generator_dim: tuple[int, ...],
        discriminator_dim: tuple[int, ...],
        pac: int,
        model_kwargs: dict[str, Any],
    ) -> tuple[Any, Any, Any, Any]:
        """Construct Generator, Discriminator, and their Adam optimizers.

        Builds the two GAN networks and their respective Adam optimizers
        using hyperparameters from ``model_kwargs``.  The Discriminator
        optimizer will subsequently be wrapped by Opacus in
        :meth:`_wrap_discriminator_with_opacus`.

        Args:
            data_dim: Number of input features (processed DataFrame columns).
            embedding_dim: Noise embedding dimension for the Generator.
            generator_dim: Hidden layer sizes for the Generator.
            discriminator_dim: Hidden layer sizes for the Discriminator.
            pac: PacGAN grouping factor.
            model_kwargs: Full CTGAN model kwargs dict (for LR and weight decay).

        Returns:
            A 4-tuple ``(generator, discriminator, optimizer_g, optimizer_d)``.
        """
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
        return generator, discriminator, optimizer_g, optimizer_d

    def _wrap_discriminator_with_opacus(
        self,
        discriminator: Any,
        optimizer_d: Any,
        dataloader: Any,
        batch_size: int,
    ) -> tuple[Any, Any]:
        """Wrap the Discriminator optimizer with Opacus DP-SGD.

        Reads ``max_grad_norm`` and ``noise_multiplier`` from
        ``self._dp_wrapper``, calls ``dp_wrapper.wrap()`` under Opacus
        warning suppression, and resolves the wrapped Discriminator module
        (Opacus stores it as ``wrapper.wrapped_module``).

        Args:
            discriminator: The ``OpacusCompatibleDiscriminator`` instance.
            optimizer_d: The Discriminator's Adam optimizer (pre-wrap).
            dataloader: The training ``DataLoader`` (needed by Opacus for
                sample-rate computation).
            batch_size: Effective batch size (used for logging only).

        Returns:
            A 2-tuple ``(dp_optimizer, dp_discriminator)`` where
            ``dp_discriminator`` is the Opacus-wrapped
            ``GradSampleModule`` (or the original ``discriminator`` if
            ``wrapped_module`` is not set on the wrapper).
        """
        assert self._dp_wrapper is not None  # type guard — caller ensures this

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

        # After wrap(), the Opacus GradSampleModule is stored on the wrapper.
        # Use it for Discriminator steps so Opacus applies per-sample gradients
        # and DP noise.  Fall back to the original discriminator if not present.
        dp_discriminator: Any = getattr(self._dp_wrapper, "wrapped_module", discriminator)
        return dp_optimizer, dp_discriminator

    def _run_gan_epoch(
        self,
        generator: Any,
        dp_discriminator: Any,
        dataloader: Any,
        optimizer_g: Any,
        dp_optimizer: Any,
        embedding_dim: int,
        data_dim: int,
        pac: int,
        batch_size: int,
        discriminator_steps: int,
    ) -> None:
        """Execute a single epoch of the WGAN training loop.

        Iterates over all batches in ``dataloader``.  For each batch:

        1. **Discriminator steps** (Opacus hooks active): runs
           ``discriminator_steps`` gradient steps on the DP optimizer using
           WGAN loss ``-(real.mean() - fake.mean())``.  Gradient penalty is
           omitted — ``torch.autograd.grad()`` conflicts with Opacus per-sample
           gradient hooks in DP mode.
        2. **Generator step** (Opacus hooks disabled): scores fake samples
           through the Discriminator with hooks disabled (to prevent the
           "Poisson sampling not compatible with grad accumulation" error),
           computes Generator loss, and updates Generator parameters.

        Args:
            generator: The CTGAN ``Generator`` module.
            dp_discriminator: The Opacus-wrapped Discriminator
                (``GradSampleModule`` or original if wrap failed).
            dataloader: Training ``DataLoader``.
            optimizer_g: Generator's Adam optimizer (not DP-wrapped).
            dp_optimizer: Discriminator's Opacus-wrapped DP optimizer.
            embedding_dim: Noise vector dimension for the Generator.
            data_dim: Number of processed feature columns.
            pac: PacGAN grouping factor (batch must be divisible by pac).
            batch_size: Effective batch size (used for Generator noise shape).
            discriminator_steps: Number of Discriminator updates per batch.
        """
        for batch_tensors in dataloader:
            (real_data,) = batch_tensors

            # --- Discriminator steps (Opacus hooks active) ---
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

                real_score = dp_discriminator(real_pac)
                fake_score = dp_discriminator(fake_pac)

                loss_d = -(real_score.mean() - fake_score.mean())
                loss_d.backward()
                dp_optimizer.step()

            # --- Generator step (Opacus hooks disabled) ---
            # Opacus with Poisson sampling (DPDataLoader) raises ValueError if
            # backward() is called without a preceding optimizer.step().  The
            # Generator step uses the Discriminator only for scoring — it does
            # not contribute to Discriminator DP accounting.  Disabling Opacus
            # hooks here prevents the "grad accumulation" error while preserving
            # gradient flow through the Generator parameters.
            optimizer_g.zero_grad()
            noise_g = torch.randn(batch_size, embedding_dim)
            fake_g = generator(noise_g)

            n_samples_g = (len(fake_g) // pac) * pac
            if n_samples_g > 0:
                fake_g_pac = fake_g[:n_samples_g]
                if hasattr(dp_discriminator, "disable_hooks"):
                    dp_discriminator.disable_hooks()
                gen_score = dp_discriminator(fake_g_pac)
                loss_g = -gen_score.mean()
                loss_g.backward()
                if hasattr(dp_discriminator, "enable_hooks"):
                    dp_discriminator.enable_hooks()
                optimizer_g.step()

    def _store_dp_training_state(self, generator: Any) -> None:
        """Store the trained Generator and mark the DP path as complete.

        Called at the end of :meth:`_train_dp_discriminator` after all epochs
        have run.  Sets ``self._dp_generator`` for use by :meth:`sample` and
        flips ``self._dp_trained`` so the sample path routes to the custom
        Generator rather than ``self._ctgan_model``.

        Args:
            generator: The trained CTGAN ``Generator`` instance.
        """
        _logger.info("DPCompatibleCTGAN: custom DP training loop complete.")
        self._dp_generator = generator
        self._dp_trained = True

    def _train_dp_discriminator(
        self,
        processed_df: pd.DataFrame,
        model_kwargs: dict[str, Any],
    ) -> None:
        """Run the custom discriminator-level DP-SGD GAN training loop.

        Delegates to focused helpers: :meth:`_parse_gan_hyperparams`,
        :meth:`_cap_batch_size`, :meth:`_prepare_dp_dataloader_checked`,
        :meth:`_build_gan_models`, :meth:`_wrap_discriminator_with_opacus`,
        :meth:`_run_gan_epoch` (per epoch), and :meth:`_store_dp_training_state`.
        Generator is NOT DP-wrapped — it never sees real data directly.

        Args:
            processed_df: Preprocessed DataFrame from SDV's ``DataProcessor``.
            model_kwargs: CTGAN hyperparameters from ``_get_model_kwargs()``.

        Raises:
            BudgetExhaustionError: Propagated immediately from ``check_budget()``.
            RuntimeError: If processed_df is too small to form a valid DataLoader.
        """
        assert self._dp_wrapper is not None, (  # type guard for mypy
            "_train_dp_discriminator must only be called when dp_wrapper is not None"
        )
        embedding_dim, generator_dim, discriminator_dim, pac, discriminator_steps, raw_bs = (
            self._parse_gan_hyperparams(model_kwargs)
        )
        self._dp_embedding_dim = embedding_dim
        self._dp_numeric_columns = list(processed_df.select_dtypes(include=[float, int]).columns)
        self._dp_processed_df_sample = processed_df
        batch_size = self._cap_batch_size(len(processed_df), raw_bs, pac)
        dataloader, data_dim = self._prepare_dp_dataloader_checked(processed_df, batch_size)

        generator, discriminator, optimizer_g, optimizer_d = self._build_gan_models(
            data_dim=data_dim,
            embedding_dim=embedding_dim,
            generator_dim=generator_dim,
            discriminator_dim=discriminator_dim,
            pac=pac,
            model_kwargs=model_kwargs,
        )
        dp_optimizer, dp_discriminator = self._wrap_discriminator_with_opacus(
            discriminator=discriminator,
            optimizer_d=optimizer_d,
            dataloader=dataloader,
            batch_size=batch_size,
        )

        _logger.info(
            "DPCompatibleCTGAN: starting WGAN loop (%d epochs, %d batches/epoch).",
            self._epochs,
            len(dataloader),
        )
        dp_discriminator.train()
        generator.train()

        for _epoch in range(self._epochs):
            self._run_gan_epoch(
                generator=generator,
                dp_discriminator=dp_discriminator,
                dataloader=dataloader,
                optimizer_g=optimizer_g,
                dp_optimizer=dp_optimizer,
                embedding_dim=embedding_dim,
                data_dim=data_dim,
                pac=pac,
                batch_size=batch_size,
                discriminator_steps=discriminator_steps,
            )
            self._dp_wrapper.check_budget(  # BudgetExhaustionError propagates immediately
                allocated_epsilon=self._allocated_epsilon,
                delta=self._delta,
            )

        self._store_dp_training_state(generator)

    def _build_proxy_dataloader(self, processed_df: pd.DataFrame) -> tuple[Any, int]:
        """Build the DataLoader and determine batch size for proxy model training.

        Converts numeric columns to a float32 tensor, sanitises non-finite
        values, and constructs a DataLoader with a proxy-appropriate batch
        size (min(64, n_rows // 2), clamped to at least 2).

        Args:
            processed_df: Preprocessed (VGM-normalized) DataFrame.

        Returns:
            A 2-tuple ``(dataloader, n_features)`` where ``dataloader`` is a
            ``torch.utils.data.DataLoader`` wrapping the processed data and
            ``n_features`` is the number of numeric feature columns (at least 1).

        Raises:
            RuntimeError: If the DataFrame is too small to form even one batch
                (i.e. ``len(dataloader) == 0``).
        """
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

        n_features: int = arr.shape[1]
        return dataloader, n_features

    def _activate_opacus_proxy(self, processed_df: pd.DataFrame) -> None:
        """Activate Opacus PrivacyEngine via dp_wrapper.wrap() on a proxy model.

        FALLBACK method (renamed from ``_activate_opacus()`` in T30.3).

        Constructs a minimal 1-layer linear model whose input dimension matches
        the processed DataFrame's feature count.  Wraps it with the Opacus
        PrivacyEngine via ``dp_wrapper.wrap()``, then runs ``len(dataloader)``
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
        assert self._dp_wrapper is not None, (  # type guard for mypy
            "_activate_opacus_proxy must only be called when dp_wrapper is not None"
        )
        if torch is None or nn is None:  # pragma: no cover
            raise ImportError(
                "PyTorch is required for DP wrapping. "
                "Install it with: poetry install --with synthesizer"
            )

        dataloader, n_features = self._build_proxy_dataloader(processed_df)

        proxy_model = nn.Linear(n_features, 1)
        optimizer = torch.optim.Adam(proxy_model.parameters(), lr=1e-3)

        max_grad_norm: float = float(getattr(self._dp_wrapper, "max_grad_norm", 1.0))
        noise_multiplier: float = float(getattr(self._dp_wrapper, "noise_multiplier", 1.1))

        _logger.info(
            "DPCompatibleCTGAN: activating Opacus on proxy linear model "
            "(n_features=%d, max_grad_norm=%.2f, noise_multiplier=%.2f).",
            n_features,
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

        loss_fn = nn.MSELoss()
        proxy_model.train()

        _logger.info(
            "DPCompatibleCTGAN: running %d DP gradient steps for epsilon accounting.",
            len(dataloader),
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
                and runs the custom WGAN training loop.  On failure,
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

        # Use embedding_dim stored during _train_dp_discriminator (avoids
        # introspecting the Generator architecture which may use Residual blocks
        # without a .in_features attribute).
        embedding_dim = self._dp_embedding_dim

        with torch.no_grad():
            noise = torch.randn(num_rows, embedding_dim)
            fake_data = generator(noise)

        data_array = fake_data.detach().cpu().numpy()

        # Build output DataFrame with the correct numeric column names so that
        # DataProcessor.reverse_transform() can map back to the original schema.
        numeric_cols = self._dp_numeric_columns
        if numeric_cols and len(numeric_cols) == data_array.shape[1]:
            synthetic_numeric = pd.DataFrame(data_array, columns=numeric_cols)
        else:
            # Fallback: use integer indices if column count mismatches.
            synthetic_numeric = pd.DataFrame(
                data_array, columns=[str(i) for i in range(data_array.shape[1])]
            )
            return synthetic_numeric

        # If the processed DataFrame had non-numeric columns (e.g. object-typed
        # categorical columns that SDV has not yet one-hot-encoded), fill them
        # by sampling from the original processed rows so that reverse_transform
        # receives the correct column structure.
        ref_df = self._dp_processed_df_sample
        if ref_df is None:
            return synthetic_numeric
        non_numeric_cols = [c for c in ref_df.columns if c not in numeric_cols]
        if not non_numeric_cols:
            return synthetic_numeric

        # Sample non-numeric column values from the original processed rows
        # (with replacement) to fill the synthetic DataFrame.
        rng = np.random.default_rng(seed=None)
        idx = rng.integers(0, len(ref_df), size=num_rows)
        non_numeric_df = ref_df[non_numeric_cols].iloc[idx].reset_index(drop=True)
        full_df = pd.concat([synthetic_numeric, non_numeric_df], axis=1)
        # Reorder columns to match the original processed_df column order.
        ordered_cols = [c for c in ref_df.columns if c in full_df.columns]
        return full_df[ordered_cols]
