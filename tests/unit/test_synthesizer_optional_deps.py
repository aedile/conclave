"""Unit tests for _optional_deps.py — centralized optional import registry (T43.2).

Verifies that:
  - The module exports the expected names.
  - ``require_synthesizer()`` raises ``ImportError`` with a clear, actionable
    message when the synthesizer group is not installed.
  - The ``TORCH_AVAILABLE`` sentinel reflects actual availability.

Task: P43-T43.2 — Consolidate Optional Import Pattern
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Tests: module-level exports
# ---------------------------------------------------------------------------


class TestOptionalDepsExports:
    """Verify _optional_deps.py exposes the expected public names."""

    def test_module_is_importable(self) -> None:
        """_optional_deps can be imported without raising."""
        import synth_engine.modules.synthesizer._optional_deps as m  # noqa: F401

        assert m is not None

    def test_exports_torch(self) -> None:
        """Module exports 'torch' at module scope (may be None)."""
        import synth_engine.modules.synthesizer._optional_deps as m

        assert hasattr(m, "torch")

    def test_exports_nn(self) -> None:
        """Module exports 'nn' at module scope (may be None)."""
        import synth_engine.modules.synthesizer._optional_deps as m

        assert hasattr(m, "nn")

    def test_exports_dataloader(self) -> None:
        """Module exports 'DataLoader' at module scope (may be None)."""
        import synth_engine.modules.synthesizer._optional_deps as m

        assert hasattr(m, "DataLoader")

    def test_exports_tensordataset(self) -> None:
        """Module exports 'TensorDataset' at module scope (may be None)."""
        import synth_engine.modules.synthesizer._optional_deps as m

        assert hasattr(m, "TensorDataset")

    def test_exports_torch_available_bool(self) -> None:
        """Module exports TORCH_AVAILABLE as a bool sentinel."""
        import synth_engine.modules.synthesizer._optional_deps as m

        assert isinstance(m.TORCH_AVAILABLE, bool)

    def test_exports_require_synthesizer_callable(self) -> None:
        """Module exports require_synthesizer as a callable."""
        from synth_engine.modules.synthesizer._optional_deps import require_synthesizer

        assert callable(require_synthesizer)


# ---------------------------------------------------------------------------
# Tests: require_synthesizer() — normal path (synthesizer group installed)
# ---------------------------------------------------------------------------


class TestRequireSynthesizerInstalled:
    """Tests for require_synthesizer() when torch is available."""

    def test_require_synthesizer_does_not_raise_when_torch_available(self) -> None:
        """require_synthesizer() returns None without raising when torch is present."""
        import synth_engine.modules.synthesizer._optional_deps as m

        if not m.TORCH_AVAILABLE:
            pytest.skip("torch not installed — skipping availability path")

        from synth_engine.modules.synthesizer._optional_deps import require_synthesizer

        # Must not raise
        result = require_synthesizer()
        assert result is None


# ---------------------------------------------------------------------------
# Tests: require_synthesizer() — absent path (synthesizer group missing)
# ---------------------------------------------------------------------------


class TestRequireSynthesizerMissing:
    """Tests for require_synthesizer() when torch is NOT available."""

    def test_require_synthesizer_raises_import_error_when_torch_none(self) -> None:
        """require_synthesizer() raises ImportError when TORCH_AVAILABLE is False."""
        import synth_engine.modules.synthesizer._optional_deps as m

        original_available = m.TORCH_AVAILABLE
        original_torch = m.torch
        try:
            m.TORCH_AVAILABLE = False  # type: ignore[assignment]
            m.torch = None
            with pytest.raises(ImportError):
                m.require_synthesizer()
        finally:
            m.TORCH_AVAILABLE = original_available  # type: ignore[assignment]
            m.torch = original_torch

    def test_require_synthesizer_error_message_mentions_poetry_install(self) -> None:
        """ImportError message includes actionable install instructions."""
        import synth_engine.modules.synthesizer._optional_deps as m

        original_available = m.TORCH_AVAILABLE
        original_torch = m.torch
        try:
            m.TORCH_AVAILABLE = False  # type: ignore[assignment]
            m.torch = None
            with pytest.raises(ImportError, match="poetry install --with synthesizer"):
                m.require_synthesizer()
        finally:
            m.TORCH_AVAILABLE = original_available  # type: ignore[assignment]
            m.torch = original_torch

    def test_require_synthesizer_error_message_mentions_torch(self) -> None:
        """ImportError message names 'torch' so the user knows what's missing."""
        import synth_engine.modules.synthesizer._optional_deps as m

        original_available = m.TORCH_AVAILABLE
        original_torch = m.torch
        try:
            m.TORCH_AVAILABLE = False  # type: ignore[assignment]
            m.torch = None
            with pytest.raises(ImportError, match="(?i)torch"):
                m.require_synthesizer()
        finally:
            m.TORCH_AVAILABLE = original_available  # type: ignore[assignment]
            m.torch = original_torch


# ---------------------------------------------------------------------------
# Tests: TORCH_AVAILABLE sentinel consistency
# ---------------------------------------------------------------------------


class TestTorchAvailableSentinel:
    """TORCH_AVAILABLE must reflect actual torch reachability."""

    def test_torch_available_is_true_when_torch_not_none(self) -> None:
        """If torch is not None, TORCH_AVAILABLE must be True."""
        import synth_engine.modules.synthesizer._optional_deps as m

        if m.torch is not None:
            assert m.TORCH_AVAILABLE is True, (
                "TORCH_AVAILABLE must be True when torch module was imported successfully"
            )

    def test_torch_available_is_false_when_torch_is_none(self) -> None:
        """If torch is None, TORCH_AVAILABLE must be False."""
        import synth_engine.modules.synthesizer._optional_deps as m

        if m.torch is None:
            assert m.TORCH_AVAILABLE is False, (
                "TORCH_AVAILABLE must be False when torch module import failed"
            )
