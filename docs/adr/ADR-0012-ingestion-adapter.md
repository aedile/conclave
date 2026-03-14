# ADR-0012: PostgreSQL Ingestion Adapter — Streaming and Privilege Check Design

**Date:** 2026-03-14
**Status:** Accepted
**Task:** P3-T3.1 — Target Ingestion Engine
**Deciders:** Engineering Team

---

## Context

The Conclave Engine must read data from a customer-supplied PostgreSQL source
database to profile schema structure and stream rows for synthetic data
generation.  Two hard constraints govern the design:

1. **Read-only guarantee**: The engine must never hold write privileges on the
   source database.  A misconfigured or compromised ingestion process must not
   be able to corrupt production data.

2. **Memory safety**: Source tables may be arbitrarily large (millions of rows).
   Loading an entire table into memory would violate the air-gapped deployment
   model's resource constraints.

---

## Decision

### Library: SQLAlchemy + psycopg2-binary (no new dependencies)

`sqlalchemy` and `psycopg2-binary` are already in the production dependency
graph (inherited from the Secure Database Layer, Task P2-T2.2).  Adding
`asyncpg` was considered but rejected because:

| Option | Verdict |
|--------|---------|
| `asyncpg` | Rejected — adds a new production dependency; async streaming requires `asyncio` integration that complicates the sync-first architecture |
| `sqlalchemy` + `psycopg2` server-side cursors | Accepted — zero new dependencies; well-tested; `stream_results=True` is the canonical SQLAlchemy approach |

### Streaming Strategy: `stream_results=True` + `fetchmany(batch_size)`

```python
result = conn.execution_options(stream_results=True).execute(stmt)
while batch := result.fetchmany(batch_size):
    yield [dict(row._mapping) for row in batch]
```

`stream_results=True` instructs psycopg2 to use a named server-side cursor,
which fetches rows in batches rather than loading the full result set.
`fetchmany(batch_size)` pulls one batch at a time; the `while batch :=` walrus
loop yields non-empty batches and exits cleanly when the cursor is exhausted.

Default `batch_size=1000` is a conservative default; callers may tune it.

### Privilege Check: Superuser Detection + information_schema

The pre-flight check runs in two stages before any data access:

**Stage 1 — Superuser detection:**
```sql
SELECT current_setting('is_superuser')
```
PostgreSQL superusers bypass the grants system entirely; they hold implicit
ALL PRIVILEGES on every object.  Detecting `is_superuser = 'on'` and raising
immediately is therefore the only reliable check for this case.

**Stage 2 — Explicit grant inspection:**
```sql
SELECT privilege_type
FROM information_schema.role_table_grants
WHERE grantee = current_user
  AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE')
```
For non-superuser accounts, the grants table correctly reflects explicitly
granted write privileges.  Any result raises `PrivilegeEscalationError`.

`PrivilegeEscalationError` is defined in `postgres_adapter.py` (not in
`shared/`) because it is a domain-specific exception of the ingestion module.
Placing it in `shared/` would violate the boundary that `shared/` must not
import from modules, and the exception carries no cross-cutting utility.

### SSL Enforcement: Local Exemption Rationale

The `validate_connection_string()` function exempts loopback hosts
(`localhost`, `127.0.0.1`, `[::1]`) from the `sslmode=require` mandate.

**Rationale:** SSL over the loopback interface provides no confidentiality
benefit (all traffic stays within the OS network stack) and adds overhead
that is visible in profiling workloads.  The exemption is narrowly scoped —
only exact loopback addresses qualify.  Any routable IP or hostname, including
private RFC-1918 addresses, must use `sslmode=require`.

### ADV-013 Compliance: Table Name Allowlist

Before constructing any SQL that references a caller-supplied table name,
`_validate_table_name()` checks the name against `SchemaInspector.get_tables()`:

```python
def _validate_table_name(self, table_name: str, schema: str = "public") -> None:
    allowed = self._schema_inspector.get_tables(schema=schema)
    if table_name not in allowed:
        raise ValueError(...)
```

This prevents SQL injection through table name parameters.  The `sa_table()`
SQLAlchemy expression API then handles quoting; no f-string SQL is used.

### ADV-012 Compliance: Composite Primary Key Detection

`SchemaInspector.get_columns()` returns the raw SQLAlchemy reflection output
where `primary_key` is an integer ordinal.  Callers MUST use `>= 1` (not
`== 1`) to identify all PK columns in a composite primary key.

---

## Consequences

