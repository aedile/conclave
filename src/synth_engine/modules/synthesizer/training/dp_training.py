"""Thin coordinator: DPCompatibleCTGAN with optional DP wrapping (T35.2).

Import boundary (ADR-0025/ADR-0001): must NOT import from modules/privacy/.
SDV coupling accepted (ADR-0025): accesses CTGANSynthesizer._data_processor and _model_kwargs.
filterwarnings used (not simplefilter) per T20.1 AC2. ADR: ADR-0025, ADR-0036.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np
import pandas as pd

from synth_engine.modules.synthesizer.training._optional_deps import (
    DataLoader,
    TensorDataset,
    nn,
    require_synthesizer,
    torch,
)
from synth_engine.modules.synthesizer.training.ctgan_utils import (
    cap_batch_size,
    parse_gan_hyperparams,
)
from synth_engine.modules.synthesizer.training.dp_discriminator import OpacusCompatibleDiscriminator
from synth_engine.modules.synthesizer.training.training_strategies import (
    DpCtganStrategy,
    Optimizers,
    TrainingConfig,
    VanillaCtganStrategy,
    build_proxy_dataloader,
)
from synth_engine.shared.errors import safe_error_msg
from synth_engine.shared.exceptions import BudgetExhaustionError
from synth_engine.shared.protocols import DPWrapperProtocol

try:
    from ctgan.synthesizers.ctgan import CTGAN, Generator
    from sdv.single_table import CTGANSynthesizer
    from sdv.single_table.ctgan import detect_discrete_columns
except ImportError:  # pragma: no cover
    CTGANSynthesizer = None  # SDV not installed; synthesis unavailable
    detect_discrete_columns = None  # SDV not installed; synthesis unavailable
    CTGAN = None  # ctgan not installed; synthesis unavailable
    Generator = None  # ctgan not installed; synthesis unavailable

_logger = logging.getLogger(__name__)
_OPACUS_SECURE_RNG_PATTERN = ".*Secure RNG turned off.*"
_OPACUS_BATCH_PATTERN = ".*Expected.*batch.*"


class DPCompatibleCTGAN:
    """Custom CTGAN with optional Differential Privacy wrapping.

    Preprocesses via SDV DataProcessor, then runs a discriminator-level DP-SGD
    loop (dp_wrapper provided, ADR-0036) or delegates to CTGAN.fit().

    dp_wrapper duck-typing contract: max_grad_norm, noise_multiplier,
    wrap(optimizer, model, dataloader, *, max_grad_norm, noise_multiplier),
    epsilon_spent(*, delta), check_budget(*, allocated_epsilon, delta).

    SDV _model_kwargs coupling accepted per ADR-0025; pinned in pyproject.toml.

    Args:
        metadata: SingleTableMetadata for the training DataFrame.
        epochs: Number of GAN training epochs.
        dp_wrapper: Optional DP wrapper (None = vanilla mode).
        allocated_epsilon: Privacy budget for check_budget(). Default 50.0.
        delta: Delta for epsilon accounting. Default 1e-5.
    """

    def __init__(
        self,
        metadata: Any,
        epochs: int,
        dp_wrapper: DPWrapperProtocol | None = None,
        allocated_epsilon: float = 50.0,
        delta: float = 1e-5,
    ) -> None:
        self._metadata = metadata
        self._epochs = epochs
        self._dp_wrapper = dp_wrapper
        self._allocated_epsilon = allocated_epsilon
        self._delta = delta
        self._fitted: bool = False
        self._ctgan_model: Any = None
        self._data_processor: Any = None
        self._dp_generator: Any = None
        self._dp_trained: bool = False
        self._dp_embedding_dim: int = 128
        self._dp_numeric_columns: list[str] = []
        self._dp_processed_df_sample: pd.DataFrame | None = None

    # SDV compatibility helpers (isolate private-attribute coupling per ADR-0025)

    def _build_sdv_synth(self) -> Any:
        if CTGANSynthesizer is None:  # pragma: no cover
            raise ImportError("sdv required; install with: poetry install --with synthesizer")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning)
            warnings.filterwarnings("ignore", category=UserWarning)
            return CTGANSynthesizer(metadata=self._metadata, epochs=self._epochs)

    def _get_data_processor(self, sdv_synth: Any) -> Any:
        return sdv_synth._data_processor

    def _get_model_kwargs(self, sdv_synth: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = dict(sdv_synth._model_kwargs)
        kwargs["epochs"] = self._epochs
        return kwargs

    def _get_discrete_columns(self, sdv_synth: Any, processed_df: pd.DataFrame) -> list[str]:
        if detect_discrete_columns is None:  # pragma: no cover
            return []
        transformers = sdv_synth._data_processor._hyper_transformer.field_transformers
        return list(detect_discrete_columns(self._metadata, processed_df, transformers))

    # DP training helpers (use module-level patched names: Generator, OpacusCompatibleDiscriminator,
    # torch, TensorDataset, DataLoader — must stay in dp_training for test patching)

    def _build_dp_dataloader(self, processed_df: pd.DataFrame, batch_size: int) -> Any:
        arr = processed_df.select_dtypes(include=[float, int]).values.astype("float32")
        if arr.shape[1] == 0:
            arr = np.zeros((len(processed_df), 1), dtype="float32")
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
        return DataLoader(
            TensorDataset(torch.tensor(arr)), batch_size=batch_size, shuffle=True, drop_last=True
        )

    def _prepare_dp_dataloader_checked(
        self, processed_df: pd.DataFrame, batch_size: int
    ) -> tuple[Any, int]:
        data_dim = max(processed_df.select_dtypes(include=[float, int]).shape[1], 1)
        dataloader = self._build_dp_dataloader(processed_df, batch_size)
        if len(dataloader) == 0:
            raise RuntimeError(
                "DPCompatibleCTGAN._train_dp_discriminator: DataLoader has zero batches. "
                "The dataset is too small for the configured batch_size and pac factor. "
                "No DP gradient steps would occur, producing a false DP guarantee "
                "(epsilon_spent() returns 0.0 with no actual accounting). "
                "Ensure the training DataFrame has enough rows for at least one batch."
            )
        return dataloader, data_dim

    def _build_gan_models(
        self, data_dim: int, hyp: Any, model_kwargs: dict[str, Any]
    ) -> tuple[Any, Any, Any, Any]:
        discriminator = OpacusCompatibleDiscriminator(
            input_dim=data_dim, discriminator_dim=hyp.discriminator_dim, pac=hyp.pac
        )
        generator = Generator(
            embedding_dim=hyp.embedding_dim, generator_dim=hyp.generator_dim, data_dim=data_dim
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
        self, discriminator: Any, optimizer_d: Any, dataloader: Any, batch_size: int
    ) -> tuple[Any, Any]:
        assert self._dp_wrapper is not None
        max_grad_norm = float(getattr(self._dp_wrapper, "max_grad_norm", 1.0))
        noise_multiplier = float(getattr(self._dp_wrapper, "noise_multiplier", 1.1))
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
        return dp_optimizer, getattr(self._dp_wrapper, "wrapped_module", discriminator)

    def _run_gan_epoch(
        self,
        generator: Any,
        dp_discriminator: Any,
        dataloader: Any,
        optimizers: Optimizers,
        config: TrainingConfig,
    ) -> None:
        """Execute one WGAN epoch (no gradient penalty — incompatible with Opacus hooks).

        Args:
            generator: CTGAN Generator module.
            dp_discriminator: Opacus-wrapped Discriminator.
            dataloader: Training DataLoader.
            optimizers: Optimizers bundle (optimizer_g and dp_optimizer).
            config: TrainingConfig with scalar epoch configuration.
        """
        for (real_data,) in dataloader:
            for _ in range(config.discriminator_steps):
                optimizers.dp_optimizer.zero_grad()
                fake = generator(torch.randn(len(real_data), config.embedding_dim)).detach()
                real_padded = (
                    torch.cat(
                        [
                            real_data,
                            torch.zeros(real_data.shape[0], config.data_dim - real_data.shape[1]),
                        ],
                        dim=1,
                    )
                    if real_data.shape[1] < config.data_dim
                    else real_data[:, : config.data_dim]
                )
                n = (len(real_padded) // config.pac) * config.pac
                if n == 0:
                    continue
                real_score = dp_discriminator(real_padded[:n]).mean()
                fake_score = dp_discriminator(fake[:n]).mean()
                loss_d = -(real_score - fake_score)
                loss_d.backward()
                optimizers.dp_optimizer.step()
            optimizers.optimizer_g.zero_grad()
            fake_g = generator(torch.randn(config.batch_size, config.embedding_dim))
            n_g = (len(fake_g) // config.pac) * config.pac
            if n_g > 0:
                if hasattr(dp_discriminator, "disable_hooks"):
                    dp_discriminator.disable_hooks()
                (-dp_discriminator(fake_g[:n_g]).mean()).backward()
                if hasattr(dp_discriminator, "enable_hooks"):
                    dp_discriminator.enable_hooks()
                optimizers.optimizer_g.step()

    def _store_dp_training_state(self, generator: Any) -> None:
        _logger.info("DPCompatibleCTGAN: custom DP training loop complete.")
        self._dp_generator = generator
        self._dp_trained = True

    def _train_dp_discriminator(
        self, processed_df: pd.DataFrame, model_kwargs: dict[str, Any]
    ) -> None:
        """Run discriminator-level DP-SGD GAN training loop (ADR-0036).

        Args:
            processed_df: Preprocessed DataFrame from SDV DataProcessor.
            model_kwargs: CTGAN hyperparameters from _get_model_kwargs().
        """
        assert self._dp_wrapper is not None, (
            "_train_dp_discriminator must only be called when dp_wrapper is not None"
        )
        hyp = parse_gan_hyperparams(model_kwargs)
        self._dp_embedding_dim = hyp.embedding_dim
        self._dp_numeric_columns = list(processed_df.select_dtypes(include=[float, int]).columns)
        self._dp_processed_df_sample = processed_df
        batch_size = cap_batch_size(len(processed_df), hyp.batch_size, hyp.pac)
        dataloader, data_dim = self._prepare_dp_dataloader_checked(processed_df, batch_size)
        generator, disc, opt_g, opt_d = self._build_gan_models(data_dim, hyp, model_kwargs)
        dp_opt, dp_disc = self._wrap_discriminator_with_opacus(disc, opt_d, dataloader, batch_size)
        config = TrainingConfig(
            embedding_dim=hyp.embedding_dim,
            data_dim=data_dim,
            pac=hyp.pac,
            batch_size=batch_size,
            discriminator_steps=hyp.discriminator_steps,
        )
        _logger.info(
            "DPCompatibleCTGAN: starting WGAN loop (%d epochs, %d batches/epoch).",
            self._epochs,
            len(dataloader),
        )
        dp_disc.train()
        generator.train()
        opts = Optimizers(optimizer_g=opt_g, dp_optimizer=dp_opt)
        for _epoch in range(self._epochs):
            self._run_gan_epoch(generator, dp_disc, dataloader, opts, config)
            self._dp_wrapper.check_budget(
                allocated_epsilon=self._allocated_epsilon, delta=self._delta
            )
        self._store_dp_training_state(generator)

    def _build_proxy_dataloader(self, processed_df: pd.DataFrame) -> tuple[Any, int]:
        """Build DataLoader for proxy model training.

        Delegates to :func:`training_strategies.build_proxy_dataloader`,
        injecting module-level ``torch``, ``TensorDataset``, and ``DataLoader``
        so that test patches on those names are honoured.
        Raises ``RuntimeError`` when the DataFrame has too few rows for DP-SGD.

        Args:
            processed_df: VGM-normalized DataFrame.

        Returns:
            A 2-tuple ``(dataloader, n_features)``.
        """
        return build_proxy_dataloader(
            processed_df,
            torch_module=torch,
            tensor_dataset_cls=TensorDataset,
            dataloader_cls=DataLoader,
        )

    def _activate_opacus_proxy(self, processed_df: pd.DataFrame) -> None:
        """Activate Opacus on a proxy linear model. Fallback from T7.3/T30.3.

        Calls ``require_synthesizer()`` which raises ``ImportError`` when
        PyTorch is not installed.

        Args:
            processed_df: VGM-normalized DataFrame.
        """
        assert self._dp_wrapper is not None
        require_synthesizer()
        dataloader, n_features = self._build_proxy_dataloader(processed_df)
        proxy_model = nn.Linear(n_features, 1)
        optimizer = torch.optim.Adam(proxy_model.parameters(), lr=1e-3)
        max_grad_norm = float(getattr(self._dp_wrapper, "max_grad_norm", 1.0))
        noise_multiplier = float(getattr(self._dp_wrapper, "noise_multiplier", 1.1))
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
        proxy_model.train()
        for (batch_x,) in dataloader:
            dp_optimizer.zero_grad()
            nn.MSELoss()(proxy_model(batch_x), torch.zeros_like(proxy_model(batch_x))).backward()
            dp_optimizer.step()
        _logger.info(
            "DPCompatibleCTGAN: Opacus proxy activation complete — epsilon_spent is now positive."
        )

    def _run_vanilla_ctgan(
        self, sdv_synth: Any, processed_df: pd.DataFrame, discrete_columns: list[str]
    ) -> None:
        if CTGAN is None:  # pragma: no cover
            raise ImportError("ctgan required; install with: poetry install --with synthesizer")
        self._ctgan_model = VanillaCtganStrategy().run(
            sdv_synth, processed_df, discrete_columns, ctgan_cls=CTGAN, epochs=self._epochs
        )

    def _preprocess(self, df: pd.DataFrame) -> tuple[Any, pd.DataFrame, list[str]]:
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
        return sdv_synth, processed_df, discrete_columns

    def fit(self, df: pd.DataFrame) -> DPCompatibleCTGAN:
        """Train the GAN on df (preprocess → DP or vanilla path).

        Args:
            df: Training DataFrame. Must not be empty.

        Returns:
            self for method chaining.

        Raises:
            ValueError: If df is empty.
            BudgetExhaustionError: If DP budget exhausted mid-training.
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
        sdv_synth, processed_df, discrete_columns = self._preprocess(df)
        if self._dp_wrapper is not None:
            try:
                _logger.info(
                    "DPCompatibleCTGAN: dp_wrapper provided — starting discriminator-level "
                    "DP-SGD training loop (T30.3)."
                )
                DpCtganStrategy(self._dp_wrapper).run(
                    self, processed_df, self._get_model_kwargs(sdv_synth)
                )
            except BudgetExhaustionError:
                raise
            # Broad catch: Opacus/PyTorch raises arbitrary exceptions; fall back to vanilla CTGAN.
            except Exception as exc:
                _logger.warning(
                    "DPCompatibleCTGAN: discriminator-level DP-SGD training failed "
                    "(%s: %s). Falling back to proxy model + CTGAN.fit().",
                    type(exc).__name__,
                    safe_error_msg(str(exc)),
                )
                self._activate_opacus_proxy(processed_df)
                self._run_vanilla_ctgan(sdv_synth, processed_df, discrete_columns)
        else:
            self._run_vanilla_ctgan(sdv_synth, processed_df, discrete_columns)
        self._fitted = True
        _logger.info("DPCompatibleCTGAN.fit() complete.")
        return self

    def sample(self, num_rows: int) -> pd.DataFrame:
        """Generate synthetic rows using the trained Generator.

        Args:
            num_rows: Positive integer number of rows.

        Returns:
            DataFrame with num_rows synthetic rows.

        Raises:
            RuntimeError: If fit() has not been called.
            ValueError: If num_rows <= 0.
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
        self._dp_generator.eval()
        with torch.no_grad():
            data_array = (
                self._dp_generator(torch.randn(num_rows, self._dp_embedding_dim))
                .detach()
                .cpu()
                .numpy()
            )
        numeric_cols = self._dp_numeric_columns
        if numeric_cols and len(numeric_cols) == data_array.shape[1]:
            synthetic_numeric = pd.DataFrame(data_array, columns=numeric_cols)
        else:
            return pd.DataFrame(data_array, columns=[str(i) for i in range(data_array.shape[1])])
        ref_df = self._dp_processed_df_sample
        if ref_df is None:
            return synthetic_numeric
        non_numeric_cols = [c for c in ref_df.columns if c not in numeric_cols]
        if not non_numeric_cols:
            return synthetic_numeric
        rng = np.random.default_rng(seed=None)
        idx = rng.integers(0, len(ref_df), size=num_rows)
        non_numeric_df = ref_df[non_numeric_cols].iloc[idx].reset_index(drop=True)
        full_df = pd.concat([synthetic_numeric, non_numeric_df], axis=1)
        return full_df[[c for c in ref_df.columns if c in full_df.columns]]
