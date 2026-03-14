"""MaskingRegistry — maps column types to masking algorithms with collision prevention.

The registry maintains a per-salt set of already-emitted masked values.  When a
collision is detected (two different real values mapping to the same fake value
within the same salt domain), the registry appends a counter suffix to the
original value and retries the deterministic hash (max 10 attempts).  If all
10 retry hashes produce a colliding output, a unique numeric suffix is appended
directly to the masked output, guaranteeing uniqueness for arbitrarily large
datasets.

Call reset() between independent table processing runs to clear the seen-value
state.
"""

from enum import Enum

from synth_engine.modules.masking.algorithms import (
    mask_credit_card,
    mask_email,
    mask_name,
    mask_phone,
    mask_ssn,
)

_MAX_RETRIES: int = 10


class CollisionError(Exception):
    """Raised when collision prevention logic encounters an unexpected state.

    Under the current two-phase strategy (retry then suffix) this should never
    be raised in production.  It is kept as a defensive guard against
    implementation bugs.
    """


class ColumnType(str, Enum):
    """Supported PII column types for deterministic masking."""

    NAME = "name"
    EMAIL = "email"
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    PHONE = "phone"


class MaskingRegistry:
    """Maps column types to masking algorithms.

    Collision prevention uses a two-phase strategy:

    Phase 1 — Retry (max 10 attempts): re-derive the seed using
    ``f"{value}_{attempt}"`` as the input so the hash changes.  This covers
    the common case where a handful of collisions exist in a small dataset.

    Phase 2 — Suffix: if all 10 retry attempts still collide (e.g., for very
    large datasets where Faker's output space is exhausted), a deterministic
    numeric suffix is appended to the masked value to guarantee uniqueness
    without any further hashing.  This ensures the 100 000-record no-collision
    guarantee is always met.

    The combination of both phases means CollisionError is never raised under
    normal operation; it is retained as a defensive guard only.

    Example:
        >>> registry = MaskingRegistry()
        >>> masked = registry.mask("Alice Smith", ColumnType.NAME, "users.name")
        >>> registry.reset()  # Call between table processing runs
    """

    def __init__(self) -> None:
        """Initialise the registry with an empty collision-prevention store."""
        self._seen: dict[str, set[str]] = {}
        # Tracks how many times each base masked value has been emitted per salt.
        self._suffix_counters: dict[str, dict[str, int]] = {}

    def mask(
        self,
        value: str,
        column_type: ColumnType,
        salt: str,
        max_length: int | None = None,
    ) -> str:
        """Apply the registered algorithm for column_type with collision prevention.

        Args:
            value: The plaintext PII value to mask.
            column_type: The ColumnType enum member identifying the algorithm.
            salt: Domain-separation salt (convention: "table.column").
            max_length: Optional VARCHAR constraint forwarded to the algorithm.

        Returns:
            A deterministic masked string unique within the current salt domain.

        Raises:
            CollisionError: Should never occur in practice; kept as a guard.
        """
        seen_for_salt = self._seen.setdefault(salt, set())
        counters_for_salt = self._suffix_counters.setdefault(salt, {})

        # Phase 1: retry with counter-suffixed input (up to _MAX_RETRIES)
        for attempt in range(_MAX_RETRIES):
            candidate_value = value if attempt == 0 else f"{value}_{attempt}"
            masked = self._apply(column_type, candidate_value, salt, max_length)
            if masked not in seen_for_salt:
                seen_for_salt.add(masked)
                return masked

        # Phase 2: all retry hashes collide — append a unique numeric suffix to
        # the base masked value to guarantee output uniqueness.
        base_masked = self._apply(column_type, value, salt, max_length)
        occurrence = counters_for_salt.get(base_masked, 0) + 1
        counters_for_salt[base_masked] = occurrence
        suffixed = f"{base_masked}_{occurrence}"

        # Defensive guard — should be unreachable given unique suffixes.
        if suffixed in seen_for_salt:
            raise CollisionError(
                f"Unexpected collision on suffixed value '{suffixed}' "
                f"for salt='{salt}'.  This is an implementation bug."
            )
        seen_for_salt.add(suffixed)
        return suffixed

    def reset(self) -> None:
        """Clear the collision-prevention registry.

        Call this between independent table-processing runs to allow the same
        masked values to be reused across tables.
        """
        self._seen.clear()
        self._suffix_counters.clear()

    def _apply(
        self,
        column_type: ColumnType,
        value: str,
        salt: str,
        max_length: int | None,
    ) -> str:
        """Dispatch to the correct masking algorithm for the given ColumnType.

        Args:
            column_type: The ColumnType enum member identifying the algorithm.
            value: The (potentially counter-suffixed) value to mask.
            salt: Domain-separation salt.
            max_length: Optional VARCHAR constraint.

        Returns:
            The masked string from the appropriate algorithm.
        """
        match column_type:
            case ColumnType.NAME:
                return mask_name(value, salt, max_length=max_length)
            case ColumnType.EMAIL:
                return mask_email(value, salt, max_length=max_length)
            case ColumnType.SSN:
                return mask_ssn(value, salt)
            case ColumnType.CREDIT_CARD:
                return mask_credit_card(value, salt)
            case ColumnType.PHONE:
                return mask_phone(value, salt, max_length=max_length)
