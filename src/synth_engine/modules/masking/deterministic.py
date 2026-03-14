"""Core deterministic masking primitives.

Provides HMAC-SHA256-based hashing and a generic mask_value helper that seeds
a module-level Faker instance deterministically so that the same (value, salt)
pair always produces the same masked output.

A single module-level Faker instance is reused across calls for performance;
``seed_instance()`` resets its state fully before each use, preserving
determinism while avoiding per-call construction overhead (~7x speedup).
"""

import hashlib
import hmac
from collections.abc import Callable

from faker import Faker

# Module-level Faker instance.  seed_instance() fully resets internal state,
# so reuse is safe and deterministic.  Not shared across threads — callers must
# instantiate their own if concurrency is required.
_FAKER: Faker = Faker()


def deterministic_hash(value: str, salt: str, length: int = 8) -> int:
    """Produce a deterministic integer from value + salt using HMAC-SHA256.

    Args:
        value: The plaintext value to mask.
        salt: A per-table/column salt for domain separation.
        length: Number of bytes to use from the digest (max 32).

    Returns:
        An integer derived from the HMAC digest, suitable for seeding Faker.
    """
    digest = hmac.new(
        salt.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).digest()
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
