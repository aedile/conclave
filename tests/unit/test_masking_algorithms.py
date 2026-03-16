"""Unit tests for per-type masking algorithms.

RED phase: these tests must fail before implementation exists.
"""

import re

import pytest

from synth_engine.modules.masking.algorithms import (
    luhn_check,
    mask_credit_card,
    mask_email,
    mask_name,
    mask_phone,
    mask_ssn,
)
from synth_engine.modules.masking.deterministic import deterministic_hash

_SALT = "test_table.column"


# ---------------------------------------------------------------------------
# mask_name
# ---------------------------------------------------------------------------


def test_mask_name_is_deterministic() -> None:
    """Masking the same name with the same salt always returns the same result."""
    assert mask_name("Alice Smith", _SALT) == mask_name("Alice Smith", _SALT)


def test_mask_name_respects_max_length() -> None:
    """Masked name is truncated to max_length when provided."""
    result = mask_name("Alice Smith", _SALT, max_length=5)
    assert len(result) <= 5


def test_mask_name_returns_string() -> None:
    """mask_name returns a non-empty string."""
    result = mask_name("Alice Smith", _SALT)
    assert isinstance(result, str)
    assert len(result) > 0


def test_mask_name_max_length_zero_returns_empty_string() -> None:
    """When max_length=0, output is truncated to an empty string."""
    result = mask_name("Alice Smith", _SALT, max_length=0)
    assert result == ""


# ---------------------------------------------------------------------------
# mask_first_name (P21-T21.2)
# ---------------------------------------------------------------------------


def test_mask_first_name_is_deterministic() -> None:
    """Masking the same first_name with the same salt always returns the same result."""
    from synth_engine.modules.masking.algorithms import mask_first_name

    assert mask_first_name("Alice", _SALT) == mask_first_name("Alice", _SALT)


def test_mask_first_name_returns_single_word() -> None:
    """mask_first_name output must contain NO spaces (single word only).

    This is the key assertion that catches the mask_name bug where Faker.name()
    produces "First Last" (two words) instead of a single first name.
    """
    from synth_engine.modules.masking.algorithms import mask_first_name

    result = mask_first_name("Alice", _SALT)
    assert " " not in result, (
        f"mask_first_name must return a single word, got: '{result}'. "
        "Use Faker.first_name(), not Faker.name()."
    )


def test_mask_first_name_respects_max_length() -> None:
    """Masked first_name is truncated to max_length when provided."""
    from synth_engine.modules.masking.algorithms import mask_first_name

    result = mask_first_name("Alice", _SALT, max_length=3)
    assert len(result) <= 3


def test_mask_first_name_returns_non_empty_string() -> None:
    """mask_first_name returns a non-empty string for a normal input."""
    from synth_engine.modules.masking.algorithms import mask_first_name

    result = mask_first_name("Alice", _SALT)
    assert isinstance(result, str)
    assert len(result) > 0


def test_mask_first_name_empty_input_is_deterministic() -> None:
    """mask_first_name with empty string input is deterministic."""
    from synth_engine.modules.masking.algorithms import mask_first_name

    assert mask_first_name("", _SALT) == mask_first_name("", _SALT)


# ---------------------------------------------------------------------------
# mask_last_name (P21-T21.2)
# ---------------------------------------------------------------------------


def test_mask_last_name_is_deterministic() -> None:
    """Masking the same last_name with the same salt always returns the same result."""
    from synth_engine.modules.masking.algorithms import mask_last_name

    assert mask_last_name("Smith", _SALT) == mask_last_name("Smith", _SALT)


def test_mask_last_name_returns_single_word() -> None:
    """mask_last_name output must contain NO spaces (single word only).

    This is the key assertion that catches the mask_name bug where Faker.name()
    produces "First Last" (two words) instead of a single last name.
    """
    from synth_engine.modules.masking.algorithms import mask_last_name

    result = mask_last_name("Smith", _SALT)
    assert " " not in result, (
        f"mask_last_name must return a single word, got: '{result}'. "
        "Use Faker.last_name(), not Faker.name()."
    )


def test_mask_last_name_respects_max_length() -> None:
    """Masked last_name is truncated to max_length when provided."""
    from synth_engine.modules.masking.algorithms import mask_last_name

    result = mask_last_name("Smith", _SALT, max_length=4)
    assert len(result) <= 4


