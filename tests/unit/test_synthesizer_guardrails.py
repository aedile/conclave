"""Unit tests for the OOM pre-flight memory guardrail.

RED phase: all tests MUST fail before guardrails.py is implemented.

Spec: T4.3a — OOM pre-flight guardrail
  - check_memory_feasibility(rows, columns, dtype_bytes, overhead_factor) -> None
  - Raises OOMGuardrailError when estimated bytes > 0.85 × available memory.
  - Error message is human-readable (GiB, not raw integers).
  - psutil and torch are fully mockable — no real hardware required.
"""

from unittest.mock import MagicMock, patch

import pytest

from synth_engine.modules.synthesizer.guardrails import (
    OOMGuardrailError,
    check_memory_feasibility,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_8_GiB = 8 * 1024**3  # 8,589,934,592 bytes — mocked available RAM


def _mock_vmem(available: int) -> MagicMock:
    """Return a mock object matching psutil.virtual_memory() return type."""
    vmem = MagicMock()
    vmem.available = available
    return vmem


# ---------------------------------------------------------------------------
# AC: at 75% of available memory (6.0 GiB) — NO error
# ---------------------------------------------------------------------------


def test_feasibility_passes_at_75_percent() -> None:
    """Input requiring 6.0 GiB (75% of 8 GiB) must return None without error."""
    # rows=100_000, columns=100, dtype_bytes=8 (float64), overhead_factor=8.0
    # estimate = 100_000 * 100 * 8 * 8 = 640,000,000 bytes = ~0.596 GiB
    # Using values that produce exactly 75%:
    # 0.75 * 8 GiB = 6,442,450,944 bytes
    # rows=1, columns=1, dtype_bytes=6_442_450_944, overhead_factor=1.0 → 6 GiB
    target_bytes = int(0.75 * _8_GiB)  # 6 GiB
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        # Should NOT raise
        result = check_memory_feasibility(
            rows=1,
            columns=1,
            dtype_bytes=target_bytes,
            overhead_factor=1.0,
        )
    assert result is None


# ---------------------------------------------------------------------------
# AC: at 85% of available memory (6.8 GiB) — OOMGuardrailError raised
# ---------------------------------------------------------------------------


def test_feasibility_raises_at_85_percent() -> None:
    """Input requiring 6.8 GiB (85% of 8 GiB) must raise OOMGuardrailError."""
    target_bytes = int(0.85 * _8_GiB)  # 6.8 GiB — right at the threshold
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        with pytest.raises(OOMGuardrailError):
            check_memory_feasibility(
                rows=1,
                columns=1,
                dtype_bytes=target_bytes,
                overhead_factor=1.0,
            )


def test_feasibility_raises_at_90_percent() -> None:
    """Input requiring 90% of available memory (7.2 GiB) must raise OOMGuardrailError."""
    target_bytes = int(0.90 * _8_GiB)
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        with pytest.raises(OOMGuardrailError):
            check_memory_feasibility(
                rows=1,
                columns=1,
                dtype_bytes=target_bytes,
                overhead_factor=1.0,
            )


# ---------------------------------------------------------------------------
# AC: error message contains human-readable byte values (not raw integers)
# ---------------------------------------------------------------------------


def test_error_message_is_human_readable() -> None:
    """OOMGuardrailError message must include GiB/MiB notation, not raw byte counts."""
    # 6.8 GiB required vs 8.0 GiB available
    target_bytes = int(0.85 * _8_GiB)
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        with pytest.raises(OOMGuardrailError) as exc_info:
            check_memory_feasibility(
                rows=1,
                columns=1,
                dtype_bytes=target_bytes,
                overhead_factor=1.0,
            )
    message = str(exc_info.value)
    # Must NOT expose a raw 10-digit byte integer like "7,316,280,320"
    # Must contain a human-readable unit
    assert "GiB" in message or "MiB" in message or "KiB" in message


def test_error_message_contains_reduction_factor() -> None:
    """OOMGuardrailError message must state how much to reduce the dataset."""
    target_bytes = int(0.90 * _8_GiB)
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        with pytest.raises(OOMGuardrailError) as exc_info:
            check_memory_feasibility(
                rows=1,
                columns=1,
                dtype_bytes=target_bytes,
                overhead_factor=1.0,
            )
    message = str(exc_info.value)
    # Message must contain a reduction factor (e.g., "reduce dataset by 1.06×")
    assert "reduce" in message.lower() or "×" in message or "factor" in message.lower()


# ---------------------------------------------------------------------------
# AC: simulate 1-billion-row dataset — guardrail rejects before any training
# ---------------------------------------------------------------------------


def test_billion_row_dataset_is_rejected() -> None:
    """A 1-billion-row dataset must be rejected cleanly before any training begins."""
    # 1_000_000_000 rows × 50 columns × 8 bytes × 6.0 overhead = 2.4 TiB
    # Far exceeds any mock available memory of 8 GiB.
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        with pytest.raises(OOMGuardrailError) as exc_info:
            check_memory_feasibility(
                rows=1_000_000_000,
                columns=50,
                dtype_bytes=8,
                overhead_factor=6.0,
            )
    # Verify the error is clean — no unhandled arithmetic overflow or TypeError
    assert isinstance(exc_info.value, OOMGuardrailError)


# ---------------------------------------------------------------------------
# AC: psutil and torch are both mockable (no real hardware required)
# ---------------------------------------------------------------------------


def test_psutil_is_mockable() -> None:
    """psutil.virtual_memory must be patchable at the guardrails module level."""
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        # A tiny dataset: 10 rows × 1 col × 1 byte × 1.0 overhead = 10 bytes
        result = check_memory_feasibility(
            rows=10,
            columns=1,
            dtype_bytes=1,
            overhead_factor=1.0,
        )
    assert result is None
    mock_psutil.virtual_memory.assert_called_once()


def test_function_signature_matches_spec() -> None:
    """check_memory_feasibility must accept (rows, columns, dtype_bytes, overhead_factor)."""
    import inspect

    sig = inspect.signature(check_memory_feasibility)
    params = list(sig.parameters.keys())
    assert params == ["rows", "columns", "dtype_bytes", "overhead_factor"]


def test_oom_guardrail_error_is_exception() -> None:
    """OOMGuardrailError must be a subclass of Exception."""
    assert issubclass(OOMGuardrailError, Exception)


def test_oom_guardrail_error_is_importable() -> None:
    """OOMGuardrailError must be importable from guardrails module directly."""
    from synth_engine.modules.synthesizer.guardrails import OOMGuardrailError as E

    assert E is OOMGuardrailError


# ---------------------------------------------------------------------------
# Edge: exactly at boundary (84.9%) — must pass
# ---------------------------------------------------------------------------


def test_feasibility_passes_just_below_threshold() -> None:
    """Input requiring 84.9% of available memory must NOT raise."""
    target_bytes = int(0.849 * _8_GiB)
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        result = check_memory_feasibility(
            rows=1,
            columns=1,
            dtype_bytes=target_bytes,
            overhead_factor=1.0,
        )
    assert result is None
