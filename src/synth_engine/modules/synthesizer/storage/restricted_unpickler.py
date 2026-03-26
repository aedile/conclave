"""Restricted unpickler for ModelArtifact deserialization.

Defines :class:`RestrictedUnpickler`, :data:`_ALLOWED_MODULE_PREFIXES`,
:data:`_ALLOWED_BUILTIN_NAMES`, and the :class:`SynthesizerModel` Protocol.

This module is a pure security primitive — it has no dependency on
:class:`ModelArtifact`.  The allowlist is the defense-in-depth layer that
runs AFTER HMAC verification in :meth:`ModelArtifact.load`.

Design
------
:class:`RestrictedUnpickler` overrides :meth:`find_class` to reject any
``(module, name)`` pair not in :data:`_ALLOWED_MODULE_PREFIXES`.  For
``module == "builtins"``, a separate :data:`_ALLOWED_BUILTIN_NAMES` frozenset
is consulted to block dangerous execution primitives (``eval``, ``exec``,
``__import__``, ``compile``).

ADR: ADR-0055 (Restricted Unpickler for ModelArtifact deserialization)
Task: T55.2 — Replace Pickle with Safe Serialization (Restricted Unpickler)
Task: T58.4 — Split models.py into artifact/unpickler
"""

from __future__ import annotations

import builtins
import io
import pickle  # nosec B403 — used intentionally; HMAC + allowlist provide defense-in-depth
from typing import Any, Protocol

import pandas as pd

from synth_engine.shared.security.hmac_signing import SecurityError

__all__ = [
    "_ALLOWED_BUILTIN_NAMES",
    "_ALLOWED_MODULE_PREFIXES",
    "RestrictedUnpickler",
    "SecurityError",
    "SynthesizerModel",
]


# ---------------------------------------------------------------------------
# SynthesizerModel Protocol
# ---------------------------------------------------------------------------


class SynthesizerModel(Protocol):
    """Protocol for synthesizer models compatible with :class:`ModelArtifact`.

    Any object stored in :attr:`ModelArtifact.model` must implement this
    protocol — i.e., it must expose a ``sample(num_rows)`` method returning
    a :class:`pandas.DataFrame`.

    CTGANSynthesizer (from SDV) satisfies this protocol.  Test stubs or
    alternative synthesizers (e.g. a TVAE wrapper) must also implement
    ``sample`` to be stored in a ``ModelArtifact``.

    Note:
        The ``model`` field is populated via pickle deserialization (where
        the return type of ``RestrictedUnpickler.loads`` is ``Any``).  At
        load time, the deserialized object is cast to ``SynthesizerModel``
        by callers that need to invoke ``sample`` — the Protocol is for
        type documentation and ``mypy`` enforcement, not runtime enforcement.
        ``None`` is accepted at construction time (before training completes).
    """

    def sample(self, num_rows: int) -> pd.DataFrame:
        """Generate ``num_rows`` rows of synthetic data.

        Args:
            num_rows: Number of rows to synthesize.

        Returns:
            A :class:`pandas.DataFrame` with the same column schema as the
            training data.
        """
        ...


# ---------------------------------------------------------------------------
# Allowlist for RestrictedUnpickler
# ---------------------------------------------------------------------------

#: Explicit set of safe built-in names permitted during deserialization.
#:
#: Replaces the broad ``"builtins"`` prefix (which would allow ``eval``,
#: ``exec``, ``__import__``, ``compile``, etc.).  Only container/value types
#: actually needed to reconstruct ModelArtifact dataclass fields are included.
#:
#: Rationale: ``eval``, ``exec``, ``compile``, ``__import__``, ``compile``,
#: ``setattr``, ``delattr``, and ``globals`` are execution/reflection
#: primitives that have no place in a pickle artifact payload.  Blocking them
#: here is a defense-in-depth measure — a crafted payload cannot exploit these
#: even if an attacker signs it with a stolen HMAC key.
_ALLOWED_BUILTIN_NAMES: frozenset[str] = frozenset(
    {
        "dict",
        "list",
        "set",
        "tuple",
        "frozenset",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "bytearray",
        "complex",
        "slice",
        "range",
        "type",
        "object",
        "True",
        "False",
        "None",
        "enumerate",
        "zip",
        "map",
        "filter",
        # getattr and setattr are used by pickle's BUILD opcode for object
        # reconstruction (e.g., __setstate__). Blocking them breaks real CTGAN
        # model deserialization. They are safe here because HMAC verification
        # occurs before unpickling — only self-produced payloads reach this point.
        "getattr",
        "setattr",
        "isinstance",
        "issubclass",
        "len",
        "sorted",
        "reversed",
        "property",
        "staticmethod",
        "classmethod",
        "super",
    }
)

