"""Synthesis engine for per-table CTGAN training and FK post-processing.

Implements the three-step FK handling strategy from ADR-0017:
  1. Topological training order (caller responsibility — bootstrapper or task
     orchestrator feeds tables in parent-before-child order).
  2. FK column conditioning during training (FK column included as a feature
     column in the training data; CTGAN learns the FK distribution naturally).
  3. FK post-processing (orphan FK values resampled from synthetic parent PK set).

The synthesizer module is deliberately kept stateless: :class:`SynthesisEngine`
holds no database connections and no global state.  It reads from Parquet files
(written by T4.1 / subsetting engine) and returns DataFrames.

Boundary constraints (import-linter enforced):
  - Must NOT import from ``modules/ingestion/``, ``modules/masking/``, or
    ``modules/subsetting/``.
  - Cross-module data transfer uses Parquet files and ``shared/`` DTOs only.
  - Must NOT import from ``modules/privacy/`` — dp_wrapper is typed as ``Any``.
    The bootstrapper injects the concrete DPTrainingWrapper instance.

Task: P4-T4.2b — Synthesizer Core (SDV/CTGAN Integration)
Task: P7-T7.3 — Opacus End-to-End Wiring (routes to DPCompatibleCTGAN when
  dp_wrapper is provided; drains ADV-048).
ADR: ADR-0017 (CTGAN + Opacus; per-table training with FK post-processing)
ADR: ADR-0025 (Custom CTGAN Training Loop Architecture)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from synth_engine.modules.synthesizer.models import ModelArtifact

_logger = logging.getLogger(__name__)

#: Default number of training epochs.  300 is the SDV default for
#: CTGANSynthesizer; callers can override via SynthesisEngine(epochs=...).
_DEFAULT_EPOCHS: int = 300

# ---------------------------------------------------------------------------
# CTGANSynthesizer import — deferred to module-level try/except so that
# environments installing only the default dependency group do not encounter
# ModuleNotFoundError at import time.  The synthesizer group must be
# installed (`poetry install --with synthesizer`) for training.
#
# The name CTGANSynthesizer is bound at module scope so that unit tests can
# patch it with: patch('synth_engine.modules.synthesizer.engine.CTGANSynthesizer')
#
# SDV 1.x API change: CTGANSynthesizer(metadata, epochs=...) requires a
# SingleTableMetadata object.  _build_metadata() auto-detects schema from
# the training DataFrame using detect_from_dataframe().
# ---------------------------------------------------------------------------
try:
    from sdv.single_table import CTGANSynthesizer
except ImportError:  # pragma: no cover — only triggered if synthesizer group is absent
    CTGANSynthesizer = None  # nosec B604 — SDV not installed; synthesis unavailable

# ---------------------------------------------------------------------------
# DPCompatibleCTGAN import — intra-module import (modules/synthesizer/ only).
# Bound at module scope for unit-test patching:
#   patch('synth_engine.modules.synthesizer.engine.DPCompatibleCTGAN')
#
# When dp_wrapper is provided to SynthesisEngine.train(), DPCompatibleCTGAN
# is used instead of vanilla CTGANSynthesizer.  The dp_wrapper is passed
# through as a duck-typed argument — engine.py never imports from modules/privacy/.
# ---------------------------------------------------------------------------
try:
    from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN
except ImportError:  # pragma: no cover — only triggered if synthesizer group is absent
    DPCompatibleCTGAN: Any = None  # type: ignore[no-redef]  # SDV not installed


def _build_metadata(df: pd.DataFrame) -> Any:
    """Auto-detect SingleTableMetadata from a DataFrame.

    Uses ``sdv.metadata.SingleTableMetadata.detect_from_dataframe()`` to
    infer column sdtypes from the DataFrame schema.  This is SDV's preferred
    API for programmatic metadata construction.

    Args:
        df: Training DataFrame to detect schema from.

    Returns:
        A ``SingleTableMetadata`` instance with detected column sdtypes.

    Raises:
        ImportError: If ``sdv`` is not installed.
    """
    try:
        from sdv.metadata import SingleTableMetadata
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "The 'sdv' package is required for synthesis. "
            "Install it with: poetry install --with synthesizer"
        ) from exc
    metadata = SingleTableMetadata()
    metadata.detect_from_dataframe(df)
    return metadata


def apply_fk_post_processing(
    child_df: pd.DataFrame,
    fk_column: str,
    valid_parent_pks: set[Any],
    rng_seed: int = 0,
) -> pd.DataFrame:
    """Eliminate orphan FK values in a synthetic child table.

    Any FK value in ``child_df[fk_column]`` that is not present in
    ``valid_parent_pks`` is replaced by a value sampled uniformly from
    ``valid_parent_pks``.  Row count is preserved (no rows are dropped).

    This is Step 3 of the FK handling strategy in ADR-0017.

    Args:
        child_df: Synthetic child table output from
            :meth:`SynthesisEngine.generate`.
        fk_column: Name of the foreign-key column in ``child_df``.
        valid_parent_pks: Set of primary key values present in the synthetic
            parent table.  Every value in this set is a valid FK target.
        rng_seed: Seed for the NumPy RNG used to sample replacement values.
            Defaults to 0.  Pass an explicit seed to ensure reproducibility.

    Returns:
        A copy of ``child_df`` with all orphan FK values replaced.
        The original ``child_df`` is not mutated.

    Raises:
        ValueError: If ``valid_parent_pks`` is empty — there are no valid
            FK targets to resample from.
        KeyError: If ``fk_column`` does not exist in ``child_df``.
    """
    if not valid_parent_pks:
        raise ValueError(
            f"valid_parent_pks must be non-empty — cannot resample FK column "
            f"'{fk_column}' when the parent table has no rows."
        )

    if child_df.empty:
        return child_df.copy()

    rng = np.random.default_rng(rng_seed)
    parent_pk_list = sorted(valid_parent_pks)
    result = child_df.copy()

    orphan_mask = ~result[fk_column].isin(valid_parent_pks)
    orphan_count = int(orphan_mask.sum())

    if orphan_count > 0:
        replacement_values = rng.choice(parent_pk_list, size=orphan_count)
        result.loc[orphan_mask, fk_column] = replacement_values
        _logger.debug(
            "FK post-processing: replaced %d orphan value(s) in column '%s'.",
            orphan_count,
            fk_column,
        )

    return result


class SynthesisEngine:
    """Per-table CTGAN training and synthetic data generation engine.

    Trains one CTGANSynthesizer (or DPCompatibleCTGAN when dp_wrapper is
    provided) per table in topological order (parent tables before child
    tables).  FK post-processing via :func:`apply_fk_post_processing` ensures
    zero orphan FKs in output.

    The engine is stateless between calls: it holds no database connections,
    no global mutable state, and no cached models.  Models are encapsulated
    in :class:`~synth_engine.modules.synthesizer.models.ModelArtifact`
    instances returned from :meth:`train`.

    SDV API note: CTGANSynthesizer in SDV 1.x requires a ``SingleTableMetadata``
    object.  This engine auto-detects metadata from the training DataFrame using
    ``SingleTableMetadata.detect_from_dataframe()`` before each training run.

    DP routing note (T7.3 — ADV-048 drain):
        When ``dp_wrapper`` is supplied to :meth:`train`, the engine uses
        :class:`~synth_engine.modules.synthesizer.dp_training.DPCompatibleCTGAN`
        instead of the vanilla ``CTGANSynthesizer``.  The ``dp_wrapper`` is
        passed through to ``DPCompatibleCTGAN`` which calls
        ``dp_wrapper.wrap()`` to activate the Opacus PrivacyEngine before
        the training loop starts.  After training, ``dp_wrapper.epsilon_spent()``
        returns a positive value reflecting the DP-SGD accounting.

    Args:
        epochs: Number of CTGAN training epochs.  Defaults to 300 (SDV default).
            Use a lower value (e.g. 2-5) for fast integration-test runs.

    Example::

        engine = SynthesisEngine(epochs=300)

        # Train parent table first
        customers_artifact = engine.train(
            "customers", "/data/customers.parquet"
        )
        customers_df = engine.generate(customers_artifact, n_rows=500)

        # Get synthetic parent PKs before training child table
        parent_pks = set(customers_df["id"])

        # Train child table (FK column included as conditioning feature)
        orders_artifact = engine.train("orders", "/data/orders.parquet")
        orders_df = engine.generate(orders_artifact, n_rows=2000)

        # Eliminate orphan FK values
        orders_df = apply_fk_post_processing(
            child_df=orders_df,
            fk_column="customer_id",
            valid_parent_pks=parent_pks,
        )
    """

    def __init__(self, epochs: int = _DEFAULT_EPOCHS) -> None:
        """Initialise the engine with a configurable epoch count.

        Args:
            epochs: Number of CTGAN training epochs.  Lower values speed up
                tests; higher values improve synthetic data quality.
        """
        self._epochs = epochs

    def train(
        self,
        table_name: str,
        parquet_path: str,
        *,
        dp_wrapper: Any = None,
    ) -> ModelArtifact:
        """Train a CTGAN model on the Parquet file at ``parquet_path``.

        Routes to :class:`~synth_engine.modules.synthesizer.dp_training.DPCompatibleCTGAN`
        when ``dp_wrapper`` is provided, or vanilla ``CTGANSynthesizer`` otherwise.

        DP routing (T7.3 — drains ADV-048):
            When ``dp_wrapper`` is not ``None``, ``DPCompatibleCTGAN`` is used.
            ``DPCompatibleCTGAN.fit()`` calls ``dp_wrapper.wrap()`` before the
            training loop begins, activating Opacus DP-SGD.  After training,
            ``dp_wrapper.epsilon_spent(delta=...)`` returns the cumulative Epsilon.

        Vanilla path:
            When ``dp_wrapper`` is ``None``, the original ``CTGANSynthesizer``
            path is used unchanged.

        Args:
            table_name: Logical name of the source table (stored in the
                artifact for logging and identification).
            parquet_path: Absolute path to the Parquet file written by the
                subsetting/ingestion pipeline.
            dp_wrapper: Optional DP wrapper implementing the duck-type contract:
                ``wrap(optimizer, model, dataloader, *, max_grad_norm,
                noise_multiplier)`` → dp_optimizer;
                ``epsilon_spent(*, delta)`` → float;
                ``check_budget(*, allocated_epsilon, delta)`` → None.
                Typed as ``Any`` to avoid import-linter boundary violations
                between ``modules/synthesizer`` and ``modules/privacy``.
                The concrete implementation is ``DPTrainingWrapper`` from
                ``modules/privacy/dp_engine.py``, injected by the bootstrapper.
                Default: ``None`` (no DP wrapping; vanilla CTGANSynthesizer used).

        Returns:
            A :class:`ModelArtifact` containing the trained model, table name,
            and schema metadata (column names, dtypes, and nullable flags).

        Raises:
            FileNotFoundError: If the Parquet file does not exist at
                ``parquet_path``.
            ImportError: If the ``sdv`` package is not installed (synthesizer
                group not installed).
        """
        from synth_engine.modules.synthesizer.models import ModelArtifact

        if not os.path.exists(parquet_path):
            raise FileNotFoundError(
                f"Parquet file not found for table '{table_name}': {parquet_path}"
            )

        if CTGANSynthesizer is None:  # pragma: no cover
            raise ImportError(
                "The 'sdv' package is required for synthesis. "
                "Install it with: poetry install --with synthesizer"
            )

        _logger.info("Loading Parquet for table '%s' from %s", table_name, parquet_path)
        source_df = pd.read_parquet(parquet_path, engine="pyarrow")

        column_names = list(source_df.columns)
        column_dtypes = {col: str(source_df[col].dtype) for col in column_names}
        column_nullables = {col: bool(source_df[col].isnull().any()) for col in column_names}

        if dp_wrapper is not None:
            # DP path — use DPCompatibleCTGAN with Opacus wrapping (T7.3)
            if DPCompatibleCTGAN is None:  # pragma: no cover
                raise ImportError(
                    "The 'sdv' package is required for DP synthesis. "
                    "Install it with: poetry install --with synthesizer"
                )

            _logger.info(
                "Training DPCompatibleCTGAN on table '%s' (%d rows, %d cols, epochs=%d) "
                "with DP wrapper (max_grad_norm=%.2f, noise_multiplier=%.2f).",
                table_name,
                len(source_df),
                len(column_names),
                self._epochs,
                getattr(dp_wrapper, "max_grad_norm", float("nan")),
                getattr(dp_wrapper, "noise_multiplier", float("nan")),
            )

            metadata = _build_metadata(source_df)
            model = DPCompatibleCTGAN(
                metadata=metadata,
                epochs=self._epochs,
                dp_wrapper=dp_wrapper,
            )
            model.fit(source_df)

        else:
            # Vanilla path — use CTGANSynthesizer (unchanged from T4.2b)
            _logger.info(
                "Training CTGANSynthesizer on table '%s' (%d rows, %d cols, epochs=%d)",
                table_name,
                len(source_df),
                len(column_names),
                self._epochs,
            )

            metadata = _build_metadata(source_df)
            model = CTGANSynthesizer(metadata=metadata, epochs=self._epochs)
            model.fit(source_df)

        _logger.info("Training complete for table '%s'.", table_name)

        return ModelArtifact(
            table_name=table_name,
            model=model,
            column_names=column_names,
            column_dtypes=column_dtypes,
            column_nullables=column_nullables,
        )

    def generate(
        self,
        artifact: ModelArtifact,
        n_rows: int,
    ) -> pd.DataFrame:
        """Generate synthetic rows using a trained :class:`ModelArtifact`.

        Calls ``artifact.model.sample(num_rows=n_rows)`` and returns the
        resulting DataFrame.  The schema (column names, dtypes) of the output
        matches the source table captured in the artifact at train time.

        Note: FK integrity is NOT enforced here.  Use
        :func:`apply_fk_post_processing` after calling this method if the
        table has FK columns referencing a parent synthetic table.

        Args:
            artifact: Trained :class:`ModelArtifact` from :meth:`train`.
            n_rows: Number of synthetic rows to generate.  Must be > 0.

        Returns:
            A :class:`pandas.DataFrame` with ``n_rows`` rows and the same
            schema as the source table.

        Raises:
            ValueError: If ``n_rows`` is 0 or negative.
        """
        if n_rows <= 0:
            raise ValueError(
                f"n_rows must be a positive integer; got {n_rows}. Use at least 1 row."
            )

        _logger.info(
            "Generating %d synthetic rows for table '%s'.",
            n_rows,
            artifact.table_name,
        )
        result: pd.DataFrame = artifact.model.sample(num_rows=n_rows)
        _logger.info(
            "Generation complete for table '%s': %d rows produced.",
            artifact.table_name,
            len(result),
        )
        return result
