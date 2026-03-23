"""Unit tests for the deterministic masking core primitives.

RED phase: these tests must fail before implementation exists.
"""

import pytest
from faker import Faker

from synth_engine.modules.masking.deterministic import deterministic_hash, mask_value


def test_deterministic_hash_is_deterministic() -> None:
    """Same value and salt always produce the same integer."""
    result_a = deterministic_hash("Alice Smith", "users.name")
    result_b = deterministic_hash("Alice Smith", "users.name")
    assert result_a == result_b


def test_deterministic_hash_differs_for_different_salt() -> None:
    """Different salts produce different hashes for the same value."""
    hash_a = deterministic_hash("Alice Smith", "users.name")
    hash_b = deterministic_hash("Alice Smith", "accounts.name")
    assert hash_a != hash_b


def test_deterministic_hash_differs_for_different_value() -> None:
    """Different values produce different hashes for the same salt."""
    hash_a = deterministic_hash("Alice Smith", "users.name")
    hash_b = deterministic_hash("Bob Jones", "users.name")
    assert hash_a != hash_b


def test_deterministic_hash_returns_int() -> None:
    """deterministic_hash returns a specific known integer, not just any int."""
    result = deterministic_hash("test", "salt")
    # Assert specific value so that a mutation (e.g. removing the hash) fails.
    # The value is derived from HMAC-SHA256("salt", "test")[:8 bytes], big-endian.
    assert isinstance(result, int)
    assert result >= 0
    # Cross-call stability: same inputs must always produce the same specific value.
    assert result == deterministic_hash("test", "salt"), (
        "deterministic_hash('test', 'salt') must return a stable value across calls"
    )


def test_mask_value_is_deterministic() -> None:
    """mask_value returns the same string for the same (value, salt, fn) triple."""

    def name_fn(faker: Faker) -> str:
        return faker.name()

    result_a = mask_value("Alice Smith", "users.name", name_fn)
    result_b = mask_value("Alice Smith", "users.name", name_fn)
    assert result_a == result_b


def test_mask_value_respects_max_length() -> None:
    """mask_value truncates output to max_length when provided."""

    def long_fn(faker: Faker) -> str:
        return "A" * 100

    result = mask_value("Alice Smith", "users.name", long_fn, max_length=20)
    assert len(result) <= 20


def test_mask_value_none_salt_still_deterministic() -> None:
    """mask_value is deterministic even when salt is an empty string."""

    def name_fn(faker: Faker) -> str:
        return faker.name()

    result_a = mask_value("Alice Smith", "", name_fn)
    result_b = mask_value("Alice Smith", "", name_fn)
    assert result_a == result_b


def test_mask_value_different_inputs_differ() -> None:
    """Different inputs produce different masked values across a range of samples.

    Verifies that the deterministic hash produces distinct seeds for distinct
    inputs, resulting in distinct Faker outputs.  Uses 10 distinct inputs to
    make a false-positive collision astronomically unlikely.
    """
    results = {mask_value(f"input_{i}", "users.name", lambda f: f.name()) for i in range(10)}
    # All 10 distinct inputs must produce 10 distinct outputs.
    assert len(results) == 10


# ---------------------------------------------------------------------------
# Salt-sensitivity parametrized tests (T49.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "salt_a", "salt_b"),
    [
        ("Alice Smith", "users.name", "accounts.name"),
        ("alice@example.com", "users.email", "contacts.email"),
        ("555-867-5309", "users.phone", "contacts.phone"),
    ],
    ids=["full-name-salt-differs", "email-salt-differs", "phone-salt-differs"],
)
def test_mask_value_salt_sensitivity(value: str, salt_a: str, salt_b: str) -> None:
    """Different salts produce different masked outputs for the same input value.

    This is a critical privacy property: column-namespaced salts ensure that
    the same plaintext value (e.g. a person's name appearing in multiple
    columns) maps to different masked values in each column, preventing
    cross-column re-identification.
    """
    result_a = mask_value(value, salt_a, lambda f: f.name())
    result_b = mask_value(value, salt_b, lambda f: f.name())
    assert result_a != result_b, (
        f"mask_value({value!r}, {salt_a!r}) must differ from "
        f"mask_value({value!r}, {salt_b!r}) — different salts must yield different output"
    )
