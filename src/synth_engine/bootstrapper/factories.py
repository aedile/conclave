"""DI factory functions for synthesis-layer application dependencies.

Houses the lazy factory functions that construct :class:`SynthesisEngine`
and :class:`DPTrainingWrapper` instances.  These factories are called at
synthesis-job start time, never at application startup, so missing GPU
infrastructure does not prevent the health check from responding.

The Docker-secrets cluster (``_read_secret``, ``_SECRETS_DIR``,
``_MINIO_ENDPOINT``, ``_EPHEMERAL_BUCKET``, ``MinioStorageBackend``,
``build_ephemeral_storage_client``) lives in ``main.py`` so that existing
test patches against ``synth_engine.bootstrapper.main.*`` continue to work
without modification (AC3 of the bootstrapper-decomposition task).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
    from synth_engine.modules.synthesizer.engine import SynthesisEngine

_logger = logging.getLogger(__name__)


def build_synthesis_engine(epochs: int = 300) -> SynthesisEngine:
    """Build a SynthesisEngine with the given epoch count.

    This factory is called lazily at synthesis job start time, not at
    application startup.  Callers receive a stateless engine instance;
    model artifacts are returned from :meth:`SynthesisEngine.train` and
    must be persisted by the caller.

    Args:
        epochs: Number of CTGAN training epochs.  Defaults to 300 (SDV
            default).  Use a lower value (2-5) for integration-test runs.

    Returns:
        A configured :class:`SynthesisEngine` instance.
    """
    from synth_engine.modules.synthesizer.engine import SynthesisEngine as _SynthesisEngine

    _logger.info("SynthesisEngine initialised (epochs=%d).", epochs)
    return _SynthesisEngine(epochs=epochs)


def build_dp_wrapper(
    max_grad_norm: float = 1.0,
    noise_multiplier: float = 1.1,
) -> DPTrainingWrapper:
    """Build a DPTrainingWrapper configured for DP-SGD training.

    This factory is the sole entry point for constructing a
    :class:`~synth_engine.modules.privacy.dp_engine.DPTrainingWrapper`.
    It is the bootstrapper's responsibility to wire the wrapper into
    ``SynthesisEngine.train(dp_wrapper=...)`` — callers must not instantiate
    ``DPTrainingWrapper`` directly outside of tests.

    The bootstrapper is the only layer that imports from both
    ``modules/privacy/`` and ``modules/synthesizer/`` — this factory is
    therefore the correct and only place for this wiring.

    This factory drains ADV-048.

    Args:
        max_grad_norm: Maximum L2 norm for per-sample gradient clipping.
            Must be strictly positive.  Default: 1.0 (canonical DP-SGD value).
        noise_multiplier: Ratio of Gaussian noise std to max_grad_norm.
            Higher values yield stronger privacy but lower utility.
            Must be strictly positive.  Default: 1.1 (canonical DP-SGD value).

    Returns:
        A configured :class:`DPTrainingWrapper` instance ready to be passed
        to :meth:`SynthesisEngine.train`.

    Raises:
        ValueError: If ``max_grad_norm`` or ``noise_multiplier`` is not
            strictly positive.

    Example::

        wrapper = build_dp_wrapper(max_grad_norm=1.0, noise_multiplier=1.1)
        engine = build_synthesis_engine(epochs=2)
        artifact = engine.train(
            "persons", "/data/persons.parquet", dp_wrapper=wrapper
        )
        epsilon = wrapper.epsilon_spent(delta=1e-5)
    """
    from synth_engine.modules.privacy.dp_engine import (
        DPTrainingWrapper as _DPTrainingWrapper,
    )

    _logger.info(
        "DPTrainingWrapper initialised (max_grad_norm=%.2f, noise_multiplier=%.2f).",
        max_grad_norm,
        noise_multiplier,
    )
    return _DPTrainingWrapper(max_grad_norm=max_grad_norm, noise_multiplier=noise_multiplier)
