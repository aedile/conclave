"""Core deterministic masking primitives.

Provides HMAC-SHA256-based hashing and a generic mask_value helper that seeds
a module-level Faker instance deterministically so that the same (value, salt)
pair always produces the same masked output.

A single module-level Faker instance is reused across calls for performance;
``seed_instance()`` resets its state fully before each use, preserving
determinism while avoiding per-call construction overhead (~7x speedup).

HMAC key design rationale (ADV-027)
-------------------------------------
The ``salt`` parameter passed to ``deterministic_hash`` (and through it to
HMAC) is a column-identity string such as ``"users.email"`` — **not** a secret.
This is intentional:

- The masking layer provides **determinism** and **format-preservation**:
  the same real value in the same column always maps to the same masked value,
  preserving referential integrity across tables.
- **Confidentiality** of the mapping (i.e. preventing an attacker from
  reversing masked values even if they have the code) is provided by a
  deployment-level ``MASKING_SALT`` environment variable injected at the
  CLI / bootstrapper layer (Phase 4, ADV-035).  That secret is combined
  with the column-identity salt at the call site, not inside this module.

Separating concerns this way keeps the masking layer stateless and testable
without requiring secret-management infrastructure.
"""

import hashlib
import hmac
from collections.abc import Callable
from typing import overload

from faker import Faker

# Module-level Faker instance.  seed_instance() fully resets internal state,
# so reuse is safe and deterministic.  Not shared across threads — callers must
# instantiate their own if concurrency is required.
_FAKER: Faker = Faker()

_HMAC_SHA256_DIGEST_BYTES: int = 32
"""Maximum bytes available from an HMAC-SHA256 digest."""


@overload
def deterministic_hash(
    value: str, salt: str, length: int = ..., *, max_length: None = ...
) -> int: ...


@overload
def deterministic_hash(value: str, salt: str, length: int = ..., *, max_length: int) -> str: ...


def deterministic_hash(
    value: str,
    salt: str,
    length: int = 8,
    *,
    max_length: int | None = None,
) -> int | str:
    """Produce a deterministic hash from value + salt using HMAC-SHA256.

    When called without ``max_length`` (the default), returns an integer
    derived from the first ``length`` bytes of the HMAC digest — suitable
    for seeding Faker.

    When called with ``max_length``, returns a hexadecimal string of the
    full digest truncated to ``max_length`` characters.  The result is
    deterministic and format-safe for use as a column identifier or key.

    Args:
        value: The plaintext value to hash.
        salt: A per-table/column salt for domain separation.  This is a
            column-identity string (e.g. ``"users.email"``), not a secret.
            See module docstring for the full design rationale.
        length: Number of bytes to use from the digest (1-32 inclusive).
            Ignored when ``max_length`` is provided.
        max_length: When provided, the function returns a hex string of the
            full digest truncated to the first ``max_length`` characters.
            When ``None`` (default), returns an integer.

    Returns:
        An integer derived from the HMAC digest when ``max_length`` is None,
        or a hex string truncated to ``max_length`` characters otherwise.

    Raises:
        ValueError: If ``length`` > 32 (exceeds HMAC-SHA256 digest size).
    """
    if length > _HMAC_SHA256_DIGEST_BYTES:
        raise ValueError(
            f"length {length} exceeds HMAC-SHA256 digest size ({_HMAC_SHA256_DIGEST_BYTES} bytes)"
        )

    digest = hmac.new(
        salt.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    if max_length is not None:
        return digest.hex()[:max_length]

    return int.from_bytes(digest[:length], "big")


def mask_value(
    value: str,
    salt: str,
    mask_fn: Callable[[Faker], str],
    max_length: int | None = None,
) -> str:
    """Apply a deterministic mask to value using a Faker-based mask function.

    Seeds the module-level Faker with deterministic_hash(value, salt) for
    reproducibility.  Truncates to max_length if provided.

    Args:
        value: Plaintext input.
        salt: Domain-separation salt (use "table.column" as convention).
        mask_fn: A callable that takes a seeded Faker instance and returns a
            masked string.
        max_length: Optional VARCHAR constraint; output is truncated if exceeded.

    Returns:
        Masked string, deterministic for the same (value, salt) inputs.
    """
    seed = deterministic_hash(value, salt)
    _FAKER.seed_instance(seed)
    result = mask_fn(_FAKER)
    if max_length is not None:
        result = result[:max_length]
    return result
