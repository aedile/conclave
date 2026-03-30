"""Unit tests for per-type masking algorithms.

RED phase: these tests must fail before implementation exists.

Task: T40.1 — Replace Shallow Assertions With Value-Checking Tests
Task: T49.2 — Assertion Hardening: Masking & Subsetting Tests
"""

import re
from collections.abc import Callable

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
_ALT_SALT = "other_table.column"


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


def test_mask_name_returns_non_empty_string_differing_from_input() -> None:
    """mask_name returns a non-empty string that differs from the original input.

    A type-only check (isinstance) does not verify masking actually occurred.
    This test asserts the result is a non-empty string AND is not identical
    to the plaintext input, proving the masking function transforms the value.
    """
    original = "Alice Smith"
    result = mask_name(original, _SALT)
    assert isinstance(result, str)
    assert len(result) > 0
    assert result != original, (
        f"mask_name must not return the original input verbatim: got {result!r}"
    )


def test_mask_name_max_length_zero_returns_empty_string() -> None:
    """When max_length=0, output is truncated to an empty string."""
    result = mask_name("Alice Smith", _SALT, max_length=0)
    assert result == ""


def test_mask_name_salt_sensitivity() -> None:
    """Different salts produce different outputs for the same name (T49.2).

    Validates that mask_name participates in the column-namespaced salt
    scheme: the same plaintext name must map to a different fake name when
    the salt (column identity) changes.
    """
    result_primary = mask_name("Alice Smith", _SALT)
    result_alt = mask_name("Alice Smith", _ALT_SALT)
    assert result_primary != result_alt, (
        f"mask_name('Alice Smith', {_SALT!r}) must differ from "
        f"mask_name('Alice Smith', {_ALT_SALT!r})"
    )


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


def test_mask_first_name_returns_non_empty_string_differing_from_input() -> None:
    """mask_first_name returns a non-empty string that differs from the original input.

    A type-only check does not verify masking occurred.  This test asserts
    the result is non-empty AND not identical to the plaintext first name.
    """
    from synth_engine.modules.masking.algorithms import mask_first_name

    original = "Alice"
    result = mask_first_name(original, _SALT)
    assert isinstance(result, str)
    assert len(result) > 0
    assert result != original, (
        f"mask_first_name must not return the original input verbatim: got {result!r}"
    )


def test_mask_first_name_empty_input_is_deterministic() -> None:
    """mask_first_name with empty string input is deterministic."""
    from synth_engine.modules.masking.algorithms import mask_first_name

    assert mask_first_name("", _SALT) == mask_first_name("", _SALT)


def test_mask_first_name_salt_sensitivity() -> None:
    """Different salts produce different outputs for the same first name (T49.2)."""
    from synth_engine.modules.masking.algorithms import mask_first_name

    result_primary = mask_first_name("Alice", _SALT)
    result_alt = mask_first_name("Alice", _ALT_SALT)
    assert result_primary != result_alt, (
        f"mask_first_name('Alice', {_SALT!r}) must differ from "
        f"mask_first_name('Alice', {_ALT_SALT!r})"
    )


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


def test_mask_last_name_returns_non_empty_string_differing_from_input() -> None:
    """mask_last_name returns a non-empty string that differs from the original input.

    A type-only check does not verify masking occurred.  This test asserts
    the result is non-empty AND not identical to the plaintext last name.
    """
    from synth_engine.modules.masking.algorithms import mask_last_name

    original = "Smith"
    result = mask_last_name(original, _SALT)
    assert isinstance(result, str)
    assert len(result) > 0
    assert result != original, (
        f"mask_last_name must not return the original input verbatim: got {result!r}"
    )


def test_mask_last_name_empty_input_is_deterministic() -> None:
    """mask_last_name with empty string input is deterministic."""
    from synth_engine.modules.masking.algorithms import mask_last_name

    assert mask_last_name("", _SALT) == mask_last_name("", _SALT)


