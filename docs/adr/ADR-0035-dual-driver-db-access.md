# ADR-0035: Dual-Driver DB Access Pattern for Huey Workers

**Status:** Accepted (amended T48.2)
**Date:** 2026-03-18
**Amended:** 2026-03-22
**Deciders:** PM, Architecture Reviewer, DevOps Reviewer
**Task:** P28-E2E-Validation — fix architecture review findings
**Amendment:** T48.2 — Connection Pooling for Huey Workers

---

## Context

The Air-Gapped Synthetic Data Generation Engine uses two asynchronous frameworks
at its core: FastAPI (ASGI, asyncpg-backed) for HTTP routes, and Huey (synchronous
thread pool) for background task execution.

When the initial P28 implementation of `build_spend_budget_fn()` called
`asyncio.run()` from inside a Huey worker thread to execute the async
`spend_budget()` path from `modules/privacy/accountant`, the following error was
raised at runtime:

```
sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called
```

This occurs because asyncpg uses a greenlet-based concurrency model internally.
When `asyncio.run()` is invoked from a Huey worker thread that was **not** started
inside a greenlet context, SQLAlchemy's async machinery cannot locate the required
greenlet and raises `MissingGreenlet`.

The root cause is architectural: Huey workers are plain OS threads — not greenlets,
not coroutines. They cannot host an async event loop that asyncpg expects.

Two DB access paths are therefore required:

1. **Async path (asyncpg):** Used by FastAPI route handlers via
   `shared/db.py:get_async_session`. This is the primary, high-performance path
   that serves all HTTP traffic.
2. **Sync path (psycopg2):** Required by Huey workers for any operation that
   touches the database. This path uses synchronous SQLAlchemy `create_engine`
   with the `psycopg2` driver, which has no greenlet dependency.

**Amendment context (T48.2):** Under concurrent synthesis job load, the main
`run_synthesis_job` Huey task used `get_engine()` — the same FastAPI connection
pool. Under load, worker tasks could exhaust the pool, denying connections to
FastAPI request handlers. Additionally, NullPool for `run_synthesis_job` creates
a new OS-level connection on every task invocation, which is wasteful when workers
process jobs back-to-back. A bounded QueuePool with a single persistent connection
per worker is both safer and more efficient.

---

## Decision

**Use `_promote_to_sync_url()` in `bootstrapper/factories.py` to demote async
driver URLs to their sync equivalents for Huey worker DB operations.**

### URL mapping convention

| Async URL prefix (asyncpg) | Sync URL prefix (psycopg2) | Driver |
|---|---|---|
| `postgresql+asyncpg://` | `postgresql://` | psycopg2 |
| `sqlite+aiosqlite:///` | `sqlite:///` | stdlib sqlite3 |
| Any other prefix | Unchanged | Already sync |

The helper `_promote_to_sync_url(database_url: str) -> str` encapsulates this
mapping. It guards against double-substitution by checking for the async prefix
before replacing.

### Engine lifecycle by use case

Two sync engine strategies coexist, each appropriate for its context:

#### 1. `build_spend_budget_fn()` — NullPool, factory-scoped

The synchronous `Engine` for `build_spend_budget_fn()` is created **once per call
to `build_spend_budget_fn()`** using `NullPool`:

```python
from sqlalchemy.pool import NullPool

engine = create_engine(sync_url, poolclass=NullPool)
```

`NullPool` is correct here because:

- This factory is called once per application startup and the returned callable
  is invoked at most once per synthesis job's budget-deduction step.
- `NullPool` opens a connection on `Session()` entry and closes it on `Session()`
  exit — exactly matching the single-call-per-invocation lifecycle.

The engine is hoisted to the **outer scope of `build_spend_budget_fn()`** (not
inside `_sync_wrapper`) so it is built exactly once per factory call.

#### 2. `get_worker_engine()` — QueuePool, module-level cached (T48.2)

The `run_synthesis_job` Huey task and any future Huey tasks that need persistent
DB access MUST use `get_worker_engine()` from `shared/db.py`:

