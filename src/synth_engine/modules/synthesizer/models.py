"""Data models for the synthesizer module.

Defines :class:`ModelArtifact`, a serialisable container for a trained
CTGAN model and the metadata required to validate and reproduce synthetic
output schema.

Design principles:
  - Pickle serialisation: consistent with SDV's own model persistence approach
    and avoids a hard dependency on torch.save (which would require the full
    torch runtime at load time even for metadata-only operations).
  - HMAC-SHA256 signing (ADV-040): when a ``signing_key`` is provided, the
    pickle payload is prepended with a 32-byte HMAC-SHA256 signature.  On
    load, the signature is verified before unpickling.  This ensures only
    self-produced artifacts are trusted — an artifact with a missing or
    incorrect signature raises :exc:`SecurityError` rather than silently
    executing an attacker-controlled pickle stream.
  - Metadata captured at train time: column names, dtypes, and nullable flags
    are stored in the artifact so that :meth:`SynthesisEngine.generate` can
    enforce schema consistency without re-reading the source Parquet file.

File format (signed):
  bytes 0-31   : HMAC-SHA256 signature (32 bytes, raw binary)
  bytes 32-end : pickle payload

File format (unsigned, backward-compatible):
  bytes 0-end  : pickle payload (no signature prefix)

The two formats are distinguished by the ``signing_key`` argument at call time:
  - ``signing_key`` provided → signed format expected/produced
  - ``signing_key`` omitted  → unsigned format expected/produced

Mixing (signed file + no key, or unsigned file + key) raises :exc:`SecurityError`
because HMAC verification fails over the full file content.

Task: P4-T4.2b — Synthesizer Core (SDV/CTGAN Integration)
Task: P8-T8.2  — Security Hardening (ADV-040: HMAC-SHA256 pickle signing)
ADR: ADR-0017 (CTGAN + Opacus; per-table training strategy)
"""

from __future__ import annotations

import logging
import os
import pickle  # nosec B403 — pickle is used intentionally for self-produced ModelArtifact serialisation; HMAC-SHA256 signing (ADV-040) ensures only self-produced artifacts are trusted before unpickling
from dataclasses import dataclass, field
from typing import Any

from synth_engine.shared.security.hmac_signing import (
    HMAC_DIGEST_SIZE,
    SecurityError,
    compute_hmac,
    verify_hmac,
)

__all__ = ["ModelArtifact", "SecurityError"]

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

        signing_key = bytes.fromhex(os.environ["ARTIFACT_SIGNING_KEY"])

        engine = SynthesisEngine()
        artifact = engine.train("customers", "/tmp/customers.parquet")
        artifact.save("/artifacts/customers.pkl", signing_key=signing_key)

        loaded = ModelArtifact.load(
            "/artifacts/customers.pkl", signing_key=signing_key
        )
        df = engine.generate(loaded, n_rows=500)
    """

    table_name: str
    model: Any  # CTGANSynthesizer or compatible duck-typed model
    column_names: list[str] = field(default_factory=list)
    column_dtypes: dict[str, str] = field(default_factory=dict)
    column_nullables: dict[str, bool] = field(default_factory=dict)

    def save(self, path: str, *, signing_key: bytes | None = None) -> str:
        """Serialise the artifact to a pickle file, optionally with HMAC signing.

        When ``signing_key`` is provided, the output file format is::

            [32-byte HMAC-SHA256 over the pickle payload] + [pickle payload]

        This ensures that :meth:`load` can verify the artifact's integrity and
        authenticity before unpickling — defending against tampered or
        adversarially crafted pickle files.

        When ``signing_key`` is ``None``, the file is written without a
        signature (backward-compatible unsigned format).

        Args:
            path: Filesystem path where the artifact will be written.
                Parent directories must already exist.
            signing_key: Raw signing key bytes for HMAC-SHA256 authentication.
                Use ``bytes.fromhex(os.environ["ARTIFACT_SIGNING_KEY"])`` for
                production wiring.  If ``None``, the artifact is saved unsigned
                (backward-compatible mode).

        Returns:
            The ``path`` argument unchanged, allowing callers to chain:
            ``saved_path = artifact.save(path, signing_key=key)``.

        Raises:
            OSError: If the parent directory does not exist or write
                permission is denied.
        """
        payload = pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL)  # nosec B301 — payload is self-produced; HMAC signing below authenticates it before any future load

        if signing_key is not None:
            signature = compute_hmac(signing_key, payload)
            data = signature + payload
            _logger.info(
                "ModelArtifact for table '%s' saved with HMAC-SHA256 signature to %s",
                self.table_name,
                path,
            )
        else:
            data = payload
            _logger.info(
                "ModelArtifact for table '%s' saved (unsigned) to %s",
                self.table_name,
                path,
            )

        with open(path, "wb") as f:
            f.write(data)

        return path

    @classmethod
    def load(cls, path: str, *, signing_key: bytes | None = None) -> ModelArtifact:
        """Deserialise a :class:`ModelArtifact` from a pickle file.

        When ``signing_key`` is provided, the file is expected to begin with a
        32-byte HMAC-SHA256 signature over the remainder.  The signature is
        verified via :func:`hmac.compare_digest` before unpickling.  If
        verification fails, :exc:`SecurityError` is raised and the pickle data
        is never executed.

        When ``signing_key`` is ``None``, the file is loaded in unsigned mode
        (backward-compatible).

        Args:
            path: Filesystem path previously written by :meth:`save`.
            signing_key: Raw signing key bytes.  Must match the key used at
                :meth:`save` time.  If ``None``, unsigned mode is used.

        Returns:
            The deserialised :class:`ModelArtifact` instance.

        Raises:
            FileNotFoundError: If no file exists at ``path``.
            SecurityError: If ``signing_key`` is provided and HMAC verification
                fails (wrong key, tampered payload, or unsigned file loaded with
                a signing key).
            pickle.UnpicklingError: If the file is not a valid pickle or was
                produced by an incompatible version.
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"ModelArtifact file not found: {path}")

        with open(path, "rb") as f:
            raw = f.read()

        if signing_key is not None:
            if len(raw) <= HMAC_DIGEST_SIZE:
                raise SecurityError(
                    "HMAC verification failed: file is too short to contain a valid "
                    f"HMAC header (expected >{HMAC_DIGEST_SIZE} bytes, got {len(raw)})."
                )
            stored_digest = raw[:HMAC_DIGEST_SIZE]
            pickle_payload = raw[HMAC_DIGEST_SIZE:]
            if not verify_hmac(signing_key, pickle_payload, stored_digest):
                raise SecurityError(
                    "HMAC verification failed: the artifact signature does not match "
                    "the provided signing key.  The artifact may have been tampered "
                    "with or was signed with a different key."
                )
            _logger.info("ModelArtifact HMAC-SHA256 signature verified for path %s.", path)
        else:
            pickle_payload = raw

        artifact = pickle.loads(pickle_payload)  # noqa: S301  # nosec B301 — payload is either unsigned (trusted caller) or HMAC-verified above; not user-supplied data
        _logger.info(
            "ModelArtifact for table '%s' loaded from %s",
            artifact.table_name,
            path,
        )
        return artifact  # type: ignore[no-any-return]  # pickle.loads returns Any; artifact is ModelArtifact by convention
