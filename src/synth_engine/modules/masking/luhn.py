"""LUHN algorithm implementation for credit card number validation.

Provides a standalone ``luhn_check`` function that verifies whether a
digit string satisfies the LUHN (Luhn formula / mod-10) check.

This module is intentionally small and has zero dependencies outside the
Python standard library.  It exists as its own file so that:

- The masking engine's ``algorithms.py`` can import it clearly.
- Vulture and import-linter see a clear, named public API.

Import boundary note
--------------------
``luhn_check`` is re-exported from ``algorithms.py`` for backward
compatibility.  All consumers **must** import through the module's public
API (``synth_engine.modules.masking``) — not directly from this submodule.
Cross-module imports into synthesizer, privacy, or any other module are
forbidden by the import-linter contracts defined in ``pyproject.toml``.
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
