"""Negative/attack tests for thread-safe masking (T68.1).

Tests verify that mask_value() and deterministic_hash() are safe under concurrent
access with no cross-thread contamination or non-deterministic output.

ATTACK-FIRST TDD — these tests are written before the GREEN phase.
CONSTITUTION Priority 0: Security — thread-unsafe masking silently corrupts masked output
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T68.1 — Thread-Local Faker in Masking Module
"""

from __future__ import annotations

import concurrent.futures

import pytest
from faker import Faker

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Tests 1-4: Thread-safety of mask_value and deterministic_hash
# ---------------------------------------------------------------------------


def test_mask_value_thread_pool_reuse_preserves_determinism() -> None:
    """Thread pool reuse across calls must preserve per-(value, salt) determinism.

    Scenario: 10 threads, each making 1000 calls with the SAME (value, salt) pair.
    All results for the same pair must be identical.

    This is the core race condition: if the module-level Faker singleton is
    shared, seed_instance() in thread A can be overwritten by thread B between
    A's seed call and A's mask_fn call, producing a different result.
    """
    from synth_engine.modules.masking.deterministic import mask_value

    def _mask_fn(faker: Faker) -> str:
        return faker.name()

    value = "Alice Smith"
    salt = "users.name"
    expected = mask_value(value, salt, _mask_fn)

    def _worker(_: int) -> list[str]:
        return [mask_value(value, salt, _mask_fn) for _ in range(1000)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_worker, i) for i in range(10)]
        all_results = [r for f in futures for r in f.result()]

    unique_results = set(all_results)
    assert len(unique_results) == 1, (
        f"mask_value must be deterministic under concurrency; got {len(unique_results)} "
        f"distinct results instead of 1. First few: {list(unique_results)[:5]}"
    )
    assert next(iter(unique_results)) == expected, (
        f"Concurrent result '{next(iter(unique_results))}' must equal single-thread "
        f"baseline '{expected}'"
    )


def test_mask_value_concurrent_different_inputs_no_cross_contamination() -> None:
    """Concurrent calls with different (value, salt) pairs must not cross-contaminate.

    Each thread masks a unique (value, salt) pair. The output must always match
    the single-threaded deterministic result for that specific pair — never
    the result of another thread's pair.

    This catches the race condition where thread A's mask_fn(_FAKER) executes
    after thread B has reseeded the shared _FAKER singleton.
    """
    from synth_engine.modules.masking.deterministic import mask_value

    def _mask_fn(faker: Faker) -> str:
        return faker.name()

    # Compute single-threaded baseline for each of 20 unique (value, salt) pairs.
    pairs = [(f"user_{i}@example.com", f"table_{i}.email") for i in range(20)]
    baselines = {(v, s): mask_value(v, s, _mask_fn) for v, s in pairs}

    results: dict[tuple[str, str], list[str]] = {p: [] for p in pairs}

    def _worker(pair: tuple[str, str]) -> list[str]:
        value, salt = pair
        return [mask_value(value, salt, _mask_fn) for _ in range(200)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(_worker, pair): pair for pair in pairs}
        for future, pair in futures.items():
            results[pair] = future.result()

    for pair, res_list in results.items():
        expected = baselines[pair]
        wrong = [r for r in res_list if r != expected]
        assert len(wrong) == 0, (
            f"Cross-contamination detected for pair {pair}: "
            f"{len(wrong)}/{len(res_list)} results did not match baseline '{expected}'. "
            f"First wrong result: '{wrong[0]}'"
        )


def test_mask_value_thread_local_respects_max_length() -> None:
    """Concurrent calls with max_length set must all truncate correctly.

    max_length enforcement must be thread-safe: all concurrent results must be
    <= max_length and must match the single-threaded baseline for the same pair.
    """
    from synth_engine.modules.masking.deterministic import mask_value

    def _long_fn(faker: Faker) -> str:
        # Return a predictably long string based on faker.name()
        return faker.name() * 5

    value = "Jane Doe"
    salt = "users.full_name"
    max_len = 10

    baseline = mask_value(value, salt, _long_fn, max_length=max_len)
    assert len(baseline) <= max_len, f"Baseline must be <= {max_len} chars; got {len(baseline)}"

    def _worker(_: int) -> list[str]:
        return [mask_value(value, salt, _long_fn, max_length=max_len) for _ in range(500)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_worker, i) for i in range(8)]
        all_results = [r for f in futures for r in f.result()]

    for result in all_results:
        assert len(result) <= max_len, (
            f"Result '{result}' (len={len(result)}) exceeds max_length={max_len}"
        )
        assert result == baseline, (
            f"Concurrent result '{result}' does not match single-threaded baseline '{baseline}'"
        )


def test_deterministic_hash_is_thread_safe() -> None:
    """Concurrent calls to deterministic_hash must never raise exceptions.

    deterministic_hash uses only stdlib primitives (hmac, hashlib) which are
    GIL-safe. This test verifies no regression is introduced by the thread-local
    refactor (e.g., no accidental shared state in HMAC).
    """
    from synth_engine.modules.masking.deterministic import deterministic_hash

    # Pre-compute baseline
    pairs = [(f"value_{i}", f"salt_{i}") for i in range(50)]
    baselines = {(v, s): deterministic_hash(v, s) for v, s in pairs}

    exceptions: list[Exception] = []

    def _worker(pair: tuple[str, str]) -> list[int]:
        try:
            return [deterministic_hash(pair[0], pair[1]) for _ in range(100)]
        except Exception as exc:
            exceptions.append(exc)
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_worker, pair): pair for pair in pairs}
        results = {pair: future.result() for future, pair in futures.items()}

    assert len(exceptions) == 0, (
        f"deterministic_hash raised {len(exceptions)} exception(s) under concurrency: "
        f"{exceptions[:3]}"
    )

    for pair, res_list in results.items():
        expected = baselines[pair]
        wrong = [r for r in res_list if r != expected]
        assert len(wrong) == 0, (
            f"deterministic_hash returned wrong value for {pair}: "
            f"expected {expected}, got {wrong[0]}"
        )
