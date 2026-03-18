# ADR-0035: Dual-Driver DB Access Pattern for Huey Workers

**Status:** Accepted
**Date:** 2026-03-18
**Deciders:** PM, Architecture Reviewer, DevOps Reviewer
**Task:** P28-E2E-Validation — fix architecture review findings

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

### Engine lifecycle: NullPool, factory-scoped

The synchronous `Engine` is created **once per call to `build_spend_budget_fn()`**
using `NullPool`:

```python
from sqlalchemy.pool import NullPool

engine = create_engine(sync_url, poolclass=NullPool)
```

`NullPool` is correct here because:

- Huey workers are single-call-per-job: each task executes once and returns.
  There is no benefit to pooling connections across calls.
- Pooling would hold idle connections between Huey task invocations, consuming
  database server resources and complicating lifecycle management.
- `NullPool` opens a connection on `Session()` entry and closes it on `Session()`
  exit — exactly matching the per-task lifecycle.

The engine is hoisted to the **outer scope of `build_spend_budget_fn()`** (not
inside `_sync_wrapper`) so it is built exactly once per factory call and reused
across all invocations of the returned callable.

### Scope constraint: sync path limited to `build_spend_budget_fn()`

The sync path is **not** a general-purpose DB access layer. It exists solely to
satisfy the greenlet constraint of Huey workers. Any future Huey task that requires
DB access MUST follow the same pattern:

1. Call `_promote_to_sync_url()` to obtain a sync-compatible URL.
2. Create a `NullPool` engine at factory scope (not call scope).
3. Use `sqlalchemy.orm.Session` (sync), not `AsyncSession`.

Direct use of `asyncio.run()` from Huey tasks is **forbidden** when the async
session uses asyncpg.

### FastAPI async path: unaffected

FastAPI route handlers continue to use the async engine via
`shared/db.py:get_async_session`. This path is not modified by this ADR.
Connection pools for the two paths are entirely separate — there is no
cross-contamination.

---

## Consequences

**Positive:**

- Eliminates the `MissingGreenlet` runtime error for Huey worker DB operations.
- The URL-demoting convention is explicit and testable: `_promote_to_sync_url()`
  is a pure function with deterministic output, covered by unit tests.
- `NullPool` prevents idle connection accumulation between Huey task invocations.
- FastAPI async path is completely unaffected.

**Negative / Constraints:**

- Two DB driver paths coexist (`asyncpg` for FastAPI, `psycopg2` for Huey). Both
  `asyncpg` and `psycopg2-binary` must remain in the production dependency graph.
- The URL-demoting convention must be followed for any future Huey tasks that need
  DB access. Deviation will silently succeed in SQLite test environments but fail
  at runtime in production (PostgreSQL + asyncpg).
- The `NullPool` engine is created at factory construction time. If `DATABASE_URL`
  changes after the factory is built (e.g., in tests that swap environments), the
  engine will hold the stale URL. Callers must rebuild the factory in that case.

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

---

## References

- ADR-0012 — Ingestion sync path (psycopg2 streaming, sync driver precedent)
- ADR-0020 — Huey task queue singleton topology
- `src/synth_engine/bootstrapper/factories.py` — `build_spend_budget_fn()` and
  `_promote_to_sync_url()` implementation
- `src/synth_engine/shared/protocols.py` — `SpendBudgetProtocol` definition
- `tests/unit/test_factories.py` — unit tests for `build_spend_budget_fn()`
