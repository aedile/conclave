# ADR-0015: Subsetting Traversal and Saga Rollback Design

**Status**: Accepted
**Date**: 2026-03-14
**Task**: P3-T3.4 — Subsetting & Materialization Core
**Supersedes**: N/A
**Related**: ADR-0001, ADR-0012 §Cross-Module, ADR-0013 §5

---

## Context

The subsetting pipeline must extract a referentially-intact subset of a source
PostgreSQL database and write it to a target database.  Several competing
concerns must be balanced:

1. **Referential integrity**: The subset must not contain orphaned FK references
   (child rows whose parent rows were not also copied).
2. **Saga safety**: If the write fails midway, the target database must be
   restored to a clean (empty) state — partial subsets must never be left in
   the target.
3. **Memory safety**: Source databases may be large; no module may load an
   entire table into memory.
4. **Cross-module isolation**: The `SubsettingEngine` lives in
   `modules/ingestion/` and must not import `SchemaReflector` or
   `DirectedAcyclicGraph` from the same package directly.  Downstream modules
   (Profiler, Synthesizer) must similarly receive schema information without
   importing ingestion-internal types.
5. **Security**: All SQL must use parameterised queries; no f-string
   interpolation of user-controlled values.

---

## Decisions

### 1. Subsetting Strategy: Topological DAG Traversal

**Decision**: Walk tables in the order prescribed by
`SchemaTopology.table_order` (parents before children, as produced by
`DirectedAcyclicGraph.topological_sort()` in T3.2).

**Rationale**: Processing parents first ensures that when a child table is
written to the target, its parent rows already exist.  This satisfies FK
constraints during INSERTs without needing to defer constraint checks.

**Implementation**: `DagTraversal.traverse()` receives a seed query for the
starting table, then follows FK edges in both directions:

- **Child direction**: If a table in `table_order` holds an FK referencing an
  already-fetched table, fetch the rows of the child that reference the fetched
  parent PKs (e.g., `employees WHERE dept_id IN (:v0, :v1, ...)`).
- **Parent direction**: If a table in `table_order` is referenced by an
  already-fetched table's FK, fetch the parent rows whose PKs appear in the
  child's FK columns (e.g., `departments WHERE id IN (:v0, :v1, ...)`).

Tables unreachable from the seed via any FK path are skipped entirely.

### 2. Saga Pattern: Track and TRUNCATE on Failure

**Decision**: `EgressWriter` tracks every table it writes to (in insertion
order).  If any exception occurs, `rollback()` TRUNCATEs all written tables in
**reverse** order (children before parents) using `CASCADE`.

**Rationale**:

- Reverse order is required because FK constraints prevent truncating a parent
  whose children still exist in the target.  Children must be truncated first.
- `CASCADE` handles any FK references within the target schema without requiring
  per-constraint knowledge at rollback time.
- Tracking tables in `EgressWriter` (not `SubsettingEngine`) keeps the Saga
  compensation logic co-located with the write logic.

**Invariant**: After a failed subset run, `EgressWriter.rollback()` is called
and the target database is left completely empty.  The bootstrapper or caller
may then retry the entire operation safely.

### 3. Memory Safety: No Full-Table Materialization

**Decision**: All row fetching in `DagTraversal` uses `conn.execute()` with
`result.mappings()` and converts rows to `list[dict]` in bounded batches.  No
entire source table is materialized into memory at once — only the rows
referenced by FK chains from the seed set are fetched.

**Rationale**: For large source databases, loading entire tables would exhaust
memory.  The FK-following strategy naturally bounds the result set to the
connected component reachable from the seed.

**Future extension**: If seed result sets are themselves large, `DagTraversal`
can be extended to paginate using `LIMIT`/`OFFSET` or server-side cursors.
This is deferred to a future task.

### 4. Cross-Module Injection: SchemaTopology from shared/

**Decision**: `SubsettingEngine` and `DagTraversal` receive a
`SchemaTopology` value object (defined in `synth_engine.shared.schema_topology`)
via constructor injection.  They do NOT import `SchemaReflector`,
`DirectedAcyclicGraph`, or `PostgresIngestionAdapter`.

**Rationale**: Per ADR-0001 (bootstrapper-as-orchestrator), ADR-0012
§Cross-Module, and ADR-0013 §5, the bootstrapper is the only layer allowed to
call `SchemaReflector.reflect()` and `DirectedAcyclicGraph.topological_sort()`.
It then packages the result into the immutable `SchemaTopology` dataclass and
injects it into downstream modules.  This preserves import-linter contract
compliance across all module boundaries.

`SchemaTopology` is frozen (`@dataclass(frozen=True)`) to prevent mutation
after construction.  It lives in `shared/` (not `modules/ingestion/`) so that
all downstream modules (Profiler, Synthesizer) can import it without violating
the ingestion independence contract.

### 5. Why TRUNCATE Rather Than DELETE

**Decision**: Rollback uses `TRUNCATE TABLE ... CASCADE`, not `DELETE FROM ...`.

**Rationale**:

- `DELETE` removes rows one-by-one and can trigger FK constraint checks per
  row, requiring careful ordering and potentially requiring `ON DELETE CASCADE`
  to be defined in the target schema.
- `TRUNCATE ... CASCADE` removes all rows atomically and handles FK references
  automatically regardless of the target schema's `ON DELETE` configuration.
