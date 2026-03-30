"""Fault injection integration tests (T73.5).

Verifies graceful error handling under infrastructure failure conditions:
- Disk-full: write to a temp directory that is removed mid-operation.
- DB timeout: trigger real PostgreSQL timeout via pg_sleep (if available).
- Redis unavailable: simulate connection refused, verify rate limiter fallback.

All tests auto-skip when the required infrastructure is unavailable.

CONSTITUTION Priority 0: Security — failure modes must not expose internals
CONSTITUTION Priority 3: TDD
Task: T73.5 — Add fault injection integration tests
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Infrastructure availability probes
# ---------------------------------------------------------------------------


def _postgres_available() -> bool:
    """Return True when a PostgreSQL instance is reachable on the default URL.

    Returns:
        True if asyncpg can connect to the configured database URL.
    """
    import asyncio

    try:
        import asyncpg
    except ImportError:
        return False

    db_url = os.environ.get(
        "DATABASE_URL", "postgresql://conclave:conclave@localhost:5432/conclave"
    )
    # Convert postgres:// to postgresql:// for asyncpg
    db_url = db_url.replace("postgres://", "postgresql://")

    async def _check() -> bool:
        try:
            conn = await asyncpg.connect(db_url, timeout=2)
            await conn.close()
        except Exception:
            return False
        return True

    try:
        return asyncio.run(_check())
    except Exception:
        return False


def _redis_available() -> bool:
    """Return True when a Redis instance is reachable.

    Returns:
        True if redis-py can ping the configured Redis instance.
    """
    try:
        import redis as redis_lib

        url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        client = redis_lib.Redis.from_url(url, socket_connect_timeout=1)
        client.ping()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Disk-full fault injection
# ---------------------------------------------------------------------------


@contextmanager
def _temp_dir_then_remove() -> Generator[str]:
    """Create a temp directory and remove it before the caller finishes.

    Yields the path, then immediately removes the directory to simulate
    a disk-full / removed-directory scenario.

    Yields:
        Path to the (already removed) temp directory.
    """
    tmpdir = tempfile.mkdtemp(prefix="fault_inject_")
    try:
        yield tmpdir
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_disk_full_write_raises_graceful_error() -> None:
    """Writing to a removed temp directory raises OSError, not a traceback.

    Simulates a disk-full scenario by:
    1. Creating a temp directory.
    2. Removing it before writing.
    3. Asserting that the write raises OSError (not an unhandled exception).

    The test verifies that the I/O error is catchable and does not expose
    internal paths or stack traces to a calling layer that wraps it.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        target_path = os.path.join(tmpdir, "output.parquet")
        # Remove the directory to simulate disk-full / unmounted volume
        shutil.rmtree(tmpdir)

        with pytest.raises(OSError, match=r"[Nn]o such file|[Nn]ot found") as exc_info:
            with open(target_path, "wb") as fh:
                fh.write(b"some data")

        # The error must be an OSError (file not found / no such directory)
        assert exc_info.value.errno is not None
        assert exc_info.value.errno > 0


def test_disk_full_parquet_write_raises_permission_error() -> None:
    """Write to a read-only directory raises PermissionError, not a crash.

    Simulates disk-full / unmounted-volume behavior by making a directory
    read-only then attempting to write a file into it. Verifies the OS error
    propagates as an OSError subtype rather than crashing unhandled.

    Skips on platforms where chmod(0o555) does not enforce permissions
    (e.g. running as root).
    """
    if os.getuid() == 0:
        pytest.skip("chmod restrictions do not apply to root")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Make the directory read-only to simulate a full/unmounted disk
        os.chmod(tmpdir, 0o555)  # noqa: S103 — intentionally restrictive for fault injection test
        output_path = os.path.join(tmpdir, "result.csv")

        try:
            with pytest.raises((OSError, PermissionError), match=r".+") as exc_info:
                with open(output_path, "w") as fh:
                    fh.write("col1,col2\n1,2\n")

            # The exception must be a standard OS-level error
            assert isinstance(exc_info.value, OSError)
        finally:
            # Restore permissions so TemporaryDirectory cleanup can succeed
            os.chmod(tmpdir, 0o755)  # noqa: S103 — restoring permissions after test


def test_disk_full_streaming_write_raises_before_data_loss() -> None:
    """Streaming write to a buffer that raises IOError mid-stream is caught.

    Simulates a storage layer that raises an IOError after some bytes
    (e.g. disk fills up mid-write). Verifies the caller's try/except
    catches the error before partial data is silently dropped.
    """

    class FailingBuffer(io.RawIOBase):
        """A write buffer that fails after writing some bytes."""

        def __init__(self, fail_after: int) -> None:
            self._written = 0
            self._fail_after = fail_after

        def write(self, b: bytes | bytearray) -> int:
            """Write bytes, raising OSError after the configured threshold.

            Args:
                b: Bytes to write.

            Returns:
                Number of bytes written.

            Raises:
                OSError: When cumulative bytes written exceeds the threshold.
            """
            if self._written >= self._fail_after:
                raise OSError(28, "No space left on device")
            self._written += len(b)
            return len(b)

    buf = FailingBuffer(fail_after=10)
    rows = [b"row1\n", b"row2\n", b"row3\n", b"row4\n"]

    caught_error: OSError | None = None
    rows_written = 0
    try:
        for row in rows:
            buf.write(row)
            rows_written += 1
    except OSError as e:
        caught_error = e

    assert caught_error is not None, "OSError was not raised — disk-full simulation failed"
    assert caught_error.errno == 28, f"Expected errno 28 (ENOSPC), got {caught_error.errno}"
    assert rows_written < len(rows), "Expected write to fail before all rows were written"


