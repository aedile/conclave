"""Per-type deterministic masking algorithms.

Each function deterministically masks a specific PII column type using
HMAC-SHA256-seeded Faker instances.  The same (value, salt) pair always
produces the same masked output, making these functions safe for repeated
runs without violating referential integrity across a table.
"""

from synth_engine.modules.masking.deterministic import mask_value


def mask_name(value: str, salt: str, max_length: int | None = None) -> str:
    """Deterministically mask a person's name using Faker.

    Args:
        value: The original name to mask.
        salt: Domain-separation salt (e.g. "users.full_name").
        max_length: Optional VARCHAR constraint; output is truncated if exceeded.

    Returns:
        A deterministic fake name string.
    """
    return mask_value(value, salt, lambda f: f.name(), max_length=max_length)


def mask_email(value: str, salt: str, max_length: int | None = None) -> str:
    """Deterministically mask an email address.

    Args:
        value: The original email address to mask.
        salt: Domain-separation salt (e.g. "users.email").
        max_length: Optional VARCHAR constraint; output is truncated if exceeded.

    Returns:
        A deterministic fake email string containing '@'.
    """
    return mask_value(value, salt, lambda f: f.email(), max_length=max_length)


def mask_ssn(value: str, salt: str) -> str:
    """Deterministically mask a US SSN in XXX-XX-XXXX format.

    Args:
        value: The original SSN to mask.
        salt: Domain-separation salt (e.g. "employees.ssn").

    Returns:
        A deterministic fake SSN matching the pattern \\d{3}-\\d{2}-\\d{4}.
    """
    return mask_value(value, salt, lambda f: f.ssn())


def mask_credit_card(value: str, salt: str) -> str:
    """Deterministically mask a credit card number that passes LUHN check.

    Uses Faker.credit_card_number() seeded deterministically.  Faker generates
    LUHN-valid numbers by default, so the output of this function will always
    pass a LUHN algorithm check.

    Args:
        value: The original credit card number to mask.
        salt: Domain-separation salt (e.g. "payments.card_number").

    Returns:
        A deterministic credit card number (digits only) that passes LUHN.
    """
    return mask_value(value, salt, lambda f: f.credit_card_number(card_type=None))


def mask_phone(value: str, salt: str, max_length: int | None = None) -> str:
    """Deterministically mask a phone number.

    Args:
        value: The original phone number to mask.
        salt: Domain-separation salt (e.g. "contacts.phone").
        max_length: Optional VARCHAR constraint; output is truncated if exceeded.

    Returns:
        A deterministic fake phone number string.
    """
    return mask_value(value, salt, lambda f: f.phone_number(), max_length=max_length)


def luhn_check(number: str) -> bool:
    """Verify that a credit card number passes the LUHN algorithm.

    Args:
        number: The credit card number as a string of digits (no spaces/dashes).

    Returns:
        True if the number is LUHN-valid, False otherwise.
    """
    digits = [int(d) for d in number if d.isdigit()]
    if not digits:
        return False
    # Double every second digit from the right
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            doubled = digit * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += digit
    return total % 10 == 0
