"""Hierarchy verification tests for CollisionError and CycleDetectionError (T34.2).

AC1: CollisionError inherits SynthEngineError.
AC2: CycleDetectionError inherits SynthEngineError.
AC3: Both are importable from their original module locations (no relocation).

CONSTITUTION Priority 3: TDD RED phase — these tests are written before
changing the base classes in the implementation.

Task: P34-T34.2 — Consolidate Module-Local Exceptions Into Shared Hierarchy
Task: T40.1 — Replace Shallow Assertions With Value-Checking Tests
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Parameterized importability tests — AC3
# ---------------------------------------------------------------------------

_EXCEPTION_IMPORT_SPECS = [
    (
        "synth_engine.modules.masking.registry",
        "CollisionError",
    ),
    (
        "synth_engine.modules.mapping.graph",
        "CycleDetectionError",
    ),
]


@pytest.mark.parametrize(("module_path", "class_name"), _EXCEPTION_IMPORT_SPECS)
def test_exception_class_is_importable(module_path: str, class_name: str) -> None:
    """Each exception must remain importable from its original module location.

    The import itself is the test — if it raises ImportError the test fails.
    We then verify the object is an exception class, not merely truthy.
    """
    import importlib

    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    assert issubclass(cls, Exception), (
        f"{class_name} from {module_path} must be an Exception subclass"
    )


class TestCollisionErrorHierarchy:
    """AC1: CollisionError must inherit from SynthEngineError."""

    def test_collision_error_inherits_synth_engine_error(self) -> None:
        """CollisionError must be a subclass of SynthEngineError."""
        from synth_engine.modules.masking.registry import CollisionError
        from synth_engine.shared.exceptions import SynthEngineError

        assert issubclass(CollisionError, SynthEngineError)

    def test_collision_error_is_catchable_as_synth_engine_error(self) -> None:
        """Raising CollisionError must be catchable via SynthEngineError."""
        from synth_engine.modules.masking.registry import CollisionError
        from synth_engine.shared.exceptions import SynthEngineError

        caught = False
        try:
            raise CollisionError("unexpected collision on suffixed value")
        except SynthEngineError:
            caught = True

        assert caught == True, "CollisionError must be catchable as SynthEngineError"
        assert caught

    def test_collision_error_preserves_message(self) -> None:
        """CollisionError must preserve the error message."""
        from synth_engine.modules.masking.registry import CollisionError

        exc = CollisionError("collision on salt='users.email'")
        assert "collision on salt='users.email'" in str(exc)

    def test_collision_error_is_still_exception(self) -> None:
        """CollisionError must remain a base Exception for broad compatibility."""
        from synth_engine.modules.masking.registry import CollisionError

        assert issubclass(CollisionError, Exception)


class TestCycleDetectionErrorHierarchy:
    """AC2: CycleDetectionError must inherit from SynthEngineError."""

    def test_cycle_detection_error_inherits_synth_engine_error(self) -> None:
        """CycleDetectionError must be a subclass of SynthEngineError."""
        from synth_engine.modules.mapping.graph import CycleDetectionError
        from synth_engine.shared.exceptions import SynthEngineError

        assert issubclass(CycleDetectionError, SynthEngineError)

    def test_cycle_detection_error_is_catchable_as_synth_engine_error(self) -> None:
        """Raising CycleDetectionError must be catchable via SynthEngineError."""
        from synth_engine.modules.mapping.graph import CycleDetectionError
        from synth_engine.shared.exceptions import SynthEngineError

        caught = False
        try:
            raise CycleDetectionError(["orders", "customers", "orders"])
        except SynthEngineError:
            caught = True

        assert caught == True, "CycleDetectionError must be catchable as SynthEngineError"
        assert caught

    def test_cycle_detection_error_preserves_cycle_attribute(self) -> None:
        """CycleDetectionError must still carry the cycle attribute after base class change."""
        from synth_engine.modules.mapping.graph import CycleDetectionError

        cycle = ["table_a", "table_b", "table_a"]
        exc = CycleDetectionError(cycle)
        assert exc.cycle == cycle

    def test_cycle_detection_error_message_contains_cycle_repr(self) -> None:
        """CycleDetectionError message must still describe the cycle path."""
        from synth_engine.modules.mapping.graph import CycleDetectionError

        exc = CycleDetectionError(["alpha", "beta", "gamma"])
        msg = str(exc)
        assert "alpha" in msg
        assert "beta" in msg
        assert "gamma" in msg

    def test_cycle_detection_error_is_still_exception(self) -> None:
        """CycleDetectionError must remain a base Exception for broad compatibility."""
        from synth_engine.modules.mapping.graph import CycleDetectionError

        assert issubclass(CycleDetectionError, Exception)
