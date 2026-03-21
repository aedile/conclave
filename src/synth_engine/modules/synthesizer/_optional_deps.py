"""Centralized optional-dependency registry for the synthesizer module (T43.2).

All optional PyTorch imports that were previously repeated across multiple
synthesizer files are consolidated here. Each dependent module imports the
already-resolved references from this single location, eliminating redundant
``try/except ImportError`` blocks and reducing ``# type: ignore[no-redef]``
annotations to exactly one file (this file).

Usage pattern in sibling modules::

    from synth_engine.modules.synthesizer._optional_deps import (
        DataLoader,
        TensorDataset,
        TORCH_AVAILABLE,
        nn,
        require_synthesizer,
        torch,
    )

Leading underscore convention:
    The leading underscore marks this as a private, package-internal module.
    External code (bootstrapper, other modules) MUST NOT import from here;
    they interact with the synthesizer through its public API only.

Import boundary (ADR-0001 / ADR-0036):
    This module MUST NOT import from ``modules/privacy/``,
    ``modules/ingestion/``, ``modules/masking/``, or any other sibling
    module outside ``modules/synthesizer/``.

Task: P43-T43.2 — Consolidate Optional Import Pattern
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "TORCH_AVAILABLE",
    "DataLoader",
    "TensorDataset",
    "nn",
    "require_synthesizer",
    "torch",
]

# ---------------------------------------------------------------------------
# Optional PyTorch imports — all bound at module scope.
#
# When the synthesizer dependency group is absent, every name resolves to
# None and TORCH_AVAILABLE is set to False.  A single ``# type: ignore``
# per aliased name is required here; sibling modules inherit the resolved
# value without needing their own ignore comments.
# ---------------------------------------------------------------------------

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    TORCH_AVAILABLE: bool = True
except ImportError:  # pragma: no cover — only triggered when synthesizer group absent
    torch: Any = None  # type: ignore[no-redef]
    nn: Any = None  # type: ignore[no-redef]
    DataLoader: Any = None  # type: ignore[no-redef]
    TensorDataset: Any = None  # type: ignore[no-redef]
    TORCH_AVAILABLE = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def require_synthesizer() -> None:
    """Assert that PyTorch (and the synthesizer group) is installed.

    Call this at the top of any function that unconditionally requires PyTorch
    so that callers receive a clear, actionable error message rather than an
    opaque ``AttributeError`` or ``TypeError`` from a ``None`` reference.

    Returns:
        None when the synthesizer group is available.

    Raises:
        ImportError: When ``torch`` is not importable, with an explicit
            instruction to install the synthesizer dependency group.

    Example::

        from synth_engine.modules.synthesizer._optional_deps import require_synthesizer

        def my_function() -> None:
            require_synthesizer()
            # torch is available from this point onwards
            import torch
            ...
    """
    if not TORCH_AVAILABLE:
        raise ImportError(
            "PyTorch (torch) is required for synthesis but is not installed. "
            "Install the synthesizer dependency group with: "
            "poetry install --with synthesizer"
        )