def test_mask_last_name_salt_sensitivity() -> None:
    """Different salts produce different outputs for the same last name (T49.2)."""
    from synth_engine.modules.masking.algorithms import mask_last_name

    result_primary = mask_last_name("Smith", _SALT)
    result_alt = mask_last_name("Smith", _ALT_SALT)
    assert result_primary != result_alt, (
        f"mask_last_name('Smith', {_SALT!r}) must differ from "
        f"mask_last_name('Smith', {_ALT_SALT!r})"
    )


# ---------------------------------------------------------------------------
# mask_address (P21-T21.2)
# ---------------------------------------------------------------------------


def test_mask_address_is_deterministic() -> None:
    """Masking the same address with the same salt always returns the same result."""
    from synth_engine.modules.masking.algorithms import mask_address

    original = "79402 Peterson Drives Apt. 511, Davisstad, PA 35172"
    assert mask_address(original, _SALT) == mask_address(original, _SALT)


def test_mask_address_returns_non_empty_string_differing_from_input() -> None:
    """mask_address returns a non-empty string that differs from the original address.

    A type-only check does not verify masking occurred.  This test asserts
    the result is non-empty AND not identical to the plaintext address.
    """
    from synth_engine.modules.masking.algorithms import mask_address

    original = "79402 Peterson Drives Apt. 511, Davisstad, PA 35172"
    result = mask_address(original, _SALT)
    assert isinstance(result, str)
    assert len(result) > 0
    assert result != original, (
        f"mask_address must not return the original input verbatim: got {result!r}"
    )


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


def test_mask_address_salt_sensitivity() -> None:
    """Different salts produce different outputs for the same address (T49.2)."""
    from synth_engine.modules.masking.algorithms import mask_address

    original = "79402 Peterson Drives Apt. 511, Davisstad, PA 35172"
    result_primary = mask_address(original, _SALT)
    result_alt = mask_address(original, _ALT_SALT)
    assert result_primary != result_alt, (
        "mask_address must produce different outputs for different salts"
    )


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


def test_mask_email_salt_sensitivity() -> None:
    """Different salts produce different outputs for the same email (T49.2)."""
    result_primary = mask_email("alice@example.com", _SALT)
    result_alt = mask_email("alice@example.com", _ALT_SALT)
    assert result_primary != result_alt, (
        "mask_email must produce different outputs for different salts"
    )


# ---------------------------------------------------------------------------
# Parametrized determinism tests — all mask functions at once (T49.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mask_fn", "value"),
    [
        (mask_name, "Alice Smith"),
        (mask_email, "alice@example.com"),
        (mask_phone, "555-867-5309"),
        (mask_ssn, "123-45-6789"),
        (mask_credit_card, "4111111111111111"),
    ],
    ids=["mask_name", "mask_email", "mask_phone", "mask_ssn", "mask_credit_card"],
)
def test_mask_function_is_deterministic(mask_fn: Callable[..., str], value: str) -> None:
    """Each mask function returns the same result for the same (value, salt) pair.

    Parametrized to avoid copy-paste test patterns and ensure all mask functions
    are verified in a single, readable sweep.  Each ID is human-readable.
    """
    result_a = mask_fn(value, _SALT)
    result_b = mask_fn(value, _SALT)
    assert result_a == result_b, (
        f"{mask_fn.__name__}({value!r}, {_SALT!r}) is not deterministic: "
        f"first call={result_a!r}, second call={result_b!r}"
    )


@pytest.mark.parametrize(
    ("mask_fn", "value"),
    [
        (mask_name, "Alice Smith"),
        (mask_email, "alice@example.com"),
        (mask_phone, "555-867-5309"),
    ],
    ids=["mask_name-salt-sensitive", "mask_email-salt-sensitive", "mask_phone-salt-sensitive"],
)
def test_mask_function_salt_sensitivity(mask_fn: Callable[..., str], value: str) -> None:
    """Each mask function produces different output when the salt changes (T49.2).

    Different salt -> different output is a core privacy property: the same
    plaintext value appearing in different columns must map to different masked
    values so that cross-column re-identification is not possible.
    """
    result_primary = mask_fn(value, _SALT)
    result_alt = mask_fn(value, _ALT_SALT)
    assert result_primary != result_alt, (
        f"{mask_fn.__name__}({value!r}) must produce different output for different salts: "
        f"{_SALT!r} -> {result_primary!r}, {_ALT_SALT!r} -> {result_alt!r}"
    )


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


