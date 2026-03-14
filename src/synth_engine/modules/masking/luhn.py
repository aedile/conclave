"""LUHN algorithm implementation for credit card number validation.

Provides a standalone ``luhn_check`` function that verifies whether a
digit string satisfies the LUHN (Luhn formula / mod-10) check.

This module is intentionally small and has zero dependencies outside the
Python standard library.  It exists as its own file so that:

- The masking engine's ``algorithms.py`` can import it clearly.
- Future synthesizer or privacy modules that need LUHN validation can
  import directly from here without taking on the full masking dependency tree.
- Vulture and import-linter see a clear, named public API.
"""


def luhn_check(number: str) -> bool:
    """Verify that a credit card number passes the LUHN algorithm.

    Args:
        number: The credit card number as a string of digits (spaces and
            dashes are ignored; all other non-digit characters also ignored).

    Returns:
        True if the number is LUHN-valid, False otherwise.
        Returns False for an empty string or a string with no digit characters.
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
