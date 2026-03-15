"""Data models for the synthesizer module.

Defines :class:`ModelArtifact`, a serialisable container for a trained
CTGAN model and the metadata required to validate and reproduce synthetic
output schema.

Design principles:
  - Pickle serialisation: consistent with SDV's own model persistence approach
    and avoids a hard dependency on torch.save (which would require the full
    torch runtime at load time even for metadata-only operations).
  - Metadata captured at train time: column names, dtypes, and nullable flags
    are stored in the artifact so that :meth:`SynthesisEngine.generate` can
    enforce schema consistency without re-reading the source Parquet file.

Task: P4-T4.2b — Synthesizer Core (SDV/CTGAN Integration)
ADR: ADR-0017 (CTGAN + Opacus; per-table training strategy)
"""

from __future__ import annotations

import logging
import pickle  # nosec B403 — pickle is used intentionally for self-produced ModelArtifact serialisation; not user-supplied data
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass
class ModelArtifact:
    """Serialisable container for a trained CTGAN model and its schema metadata.

    Attributes:
        table_name: Name of the source table this model was trained on.
        model: The trained CTGANSynthesizer instance (or any duck-typed
            synthesizer with a ``sample(num_rows)`` method).
        column_names: Ordered list of column names from the training DataFrame.
        column_dtypes: Mapping of column name to its pandas dtype string
            (e.g. ``{"id": "int64", "name": "object"}``).
        column_nullables: Mapping of column name to a boolean indicating
            whether the source column contained any null values during
            training (e.g. ``{"id": False, "opt_field": True}``).

    Example::

        engine = SynthesisEngine()
        artifact = engine.train("customers", "/tmp/customers.parquet")
        artifact.save("/artifacts/customers.pkl")

        loaded = ModelArtifact.load("/artifacts/customers.pkl")
        df = engine.generate(loaded, n_rows=500)
    """

    table_name: str
    model: Any  # CTGANSynthesizer or compatible duck-typed model
    column_names: list[str] = field(default_factory=list)
    column_dtypes: dict[str, str] = field(default_factory=dict)
    column_nullables: dict[str, bool] = field(default_factory=dict)

    def save(self, path: str) -> str:
        """Serialise the artifact to a pickle file.

        Uses Python's standard ``pickle`` module for portability.  The saved
        file can be loaded with :meth:`load` on any machine with the same
        Python and SDV version.

        Args:
            path: Filesystem path where the artifact will be written.
                Parent directories must already exist.

        Returns:
            The ``path`` argument unchanged, allowing callers to chain:
            ``saved_path = artifact.save(path)``.

        Raises:
            OSError: If the parent directory does not exist or write
                permission is denied.
        """
        with open(path, "wb") as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)
        _logger.info("ModelArtifact for table '%s' saved to %s", self.table_name, path)
        return path

    @classmethod
    def load(cls, path: str) -> ModelArtifact:
        """Deserialise a :class:`ModelArtifact` from a pickle file.

        Args:
            path: Filesystem path previously written by :meth:`save`.

        Returns:
            The deserialised :class:`ModelArtifact` instance.

        Raises:
            FileNotFoundError: If no file exists at ``path``.
            pickle.UnpicklingError: If the file is not a valid pickle or was
                produced by an incompatible version.
        """
        import os

        if not os.path.exists(path):
            raise FileNotFoundError(f"ModelArtifact file not found: {path}")

        with open(path, "rb") as f:
            artifact = pickle.load(f)  # noqa: S301  # nosec B301 — loading self-produced artifacts signed by this codebase; not user-supplied data
        _logger.info(
            "ModelArtifact for table '%s' loaded from %s",
            artifact.table_name,
            path,
        )
        return artifact  # type: ignore[no-any-return]  # pickle.load returns Any; artifact is ModelArtifact by convention
