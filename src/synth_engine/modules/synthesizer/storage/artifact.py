"""ModelArtifact — serialisable container for a trained CTGAN model.

Defines :class:`ModelArtifact`, helper functions ``_detect_signed_format``,
``_validate_signing_key``, ``_log_verification_failure``, and the Prometheus
counter ``ARTIFACT_VERIFICATION_FAILURE_TOTAL``.

Design principles
-----------------
- Pickle serialisation is consistent with SDV's own model persistence approach.
- HMAC-SHA256 signing (ADV-040): the pickle payload is prepended with a 32-byte
  HMAC-SHA256 signature when ``signing_key`` is provided.  On load, the signature
  is verified before unpickling so only self-produced artifacts are trusted.
- Restricted unpickling (T55.2): :class:`RestrictedUnpickler` replaces bare
  ``pickle.loads``.  Defense-in-depth: HMAC verification runs first, then class
  filtering.

File format (signed):
  bytes 0-31   : HMAC-SHA256 signature (32 bytes, raw binary)
  bytes 32-end : pickle payload

File format (unsigned, backward-compatible):
  bytes 0-end  : pickle payload (no signature prefix)

Task: P4-T4.2b — Synthesizer Core (SDV/CTGAN Integration)
Task: P8-T8.2  — Security Hardening (ADV-040: HMAC-SHA256 pickle signing)
Task: T47.6    — Harden Model Artifact Signature Verification
Task: T50.4    — Pickle TOCTOU Mitigation (ADV-P47-07: bounded read, no pre-stat)
Task: T55.2    — Replace Pickle with Safe Serialization (Restricted Unpickler)
Task: T58.4    — Split models.py into artifact/unpickler
ADR: ADR-0017 (CTGAN + Opacus; per-table training strategy)
ADR: ADR-0055 (Restricted Unpickler for ModelArtifact deserialization)
"""

from __future__ import annotations

import logging
import pickle  # nosec B403 — pickle is used intentionally for self-produced ModelArtifact serialisation; HMAC-SHA256 signing (ADV-040) + RestrictedUnpickler (T55.2) ensure only safe artifacts are deserialized
from dataclasses import dataclass, field

from prometheus_client import Counter

from synth_engine.modules.synthesizer.storage.restricted_unpickler import (
    RestrictedUnpickler,
    SynthesizerModel,
)
from synth_engine.shared.exceptions import ArtifactTamperingError
from synth_engine.shared.security.hmac_signing import (
    HMAC_DIGEST_SIZE,
    SecurityError,
    compute_hmac,
    verify_hmac,
)

__all__ = [
    "ARTIFACT_VERIFICATION_FAILURE_TOTAL",
    "ModelArtifact",
    "SecurityError",
    "_detect_signed_format",
    "_log_verification_failure",
    "_validate_signing_key",
]

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ADV-P55-04 — Prometheus counter for artifact verification failures.
# Incremented in _log_verification_failure() for every failed HMAC or
# tamper-detection check on a ModelArtifact.
# ---------------------------------------------------------------------------
ARTIFACT_VERIFICATION_FAILURE_TOTAL: Counter = Counter(
    "artifact_verification_failure_total",
    "Total number of ModelArtifact signature verification failures "
    "(HMAC mismatch, tampered payload, or invalid format).",
)

#: Pickle protocol-2+ opcode byte (``0x80``).  All artifacts produced by
#: :meth:`ModelArtifact.save` use ``pickle.HIGHEST_PROTOCOL`` (≥ 2), so a
#: valid unsigned payload always begins with this byte.
_PICKLE_OPCODE: int = 0x80

#: Maximum permitted artifact file size in bytes (2 GiB).
#: Files exceeding this limit are rejected before reading to prevent
#: memory exhaustion attacks via crafted oversized artifacts.
_MAX_ARTIFACT_SIZE_BYTES: int = 2 * 1024 * 1024 * 1024

#: Minimum signing key length in bytes.
#: Keys shorter than 32 bytes provide insufficient security strength
#: for HMAC-SHA256 and are rejected at the API boundary.
_MIN_SIGNING_KEY_BYTES: int = 32


def _detect_signed_format(raw: bytes) -> bool:
    """Heuristically detect whether *raw* is a signed (HMAC-prefixed) artifact.

    Format detector — NOT a security check. Used to produce better error
    messages when operators omit their signing key.

    A signed file has 32 bytes of raw HMAC digest followed by a pickle payload
    that starts with the pickle protocol-2+ opcode (``0x80``).  An unsigned
    file starts directly with the opcode.

    This check is deliberately conservative: it only returns ``True`` when the
    evidence is unambiguous (byte 0 is not a pickle opcode AND byte 32 is the
    pickle opcode AND the file is long enough to contain both parts).

    Args:
        raw: The full raw bytes read from the artifact file.

    Returns:
        ``True`` if the file appears to be a signed artifact, ``False``
        otherwise.
    """
    return (
        len(raw) > HMAC_DIGEST_SIZE
        and raw[0] != _PICKLE_OPCODE
        and raw[HMAC_DIGEST_SIZE] == _PICKLE_OPCODE
    )


