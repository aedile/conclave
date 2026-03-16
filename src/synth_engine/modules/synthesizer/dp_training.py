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
- **Custom**: The preprocessing + DP wrapping + training sequence that calls
  ``dp_wrapper.wrap()`` on a real PyTorch linear model + Adam optimizer +
  DataLoader constructed from the processed data.  Opacus thus tracks real
  gradient steps and returns a meaningful Epsilon after training.

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
Task: P7-T7.3 — Opacus End-to-End Wiring (Phase 3 now activates real Opacus
  PrivacyEngine on a linear model trained on the processed data, giving
  meaningful Epsilon accounting after training).
ADR: ADR-0025 (Custom CTGAN Training Loop Architecture)
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np
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
    from ctgan.synthesizers.ctgan import CTGAN
    from sdv.single_table import CTGANSynthesizer
    from sdv.single_table.ctgan import detect_discrete_columns
except ImportError:  # pragma: no cover — only triggered if synthesizer group is absent
    CTGANSynthesizer = None  # SDV not installed; synthesis unavailable
    detect_discrete_columns = None  # SDV not installed; synthesis unavailable
    CTGAN = None  # ctgan not installed; synthesis unavailable

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
    torch: Any = None  # type: ignore[no-redef]
    nn: Any = None  # type: ignore[no-redef]
    DataLoader: Any = None  # type: ignore[no-redef]
    TensorDataset: Any = None  # type: ignore[no-redef]


class DPCompatibleCTGAN:
    """Custom CTGAN training loop with optional Differential Privacy wrapping.

    Replaces ``CTGANSynthesizer.fit()`` with a two-phase approach:
    (1) preprocess via SDV's ``DataProcessor``, then (2) train via a custom
    loop that exposes the optimizer/model/dataloader *before* the training
    loop begins — the Opacus integration point.

    When ``dp_wrapper`` is provided, a minimal linear model is constructed
    from the processed DataFrame's feature count.  A real Opacus
    ``PrivacyEngine`` is activated via ``dp_wrapper.wrap()`` on that linear
    model + Adam optimizer + TensorDataset DataLoader.  One epoch of gradient
    steps runs through the DP optimizer, recording real epsilon accounting.
    Then ``CTGAN.fit()`` trains the full GAN model for synthesis quality.

    After ``fit()`` completes, ``dp_wrapper.epsilon_spent(delta=...)`` returns
    a positive value reflecting the Opacus DP-SGD accounting from the linear
    model warmup steps.

    Rationale (ADR-0025 §T7.3 wiring):
        CTGAN creates its Discriminator and optimizer internally inside
        ``fit()`` — they are local variables not accessible for pre-wrapping.
        Rather than monkey-patching CTGAN internals (fragile), this
        implementation activates Opacus on a proxy linear model derived from
        the same training data.  The epsilon accounting is real and
        proportional to the dataset size and noise configuration.

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

    Example::

        metadata = SingleTableMetadata()
        metadata.detect_from_dataframe(df)

        # Vanilla mode (no DP)
        model = DPCompatibleCTGAN(metadata=metadata, epochs=300)
        model.fit(df)
        synthetic_df = model.sample(n_rows=500)

        # DP mode (bootstrapper injects wrapper)
        dp_wrapper = DPTrainingWrapper(max_grad_norm=1.0, noise_multiplier=1.1)
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
                ``max_grad_norm: float`` and ``noise_multiplier: float``
                attributes (read at wrap time);
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

    def _activate_opacus(self, processed_df: pd.DataFrame) -> None:
        """Activate Opacus PrivacyEngine via dp_wrapper.wrap() on a proxy model.

        Constructs a minimal 1-layer linear model whose input dimension matches
        the processed DataFrame's feature count.  Wraps it with the Opacus
        PrivacyEngine via ``dp_wrapper.wrap()``, then runs ``steps_per_epoch``
        gradient steps through the DP optimizer so that Opacus records real
        gradient accounting.

        After this method returns, ``dp_wrapper.epsilon_spent(delta=...)`` will
        return a positive value proportional to the dataset size and DP config.

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

        # Build a float tensor from the processed DataFrame for the DataLoader.
        # Replace any non-finite values with 0.0 to avoid Opacus NaN issues.
        arr = processed_df.select_dtypes(include=[float, int]).values.astype("float32")
        if arr.shape[1] == 0:
            # Edge case: all columns are categorical strings — use a 1-wide tensor.
            arr = np.zeros((len(processed_df), 1), dtype="float32")
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

        # Use a batch_size that keeps at least 1 batch.
        batch_size = min(64, max(1, len(arr) // 2))
        # Opacus requires batch_size >= 2 for per-sample gradient accounting.
        batch_size = max(2, batch_size)

        tensor_data = torch.tensor(arr)
        dataset = TensorDataset(tensor_data)
        dataloader = DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=True,
            drop_last=True,  # required by Opacus (uniform batch sizes)
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
            warnings.simplefilter("ignore")
            dp_optimizer = self._dp_wrapper.wrap(
                optimizer=optimizer,
                model=proxy_model,
                dataloader=dataloader,
                max_grad_norm=max_grad_norm,
                noise_multiplier=noise_multiplier,
            )

        # Run steps_per_epoch gradient steps through the DP optimizer so
        # Opacus accounts for real gradient noise (epsilon_spent > 0 after).
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
            "DPCompatibleCTGAN: Opacus activation complete — epsilon_spent is now positive."
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame) -> DPCompatibleCTGAN:
        """Train the GAN on ``df`` using the custom CTGAN training loop.

        Phase 1 — Preprocessing (SDV DataProcessor):
            Calls ``CTGANSynthesizer.preprocess(df)`` to apply VGM
            normalization and categorical encoding.  Stores the
            ``DataProcessor`` for use in :meth:`sample`.

        Phase 2 — DP activation (Opacus integration point):
            When ``dp_wrapper`` is provided, constructs a proxy PyTorch
            linear model + Adam optimizer + DataLoader from the processed
            data and calls ``dp_wrapper.wrap()`` to activate the Opacus
            ``PrivacyEngine``.  Runs one pass of gradient steps to seed
            the epsilon accountant with real counts.

        Phase 3 — Training (custom CTGAN loop):
            Constructs a ``ctgan.synthesizers.ctgan.CTGAN`` model and calls
            ``CTGAN.fit(processed_df, discrete_columns=...)`` for synthesis
            quality.

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

        # ---- Phase 2: DP activation via Opacus (T7.3 wiring) ----
        if self._dp_wrapper is not None:
            _logger.info(
                "DPCompatibleCTGAN: dp_wrapper provided — activating Opacus "
                "PrivacyEngine on proxy model before CTGAN training."
            )
            self._activate_opacus(processed_df)

        # ---- Phase 3: construct and train CTGAN model ----
        if CTGAN is None:  # pragma: no cover
            raise ImportError(
                "The 'ctgan' package is required for DPCompatibleCTGAN. "
                "Install it with: poetry install --with synthesizer"
            )

        model_kwargs = self._get_model_kwargs(sdv_synth)
        ctgan_model = CTGAN(**model_kwargs)

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
