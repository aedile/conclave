"""FastAPI router for the readiness probe endpoint.

Implements ``GET /ready`` — a Kubernetes readiness probe that checks all
external dependencies before the application accepts traffic.

Dependency checks
-----------------
Three checks are performed concurrently via ``asyncio.gather``:

1. **database** — executes ``SELECT 1`` via the shared async SQLAlchemy engine
   from :func:`~synth_engine.shared.db.get_async_engine`.  The shared engine
   has ``pool_pre_ping=True`` and is reused across probes (no per-probe
   engine creation overhead).
2. **cache** — sends ``PING`` to Redis via the sync client (run in thread).
3. **object_store** — calls ``head_bucket`` on the configured MinIO bucket
   (run in thread).  Skipped and reported as ``"skipped"`` when MinIO is not
   configured (Docker secrets absent).

Each check has an individual 3-second timeout enforced by
``asyncio.wait_for``.  A single slow dependency cannot hang the entire probe.

Security properties
-------------------
- **No information leakage**: the 503 response body uses generic service names
  (``database``, ``cache``, ``object_store``) and never includes internal
  hostnames, ports, connection strings, or exception messages.
- **Auth/seal exempt**: ``/ready`` is added to ``COMMON_INFRA_EXEMPT_PATHS``
  so it bypasses ``SealGateMiddleware`` and ``AuthenticationGateMiddleware``.
- **Rate limiting**: ``RateLimitGateMiddleware`` still applies — ``/ready``
  is subject to the ``general_limit`` tier to prevent DDoS via probe endpoint.

HTTP status mapping
-------------------
- ``200 OK`` — all configured dependency checks passed.
- ``503 Service Unavailable`` — one or more checks failed.

Response schema (both 200 and 503)::

    {
        "status": "ok" | "degraded",
        "checks": {
            "database": "ok" | "error",
            "cache": "ok" | "error",
            "object_store": "ok" | "error" | "skipped"  # present only when checked
        }
    }

CONSTITUTION Priority 0: Security — no info leakage, exempt from auth/seal gates
Task: T48.3 — Readiness Probe & External Dependency Health Checks
Task: P48 review F5 — Reuse shared async engine in /ready probe
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

_logger = logging.getLogger(__name__)

#: Per-check timeout in seconds.  A single dependency cannot block the probe
#: longer than this window.  Kubernetes default probe timeout is 1s; 3s gives
#: enough margin for transient network latency without excessive probe delay.
_CHECK_TIMEOUT_SECONDS: float = 3.0

router = APIRouter(tags=["ops"])


async def _check_database() -> bool:
    """Execute a minimal liveness query against the configured database.

    Reuses the shared async engine from :func:`~synth_engine.shared.db.get_async_engine`
    (which has ``pool_pre_ping=True``) rather than creating a new engine on
    every probe invocation.  This avoids connection pool churn and prevents
    exhausting available database connections under high probe frequency.

    If the database URL is not configured, the check is skipped and returns
    ``True`` (no database configured is not a readiness failure — the startup
    validator will catch misconfiguration independently).

    Returns:
        ``True`` when the database responds successfully.  Propagates any
        connection or query exception to the caller for failure handling.
    """
    from sqlalchemy import text

    from synth_engine.shared.db import get_async_engine
    from synth_engine.shared.settings import get_settings

    settings = get_settings()
    database_url = settings.database_url
    if not database_url:
        # No database configured — not a readiness failure for this check.
        return True

    engine = get_async_engine(database_url)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True


async def _check_redis() -> bool:
    """Send a PING command to the configured Redis instance.

    Uses the sync Redis client from the bootstrapper singleton, dispatched via
    ``asyncio.to_thread`` so it does not block the event loop.

    Returns:
        ``True`` when Redis responds to PING.  Propagates any connection
        or Redis exception to the caller for failure handling.
    """
    from synth_engine.bootstrapper.dependencies.redis import get_redis_client

    client = get_redis_client()
    result = await asyncio.to_thread(client.ping)
    return bool(result)


async def _check_minio() -> bool | None:
    """Check reachability of the configured MinIO bucket.

    Reads the MinIO endpoint and bucket name from
    :mod:`~synth_engine.bootstrapper.docker_secrets`.  When Docker secrets are
    not present, the check is skipped and ``None`` is returned.

    Returns:
        ``True`` when the bucket is reachable, ``None`` when MinIO is not
        configured or credentials are not available (skip — not a failure).
        Propagates any network or S3 exception (when MinIO is configured) to
        the caller for failure handling.
    """
    try:
        import boto3
    except ImportError:
        # boto3 not installed — MinIO check skipped.
        return None

    try:
        from synth_engine.bootstrapper.docker_secrets import (
            EPHEMERAL_BUCKET,
            MINIO_ENDPOINT,
            _read_secret,
        )

        access_key = _read_secret("minio_ephemeral_access_key")
        secret_key = _read_secret("minio_ephemeral_secret_key")
    except (RuntimeError, OSError):
        # Secrets not mounted — MinIO is not configured in this environment.
        return None

    def _head_bucket() -> bool:
        """Run head_bucket synchronously (for asyncio.to_thread).

        Returns:
            True when the bucket exists and is reachable.  Propagates any
            S3 exception (botocore.ClientError) when the bucket is unreachable.
        """
        s3 = boto3.client(
            "s3",
            endpoint_url=MINIO_ENDPOINT,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        s3.head_bucket(Bucket=EPHEMERAL_BUCKET)
        return True

    result: bool = await asyncio.to_thread(_head_bucket)
    return result


async def _run_check_with_timeout(
    name: str,
    coro: Coroutine[Any, Any, bool | None],
) -> bool | None:
    """Run a single dependency check with a per-check timeout.

    Wraps the coroutine in ``asyncio.wait_for`` with ``_CHECK_TIMEOUT_SECONDS``.
    Any exception (including ``TimeoutError``) is caught, logged generically
    (no internal details forwarded to the caller), and re-raised so the caller
    can mark the check as failed.

    Args:
        name: Generic service name for logging (e.g. ``"database"``).
        coro: Async coroutine to execute.

    Returns:
        The coroutine's return value on success.

    Raises:
        TimeoutError: When the check exceeds _CHECK_TIMEOUT_SECONDS.
        Exception: When the check fails with a non-timeout error.
    """
    try:
        result: bool | None = await asyncio.wait_for(coro, timeout=_CHECK_TIMEOUT_SECONDS)
        return result
    except TimeoutError:
        _logger.warning("Readiness check timed out: %s (timeout=%ss)", name, _CHECK_TIMEOUT_SECONDS)
        raise
    except Exception:
        _logger.warning("Readiness check failed: %s", name, exc_info=False)
        raise


@router.get("/ready", tags=["ops"])
async def readiness_check() -> JSONResponse:
    """Readiness probe for Kubernetes and Docker Compose healthchecks.

    Checks all external dependencies concurrently via ``asyncio.gather``.
    Each check is wrapped in an individual 3-second timeout so a single slow
    service cannot hang the entire probe.

    Security: the response body uses generic service names only.  Internal
    hostnames, ports, connection strings, and exception messages are logged
    at WARNING level but NEVER included in the HTTP response body.

    Returns:
        ``JSONResponse`` with HTTP 200 when all checks pass, or HTTP 503 when
        any check fails.  Both responses use the same schema::

            {"status": "ok"|"degraded", "checks": {"database": ..., "cache": ...}}
    """
    # Run all checks concurrently. return_exceptions=True prevents gather from
    # short-circuiting — all checks run even if one fails.
    results = await asyncio.gather(
        _run_check_with_timeout("database", _check_database()),
        _run_check_with_timeout("cache", _check_redis()),
        _run_check_with_timeout("object_store", _check_minio()),
        return_exceptions=True,
    )
    db_result, redis_result, minio_result = results

    checks: dict[str, str] = {}
    any_failed = False

    # --- database ---
    if isinstance(db_result, BaseException):
        checks["database"] = "error"
        any_failed = True
    else:
        checks["database"] = "ok"

    # --- cache (Redis) ---
    if isinstance(redis_result, BaseException):
        checks["cache"] = "error"
        any_failed = True
    else:
        checks["cache"] = "ok"

    # --- object_store (MinIO) — optional ---
    if isinstance(minio_result, BaseException):
        checks["object_store"] = "error"
        any_failed = True
    elif minio_result is None:
        # Skipped — MinIO not configured in this environment.
        checks["object_store"] = "skipped"
    else:
        checks["object_store"] = "ok"

    status = "degraded" if any_failed else "ok"
    http_status = 503 if any_failed else 200

    return JSONResponse(
        status_code=http_status,
        content={
            "status": status,
            "checks": checks,
        },
    )