def _validate_signing_key(signing_key: bytes, *, context: str) -> None:
    """Validate a signing key's presence and minimum length.

    Args:
        signing_key: The signing key bytes to validate.
        context: Human-readable label for error messages (e.g. ``"save"``).

    Raises:
        ValueError: If the key is empty or shorter than
            :data:`_MIN_SIGNING_KEY_BYTES`.
    """
    if len(signing_key) == 0:
        raise ValueError(
            "signing_key must not be empty. "
            f"Provide a key of at least {_MIN_SIGNING_KEY_BYTES} bytes or pass "
            f"signing_key=None to {context} an unsigned artifact."
        )
    if len(signing_key) < _MIN_SIGNING_KEY_BYTES:
        raise ValueError(
            f"Signing key must be at least {_MIN_SIGNING_KEY_BYTES} bytes; "
            f"got {len(signing_key)} bytes.  Short keys provide insufficient "
            "security strength for HMAC-SHA256."
        )


def _log_verification_failure(path: str, reason: str) -> None:
    """Emit a best-effort audit log entry for an artifact verification failure.

    Best-effort: any exception raised during logging is silently suppressed
    so that the original security error is never blocked by a logging failure.

    Args:
        path: Filesystem path of the artifact that failed verification.
        reason: Human-readable description of the failure (must not contain
            PII or secret material).
    """
    ARTIFACT_VERIFICATION_FAILURE_TOTAL.inc()
    try:
        _logger.warning(
            "ARTIFACT_VERIFICATION_FAILURE event_type=ARTIFACT_VERIFICATION_FAILURE "
            "action=load resource=model_artifact path=%s reason=%s",
            path,
            reason,
        )
    except Exception:  # best-effort; must not block error propagation
        import sys

        sys.stderr.write(f"CRITICAL: audit logging failed in _log_verification_failure: {path}\n")


