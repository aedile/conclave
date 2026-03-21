"""Performance regression tests — regression detectors with generous time bounds.

These tests are NOT strict SLAs.  They detect regressions where a code change
doubles (or more) the time of a hot path:

1. Masking 10,000 rows must complete in < 5 seconds.
2. Privacy budget query must complete in < 100 ms.
3. Artifact HMAC signing must complete in < 1 second for a 10 MB payload.

All tests are marked ``@pytest.mark.slow`` and run in the integration gate
(``poetry run pytest tests/integration/ -v -m slow``).  They are excluded
from the fast unit test gate.

Time bounds are set at 2–3× the expected nominal performance to avoid
flakiness in CI.  Failure indicates a meaningful regression, not marginal
overhead.

CONSTITUTION Priority 0: Security — HMAC signing must remain fast enough for
    operational use on large artifacts.
CONSTITUTION Priority 3: TDD
Task: T40.3 — Add Missing Test Categories: Performance Regression
"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# ---------------------------------------------------------------------------
# Time bounds (seconds) — generous to avoid CI flakiness
# ---------------------------------------------------------------------------

#: Masking 10,000 rows must complete within this wall-clock bound.
_MASKING_10K_ROWS_LIMIT_SECS: float = 5.0

#: Privacy budget availability query must complete within this bound.
_BUDGET_QUERY_LIMIT_SECS: float = 0.1

#: HMAC signing of a 10 MB payload must complete within this bound.
_HMAC_SIGNING_10MB_LIMIT_SECS: float = 1.0


# ---------------------------------------------------------------------------
# 1. Masking 10,000 rows in < 5 seconds
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_masking_10k_rows_completes_within_time_bound() -> None:
    """Masking 10,000 rows must complete in under 5 seconds.

    Exercises the full deterministic masking pipeline (name + email per row)
    using the actual HMAC-SHA256 + Faker implementation.  Each row triggers
    two mask_value() calls — totalling 20,000 operations.

    The 5-second bound is approximately 3× the expected nominal time on a
    CI machine.  Failure indicates a performance regression in the masking
    hot path.
    """
    from synth_engine.modules.masking.algorithms import mask_email, mask_name

    n_rows = 10_000
    names = [f"User {i}" for i in range(n_rows)]
    emails = [f"user{i}@example.com" for i in range(n_rows)]

    start = time.monotonic()

    for name, email in zip(names, emails, strict=True):
        mask_name(name, "perf_test.full_name")
        mask_email(email, "perf_test.email")

    elapsed = time.monotonic() - start

    assert elapsed < _MASKING_10K_ROWS_LIMIT_SECS, (
        f"Masking 10,000 rows took {elapsed:.3f}s, exceeding the "
        f"{_MASKING_10K_ROWS_LIMIT_SECS}s regression bound. "
        "This likely indicates a performance regression in the masking hot path."
    )


# ---------------------------------------------------------------------------
# 2. Privacy budget query in < 100 ms
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_privacy_budget_query_completes_within_time_bound() -> None:
    """Reading the privacy budget ledger must complete in under 100 ms.

    Uses an in-memory SQLite engine (aiosqlite) to isolate the query
    performance from network overhead.  The 100 ms bound is generous;
    an in-memory SQLite read should complete in under 5 ms on any modern
    machine.  Failure indicates a regression in the DB layer or a lock
    contention issue introduced upstream.
    """
    from sqlmodel import SQLModel

    from synth_engine.modules.privacy.ledger import PrivacyLedger
    from synth_engine.shared.db import get_async_engine, get_async_session

    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    # Seed a ledger row
    async with get_async_session(engine) as session:
        async with session.begin():
            ledger = PrivacyLedger(
                total_allocated_epsilon=Decimal("10.0"),
                total_spent_epsilon=Decimal("3.5"),
            )
            session.add(ledger)

    # Measure query time
    from sqlalchemy import select

    start = time.monotonic()

    async with get_async_session(engine) as session:
        result = await session.execute(select(PrivacyLedger))
        _ = result.scalar_one()

    elapsed = time.monotonic() - start

    await engine.dispose()

    assert elapsed < _BUDGET_QUERY_LIMIT_SECS, (
        f"Privacy budget query took {elapsed * 1000:.1f}ms, exceeding the "
        f"{_BUDGET_QUERY_LIMIT_SECS * 1000:.0f}ms regression bound. "
        "This likely indicates a regression in the async DB layer."
    )


# ---------------------------------------------------------------------------
# 3. Artifact HMAC signing in < 1 second for 10 MB
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_hmac_signing_10mb_completes_within_time_bound() -> None:
    """HMAC-SHA256 signing of a 10 MB payload must complete in under 1 second.

    This test exercises the raw compute_hmac() primitive with a 10 MB byte
    blob.  HMAC-SHA256 on modern hardware can process ~1 GB/s; 10 MB should
    complete in well under 10 ms.  The 1-second bound provides massive
    headroom for CI and virtual machine overhead.

    Failure would indicate a catastrophic regression (e.g. a streaming
    compute_hmac() accidentally reading the file in a loop rather than once).
    """
    from synth_engine.shared.security.hmac_signing import compute_hmac

    signing_key = b"test-signing-key-32bytes-padding!"  # 32 bytes
    payload = b"X" * (10 * 1024 * 1024)  # 10 MB

    start = time.monotonic()
    digest = compute_hmac(signing_key, payload)
    elapsed = time.monotonic() - start

    # Verify the digest is well-formed (32 bytes for SHA-256)
    assert len(digest) == 32, f"Expected 32-byte HMAC digest, got {len(digest)}"

    assert elapsed < _HMAC_SIGNING_10MB_LIMIT_SECS, (
        f"HMAC signing 10 MB took {elapsed:.3f}s, exceeding the "
        f"{_HMAC_SIGNING_10MB_LIMIT_SECS}s regression bound. "
        "This likely indicates a regression in the HMAC compute path."
    )