- `TRUNCATE` is significantly faster for large tables (no WAL per row, no
  index updates per row).
- The target database during subsetting contains only rows copied from the
  source; there is no risk of accidental production data loss from TRUNCATE.

**Constraint**: TRUNCATE acquires an `ACCESS EXCLUSIVE` lock on the table.
This is acceptable because the target database is used exclusively by the
subsetting pipeline during a run.

### 6. Async Call-Site Contract

`SubsettingEngine.run()` is **synchronous** — it is backed by the blocking
psycopg2 driver (consistent with the precedent established in ADR-0012 for
`PostgresIngestionAdapter`, and the sync/async boundaries in T2.1 and T2.4).

**Mandatory call-site contract:** Any caller invoking `SubsettingEngine.run()`
from an async context (e.g., a FastAPI route handler or an async bootstrapper
orchestrator) **MUST** wrap the call via `asyncio.to_thread()` to avoid
blocking the event loop.  Calling `run()` directly from a coroutine will block
all concurrent requests and violate the async-first mandate in ADR-0001.

Canonical usage pattern:

```python
# In an async FastAPI handler or bootstrapper orchestrator:
import asyncio
from synth_engine.modules.ingestion.core import SubsettingEngine

result = await asyncio.to_thread(engine.run, seed_table, seed_query)
```

Cross-reference: ADR-0001 (async-first mandate), ADR-0012 §Async Call-Site
Contract, T2.1 Redis sync/async boundary, T2.4 PBKDF2 sync/async boundary.

### 7. row_transformer Injection Contract

**Rationale for IoC over direct import**: `modules/ingestion` and
`modules/masking` are independence-contracted by import-linter.  A direct
import of any masking callable from `core.py` would cause import-linter to
fail CI.  The solution is inversion of control: the bootstrapper constructs a
masking callable (using `MaskingRegistry` or equivalent) and injects it into
`SubsettingEngine` at construction time via the `row_transformer` parameter.
The ingestion module never acquires a dependency on masking; the module
boundary is preserved entirely.

**Callback signature and purity contract**:

```python
Callable[[str, dict[str, Any]], dict[str, Any]]
```

- First argument — `table_name: str`: the name of the table currently being
  processed.  The transformer uses this to look up which columns require
  masking in that table.
- Second argument — `row: dict[str, Any]`: a single source row as a plain
  Python dict (column name → value).
- Return value — `dict[str, Any]`: the transformed row.  **MUST NOT return
  `None`.**  Returning `None` causes `SubsettingEngine.run()` to raise
  `TypeError` immediately and trigger the Saga rollback.
- **Purity**: the transformer MUST be a pure function with no observable side
  effects (no I/O, no mutation of shared state, no modification of the input
  dict in place).
- **Error contract**: the transformer MUST NOT raise unless the row is
  genuinely unprocessable.  Any exception raised by the transformer propagates
  to `SubsettingEngine.run()`'s `except` block, which calls
  `egress.rollback()` and re-raises.  This ensures the Saga invariant (target
  left clean) is preserved even on transformer failures.

**Bootstrapper responsibility**: The Phase 4/5 bootstrapper is responsible for
constructing the transformer callable using `MaskingRegistry` and injecting it
via `SubsettingEngine.__init__()`.  The test file
`tests/integration/test_e2e_subsetting.py` contains the canonical reference
implementation of this pattern: the `_mask_row` function demonstrates how the
bootstrapper should construct a table-aware, column-dispatching transformer
that calls masking algorithms without the ingestion module ever importing from
`modules/masking`.

**Cross-reference**: ADR-0014 (`docs/adr/ADR-0014-masking-engine.md`) is the
canonical source of the masking algorithm callable implementations
(`mask_email`, `mask_name`, `mask_ssn`, etc.) that the bootstrapper wires into
the transformer.

---

## Consequences

### Positive

- Referential integrity is guaranteed in the target: parents are written before
  children; rollback removes children before parents.
- The Saga pattern provides an atomic "all or nothing" guarantee for the target
  database state.
- `SchemaTopology` in `shared/` provides a clean, dependency-free value type
  for cross-module schema data handoff, satisfying import-linter contracts.

### Negative / Trade-offs

- The FK-following strategy fetches only directly reachable rows.  Tables with
  no FK connection to the seed table are not copied (by design — they would be
  orphaned anyway).
- Single-column PK assumption: the current `DagTraversal` implementation uses
  only the first PK column for FK matching.  Composite PK tables are partially
  supported but may require enhancement in a future task.
- Memory bounding relies on the seed set being small.  If the seed query
  returns millions of rows, the entire set is materialized.  Pagination support
  should be added as a follow-up.

---

## Alternatives Considered

| Alternative | Reason Rejected |
|---|---|
| `DELETE` for rollback | Slower, requires `ON DELETE CASCADE` in target schema or careful ordering |
| Loading full tables then filtering | Exceeds memory constraints for large schemas |
| Importing `SchemaReflector` directly in `SubsettingEngine` | Violates import-linter independence contract |
| Storing SchemaTopology as a dict/TypedDict | Mutable and harder to type strictly; frozen dataclass provides stronger safety guarantees |
| Direct import of masking callables in `core.py` | Violates import-linter ingestion↔masking independence contract; IoC via `row_transformer` is the correct pattern |