```python
from synth_engine.shared.db import get_worker_engine

db_engine = get_worker_engine(database_url)
with Session(db_engine) as session:
    ...
```

`get_worker_engine()` uses `QueuePool` with bounded sizing:

```python
from sqlalchemy.pool import QueuePool

engine = create_engine(
    database_url,
    poolclass=QueuePool,
    pool_size=1,
    max_overflow=2,
    pool_timeout=30,
    pool_pre_ping=True,
    pool_recycle=1800,
)
```

**Worker pool sizing rationale (T48.2):**

- `pool_size=1`: Each Huey worker process handles one task at a time. A single
  persistent connection avoids connection-setup overhead on back-to-back task
  invocations (contrast with NullPool which opens a new OS connection per call).
- `max_overflow=2`: The `run_synthesis_job` task opens two sessions sequentially:
  a short pre-flight session (DP parameter read) and a main training session.
  Although they do not overlap, overflow=2 provides burst headroom for future
  tasks that may open concurrent sub-sessions or for brief timing overlaps during
  task transitions.
- `pool_timeout=30`: Raises `TimeoutError` after 30 seconds rather than blocking
  indefinitely when all pool slots (1 + 2) are occupied. This enables the Huey
  task runner to handle pool exhaustion as a recoverable error rather than a
  deadlock.
- `pool_pre_ping=True`: Issues a lightweight `SELECT 1` before handing out a
  connection. Required after PgBouncer restarts or network interruptions where
  pooled connections may be silently invalidated.
- `pool_recycle=1800`: Proactively recycles connections after 30 minutes to match
  PgBouncer's `server_idle_timeout=1800s`. Prevents SQLAlchemy from returning a
  stale connection that PgBouncer has already discarded.

**Connection budget (documented):**

| Component | Pool size | Max overflow | Max connections |
|-----------|-----------|--------------|-----------------|
| FastAPI (get_engine) | 5 | 10 | 15 |
| Per Huey worker (get_worker_engine) | 1 | 2 | 3 |
| 4 Huey workers | — | — | 12 |
| **Total** | — | — | **27** |

PgBouncer `max_client_conn=100`. Total (27) << limit (100).

**Pool isolation:** The worker engine is cached in `_worker_engine_cache`, entirely
separate from `_engine_cache` (FastAPI) and `_async_engine_cache`. A stuck or slow
worker task cannot exhaust the FastAPI connection pool.

**SQLite test compatibility:** When the URL starts with `sqlite`, `get_worker_engine()`
skips QueuePool configuration and uses a plain SQLite engine (StaticPool). This
preserves compatibility with the in-process test suite without requiring a live
PostgreSQL instance.

### Scope constraint: sync path limited to Huey workers

The sync path is **not** a general-purpose DB access layer. It exists solely to
satisfy the greenlet constraint of Huey workers. Any Huey task that requires DB
access MUST:

1. Call `_promote_to_sync_url()` to obtain a sync-compatible URL (if the URL may
   contain an async driver prefix).
2. Use `get_worker_engine()` from `shared/db.py` for persistent connections.
3. Use `sqlalchemy.orm.Session` (sync), not `AsyncSession`.
4. Wrap all DB operations in `with Session(engine) as session:` blocks to guarantee
   connection return on both success and exception.

For single-call factory patterns (like `build_spend_budget_fn`), `NullPool` with a
factory-scoped engine remains appropriate. Use `get_worker_engine()` only when
connection persistence across invocations is beneficial.

Direct use of `asyncio.run()` from Huey tasks is **forbidden** when the async
session uses asyncpg.

### FastAPI async path: unaffected

FastAPI route handlers continue to use the async engine via
`shared/db.py:get_async_session`. This path is not modified by this ADR.
Connection pools for all three paths are entirely separate — there is no
cross-contamination.

### `dispose_engines()` coverage

