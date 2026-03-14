"""Unit tests for the MaskingRegistry with collision prevention.

RED phase: these tests must fail before implementation exists.
"""

import time
from unittest.mock import patch

import pytest

from synth_engine.modules.masking.registry import (
    CollisionError,
    ColumnType,
    MaskingRegistry,
)

_SALT = "test_table.name"


# ---------------------------------------------------------------------------
# Basic determinism
# ---------------------------------------------------------------------------


def test_registry_mask_name_is_deterministic() -> None:
    """Registry produces the same masked value for the same (value, salt) pair."""
    registry = MaskingRegistry()
    result_a = registry.mask("Alice Smith", ColumnType.NAME, _SALT)
    registry.reset()
    result_b = registry.mask("Alice Smith", ColumnType.NAME, _SALT)
    assert result_a == result_b


def test_registry_mask_email_is_deterministic() -> None:
    """Registry produces the same masked email for the same (value, salt) pair."""
    registry = MaskingRegistry()
    result_a = registry.mask("alice@example.com", ColumnType.EMAIL, "t.email")
    registry.reset()
    result_b = registry.mask("alice@example.com", ColumnType.EMAIL, "t.email")
    assert result_a == result_b


# ---------------------------------------------------------------------------
# 100,000 no-collision test — MANDATORY BACKLOG TEST
# ---------------------------------------------------------------------------


def test_registry_100k_no_collisions() -> None:
    """Generate 100,000 masked names and assert 0 collisions in the output set.

    This is the mandatory backlog acceptance test for T3.3.
    Runtime must complete within a reasonable time bound (< 60 seconds).
    """
    registry = MaskingRegistry()
    salt = "big_table.full_name"
    results: list[str] = []

    start = time.monotonic()
    for i in range(100_000):
        masked = registry.mask(f"name_{i}", ColumnType.NAME, salt)
        results.append(masked)
    elapsed = time.monotonic() - start

    # Zero collisions: the set size equals the list size
    unique_count = len(set(results))
    total_count = len(results)
    assert unique_count == total_count, (
        f"Collision detected: {total_count - unique_count} duplicate(s) "
        f"in {total_count} masked values"
    )

    # Performance guard: must complete in under 60 seconds
    assert elapsed < 60, f"100k masking took {elapsed:.1f}s — exceeds 60s budget"


# ---------------------------------------------------------------------------
# Collision prevention mechanism
# ---------------------------------------------------------------------------


def test_registry_collision_prevention_triggers() -> None:
    """When a collision is detected, the registry retries and returns a unique value.

    We force two different inputs to produce the same initial masked value
    by patching the underlying algorithm for the first two calls.
    """
    registry = MaskingRegistry()
    salt = "t.col"

    # Patch mask_name to return "John Doe" for both inputs on first call,
    # then a unique value on retry.
    call_count = 0
    colliding_value = "John Doe"
    unique_value = "Jane Roe"

    def patched_mask_name(value: str, salt_arg: str, max_length: int | None = None) -> str:
        nonlocal call_count
        call_count += 1
        # First two calls return the colliding value; subsequent calls are unique
        if call_count <= 2:
            return colliding_value
        return unique_value

    with patch("synth_engine.modules.masking.registry.mask_name", patched_mask_name):
        first = registry.mask("Alice", ColumnType.NAME, salt)
        assert first == colliding_value

        # Second mask with different input — will collide on first attempt, then recover
        second = registry.mask("Bob", ColumnType.NAME, salt)
        assert second == unique_value
        assert second != first


def test_registry_collision_exhaustion_raises() -> None:
    """CollisionError is raised when all 10 retry attempts produce collisions."""
    registry = MaskingRegistry()
    salt = "t.col"
    constant_value = "Always Same"

    def always_same(value: str, salt_arg: str, max_length: int | None = None) -> str:
        return constant_value

    with patch("synth_engine.modules.masking.registry.mask_name", always_same):
        # First call succeeds (no prior collision)
        registry.mask("Alice", ColumnType.NAME, salt)
        # Second call exhausts all retries
        with pytest.raises(CollisionError):
            registry.mask("Bob", ColumnType.NAME, salt)


# ---------------------------------------------------------------------------
# max_length constraint
# ---------------------------------------------------------------------------


def test_registry_max_length_respected() -> None:
    """Registry forwards max_length to the underlying algorithm."""
    registry = MaskingRegistry()
    result = registry.mask("Alice Smith", ColumnType.NAME, _SALT, max_length=5)
    assert len(result) <= 5


def test_registry_max_length_email() -> None:
    """Registry forwards max_length to the email algorithm."""
    registry = MaskingRegistry()
    result = registry.mask("alice@example.com", ColumnType.EMAIL, "t.email", max_length=15)
    assert len(result) <= 15


# ---------------------------------------------------------------------------
# Unknown column type
# ---------------------------------------------------------------------------


def test_registry_unknown_column_type_raises() -> None:
    """Registry raises ValueError for an unregistered column type string."""
    with pytest.raises(ValueError, match="not-a-type"):
        ColumnType("not-a-type")


# ---------------------------------------------------------------------------
# reset() clears seen set
# ---------------------------------------------------------------------------


def test_registry_reset_clears_seen() -> None:
    """reset() clears the collision-prevention registry."""
    registry = MaskingRegistry()
    salt = "t.col"

    constant_value = "Same Name"

    def always_same(value: str, salt_arg: str, max_length: int | None = None) -> str:
        return constant_value

    with patch("synth_engine.modules.masking.registry.mask_name", always_same):
        registry.mask("Alice", ColumnType.NAME, salt)
        # Without reset, this would collide and retry/fail
        registry.reset()
        # After reset, the same masked value is allowed again
        result = registry.mask("Bob", ColumnType.NAME, salt)
        assert result == constant_value


# ---------------------------------------------------------------------------
# All ColumnTypes are handled
# ---------------------------------------------------------------------------


def test_registry_handles_all_column_types() -> None:
    """Registry can mask every supported ColumnType without raising."""
    registry = MaskingRegistry()
    test_cases: list[tuple[str, ColumnType]] = [
        ("Alice Smith", ColumnType.NAME),
        ("alice@example.com", ColumnType.EMAIL),
        ("123-45-6789", ColumnType.SSN),
        ("4111111111111111", ColumnType.CREDIT_CARD),
        ("555-867-5309", ColumnType.PHONE),
    ]
    for value, col_type in test_cases:
        result = registry.mask(value, col_type, f"t.{col_type.value}")
        assert isinstance(result, str)
        assert len(result) > 0
