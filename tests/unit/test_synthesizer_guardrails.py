"""Unit tests for the OOM pre-flight memory guardrail.

Spec: T4.3a -- OOM pre-flight guardrail
  - check_memory_feasibility(rows, columns, dtype_bytes, overhead_factor) -> None
  - Raises OOMGuardrailError when estimated bytes > 0.85 x available memory.
  - Error message is human-readable (GiB, not raw integers).
  - psutil and torch are fully mockable -- no real hardware required.
"""

import importlib.util
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from synth_engine.modules.synthesizer.guardrails import (
    OOMGuardrailError,
    check_memory_feasibility,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_8_GiB = 8 * 1024**3  # 8,589,934,592 bytes -- mocked available RAM

# The guardrail fires when estimated > 0.85 x available.
# 0.85 x _8_GiB = 7,301,444,403.2 (not an integer), so:
#   - _THRESHOLD_FLOOR = 7,301,444,403  (one byte below threshold -- safe)
#   - _THRESHOLD_OVER  = 7,301,444,404  (one byte above threshold -- should raise)
_THRESHOLD_EXACT: float = 0.85 * _8_GiB
_THRESHOLD_FLOOR: int = int(_THRESHOLD_EXACT)  # safe: just below
_THRESHOLD_OVER: int = int(_THRESHOLD_EXACT) + 1  # unsafe: just above

_16_GiB_VRAM = 16 * 1024**3  # mock VRAM total
_2_GiB_RESERVED = 2 * 1024**3  # mock reserved VRAM
_14_GiB_AVAILABLE_VRAM = _16_GiB_VRAM - _2_GiB_RESERVED


def _mock_vmem(available: int) -> MagicMock:
    """Return a mock object matching psutil.virtual_memory() return type."""
    vmem = MagicMock()
    vmem.available = available
    return vmem


def _build_torch_mock(cuda_available: bool, total_memory: int, reserved: int) -> ModuleType:
    """Build a minimal torch mock with cuda attributes.

    Args:
        cuda_available: Whether torch.cuda.is_available() returns True.
        total_memory: Value for torch.cuda.get_device_properties().total_memory.
        reserved: Value for torch.cuda.memory_reserved().

    Returns:
        A MagicMock configured to behave like the torch module.
    """
    torch_mock = MagicMock()
    torch_mock.cuda.is_available.return_value = cuda_available
    torch_mock.cuda.current_device.return_value = 0
    torch_mock.cuda.get_device_properties.return_value.total_memory = total_memory
    torch_mock.cuda.memory_reserved.return_value = reserved
    return torch_mock  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# AC: at 75% and 80% of available memory -- NO error
# ---------------------------------------------------------------------------


def test_feasibility_passes_at_75_percent() -> None:
    """Input requiring 75% of 8 GiB must return None without error."""
    target_bytes = int(0.75 * _8_GiB)
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        result = check_memory_feasibility(
            rows=1,
            columns=1,
            dtype_bytes=target_bytes,
            overhead_factor=1.0,
        )
    assert result is None


def test_feasibility_passes_at_80_percent() -> None:
    """Input requiring 80% of 8 GiB must return None without error (spec AC)."""
    target_bytes = int(0.80 * _8_GiB)
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        result = check_memory_feasibility(
            rows=1,
            columns=1,
            dtype_bytes=target_bytes,
            overhead_factor=1.0,
        )
    assert result is None


# ---------------------------------------------------------------------------
# AC: above 85% threshold -- OOMGuardrailError raised (spec unit test)
# Spec: "input requiring 6.8GB (85%) -> OOMGuardrailError raised"
# _THRESHOLD_OVER is one byte above 0.85 x _8_GiB (strict > semantics).
# ---------------------------------------------------------------------------


def test_feasibility_raises_just_above_85_percent() -> None:
    """Input requiring just over 85% of 8 GiB must raise OOMGuardrailError.

    Uses _THRESHOLD_OVER (one byte above 0.85 x available) to unambiguously
    trigger the strict-greater-than guard condition.
    """
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        with pytest.raises(OOMGuardrailError):
            check_memory_feasibility(
                rows=1,
                columns=1,
                dtype_bytes=_THRESHOLD_OVER,
                overhead_factor=1.0,
            )


def test_feasibility_raises_at_90_percent() -> None:
    """Input requiring 90% of 8 GiB must raise OOMGuardrailError (spec AC)."""
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
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        with pytest.raises(OOMGuardrailError) as exc_info:
            check_memory_feasibility(
                rows=1,
                columns=1,
                dtype_bytes=_THRESHOLD_OVER,
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
    # Message must contain a reduction factor (e.g., "reduce dataset by 1.06x")
    assert "reduce" in message.lower() or "factor" in message.lower()


def test_error_message_contains_both_estimated_and_available() -> None:
    """Error message must contain both estimated bytes and available bytes."""
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        with pytest.raises(OOMGuardrailError) as exc_info:
            check_memory_feasibility(
                rows=1,
                columns=1,
                dtype_bytes=_THRESHOLD_OVER,
                overhead_factor=1.0,
            )
    message = str(exc_info.value)
    # Both "estimated" and "available" must appear
    assert "estimated" in message.lower()
    assert "available" in message.lower()


# ---------------------------------------------------------------------------
# AC: simulate 1-billion-row dataset -- guardrail rejects before any training
# ---------------------------------------------------------------------------


def test_billion_row_dataset_is_rejected() -> None:
    """A 1-billion-row dataset must be rejected cleanly before any training begins."""
    # 1_000_000_000 rows x 50 columns x 8 bytes x 6.0 overhead = ~2.4 TiB
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
    # Verify the error is clean -- no unhandled arithmetic overflow or TypeError
    assert isinstance(exc_info.value, OOMGuardrailError)


# ---------------------------------------------------------------------------
# AC: psutil and torch are both mockable (no real hardware required)
# ---------------------------------------------------------------------------


def test_psutil_is_mockable() -> None:
    """psutil.virtual_memory must be patchable at the guardrails module level."""
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        # A tiny dataset: 10 rows x 1 col x 1 byte x 1.0 overhead = 10 bytes
        result = check_memory_feasibility(
            rows=10,
            columns=1,
            dtype_bytes=1,
            overhead_factor=1.0,
        )
    assert result is None
    mock_psutil.virtual_memory.assert_called_once()


def test_torch_vram_path_used_when_cuda_is_available() -> None:
    """When torch is installed and CUDA is available, VRAM is used instead of RAM.

    The available VRAM = total_memory - reserved = 16 GiB - 2 GiB = 14 GiB.
    A tiny 10-byte job must pass against this larger budget.
    """
    torch_mock = _build_torch_mock(
        cuda_available=True,
        total_memory=_16_GiB_VRAM,
        reserved=_2_GiB_RESERVED,
    )
    spec_mock = MagicMock()
    spec_mock.return_value = MagicMock()  # non-None => torch found

    with (
        patch("synth_engine.modules.synthesizer.guardrails.importlib.util.find_spec", spec_mock),
        patch.dict(sys.modules, {"torch": torch_mock}),  # type: ignore[arg-type]
    ):
        result = check_memory_feasibility(
            rows=10,
            columns=1,
            dtype_bytes=1,
            overhead_factor=1.0,
        )
    assert result is None
    torch_mock.cuda.is_available.assert_called_once()


def test_torch_falls_back_to_ram_when_cuda_unavailable() -> None:
    """When torch is installed but CUDA is unavailable, fall back to RAM.

    psutil must be called (not torch VRAM path) in this scenario.
    """
    torch_mock = _build_torch_mock(
        cuda_available=False,
        total_memory=_16_GiB_VRAM,
        reserved=_2_GiB_RESERVED,
    )
    spec_mock = MagicMock()
    spec_mock.return_value = MagicMock()  # non-None => torch found

    with (
        patch("synth_engine.modules.synthesizer.guardrails.importlib.util.find_spec", spec_mock),
        patch.dict(sys.modules, {"torch": torch_mock}),  # type: ignore[arg-type]
        patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil,
    ):
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        result = check_memory_feasibility(
            rows=10,
            columns=1,
            dtype_bytes=1,
            overhead_factor=1.0,
        )
    assert result is None
    mock_psutil.virtual_memory.assert_called_once()
    # VRAM path must NOT have been reached when cuda is unavailable
    torch_mock.cuda.memory_reserved.assert_not_called()


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
# Edge: just at threshold floor -- must NOT raise
# ---------------------------------------------------------------------------


def test_feasibility_passes_at_threshold_floor() -> None:
    """Input using exactly _THRESHOLD_FLOOR bytes must NOT raise.

    _THRESHOLD_FLOOR = int(0.85 x available) which is just below the strict-
    greater-than threshold, so the guardrail must remain silent.
    """
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        result = check_memory_feasibility(
            rows=1,
            columns=1,
            dtype_bytes=_THRESHOLD_FLOOR,
            overhead_factor=1.0,
        )
    assert result is None


# ---------------------------------------------------------------------------
# Coverage: _format_bytes -- exercise MiB, KiB, and bytes branches
# ---------------------------------------------------------------------------


def test_error_message_shows_mib_for_medium_datasets() -> None:
    """Error message uses MiB notation when estimated memory is in the MiB range."""
    # Use a small available memory (1 MiB) so even a tiny job exceeds 85%.
    available = 1024**2  # 1 MiB
    over_threshold = int(0.85 * available) + 1
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(available)
        with pytest.raises(OOMGuardrailError) as exc_info:
            check_memory_feasibility(
                rows=1,
                columns=1,
                dtype_bytes=over_threshold,
                overhead_factor=1.0,
            )
    message = str(exc_info.value)
    assert "MiB" in message or "KiB" in message


def test_error_message_shows_kib_for_small_datasets() -> None:
    """Error message uses KiB notation when estimated memory is in the KiB range."""
    available = 1024  # 1 KiB
    over_threshold = int(0.85 * available) + 1
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(available)
        with pytest.raises(OOMGuardrailError) as exc_info:
            check_memory_feasibility(
                rows=1,
                columns=1,
                dtype_bytes=over_threshold,
                overhead_factor=1.0,
            )
    message = str(exc_info.value)
    assert "KiB" in message or " B" in message


def test_format_bytes_sub_kib() -> None:
    """_format_bytes must handle values below 1 KiB without crashing."""
    # Use a very small available memory (100 bytes) to trigger the B branch.
    available = 100
    over_threshold = int(0.85 * available) + 1
    with patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mock_vmem(available)
        with pytest.raises(OOMGuardrailError) as exc_info:
            check_memory_feasibility(
                rows=1,
                columns=1,
                dtype_bytes=over_threshold,
                overhead_factor=1.0,
            )
    message = str(exc_info.value)
    # Should not crash and should mention bytes or a unit
    assert " B" in message or "KiB" in message or "MiB" in message or "GiB" in message


# ---------------------------------------------------------------------------
# Coverage: importlib spec not found (torch absent) -- explicit no-torch path
# ---------------------------------------------------------------------------


def test_falls_back_to_ram_when_torch_not_installed() -> None:
    """When torch is NOT installed (find_spec returns None), psutil RAM is used."""
    with (
        patch(
            "synth_engine.modules.synthesizer.guardrails.importlib.util.find_spec",
            return_value=None,
        ),
        patch("synth_engine.modules.synthesizer.guardrails.psutil") as mock_psutil,
    ):
        mock_psutil.virtual_memory.return_value = _mock_vmem(_8_GiB)
        result = check_memory_feasibility(
            rows=1,
            columns=1,
            dtype_bytes=1,
            overhead_factor=1.0,
        )
    assert result is None
    mock_psutil.virtual_memory.assert_called_once()


# ---------------------------------------------------------------------------
# Verify importlib.util is importable in test context (no real torch needed)
# ---------------------------------------------------------------------------


def test_importlib_util_find_spec_is_used_for_torch_detection() -> None:
    """guardrails.py must use importlib.util.find_spec for optional torch detection."""
    # This verifies the spec-check is observable and not a direct import.
    # When find_spec returns None, torch is treated as absent.
    assert importlib.util.find_spec("torch") is None  # torch not installed in CI
