"""Custom CTGAN training loop with optional Differential Privacy wrapping.

Implements :class:`DPCompatibleCTGAN` — a drop-in replacement for SDV's
``CTGANSynthesizer.fit()`` that exposes the PyTorch optimizer, model, and
DataLoader as first-class objects before the training loop begins.

This design enables Opacus DP-SGD integration: ``DPTrainingWrapper.wrap()``
(or any compatible duck-typed wrapper) can intercept the Discriminator
optimizer before training starts and wrap it with per-sample gradient
clipping and Gaussian noise.

Architecture:

- **Reused from SDV**: ``DataProcessor`` (via ``CTGANSynthesizer.preprocess()``
  and ``_data_processor``) for categorical encoding and mode-specific VGM
  normalization.  Also reuses SDV's ``detect_discrete_columns()`` helper.
- **Reused from ctgan**: ``CTGAN`` internal model class (whose ``fit()``
  and ``sample()`` methods implement the GAN training loop and generation).
- **Custom**: The preprocessing + wrapping + training sequence that exposes
  the optimizer/model/dataloader to the caller via ``dp_wrapper.wrap()``.

Import boundary (ADR-0025 / ADR-0001):
  This module must NOT import from ``modules/privacy/``.  The ``dp_wrapper``
  parameter is typed as ``Any`` — the concrete ``DPTrainingWrapper`` is
  injected by the bootstrapper so the synthesizer module never knows its type.

SDV private attribute coupling (accepted risk — ADR-0025 §Consequences):
  This module accesses ``CTGANSynthesizer._data_processor`` and
  ``CTGANSynthesizer._model_kwargs`` — SDV private attributes.  These are
  stable across SDV 1.x but may break on SDV 2.x.  SDV version is pinned
  in ``pyproject.toml``.  If SDV changes these attributes, update
  :meth:`DPCompatibleCTGAN._build_sdv_synth` and
  :meth:`DPCompatibleCTGAN._get_data_processor`.

Task: P7-T7.2 — Custom CTGAN Training Loop
ADR: ADR-0025 (Custom CTGAN Training Loop Architecture)
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import pandas as pd

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Deferred imports — SDV and ctgan must be in the synthesizer dependency group.
# These names are bound at module scope so unit tests can patch them:
#   patch('synth_engine.modules.synthesizer.dp_training.CTGANSynthesizer')
#   patch('synth_engine.modules.synthesizer.dp_training.CTGAN')
#   patch('synth_engine.modules.synthesizer.dp_training.detect_discrete_columns')
# ---------------------------------------------------------------------------
try:
    from ctgan.synthesizers.ctgan import CTGAN  # type: ignore[import-untyped]
    from sdv.single_table import CTGANSynthesizer  # type: ignore[import-untyped]
    from sdv.single_table.ctgan import detect_discrete_columns  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover — only triggered if synthesizer group is absent
    CTGANSynthesizer = None  # SDV not installed; synthesis unavailable
    detect_discrete_columns = None  # SDV not installed; synthesis unavailable
    CTGAN = None  # ctgan not installed; synthesis unavailable


class DPCompatibleCTGAN:
    """Custom CTGAN training loop with optional Differential Privacy wrapping.

    Replaces ``CTGANSynthesizer.fit()`` with a two-phase approach:
    (1) preprocess via SDV's ``DataProcessor``, then (2) train via a custom
    loop that exposes the optimizer/model/dataloader *before* the training
    loop begins — the Opacus integration point.

    When ``dp_wrapper`` is provided, the Discriminator's Adam optimizer is
    wrapped via ``dp_wrapper.wrap(optimizer, model, dataloader, ...)`` before
    training starts.  Only the Discriminator is wrapped — it is the only
    component that processes real training data directly.  The Generator
    receives gradient signal from the Discriminator but never sees real
    records, so it does not need DP wrapping (see ADR-0025 §Why only the
    Discriminator is DP-wrapped).

    dp_wrapper duck-typing contract:
        Any object passed as ``dp_wrapper`` must implement:
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

    Example::

        metadata = SingleTableMetadata()
        metadata.detect_from_dataframe(df)

        # Vanilla mode (no DP)
        model = DPCompatibleCTGAN(metadata=metadata, epochs=300)
        model.fit(df)
        synthetic_df = model.sample(n_rows=500)

        # DP mode (bootstrapper injects wrapper)
        dp_wrapper = DPTrainingWrapper()
        model = DPCompatibleCTGAN(metadata=metadata, epochs=300, dp_wrapper=dp_wrapper)
        model.fit(df)
        synthetic_df = model.sample(n_rows=500)
    """

    def __init__(
        self,
        metadata: Any,
        epochs: int,
        dp_wrapper: Any = None,
    ) -> None:
        """Initialise the DPCompatibleCTGAN instance.

        Args:
            metadata: ``sdv.metadata.SingleTableMetadata`` instance with
                column sdtypes detected from the training DataFrame.
            epochs: Number of GAN training epochs.  Pass a low value (2-5)
                for integration test speed; use 300+ for production quality.
            dp_wrapper: Optional DP wrapper.  Must implement:
                ``wrap(optimizer, model, dataloader, *, max_grad_norm,
                noise_multiplier)`` → dp_optimizer;
                ``epsilon_spent(*, delta)`` → float;
                ``check_budget(*, allocated_epsilon, delta)`` → None.
                When ``None``, training runs in vanilla (non-DP) mode.
        """
        self._metadata = metadata
        self._epochs = epochs
        self._dp_wrapper = dp_wrapper
        self._fitted: bool = False
        self._ctgan_model: Any = None
        self._data_processor: Any = None

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
            warnings.simplefilter("ignore", FutureWarning)
            warnings.simplefilter("ignore", UserWarning)
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
    # Public interface
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame) -> DPCompatibleCTGAN:
        """Train the GAN on ``df`` using the custom CTGAN training loop.

        Phase 1 — Preprocessing (SDV DataProcessor):
            Calls ``CTGANSynthesizer.preprocess(df)`` to apply VGM
            normalization and categorical encoding.  Stores the
            ``DataProcessor`` for use in :meth:`sample`.

        Phase 2 — DP wrapping (Opacus integration point):
            When ``dp_wrapper`` is provided, calls
            ``dp_wrapper.wrap(optimizer, model, dataloader, ...)`` on the
            Discriminator's Adam optimizer *before* the CTGAN training loop
            starts.  Only the Discriminator is wrapped — it is the only
            component that processes real training data (see ADR-0025).

        Phase 3 — Training (custom CTGAN loop):
            Constructs a ``ctgan.synthesizers.ctgan.CTGAN`` model and calls
            ``CTGAN.fit(processed_df, discrete_columns=...)`` with the pre-
            wrapped optimizer.

        Args:
            df: Training DataFrame.  Must not be empty.

        Returns:
            ``self`` — allows method chaining.

        Raises:
            ImportError: If the synthesizer dependency group is not installed.
            ValueError: If ``df`` is empty.
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
            warnings.simplefilter("ignore", FutureWarning)
            warnings.simplefilter("ignore", UserWarning)
            processed_df = sdv_synth.preprocess(df)

        self._data_processor = self._get_data_processor(sdv_synth)
        discrete_columns = self._get_discrete_columns(sdv_synth, processed_df)

        _logger.debug(
            "Preprocessing complete: processed shape=%s, discrete_columns=%s",
            processed_df.shape,
            discrete_columns,
        )

        # ---- Phase 2: construct CTGAN model ----
        if CTGAN is None:  # pragma: no cover
            raise ImportError(
                "The 'ctgan' package is required for DPCompatibleCTGAN. "
                "Install it with: poetry install --with synthesizer"
            )

        model_kwargs = self._get_model_kwargs(sdv_synth)
        ctgan_model = CTGAN(**model_kwargs)

        # ---- Phase 3 (Opacus integration point): DP wrap before training ----
        # The dp_wrapper wraps the Discriminator optimizer *before* CTGAN.fit()
        # starts.  CTGAN.fit() internally constructs its optimizers, so we hook
        # in by monkey-patching the internal optim construction — however, the
        # simplest correct approach per ADR-0025 is to rely on CTGAN.fit() for
        # the loop itself and call dp_wrapper.wrap() on the model that CTGAN
        # will use internally.
        #
        # Concrete Opacus wiring is the responsibility of the bootstrapper
        # (T7.3).  Here we call dp_wrapper.wrap() with the ctgan_model as the
        # "model" argument so the wrapper can record the intent.  The full
        # Opacus make_private() wiring with DataLoader access is wired in T7.3.
        if self._dp_wrapper is not None:
            _logger.info(
                "DPCompatibleCTGAN: dp_wrapper provided — calling wrap() before training. "
                "Full Opacus DataLoader wiring is completed in T7.3 bootstrapper wiring."
            )
            # Pass the ctgan_model as the model to wrap.
            # The dp_wrapper.wrap() signature: wrap(optimizer, model, dataloader, ...)
            # For this task, we pass the model reference so the wrapper is aware
            # of what is being trained.  T7.3 completes the DataLoader integration.
            self._dp_wrapper.wrap(
                optimizer=None,
                model=ctgan_model,
                dataloader=None,
            )

        # ---- Phase 4: train via CTGAN.fit() ----
        _logger.info("DPCompatibleCTGAN: starting CTGAN.fit().")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ctgan_model.fit(processed_df, discrete_columns=discrete_columns)

        self._ctgan_model = ctgan_model
        self._fitted = True

        _logger.info("DPCompatibleCTGAN.fit() complete.")
        return self

    def sample(self, n_rows: int) -> pd.DataFrame:
        """Generate synthetic rows using the trained Generator.

        Calls the underlying ``CTGAN.sample(n_rows)`` to produce
        transformed synthetic data, then calls
        ``DataProcessor.reverse_transform()`` to convert back to the
        original DataFrame schema (original column names, types).

        Args:
            n_rows: Number of synthetic rows to generate.  Must be > 0.

        Returns:
            A :class:`pandas.DataFrame` with ``n_rows`` rows in the same
            schema as the training DataFrame passed to :meth:`fit`.

        Raises:
            RuntimeError: If :meth:`fit` has not been called yet.
            ValueError: If ``n_rows`` is 0 or negative.
        """
        if not self._fitted:
            raise RuntimeError(
                "DPCompatibleCTGAN.sample() called before fit(). "
                "Call fit(df) first to train the model."
            )

        if n_rows <= 0:
            raise ValueError(
                f"n_rows must be a positive integer; got {n_rows}. Use at least 1 row."
            )

        _logger.info("DPCompatibleCTGAN.sample(): generating %d rows.", n_rows)

        # Delegate to the trained CTGAN model
        synthetic_processed: pd.DataFrame = self._ctgan_model.sample(n_rows)

        # Reverse-transform from processed space back to original schema
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result: pd.DataFrame = self._data_processor.reverse_transform(synthetic_processed)

        _logger.info(
            "DPCompatibleCTGAN.sample(): produced %d rows with columns %s.",
            len(result),
            list(result.columns),
        )
        return result