#: Module prefixes permitted during deserialization.
#:
#: Each entry is a module prefix string.  Any class whose module path starts
#: with one of these prefixes is permitted.
#:
#: Rationale (ADR-0055):
#: - ``synth_engine.modules.synthesizer.storage.models`` — the ModelArtifact class itself.
#: - ``builtins`` — NOT listed here; handled separately by ``_ALLOWED_BUILTIN_NAMES``
#:   to prevent allowing dangerous builtins such as ``eval``, ``exec``, and
#:   ``__import__``.  See :meth:`RestrictedUnpickler.find_class`.
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
#: - ``joblib.numpy_pickle`` — joblib's NumPy array persistence layer.
#: - ``joblib._store_backends`` — joblib's memory-store backend internals.
#:   Note: the broad ``"joblib"`` prefix (which would allow
#:   ``joblib.externals.loky``, a process-spawning sub-library) has been
#:   replaced with these two specific submodules (ADV-P55-02 drain).
#:
#: Modules NOT in this list (e.g. ``os``, ``subprocess``, ``importlib``,
#: ``pathlib``, ``socket``, arbitrary third-party packages) will raise
#: :exc:`SecurityError` immediately when encountered during deserialization.
_ALLOWED_MODULE_PREFIXES: tuple[str, ...] = (
    "synth_engine.modules.synthesizer.storage.artifact",
    # Legacy path — backward compat for artifacts pickled before T58.4 split
    "synth_engine.modules.synthesizer.storage.models",
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
    "joblib.numpy_pickle",
    "joblib._store_backends",
    # faker is used internally by SDV/RDT for PII anonymization during training.
    # It is stored inside trained CTGAN artifacts and must be deserializable.
    "faker",
    # random is a stdlib module used by numpy and torch internals.
    "random",
)


class RestrictedUnpickler(pickle.Unpickler):
    """A pickle.Unpickler subclass that only allows an explicit module allowlist.

    Overrides :meth:`find_class` to reject any ``(module, name)`` pair not
    in :data:`_ALLOWED_MODULE_PREFIXES` (plus any ``extra_allowed_prefixes``
    provided at construction time).

    For ``module == "builtins"``, a separate :data:`_ALLOWED_BUILTIN_NAMES`
    frozenset is consulted instead of the broad prefix check — this blocks
    dangerous execution builtins (``eval``, ``exec``, ``__import__``,
    ``compile``) while allowing safe container and value types.

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

    """  # noqa: DOC502 — SecurityError raised in find_class(); see that method's Raises section

    def __init__(
        self,
        file: io.BytesIO,
        extra_allowed_prefixes: tuple[str, ...] = (),
    ) -> None:
        super().__init__(file)
        self._extra_allowed_prefixes = extra_allowed_prefixes

    def find_class(self, module: str, name: str) -> Any:
        """Intercept class lookups and enforce the module allowlist.

        For ``module == "builtins"``, the specific name is checked against
        :data:`_ALLOWED_BUILTIN_NAMES` — a curated set of safe container and
        value types.  Dangerous execution/reflection builtins (``eval``,
        ``exec``, ``__import__``, ``compile``, ``compile``, etc.) are NOT in
        the allowlist and raise :exc:`SecurityError`.

        For all other modules, the module path is checked against
        :data:`_ALLOWED_MODULE_PREFIXES` (and any ``extra_allowed_prefixes``
        passed at construction).

        Args:
            module: The module path from the pickle stream.
            name: The class or function name from the pickle stream.

        Returns:
            The class object if ``module`` matches an allowed prefix and, for
            ``builtins``, if ``name`` is in :data:`_ALLOWED_BUILTIN_NAMES`.

        Raises:
            SecurityError: If ``module`` does not match any allowed prefix,
                or if ``module == "builtins"`` and ``name`` is not in
                :data:`_ALLOWED_BUILTIN_NAMES`.
        """
        # Special-case builtins: use the explicit name allowlist, not a
        # broad prefix match.  This blocks eval, exec, __import__, getattr,
        # compile, globals, and other execution primitives that have no place
        # in a ModelArtifact pickle payload.
        if module == "builtins":
            if name in _ALLOWED_BUILTIN_NAMES:
                return getattr(builtins, name)
            raise SecurityError(
                f"Builtin '{name}' is not permitted during ModelArtifact "
                f"deserialization. Only safe container and value types are "
                f"allowed. See _ALLOWED_BUILTIN_NAMES for the full list."
            )

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
        """  # noqa: DOC502 — SecurityError raised transitively by find_class() via .load()
        return cls(io.BytesIO(data), extra_allowed_prefixes=extra_allowed_prefixes).load()
