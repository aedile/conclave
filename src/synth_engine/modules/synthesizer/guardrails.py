"""Pre-flight OOM memory guardrail for the synthesizer.

This module provides a single pure function, ``check_memory_feasibility``,
that estimates the RAM required for a training job and raises
:exc:`OOMGuardrailError` before any training begins if the job would exhaust
available memory.

:exc:`OOMGuardrailError` is defined in :mod:`synth_engine.shared.exceptions`
and re-exported here for backward compatibility.

The guardrail is intentionally dependency-light: it only requires ``psutil``
for RAM measurement and falls back gracefully when ``torch`` (VRAM) is absent.
No synthesis library is imported here.

Task: P26-T26.2 — Exception Hierarchy (OOMGuardrailError moved to shared)
"""

from __future__ import annotations

import importlib.util

import psutil

from synth_engine.shared.exceptions import OOMGuardrailError

__all__ = ["OOMGuardrailError", "check_memory_feasibility"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Jobs that would consume more than this fraction of available memory are
#: rejected.  0.85 means "15% headroom must remain."
_SAFETY_THRESHOLD: float = 0.85


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_bytes(n: int) -> str:
    """Format a byte count as a human-readable string using binary prefixes.

    Args:
        n: Number of bytes (non-negative).

    Returns:
        A string such as ``"6.8 GiB"``, ``"512.0 MiB"``, or ``"1.0 KiB"``.
    """
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GiB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MiB"
    if n >= 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n} B"


def _available_memory() -> int:
    """Return the number of bytes currently available for use.

    Prefers GPU VRAM when ``torch`` is present and a CUDA device is
    available; otherwise returns system RAM via ``psutil``.

    Returns:
        Available memory in bytes.
    """
    # Attempt VRAM path only when torch is installed.
    if importlib.util.find_spec("torch") is not None:
        import torch  # type: ignore[import-not-found, unused-ignore]  # optional dep: absent when synthesizer group not installed

        if torch.cuda.is_available():
            # Reserved memory accounts for the CUDA context already allocated.
            device = torch.cuda.current_device()
            props = torch.cuda.get_device_properties(device)
            reserved = torch.cuda.memory_reserved(device)
            return int(props.total_memory - reserved)

    # Fallback: system RAM.
    return int(psutil.virtual_memory().available)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_memory_feasibility(
    rows: int,
    columns: int,
    dtype_bytes: int,
    overhead_factor: float,
) -> None:
    """Verify that a training job fits in available memory before it starts.

    Estimates memory as::

        estimated_bytes = rows x columns x dtype_bytes x overhead_factor

    If ``estimated_bytes > 0.85 x available_memory``, the job is rejected
    immediately with a clear, human-readable error.

    Args:
        rows: Number of data rows in the training dataset.
        columns: Number of feature columns.
        dtype_bytes: Average number of bytes per cell (e.g., 8 for float64).
        overhead_factor: Algorithm-specific multiplier that accounts for
            gradient buffers, optimizer state, and other runtime overhead
            (typical range: 4-8 for GAN training).

    Returns:
        ``None`` when the job fits within the safety threshold.

    Raises:
        ValueError: When any of ``rows``, ``columns``, ``dtype_bytes`` are
            ≤ 0, or ``overhead_factor`` ≤ 0.0. Non-positive inputs produce
            nonsensical or silent results and are rejected early.
        OOMGuardrailError: When the estimate exceeds 85% of available memory,
            with a message that includes estimated size, available size, and
            the factor by which the dataset must be reduced.

    Example::

        check_memory_feasibility(
            rows=100_000,
            columns=50,
            dtype_bytes=8,
            overhead_factor=6.0,
        )
    """
    if rows <= 0:
        raise ValueError(f"rows must be > 0, got {rows}")
    if columns <= 0:
        raise ValueError(f"columns must be > 0, got {columns}")
    if dtype_bytes <= 0:
        raise ValueError(f"dtype_bytes must be > 0, got {dtype_bytes}")
    if overhead_factor <= 0.0:
        raise ValueError(f"overhead_factor must be > 0.0, got {overhead_factor}")

    estimated: int = int(rows * columns * dtype_bytes * overhead_factor)
    available: int = _available_memory()

    if estimated > _SAFETY_THRESHOLD * available:
        # Compute the factor by which the dataset must shrink to fit.
        # We target fitting inside the safety threshold, so the target is
        # 0.85 x available.  Factor = estimated / (0.85 x available).
        safe_capacity = _SAFETY_THRESHOLD * available
        reduction_factor = estimated / safe_capacity if safe_capacity > 0 else float("inf")

        raise OOMGuardrailError(
            f"{_format_bytes(estimated)} estimated, "
            f"{_format_bytes(available)} available -- "
            f"reduce dataset by {reduction_factor:.2f}x"
        )