# Intentionally mutable: column_names, column_dtypes, and column_nullables
# are populated incrementally during job finalization after training completes.
# frozen=True would require all fields at construction time, conflicting with
# the step-based orchestration lifecycle (T35.1, ADR-0038).
@dataclass
class ModelArtifact:
    """Serialisable container for a trained CTGAN model and its schema metadata.

    Attributes:
        table_name: Name of the source table this model was trained on.
        model: The trained CTGANSynthesizer instance (or any duck-typed
            synthesizer satisfying the :class:`SynthesizerModel` Protocol,
            i.e., exposing a ``sample(num_rows)`` method).  ``None`` is
            accepted at construction time; callers must set this field before
            invoking :meth:`save` or passing the artifact to
            :meth:`SynthesisEngine.generate`.
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
    model: SynthesizerModel | None  # CTGANSynthesizer or compatible duck-typed model
    # (None is accepted at construction time, before training completes)
    column_names: list[str] = field(default_factory=list)
    column_dtypes: dict[str, str] = field(default_factory=dict)
    column_nullables: dict[str, bool] = field(default_factory=dict)

    def save(self, path: str, *, signing_key: bytes | None = None) -> str:
        """Serialise the artifact to a pickle file, optionally with HMAC signing.

        When ``signing_key`` is provided, the output file format is::

            [32-byte HMAC-SHA256 over the pickle payload] + [pickle payload]

        When ``signing_key`` is ``None``, the file is written without a
        signature (backward-compatible unsigned format).

        Args:
            path: Filesystem path where the artifact will be written.
                Parent directories must already exist.
            signing_key: Raw signing key bytes for HMAC-SHA256 authentication.
                Must be at least 32 bytes.  If ``None``, the artifact is saved
                unsigned (backward-compatible mode).  An empty bytes value
                (``b""``) is rejected with :exc:`ValueError`.

        Returns:
            The ``path`` argument unchanged, allowing callers to chain:
            ``saved_path = artifact.save(path, signing_key=key)``.

        Raises:
            ValueError: If ``signing_key`` is empty or shorter than 32 bytes.
        """  # noqa: DOC502 — ValueError is raised by _validate_signing_key(), not inline
        if signing_key is not None:
            _validate_signing_key(signing_key, context="save")

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
    def load(
        cls,
        path: str,
        *,
        signing_key: bytes | None = None,
        extra_allowed_prefixes: tuple[str, ...] = (),
    ) -> ModelArtifact:
        """Deserialise a :class:`ModelArtifact` from a pickle file.

        When ``signing_key`` is provided, the file is expected to begin with a
        32-byte HMAC-SHA256 signature over the remainder.  The signature is
        verified via :func:`hmac.compare_digest` before unpickling.  If
        verification fails, :exc:`SecurityError` is raised and the pickle data
        is never executed.

        After HMAC verification, the pickle payload is deserialized via
        :class:`RestrictedUnpickler`, which enforces a module allowlist.

        Args:
            path: Filesystem path previously written by :meth:`save`.
            signing_key: Raw signing key bytes.  Must match the key used at
                :meth:`save` time and be at least 32 bytes.  If ``None``,
                unsigned mode is used.
            extra_allowed_prefixes: Additional module prefixes to allow
                beyond :data:`_ALLOWED_MODULE_PREFIXES` during unpickling.
                In production, leave empty.

        Returns:
            The deserialised :class:`ModelArtifact` instance.

        Note:
            :exc:`FileNotFoundError` propagates naturally from the underlying
            ``open()`` call when no file exists at ``path`` — it is not raised
            explicitly, eliminating the TOCTOU race between a pre-check and the
            actual open (T50.4, ADV-P47-07).

        Raises:
            ValueError: If ``signing_key`` is an empty bytes value (``b""``),
                shorter than 32 bytes, or if the artifact file exceeds 2 GiB.
            SecurityError: If HMAC verification fails for any reason:
                wrong key, tampered payload, signed file loaded without a key,
                unsigned file loaded with a key; OR if the pickle payload
                references a non-allowlisted module/class (T55.2).
            ArtifactTamperingError: If the unpickled object is not a
                :class:`ModelArtifact` instance (even with a valid HMAC).
        """
        if signing_key is not None:
            _validate_signing_key(signing_key, context="load")

        with open(path, "rb") as f:  # FileNotFoundError propagates naturally — no TOCTOU pre-check
            raw = f.read(_MAX_ARTIFACT_SIZE_BYTES + 1)

        if len(raw) > _MAX_ARTIFACT_SIZE_BYTES:
            raise ValueError(
                f"File too large: {len(raw)} bytes exceeds the 2 GiB size limit "
                f"({_MAX_ARTIFACT_SIZE_BYTES} bytes).  Artifact rejected to prevent "
                "memory exhaustion."
            )

        if signing_key is not None:
            if len(raw) <= HMAC_DIGEST_SIZE:
                _log_verification_failure(path, "file too short for HMAC header")
                raise SecurityError(
                    "HMAC verification failed: file is too short to contain a valid "
                    f"HMAC header (expected >{HMAC_DIGEST_SIZE} bytes, got {len(raw)})."
                )
            stored_digest = raw[:HMAC_DIGEST_SIZE]
            pickle_payload = raw[HMAC_DIGEST_SIZE:]
            if not verify_hmac(signing_key, pickle_payload, stored_digest):
                _log_verification_failure(path, "HMAC digest mismatch")
                raise SecurityError(
                    "HMAC verification failed: the artifact signature does not match "
                    "the provided signing key.  The artifact may have been tampered "
                    "with or was signed with a different key."
                )
            _logger.info("ModelArtifact HMAC-SHA256 signature verified for path %s.", path)
        else:
            # No key provided — check whether the file appears to be signed.
            # A signed file cannot be silently downgraded to unsigned mode.
            if _detect_signed_format(raw):
                _log_verification_failure(path, "signed artifact loaded without key")
                raise SecurityError(
                    "HMAC verification failed: the artifact appears to be signed "
                    "(HMAC header detected) but no signing_key was provided. "
                    "Pass the signing_key used at save time to load this artifact."
                )
            pickle_payload = raw

        # Use RestrictedUnpickler instead of bare pickle.loads (T55.2, ADR-0055).
        # nosec B301 — RestrictedUnpickler enforces module allowlist; HMAC verified above
        artifact = RestrictedUnpickler.loads(
            pickle_payload, extra_allowed_prefixes=extra_allowed_prefixes
        )

        if not isinstance(artifact, ModelArtifact):
            _log_verification_failure(
                path,
                f"unpickled object is {type(artifact).__name__!r}, not ModelArtifact",
            )
            raise ArtifactTamperingError(
                "Artifact tampering detected: unpickled object is not a ModelArtifact "
                f"instance (got {type(artifact).__name__!r}).  The artifact may have "
                "been replaced or the signing key may be compromised."
            )

        _logger.info(
            "ModelArtifact for table '%s' loaded from %s",
            artifact.table_name,
            path,
        )
        return artifact