**Positive:**
- Zero new production dependencies.
- The pre-flight check provides a hard gate that fails fast before any data is
  touched, protecting both source integrity and the engine's operational posture.
- Server-side cursors make the engine safe to operate against arbitrarily large
  tables in resource-constrained air-gapped deployments.
- `SchemaInspector` is a thin, mockable façade: unit tests cover 100% of the
  adapter logic without a real database.

**Negative / Mitigations:**
- `psycopg2` (sync driver) means the streaming path is blocking.  For the
  current modular monolith architecture this is acceptable; an async driver can
  be introduced in a future ADR if Uvicorn worker concurrency becomes a bottleneck.
- The superuser check relies on `current_setting('is_superuser')`.  This is a
  PostgreSQL-specific function — not portable to other databases.  The ingestion
  module explicitly targets PostgreSQL, so this is acceptable by design.
- The `information_schema.role_table_grants` check reflects explicitly granted
  privileges only.  Privileges inherited via role membership may not appear.
  The superuser check (Stage 1) handles the most dangerous case; role-inherited
  privileges are a known gap and should be addressed in a future task if
  role-based PostgreSQL deployments are required.

### Async Call-Site Contract (arch-review finding — P3-T3.1)

`stream_table()` and `preflight_check()` are **synchronous** methods backed by
the blocking `psycopg2` driver.  This is a deliberate architectural decision
(see Library decision above), consistent with the precedent established in:

- **T2.1** (Redis client): synchronous Redis calls wrapped via `asyncio.to_thread()`
- **T2.4** (PBKDF2 key derivation): synchronous crypto wrapped via `asyncio.to_thread()`

**Mandatory call-site contract:** Any caller invoking `stream_table()` or
`preflight_check()` from an async context (FastAPI route handler, async
orchestrator in `bootstrapper/`) MUST wrap the call via `asyncio.to_thread()`.
Calling these methods directly from a coroutine will block the event loop and
violate the async-first mandate in ADR-0001.

Canonical usage pattern:

```python
# In an async FastAPI handler or bootstrapper orchestrator:
import asyncio
from synth_engine.modules.ingestion.postgres_adapter import PostgresIngestionAdapter

adapter = PostgresIngestionAdapter(connection_url)
await asyncio.to_thread(adapter.preflight_check)
rows = await asyncio.to_thread(adapter.stream_table, table_name)
```

Cross-reference: ADR-0001 (async-first mandate), T2.1 Redis sync/async boundary,
T2.4 PBKDF2 sync/async boundary.

### Cross-Module Schema Data Flow (arch-review finding — P3-T3.1)

`SchemaInspector` and `PostgresIngestionAdapter` live in
`synth_engine.modules.ingestion`.  Per ADR-0001, modules must not import
directly from each other — the bootstrapper is the sole orchestrator.

**Import-linter enforcement:** If any module outside
`synth_engine.modules.ingestion` imports `SchemaInspector` directly, the
import-linter CI contract defined in `pyproject.toml` will fail. This is not
a soft guideline — it is a hard CI gate.

**Required data-flow pattern for downstream consumers (e.g., T3.2 Relational
Mapping Engine):**

1. The **bootstrapper** calls `adapter.get_schema_inspector()` and invokes
   `inspector.get_tables()`, `inspector.get_columns()`, and
   `inspector.get_foreign_keys()` to extract schema data.
2. The bootstrapper transforms that raw data into a **plain value object** —
   a `dict`, `dataclass`, or `TypedDict` — containing only the information
   the downstream module needs.
3. The bootstrapper passes the **value object** to downstream modules. It
   never passes the `SchemaInspector` instance itself.

This pattern keeps the module boundary clean: T3.2 and all later modules
receive plain Python data structures, not ingestion-layer objects. The
`SchemaInspector` class is an implementation detail of the ingestion module
and must not leak across the boundary.

Concretely:

```python
# bootstrapper/orchestrator.py (sketch — not yet implemented)
import asyncio
from synth_engine.modules.ingestion.postgres_adapter import PostgresIngestionAdapter

async def run_ingestion_phase(connection_url: str, table_name: str) -> dict:
    adapter = PostgresIngestionAdapter(connection_url)
    await asyncio.to_thread(adapter.preflight_check)
    inspector = adapter.get_schema_inspector()
    # Extract to a plain value object — do NOT pass inspector to other modules
    schema_data = {
        "tables": inspector.get_tables(),
        "columns": inspector.get_columns(table_name),
        "foreign_keys": inspector.get_foreign_keys(table_name),
    }
    return schema_data  # plain dict passed to downstream modules
```
