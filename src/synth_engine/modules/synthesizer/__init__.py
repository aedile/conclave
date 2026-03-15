"""Synthesizer — DP-SGD generation engine and edge case amplification.

Public API:
  - :class:`~synth_engine.modules.synthesizer.engine.SynthesisEngine`
  - :class:`~synth_engine.modules.synthesizer.models.ModelArtifact`
  - :func:`~synth_engine.modules.synthesizer.engine.apply_fk_post_processing`
  - :class:`~synth_engine.modules.synthesizer.storage.EphemeralStorageClient`
  - :class:`~synth_engine.modules.synthesizer.storage.MinioStorageBackend`

Imports are intentionally deferred (not performed at package-import time) to
prevent SDV / rdt from being imported during pytest collection.  rdt 1.x
imports ``sre_parse`` at module scope; on Python 3.14 this fires a
``DeprecationWarning`` before pytest's filterwarnings configuration is active,
which causes the collection to fail under ``-W error``.

Callers should import directly from the sub-modules, e.g.:

    from synth_engine.modules.synthesizer.engine import SynthesisEngine
    from synth_engine.modules.synthesizer.models import ModelArtifact
"""

__all__ = [
    "ModelArtifact",
    "SynthesisEngine",
    "apply_fk_post_processing",
]