def test_mask_ssn_salt_sensitivity() -> None:
    """Different salts produce different SSNs for the same input (T49.2)."""
    result_primary = mask_ssn("123-45-6789", _SALT)
    result_alt = mask_ssn("123-45-6789", _ALT_SALT)
    assert result_primary != result_alt, (
        "mask_ssn must produce different outputs for different salts"
    )


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


def test_mask_credit_card_salt_sensitivity() -> None:
    """Different salts produce different card numbers for the same input (T49.2)."""
    result_primary = mask_credit_card("4111111111111111", _SALT)
    result_alt = mask_credit_card("4111111111111111", _ALT_SALT)
    assert result_primary != result_alt, (
        "mask_credit_card must produce different outputs for different salts"
    )


# ---------------------------------------------------------------------------
# luhn_check
# ---------------------------------------------------------------------------


def test_luhn_check_valid_number() -> None:
    """luhn_check returns True for a known valid LUHN number."""
    # Visa test card — well-known valid LUHN number
    assert luhn_check("4111111111111111") is True
    assert luhn_check("4111111111111111")


def test_luhn_check_invalid_number() -> None:
    """luhn_check returns False for an invalid LUHN number."""
    assert luhn_check("1234567890123456") is False
    assert not luhn_check("1234567890123456")


def test_luhn_check_with_spaces() -> None:
    """luhn_check passes the raw spaced input without pre-stripping.

    luhn_check must handle spaces itself by filtering non-digit characters
    internally (via str.isdigit()), so callers should NOT pre-strip spaces.
    """
    # Pass the raw spaced string — luhn_check must handle spaces itself.
    assert luhn_check("4111 1111 1111 1111") is True
    assert luhn_check("4111 1111 1111 1111")


def test_luhn_check_empty_string() -> None:
    """luhn_check returns False for an empty string (no digits to validate)."""
    assert luhn_check("") is False
    assert not luhn_check("")


def test_luhn_check_non_digit_input() -> None:
    """luhn_check returns False when input contains no digits at all."""
    assert luhn_check("abcdefghijk") is False
    assert not luhn_check("abcdefghijk")


# ---------------------------------------------------------------------------
# mask_phone
# ---------------------------------------------------------------------------


def test_mask_phone_is_deterministic() -> None:
    """Masking the same phone number with the same salt always returns the same result."""
    assert mask_phone("555-867-5309", _SALT) == mask_phone("555-867-5309", _SALT)


def test_mask_phone_returns_non_empty_string_differing_from_input() -> None:
    """mask_phone returns a non-empty string that differs from the original phone number.

    A type-only check does not verify masking occurred.  This test asserts
    the result is non-empty AND not identical to the plaintext phone number.
    """
    original = "555-867-5309"
    result = mask_phone(original, _SALT)
    assert isinstance(result, str)
    assert len(result) > 0
    assert result != original, (
        f"mask_phone must not return the original input verbatim: got {result!r}"
    )


def test_mask_phone_respects_max_length() -> None:
    """Masked phone is truncated to max_length when provided."""
    result = mask_phone("555-867-5309", _SALT, max_length=10)
    assert len(result) <= 10


def test_mask_phone_salt_sensitivity() -> None:
    """Different salts produce different phone numbers for the same input (T49.2)."""
    result_primary = mask_phone("555-867-5309", _SALT)
    result_alt = mask_phone("555-867-5309", _ALT_SALT)
    assert result_primary != result_alt, (
        "mask_phone must produce different outputs for different salts"
    )


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
    """deterministic_hash with max_length=10 returns a str of exactly the truncated length.

    The truncation must be deterministic: calling with the same arguments
    a second time must return the identical string.  The return type must be
    str, and the length must be at most max_length.
    """
    result_a = deterministic_hash("x", "y", max_length=10)
    result_b = deterministic_hash("x", "y", max_length=10)
    assert isinstance(result_a, str), "max_length variant must return str"
    assert len(result_a) <= 10
    assert len(result_a) > 0, "max_length variant must not produce an empty string"
    assert result_a == result_b, "max_length variant must be deterministic"