`dispose_engines()` in `shared/db.py` now disposes all three engine caches:
FastAPI sync (`_engine_cache`), FastAPI async (`_async_engine_cache`), and worker
(`_worker_engine_cache`). Call `dispose_engines()` at application shutdown and
between test cases that change `DATABASE_URL` or mTLS state.

---

## Consequences

**Positive:**

- Eliminates the `MissingGreenlet` runtime error for Huey worker DB operations.
- The URL-demoting convention is explicit and testable: `_promote_to_sync_url()`
  is a pure function with deterministic output, covered by unit tests.
- Worker engine pool (QueuePool pool_size=1) is isolated from the FastAPI pool.
  Worker tasks cannot exhaust the FastAPI connection pool under load.
- `pool_pre_ping=True` and `pool_recycle=1800` provide production robustness
  after PgBouncer restarts and for long-lived worker processes.
- Connection budget is explicit and within PgBouncer capacity (27 << 100).
- FastAPI async path is completely unaffected.

**Negative / Constraints:**

- Two DB driver paths coexist (`asyncpg` for FastAPI, `psycopg2` for Huey). Both
  `asyncpg` and `psycopg2-binary` must remain in the production dependency graph.
- The URL-demoting convention must be followed for any future Huey tasks that need
  DB access. Deviation will silently succeed in SQLite test environments but fail
  at runtime in production (PostgreSQL + asyncpg).
- The `NullPool` engine in `build_spend_budget_fn()` is created at factory
  construction time. If `DATABASE_URL` changes after the factory is built (e.g.,
  in tests that swap environments), the engine will hold the stale URL. Callers
  must rebuild the factory in that case.
- Scaling Huey concurrency beyond 28 workers (28 x 3 = 84 + 15 = 99) would
  approach the PgBouncer `max_client_conn=100` limit. Review pool sizing before
  scaling Huey concurrency significantly beyond 4 workers.

---

## Alternatives Considered

**`asyncio.run()` from Huey workers:** Rejected. Raises `MissingGreenlet` when
asyncpg is the driver. Not fixable without replacing asyncpg or adding a greenlet
shim, both of which are higher-cost changes.

**Replace asyncpg with psycopg3 (sync+async in one driver):** Rejected. psycopg3
is not in the current dependency graph. Introducing it requires an ADR for the
substitution, migration of all async session config, and retesting of all DB paths.
Out of scope for a bug-fix task.

**Spawn a separate sync process for Huey DB work:** Rejected. Adds process
management complexity with no benefit over a simple sync engine.

**`run_sync()` on the existing async engine:** Rejected. `AsyncEngine.run_sync()`
still requires a running event loop; it does not eliminate the greenlet dependency
for asyncpg.

**NullPool for `run_synthesis_job`:** Considered and rejected for T48.2. NullPool
opens a new OS connection on every `Session()` entry. For `run_synthesis_job`
which opens two sessions per invocation (pre-flight + training), and for a worker
that processes back-to-back jobs, this creates unnecessary connection churn. A
single persistent QueuePool connection with pool_size=1 is both safer (bounded
pool) and more efficient (connection reuse).

**Shared engine between FastAPI and workers:** Rejected. A shared pool would allow
slow synthesis jobs to starve HTTP request handlers. Engine isolation is a
correctness requirement, not a performance optimisation.

---

## References

- ADR-0012 — Ingestion sync path (psycopg2 streaming, sync driver precedent)
- ADR-0020 — Huey task queue singleton topology
- `src/synth_engine/bootstrapper/factories.py` — `build_spend_budget_fn()` and
  `_promote_to_sync_url()` implementation
- `src/synth_engine/shared/db.py` — `get_worker_engine()` implementation (T48.2)
- `src/synth_engine/shared/protocols.py` — `SpendBudgetProtocol` definition
- `tests/unit/test_synthesizer_tasks.py` — unit tests for `build_spend_budget_fn()`
- `tests/unit/test_worker_connection_pool_attack.py` — attack/negative tests (T48.2)
- `tests/integration/test_worker_connection_pool.py` — integration tests (T48.2)