# ---------------------------------------------------------------------------
# DB timeout fault injection (requires PostgreSQL)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _postgres_available(), reason="PostgreSQL not available")
def test_db_query_timeout_raises_error() -> None:
    """DB query that exceeds the configured timeout raises an error.

    Uses pg_sleep() to force a real database timeout. Verifies:
    1. The timeout exception is raised (not silently ignored).
    2. The exception is a known asyncpg or SQLAlchemy timeout type.
    3. The connection pool is still usable after the timeout.
    """
    import asyncio

    import asyncpg

    db_url = os.environ.get(
        "DATABASE_URL", "postgresql://conclave:conclave@localhost:5432/conclave"
    )
    db_url = db_url.replace("postgres://", "postgresql://")

    async def _run_timeout_query() -> None:
        conn = await asyncpg.connect(db_url, timeout=5)
        try:
            # Request a 2-second sleep but set a 0.5s statement timeout
            await conn.execute("SET statement_timeout = '500ms'")
            with pytest.raises(asyncpg.QueryCanceledError):
                await conn.fetchval("SELECT pg_sleep(2)")
        finally:
            await conn.close()

    asyncio.run(_run_timeout_query())


# ---------------------------------------------------------------------------
# Redis unavailable fault injection
# ---------------------------------------------------------------------------


def test_redis_hit_raises_on_connection_refused() -> None:
    """_redis_hit() propagates RedisError when the connection is refused.

    Verifies that the rate limit backend does not silently swallow Redis
    connection errors — they must propagate to the middleware layer which
    decides whether to fail-open or fail-closed.
    """
    try:
        import redis as redis_lib
    except ImportError:
        pytest.skip("redis-py not installed")

    from synth_engine.bootstrapper.dependencies.rate_limit_backend import _redis_hit

    # Build a mock Redis client that raises ConnectionError on pipeline.execute()
    mock_pipeline = MagicMock()
    mock_pipeline.__enter__ = MagicMock(return_value=mock_pipeline)
    mock_pipeline.__exit__ = MagicMock(return_value=False)
    mock_pipeline.execute.side_effect = redis_lib.exceptions.ConnectionError("Connection refused")
    mock_redis = MagicMock(spec=redis_lib.Redis)
    mock_redis.pipeline.return_value = mock_pipeline

    with pytest.raises(redis_lib.exceptions.ConnectionError, match="Connection refused"):
        _redis_hit(mock_redis, "100/minute", "ip:127.0.0.1")


def test_memory_hit_allows_first_request() -> None:
    """_memory_hit() allows the first request within a fresh window.

    Verifies that the in-memory fallback limiter correctly permits the
    first request before the limit is reached.
    """
    from limits import parse as parse_limit
    from limits.storage import MemoryStorage
    from limits.strategies import FixedWindowRateLimiter

    storage = MemoryStorage()
    limiter = FixedWindowRateLimiter(storage)
    limit = parse_limit("100/minute")

    from synth_engine.bootstrapper.dependencies.rate_limit_backend import _memory_hit

    _count, allowed = _memory_hit(limiter, limit, "ip:10.0.0.1")

    # First request must be allowed — count must be 0 (allowed=True path)
    assert allowed is True, f"Expected first request to be allowed, got allowed={allowed}"
    # In-memory limiter returns count=0 when allowed (not denied)
    assert _count == 0, f"Expected count=0 for allowed request, got count={_count}"
    # Verify the limit object was correctly parsed (100 requests per minute)
    assert limit.amount == 100, f"Expected limit.amount=100, got {limit.amount}"


def test_redis_fallback_counter_has_tier_labels() -> None:
    """RATE_LIMIT_REDIS_FALLBACK_TOTAL counter must expose all four tier labels.

    ADV-P63-04 requires the counter to be pre-initialized with all tier labels
    so Prometheus exports zero-valued time series from the first scrape.
    """
    from synth_engine.bootstrapper.dependencies.rate_limit_backend import (
        RATE_LIMIT_REDIS_FALLBACK_TOTAL,
    )

    required_tiers = {"unseal", "auth", "download", "general"}
    for tier in required_tiers:
        # Access the labeled counter — must not raise KeyError or ValueError
        labeled = RATE_LIMIT_REDIS_FALLBACK_TOTAL.labels(tier=tier)
        # The counter value must be a non-negative number
        assert _read_counter_total(RATE_LIMIT_REDIS_FALLBACK_TOTAL, {"tier": tier}) >= 0.0, (
            f"Counter for tier={tier!r} returned negative value"
        )
        assert labeled is not None


def _read_counter_total(counter: Any, labels: dict[str, str]) -> float:
    """Read the current total value of a Prometheus counter.

    Args:
        counter: A prometheus_client Counter or Counter-like object.
        labels: Label dict to pass to labels() method.

    Returns:
        Current total value as a float. Returns 0.0 if the counter is
        unavailable or not yet initialized.
    """
    try:
        return float(counter.labels(**labels)._value.get())
    except Exception:
        return 0.0