def test_deterministic_hash_max_length_none_no_truncation() -> None:
    """deterministic_hash with max_length=None (default) returns an int, no truncation."""
    result = deterministic_hash("x", "y", max_length=None)
    assert isinstance(result, int), "Without max_length, return type must be int"
    # The int result must be positive (hash of non-empty inputs)
    assert result > 0


def test_deterministic_hash_length_zero_raises_value_error() -> None:
    """deterministic_hash raises ValueError when length=0 (must be >= 1).

    A length of zero would result in int.from_bytes of an empty byte slice,
    yielding a constant 0 for all inputs and silently breaking determinism.
    The lower-bound guard makes this constraint explicit and symmetric with
    the upper-bound guard for length > 32.
    """
    with pytest.raises(ValueError, match="length"):
        deterministic_hash("x", "y", length=0)


# ---------------------------------------------------------------------------
# mask_value edge cases — salt boundary inputs (T36.4)
# ---------------------------------------------------------------------------


def test_mask_value_empty_salt_is_deterministic() -> None:
    """mask_value with an empty salt string is deterministic across calls.

    An empty salt is unusual but must not silently fail or raise.
    The same (value, salt="") pair must always produce the same output.
    """
    from synth_engine.modules.masking.deterministic import mask_value

    result_a = mask_value("Alice", "", lambda f: f.name())
    result_b = mask_value("Alice", "", lambda f: f.name())
    assert result_a == result_b, (
        "mask_value with empty salt must be deterministic: "
        f"first call returned {result_a!r}, second returned {result_b!r}"
    )


def test_mask_value_none_salt_raises_attribute_error() -> None:
    """mask_value with None as salt raises AttributeError on .encode().

    The salt parameter is typed as str.  Passing None is a caller error;
    the function must not silently produce wrong output — it will raise
    AttributeError when calling None.encode() inside deterministic_hash.
    This test documents the failure mode explicitly.
    """
    from synth_engine.modules.masking.deterministic import mask_value

    with pytest.raises((AttributeError, TypeError)):
        mask_value("Alice", None, lambda f: f.name())  # type: ignore[arg-type]


def test_mask_value_special_char_salt_is_deterministic() -> None:
    """mask_value with a salt containing special characters is deterministic.

    Special characters in the salt (e.g. unicode, punctuation, null-adjacent
    chars) must not break HMAC computation or produce non-deterministic output.
    """
    from synth_engine.modules.masking.deterministic import mask_value

    special_salt = "table\u2019s.col\u00fcmn!@#$%^&*()"
    result_a = mask_value("Bob", special_salt, lambda f: f.name())
    result_b = mask_value("Bob", special_salt, lambda f: f.name())
    assert result_a == result_b, (
        f"mask_value with special-char salt must be deterministic: {result_a!r} != {result_b!r}"
    )


def test_mask_value_null_bytes_in_salt_is_deterministic() -> None:
    """mask_value with null bytes embedded in the salt string is deterministic.

    Null bytes are valid Python string characters but may cause issues in
    C-extension encoding paths.  HMAC-SHA256 handles arbitrary byte sequences;
    this test verifies the Python .encode('utf-8') path handles null bytes too.
    """
    from synth_engine.modules.masking.deterministic import mask_value

    null_salt = "col\x00umn"
    result_a = mask_value("Charlie", null_salt, lambda f: f.name())
    result_b = mask_value("Charlie", null_salt, lambda f: f.name())
    assert result_a == result_b, (
        f"mask_value with null-byte salt must be deterministic: {result_a!r} != {result_b!r}"
    )