def test_mask_last_name_returns_non_empty_string() -> None:
    """mask_last_name returns a non-empty string for a normal input."""
    from synth_engine.modules.masking.algorithms import mask_last_name

    result = mask_last_name("Smith", _SALT)
    assert isinstance(result, str)
    assert len(result) > 0


def test_mask_last_name_empty_input_is_deterministic() -> None:
    """mask_last_name with empty string input is deterministic."""
    from synth_engine.modules.masking.algorithms import mask_last_name

    assert mask_last_name("", _SALT) == mask_last_name("", _SALT)


# ---------------------------------------------------------------------------
# mask_address (P21-T21.2)
# ---------------------------------------------------------------------------


def test_mask_address_is_deterministic() -> None:
    """Masking the same address with the same salt always returns the same result."""
    from synth_engine.modules.masking.algorithms import mask_address

    original = "79402 Peterson Drives Apt. 511, Davisstad, PA 35172"
    assert mask_address(original, _SALT) == mask_address(original, _SALT)


def test_mask_address_returns_non_empty_string() -> None:
    """mask_address returns a non-empty string."""
    from synth_engine.modules.masking.algorithms import mask_address

    result = mask_address("79402 Peterson Drives Apt. 511, Davisstad, PA 35172", _SALT)
    assert isinstance(result, str)
    assert len(result) > 0


def test_mask_address_respects_max_length() -> None:
    """Masked address is truncated to max_length when provided."""
    from synth_engine.modules.masking.algorithms import mask_address

    result = mask_address(
        "79402 Peterson Drives Apt. 511, Davisstad, PA 35172",
        _SALT,
        max_length=20,
    )
    assert len(result) <= 20


def test_mask_address_empty_input_is_deterministic() -> None:
    """mask_address with empty string input is deterministic."""
    from synth_engine.modules.masking.algorithms import mask_address

    assert mask_address("", _SALT) == mask_address("", _SALT)


# ---------------------------------------------------------------------------
# mask_email
# ---------------------------------------------------------------------------


def test_mask_email_is_deterministic() -> None:
    """Masking the same email with the same salt always returns the same result."""
    assert mask_email("alice@example.com", _SALT) == mask_email("alice@example.com", _SALT)


def test_mask_email_respects_max_length() -> None:
    """Masked email is truncated to max_length when provided."""
    result = mask_email("alice@example.com", _SALT, max_length=15)
    assert len(result) <= 15


def test_mask_email_contains_at_sign() -> None:
    """Masked email contains an '@' character (valid email format)."""
    result = mask_email("alice@example.com", _SALT)
    assert "@" in result


# ---------------------------------------------------------------------------
# mask_ssn
# ---------------------------------------------------------------------------


def test_mask_ssn_format() -> None:
    """Masked SSN matches the XXX-XX-XXXX pattern."""
    result = mask_ssn("123-45-6789", _SALT)
    assert re.match(r"^\d{3}-\d{2}-\d{4}$", result), f"SSN format invalid: {result}"


def test_mask_ssn_is_deterministic() -> None:
    """Masking the same SSN with the same salt always returns the same result."""
    assert mask_ssn("123-45-6789", _SALT) == mask_ssn("123-45-6789", _SALT)


def test_mask_ssn_differs_from_original() -> None:
    """Masked SSN should not be identical to the original (statistical check)."""
    # Run multiple to reduce false-positive probability
    collisions = sum(
        1 for i in range(20) if mask_ssn(f"000-00-{i:04d}", _SALT) == f"000-00-{i:04d}"
    )
    # Allow at most 1 accidental match out of 20
    assert collisions <= 1


# ---------------------------------------------------------------------------
# mask_credit_card — MANDATORY BACKLOG TEST
# ---------------------------------------------------------------------------


def test_mask_credit_card_passes_luhn() -> None:
    """Masked credit card number MUST pass the LUHN algorithm check.

    This is the mandatory backlog acceptance test for T3.3.
    """
    result = mask_credit_card("4111111111111111", _SALT)
    assert luhn_check(result), f"Credit card '{result}' failed LUHN check"


