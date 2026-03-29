"""FastAPI router for readiness probe and vault health endpoints.

Implements:
- ``GET /ready`` — Kubernetes readiness probe.  Checks database (SELECT 1),
  Redis (PING), and MinIO (head_bucket) concurrently with 3-second timeouts.
  Returns 503 if any check fails OR if the vault is sealed.
- ``GET /health/vault`` — Per-worker vault seal status.  Always 200 OK.

Strict mode (T68.4)
-------------------
When ``conclave_health_strict=True`` (default in production), the ``/ready``
endpoint returns 503 if:
- ``DATABASE_URL`` is configured but the database is unreachable.
- ``DATABASE_URL`` is not configured (strict mode treats missing config as a
  deployment error — the operator must either set the URL or disable strict mode).
- Redis is unreachable.

When ``conclave_health_strict=False`` (default in development), unconfigured
services are skipped and the endpoint returns 200 — preserving the existing
permissive behavior for local development.

Both modes suppress error details in responses to prevent info leakage; errors
are logged at WARNING level with ``exc_info=False``.

Security properties
-------------------
- No information leakage: 503 body uses generic names (database, cache,
  object_store) — no hostnames, ports, or exception messages.
- Auth/seal exempt: both endpoints are in ``COMMON_INFRA_EXEMPT_PATHS`` to
  avoid a deadlock where the seal gate blocks the seal-status endpoint.
- Rate limited: ``/ready`` is subject to the general_limit tier.

Response schema is published in OpenAPI (see ``docs/api/openapi.json``).

CONSTITUTION Priority 0: Security — no info leakage, exempt from auth/seal gates
Task: T48.3 — Readiness Probe & External Dependency Health Checks
Task: P48 review F5 — Reuse shared async engine in /ready probe
Task: T55.1 — Vault State Health Endpoint & Multi-Worker Coordination
Task: T60.2 — Move /health liveness probe here from lifecycle.py
Task: T68.4 — Health Check Strict Mode for Production
"""

from __future__ import annotations

import asyncio
import logging
import uuid
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

#: Opaque per-worker identifier generated once at import time.
#: Replaces os.getpid() to avoid exposing process topology to API consumers.
_WORKER_ID: str = str(uuid.uuid4())


async def _check_database() -> bool:
    """Execute a minimal liveness query against the configured database.

    Reuses the shared async engine from :func:`~synth_engine.shared.db.get_async_engine`
    (which has ``pool_pre_ping=True``) rather than creating a new engine on
    every probe invocation.  This avoids connection pool churn and prevents
    exhausting available database connections under high probe frequency.

    If the database URL is not configured, the check is skipped and returns
    ``True`` (no database configured is not a readiness failure for this check —
    strict-mode enforcement happens in :func:`readiness_check`).

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
        # Strict-mode enforcement (treating missing config as a failure) happens
        # in readiness_check(), not here, so that _check_database() remains pure.
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


@router.get("/health", tags=["ops"])
async def health_check() -> JSONResponse:
    """Liveness probe for container orchestrators and load balancers.

    Always returns HTTP 200.  A non-200 response means the process is
    not functioning and should be restarted by the container runtime.
    For readiness (dependency checks), use GET /ready.

    Returns:
        JSON body {"status": "ok"} with HTTP 200.
    """
    return JSONResponse(content={"status": "ok"})


@router.get("/ready", tags=["ops"])
async def readiness_check() -> JSONResponse:
    """Readiness probe for Kubernetes and Docker Compose healthchecks.

    Checks all external dependencies concurrently via ``asyncio.gather``.
    Each check is wrapped in an individual 3-second timeout so a single slow
    service cannot hang the entire probe.

    Also checks vault seal state: a sealed worker MUST NOT be marked ready
    even if all dependency checks pass.  The vault seal state is reported in
    the ``vault_sealed`` field of the response body.

    Strict mode (T68.4): when ``conclave_health_strict=True`` (default in
    production), any unconfigured-but-expected service also triggers 503.
    Specifically, if ``DATABASE_URL`` is not set in strict mode, the database
    check is treated as failed rather than skipped.

    Security: the response body uses generic service names only.  Internal
    hostnames, ports, connection strings, and exception messages are logged
    at WARNING level but NEVER included in the HTTP response body.

    Returns:
        ``JSONResponse`` with HTTP 200 when all checks pass AND vault is
        unsealed, or HTTP 503 when any check fails OR vault is sealed.
        Both responses use the same schema::

            {
                "status": "ok"|"degraded",
                "vault_sealed": bool,
                "checks": {"database": ..., "cache": ...}
            }
    """
    from synth_engine.shared.security.vault import VaultState
    from synth_engine.shared.settings import get_settings

    settings = get_settings()
    strict = settings.conclave_health_strict

    # T68.4: In strict mode, an unconfigured DATABASE_URL is treated as a
    # deployment error rather than being skipped.  Load balancers must not
    # route traffic to an instance with missing critical configuration.
    strict_db_missing = strict and not (settings.database_url or "").strip()

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
    # Determine whether DATABASE_URL was absent (permissive skip).
    # _check_database() returns True early when the URL is absent, so we need
    # the URL value here to distinguish "connected OK" from "skipped (no URL)".
    db_url_absent = not (settings.database_url or "").strip()
    if strict_db_missing:
        # T68.4: Strict mode — DATABASE_URL is not configured; treat as failure.
        checks["database"] = "error"
        any_failed = True
    elif isinstance(db_result, BaseException):
        checks["database"] = "error"
        any_failed = True
    elif db_url_absent:
        # Permissive mode — no DATABASE_URL configured; check was skipped.
        checks["database"] = "skipped"
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

    # --- vault seal state ---
    # A sealed worker MUST NOT be admitted to the load-balancer pool.
    # Even if all dependency checks pass, a sealed vault means the worker
    # cannot perform any cryptographic operations or process requests safely.
    vault_sealed = VaultState.is_sealed()
    if vault_sealed:
        any_failed = True

    status = "degraded" if any_failed else "ok"
    http_status = 503 if any_failed else 200

    return JSONResponse(
        status_code=http_status,
        content={
            "status": status,
            "vault_sealed": vault_sealed,
            "checks": checks,
        },
    )


@router.get("/health/vault", tags=["ops"])
async def vault_health() -> JSONResponse:
    """Report this worker's vault seal status and process PID.

    In multi-worker deployments (e.g. Gunicorn with multiple Uvicorn workers),
    each worker process has an independent ``VaultState``.  This endpoint
    lets operators verify that a specific worker has been unsealed.

    This endpoint is always ``200 OK`` — it reports state, it does not assert
    health.  Use ``/ready`` for health-gating.

    Returns:
        ``JSONResponse`` with HTTP 200 and body::

            {"vault_sealed": bool, "worker_id": str}
    """
    from synth_engine.shared.security.vault import VaultState

    return JSONResponse(
        status_code=200,
        content={
            "vault_sealed": VaultState.is_sealed(),
            "worker_id": _WORKER_ID,
        },
    )
