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
  - Restricted unpickling (T55.2): :class:`RestrictedUnpickler` replaces the
    bare ``pickle.loads`` call in :meth:`ModelArtifact.load`.  It maintains
    an explicit allowlist of permitted modules/classes.  Any class not in the
    allowlist raises :exc:`SecurityError` before any bytecode is executed.
    Defense-in-depth: HMAC verification runs first, then class filtering.
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

Loading a signed file without a key raises :exc:`SecurityError` — the engine
detects the HMAC header by checking whether the file starts with non-pickle
bytes followed by valid pickle magic at byte 32, preventing silent bypass of
signature verification.

Task: P4-T4.2b — Synthesizer Core (SDV/CTGAN Integration)
Task: P8-T8.2  — Security Hardening (ADV-040: HMAC-SHA256 pickle signing)
Task: T47.6    — Harden Model Artifact Signature Verification
Task: T50.4    — Pickle TOCTOU Mitigation (ADV-P47-07: bounded read, no pre-stat)
Task: T55.2    — Replace Pickle with Safe Serialization (Restricted Unpickler)
ADR: ADR-0017 (CTGAN + Opacus; per-table training strategy)
ADR: ADR-0055 (Restricted Unpickler for ModelArtifact deserialization)
"""

from __future__ import annotations

import io
import logging
import pickle  # nosec B403 — pickle is used intentionally for self-produced ModelArtifact serialisation; HMAC-SHA256 signing (ADV-040) + RestrictedUnpickler (T55.2) ensure only safe artifacts are deserialized
from dataclasses import dataclass, field
from typing import Any

from synth_engine.shared.exceptions import ArtifactTamperingError
from synth_engine.shared.security.hmac_signing import (
    HMAC_DIGEST_SIZE,
    SecurityError,
    compute_hmac,
    verify_hmac,
)

# SecurityError is re-exported here for backward compat; canonical: synth_engine.shared.security
__all__ = ["ModelArtifact", "RestrictedUnpickler", "SecurityError"]

_logger = logging.getLogger(__name__)

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

# ---------------------------------------------------------------------------
# Allowlist for RestrictedUnpickler
# ---------------------------------------------------------------------------

#: Module prefixes permitted during deserialization.
#:
#: Each entry is a tuple of ``(module_prefix, classname_or_None)``.
#: If ``classname`` is ``None``, any class from that module prefix is allowed.
#: If ``classname`` is a string, only that exact class is allowed.
#:
#: Rationale (ADR-0055):
#: - ``synth_engine.modules.synthesizer.models`` — the ModelArtifact class itself.
#: - ``builtins`` — Python built-in types (list, dict, str, int, float, etc.)
#:   needed for dataclass field reconstruction.
#: - ``_codecs`` — used by pickle internally for bytes/bytearray reconstruction.
#: - ``collections`` — OrderedDict used in some sklearn/SDV internals.
#: - ``datetime`` — datetime objects may appear in SDV model metadata.
#: - ``numpy`` — array dtypes and values in DataTransformer internals.
#: - ``pandas`` — dtype reconstruction from Parquet-originated DataFrames.
#: - ``torch`` — model weights (tensors) in CTGANSynthesizer.
#: - ``sdv``, ``ctgan``, ``rdt`` — SDV/CTGAN synthesizer state.
#: - ``copulas`` — SDV dependency for statistical distributions.
#: - ``opacus`` — DP wrapper around the PyTorch training loop.
#: - ``sklearn`` — scikit-learn transformers used by rdt/DataTransformer.
#:
#: Modules NOT in this list (e.g. ``os``, ``subprocess``, ``importlib``,
#: ``pathlib``, ``socket``, arbitrary third-party packages) will raise
#: :exc:`SecurityError` immediately when encountered during deserialization.
_ALLOWED_MODULE_PREFIXES: tuple[str, ...] = (
    "synth_engine.modules.synthesizer.models",
    "builtins",
    "_codecs",
    "collections",
    "datetime",
    "numpy",
    "pandas",
    "torch",
    "sdv",
    "ctgan",
    "rdt",
    "copulas",
    "opacus",
    "sklearn",
    "scipy",
    "joblib",
)


class RestrictedUnpickler(pickle.Unpickler):
    """A pickle.Unpickler subclass that only allows an explicit module allowlist.

    Overrides :meth:`find_class` to reject any ``(module, name)`` pair not
    in :data:`_ALLOWED_MODULE_PREFIXES` (plus any ``extra_allowed_prefixes``
    provided at construction time).

    This prevents deserialization of arbitrary classes — the primary vector
    for pickle-based remote code execution attacks.

    HMAC verification in :meth:`ModelArtifact.load` is the first line of
    defense.  This class is the second line: even if an attacker obtains the
    signing key and forges a valid HMAC, only allowlisted classes can be
    instantiated during deserialization.

    Args:
        file: File-like object to read pickle data from (passed to
            :class:`pickle.Unpickler`).
        extra_allowed_prefixes: Additional module prefixes to allow beyond
            :data:`_ALLOWED_MODULE_PREFIXES`.  Used by callers that need to
            deserialize classes from non-default modules (e.g., test stubs).
            In production, leave empty — the production allowlist covers all
            legitimate SDV/CTGAN/torch dependencies.

    Usage::

        artifact = RestrictedUnpickler.loads(pickle_bytes)
        # With extra prefixes (test use only):
        artifact = RestrictedUnpickler.loads(
            pickle_bytes, extra_allowed_prefixes=("mypackage",)
        )

    Raises:
        SecurityError: If deserialization encounters a class whose module
            is not in :data:`_ALLOWED_MODULE_PREFIXES` or
            ``extra_allowed_prefixes``.
    """

    def __init__(
        self,
        file: io.BytesIO,
        extra_allowed_prefixes: tuple[str, ...] = (),
    ) -> None:
        super().__init__(file)
        self._extra_allowed_prefixes = extra_allowed_prefixes

    def find_class(self, module: str, name: str) -> Any:
        """Intercept class lookups and enforce the module allowlist.

        Args:
            module: The module path from the pickle stream.
            name: The class or function name from the pickle stream.

        Returns:
            The class object if ``module`` matches an allowed prefix.

        Raises:
            SecurityError: If ``module`` does not match any allowed prefix.
        """
        # Check if the module starts with any allowed prefix.
        # Note: we check the full module name AND each allowed prefix to
        # prevent attacks like "numpy_malicious" matching "numpy" prefix.
        all_prefixes = _ALLOWED_MODULE_PREFIXES + self._extra_allowed_prefixes
        for allowed_prefix in all_prefixes:
            if module == allowed_prefix or module.startswith(allowed_prefix + "."):
                return super().find_class(module, name)

        raise SecurityError(
            f"Deserialization of class '{module}.{name}' is not permitted. "
            f"Only classes from explicitly allowlisted modules may be "
            f"deserialized from ModelArtifact pickle payloads. "
            f"See ADR-0055 for the allowlist rationale."
        )

    @classmethod
    def loads(
        cls,
        data: bytes,
        *,
        extra_allowed_prefixes: tuple[str, ...] = (),
    ) -> Any:
        """Deserialize *data* using the restricted unpickler.

        Args:
            data: Raw pickle bytes to deserialize.
            extra_allowed_prefixes: Additional module prefixes to allow
                beyond :data:`_ALLOWED_MODULE_PREFIXES`.  Use only for
                test stubs or temporary compatibility; prefer extending
                the production allowlist instead.

        Returns:
            The deserialized Python object.

        Raises:
            SecurityError: If deserialization encounters a non-allowlisted class.
        """
        return cls(io.BytesIO(data), extra_allowed_prefixes=extra_allowed_prefixes).load()


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
    try:
        _logger.warning(
            "ARTIFACT_VERIFICATION_FAILURE event_type=ARTIFACT_VERIFICATION_FAILURE "
            "action=load resource=model_artifact path=%s reason=%s",
            path,
            reason,
        )
    except Exception:  # noqa: S110  # nosec B110 — best-effort; must not block error propagation
        pass


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
                production wiring.  Must be at least 32 bytes.  If ``None``,
                the artifact is saved unsigned (backward-compatible mode).
                An empty bytes value (``b""``) is rejected with
                :exc:`ValueError` because an empty key provides no security.

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
        :class:`RestrictedUnpickler`, which enforces a module allowlist.  Any
        class not in the allowlist raises :exc:`SecurityError` before any
        bytecode is executed.  This is the second line of defense against
        pickle-based remote code execution (T55.2, ADR-0055).

        After unpickling, the result is asserted to be a :class:`ModelArtifact`
        instance.  If it is not, :exc:`ArtifactTamperingError` is raised even
        when HMAC verification passed — a compromised signing key could
        produce a valid-HMAC payload that is not a :class:`ModelArtifact`.

        When ``signing_key`` is ``None``, the file is loaded in unsigned mode
        (backward-compatible).  If the file appears to be signed (i.e., it
        carries an HMAC header), :exc:`SecurityError` is raised to prevent
        silent downgrade attacks — an attacker cannot bypass verification by
        simply omitting the key.

        Args:
            path: Filesystem path previously written by :meth:`save`.
            signing_key: Raw signing key bytes.  Must match the key used at
                :meth:`save` time and be at least 32 bytes.  If ``None``,
                unsigned mode is used.
            extra_allowed_prefixes: Additional module prefixes to allow
                beyond :data:`_ALLOWED_MODULE_PREFIXES` during unpickling.
                In production, leave empty — the default allowlist covers all
                legitimate SDV/CTGAN/torch dependencies.  Test code may pass
                test module prefixes here to allow test stubs (e.g.
                ``extra_allowed_prefixes=("tests.unit",)``).

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
            # A signed file cannot be silently downgraded to unsigned mode;
            # that would allow an attacker to bypass HMAC verification by
            # simply omitting the key.
            if _detect_signed_format(raw):
                _log_verification_failure(path, "signed artifact loaded without key")
                raise SecurityError(
                    "HMAC verification failed: the artifact appears to be signed "
                    "(HMAC header detected) but no signing_key was provided. "
                    "Pass the signing_key used at save time to load this artifact."
                )
            pickle_payload = raw

        # Use RestrictedUnpickler instead of bare pickle.loads (T55.2, ADR-0055).
        # This is the second defense layer: HMAC guards integrity, the restricted
        # unpickler guards against non-allowlisted classes even with a valid HMAC.
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