def test_mask_credit_card_is_deterministic() -> None:
    """Masking the same card number with the same salt always returns the same result."""
    assert mask_credit_card("4111111111111111", _SALT) == mask_credit_card(
        "4111111111111111", _SALT
    )


def test_mask_credit_card_digits_only() -> None:
    """Masked credit card should contain only digits (no dashes or spaces)."""
    result = mask_credit_card("4111111111111111", _SALT)
    assert result.isdigit(), f"Credit card contains non-digit characters: {result}"


# ---------------------------------------------------------------------------
# luhn_check
# ---------------------------------------------------------------------------


def test_luhn_check_valid_number() -> None:
    """luhn_check returns True for a known valid LUHN number."""
    # Visa test card — well-known valid LUHN number
    assert luhn_check("4111111111111111") is True


def test_luhn_check_invalid_number() -> None:
    """luhn_check returns False for an invalid LUHN number."""
    assert luhn_check("1234567890123456") is False


def test_luhn_check_with_spaces() -> None:
    """luhn_check passes the raw spaced input without pre-stripping.

    luhn_check must handle spaces itself by filtering non-digit characters
    internally (via str.isdigit()), so callers should NOT pre-strip spaces.
    """
    # Pass the raw spaced string — luhn_check must handle spaces itself.
    assert luhn_check("4111 1111 1111 1111") is True


def test_luhn_check_empty_string() -> None:
    """luhn_check returns False for an empty string (no digits to validate)."""
    assert luhn_check("") is False


def test_luhn_check_non_digit_input() -> None:
    """luhn_check returns False when input contains no digits at all."""
    assert luhn_check("abcdefghijk") is False


# ---------------------------------------------------------------------------
# mask_phone
# ---------------------------------------------------------------------------


def test_mask_phone_is_deterministic() -> None:
    """Masking the same phone number with the same salt always returns the same result."""
    assert mask_phone("555-867-5309", _SALT) == mask_phone("555-867-5309", _SALT)


def test_mask_phone_returns_string() -> None:
    """mask_phone returns a non-empty string."""
    result = mask_phone("555-867-5309", _SALT)
    assert isinstance(result, str)
    assert len(result) > 0


def test_mask_phone_respects_max_length() -> None:
    """Masked phone is truncated to max_length when provided."""
    result = mask_phone("555-867-5309", _SALT, max_length=10)
    assert len(result) <= 10


# ---------------------------------------------------------------------------
# deterministic_hash — ADV-026 guard and max_length
# ---------------------------------------------------------------------------


def test_deterministic_hash_length_exceeds_32_raises_value_error() -> None:
    """deterministic_hash raises ValueError when length > 32 (HMAC-SHA256 digest is 32 bytes).

    Passing length=33 would silently produce an incorrect result by reading
    beyond the digest boundary; this guard makes the constraint explicit.
    """
    with pytest.raises(ValueError, match="length"):
        deterministic_hash("x", "y", length=33)


def test_deterministic_hash_max_length_truncates_deterministically() -> None:
    """deterministic_hash with max_length=10 returns a string of length <= 10.

    The truncation must be deterministic: calling with the same arguments
    a second time must return the identical string.
    """
    result_a = deterministic_hash("x", "y", max_length=10)
    result_b = deterministic_hash("x", "y", max_length=10)
    assert isinstance(result_a, str), "max_length variant must return str"
    assert len(result_a) <= 10
    assert result_a == result_b, "max_length variant must be deterministic"


def test_deterministic_hash_max_length_none_no_truncation() -> None:
    """deterministic_hash with max_length=None (default) returns an int, no truncation."""
    result = deterministic_hash("x", "y", max_length=None)
    assert isinstance(result, int), "Without max_length, return type must be int"


def test_deterministic_hash_length_zero_raises_value_error() -> None:
    """deterministic_hash raises ValueError when length=0 (must be >= 1).

    A length of zero would result in int.from_bytes of an empty byte slice,
    yielding a constant 0 for all inputs and silently breaking determinism.
    The lower-bound guard makes this constraint explicit and symmetric with
    the upper-bound guard for length > 32.
    """
    with pytest.raises(ValueError, match="length"):
        deterministic_hash("x", "y", length=0)
