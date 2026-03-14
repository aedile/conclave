"""Unit tests for the MaskingRegistry with collision prevention.

The registry uses a two-phase collision-prevention strategy:
  Phase 1 — Retry (max 10 attempts): re-derive seed with counter-suffixed input.
  Phase 2 — Suffix: append a numeric suffix to the output when retries are exhausted.

This guarantees uniqueness for arbitrarily large datasets including the mandatory
100 000-record backlog test.
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
    by patching the underlying algorithm.  The first call returns a colliding value
    and the retry returns a unique value.
    """
    registry = MaskingRegistry()
    salt = "t.col"

    colliding_value = "John Doe"
    unique_value = "Jane Roe"
    call_count = 0

    def patched_mask_name(value: str, salt_arg: str, max_length: int | None = None) -> str:
        nonlocal call_count
        call_count += 1
        # First call (Alice): returns colliding_value → stored
        # Second call (Bob attempt 0): returns colliding_value → collision detected
        # Third call (Bob attempt 1, retry): returns unique_value → accepted
        if call_count in {1, 2}:
            return colliding_value
        return unique_value

    with patch("synth_engine.modules.masking.registry.mask_name", patched_mask_name):
        first = registry.mask("Alice", ColumnType.NAME, salt)
        assert first == colliding_value

        # Second mask — will collide on first attempt, then recover on retry
        second = registry.mask("Bob", ColumnType.NAME, salt)
        assert second != first
        assert second == unique_value


def test_registry_suffix_phase_triggers_when_retries_exhausted() -> None:
    """When all retry attempts collide, the registry appends a numeric suffix.

    This verifies Phase 2 of the collision-prevention strategy: suffix-based
    disambiguation for large datasets where Faker's output space is exhausted.
    """
    registry = MaskingRegistry()
    salt = "t.col"
    constant_value = "Always Same"

    def always_same(value: str, salt_arg: str, max_length: int | None = None) -> str:
        return constant_value

    with patch("synth_engine.modules.masking.registry.mask_name", always_same):
        # First call succeeds — no collision yet
        first = registry.mask("Alice", ColumnType.NAME, salt)
        assert first == constant_value

        # Second call — all 10 retries return constant_value, suffix phase kicks in
        second = registry.mask("Bob", ColumnType.NAME, salt)
        assert second != first
        assert second.startswith(constant_value)  # Suffix appended to base


def test_registry_collision_error_is_importable() -> None:
    """CollisionError is importable (defensive guard class must exist)."""
    assert issubclass(CollisionError, Exception)


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


def test_registry_apply_raises_for_unregistered_column_type() -> None:
    """_apply() raises ValueError when called with a non-member ColumnType value.

    This tests the `case _:` default arm added to guarantee -> str annotation
    correctness and prevent silent None returns for future ColumnType additions.

    ColumnType inherits from str, so a plain str subclass with an unknown value
    will not match any named case arm and will fall to `case _:`.
    """
    registry = MaskingRegistry()

    class _UnknownType(str):
        """A str subclass that does not equal any declared ColumnType member."""

    fake_type = _UnknownType("totally_unknown")
    with pytest.raises(ValueError, match="No masking algorithm registered for"):
        registry._apply(fake_type, "test-value", "t.col", None)  # type: ignore[arg-type]


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
        # Without reset, Phase 2 suffix would kick in
        registry.reset()
        # After reset, the same masked value is allowed again (no collision)
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
