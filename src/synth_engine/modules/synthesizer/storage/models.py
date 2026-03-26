"""Data models for the synthesizer module — re-export shim.

This module exists for backward compatibility.  All symbols are now defined
in the following focused sub-modules:

- :mod:`synth_engine.modules.synthesizer.storage.restricted_unpickler` —
  :class:`RestrictedUnpickler`, :data:`_ALLOWED_MODULE_PREFIXES`,
  :data:`_ALLOWED_BUILTIN_NAMES`, :class:`SynthesizerModel` Protocol.
- :mod:`synth_engine.modules.synthesizer.storage.artifact` —
  :class:`ModelArtifact`, :func:`_detect_signed_format`,
  :func:`_validate_signing_key`, :func:`_log_verification_failure`,
  :data:`ARTIFACT_VERIFICATION_FAILURE_TOTAL`.

All existing callers that use::

    from synth_engine.modules.synthesizer.storage.models import ModelArtifact
    from synth_engine.modules.synthesizer.storage.models import RestrictedUnpickler

continue to work unchanged — this file re-exports every public name.

Task: T58.4 — Split models.py into artifact/unpickler
ADR: ADR-0017 (CTGAN + Opacus; per-table training strategy)
ADR: ADR-0055 (Restricted Unpickler for ModelArtifact deserialization)
"""

from synth_engine.modules.synthesizer.storage.artifact import (
    ARTIFACT_VERIFICATION_FAILURE_TOTAL,
    ModelArtifact,
    SecurityError,
    _detect_signed_format,
    _log_verification_failure,
    _validate_signing_key,
)
from synth_engine.modules.synthesizer.storage.restricted_unpickler import (
    _ALLOWED_BUILTIN_NAMES,
    _ALLOWED_MODULE_PREFIXES,
    RestrictedUnpickler,
    SynthesizerModel,
)

# SecurityError is re-exported here for backward compat; canonical: synth_engine.shared.security
__all__ = [
    "ARTIFACT_VERIFICATION_FAILURE_TOTAL",
    "_ALLOWED_BUILTIN_NAMES",
    "_ALLOWED_MODULE_PREFIXES",
    "ModelArtifact",
    "RestrictedUnpickler",
    "SecurityError",
    "SynthesizerModel",
    "_detect_signed_format",
    "_log_verification_failure",
    "_validate_signing_key",
]
