# ADR-0013: Relational Mapping DAG and Topological Sort Design

**Status:** Accepted
**Date:** 2026-03-13
**Task:** P3-T3.2 -- Relational Mapping & Topological Sort
**Author:** Conclave Engine Development Team

---

## Context

The synthetic data generation pipeline must process database tables in dependency order. A table
holding a foreign key referencing another table (the "parent") cannot be synthesised before the
parent has been processed -- doing so would violate referential integrity in the output dataset.

The ingestion module needs a mechanism to:

1. Extract the schema topology from the connected source database (tables, columns, FKs).
2. Represent that topology as a directed graph.
3. Determine a valid linear processing order (topological sort).
4. Detect and report circular dependencies that make a linear order impossible.

---

## Decision

### 1. Schema Reflection via SQLAlchemy `inspect()`

`SchemaReflector` (in `reflection.py`) wraps SQLAlchemy's `inspect()` facade to expose three
discrete methods:

| Method | Returns |
|---|---|
| `get_tables(schema)` | `list[str]` -- table names visible to the connected user |
| `get_columns(table, schema)` | `list[dict]` -- column metadata including `primary_key` position |
| `get_foreign_keys(table, schema)` | `list[dict]` -- FK constraints with `referred_table` and `constrained_columns` |

The `primary_key` integer field follows SQLAlchemy's convention: `0` = not a PK column;
`>= 1` = PK membership, with the integer indicating position in composite PKs (1, 2, ...).
Callers MUST use `>= 1` (not `== 1`) to correctly identify composite PK members (ADV-012).

### 2. DirectedAcyclicGraph with Explicit FK Edges Only

`DirectedAcyclicGraph` (in `graph.py`) stores:

- `_nodes: set[str]` -- all table names.
- `_adjacency: dict[str, list[str]]` -- parent -> [children] mapping.
- `_edge_set: set[tuple[str, str]]` -- O(1) duplicate detection for idempotent `add_edge()`.
- `_edges: list[tuple[str, str]]` -- ordered (parent, child) edge list for introspection.

**Edge direction:** An edge `(parent, child)` means the child table holds a FK referencing the
parent. This represents the dependency: the parent must be processed first.

**add_edge() is idempotent:** Calling `add_edge(parent, child)` with an already-present pair is
a no-op. This matches `add_node()`'s idempotency contract and prevents duplicate edges when
`SchemaReflector.reflect()` processes schemas with composite or redundant FK constraints (e.g.,
`created_by` and `updated_by` columns that both reference the same parent table produce the same
`(parent, child)` edge tuple).

**Explicit FKs only:** Only relationships defined as database-level FK constraints are represented
as graph edges. Implicit or virtual FK relationships (e.g., column-name conventions, user-defined
override mappings) are **not inferred** from the schema. This is a deliberate security and
correctness choice: inferring FKs from naming patterns would introduce ambiguity and risk
incorrect processing order in customer schemas.

**Virtual FK support is deferred.** A future task may introduce an override mechanism allowing
users to declare virtual FK relationships via configuration. That mechanism will be implemented
as a separate pass over the DAG after reflection, not by modifying `SchemaReflector`.

### 3. Kahn's Algorithm for Topological Sort

`DirectedAcyclicGraph.topological_sort()` implements Kahn's Algorithm (BFS-based):

```
1. Compute in-degree for every node.
2. Seed a queue with all zero-in-degree nodes (sorted for determinism).
3. While the queue is non-empty:
   a. Dequeue a node, append to result.
   b. Decrement in-degree for each neighbour.
   c. Enqueue any neighbour whose in-degree reaches zero.
4. If result length < node count: a cycle exists -- call _find_cycle() and raise CycleDetectionError.
```

Sorting neighbour lists before enqueueing ensures deterministic output across Python runs
regardless of `set` iteration order.

### 4. Cycle Detection and `CycleDetectionError`

When Kahn's Algorithm terminates with unprocessed nodes, a cycle is present. `_find_cycle()`
performs DFS with a recursion stack to identify the actual participating nodes and their order.

`CycleDetectionError` is raised with a `cycle: list[str]` attribute naming the nodes in the
cycle. The exception message includes the full cycle representation so that the user can
identify which tables need cycle-breaking rules.

`CycleDetectionError` is defined in `graph.py` (not in `shared/`): it is an ingestion-domain
concept and should not leak into cross-cutting utilities.

Common cycle sources:
- **Self-referential tables:** An `employees` table with a `manager_id` FK referencing itself.
- **Circular FK loops:** `A -> B -> C -> A` across three tables.

The engine does not automatically resolve cycles. Callers must provide explicit cycle-breaking
rules (e.g., treat one FK as virtual and handle it in a post-processing pass) before ingestion
can proceed.

### 5. Inter-Module Data Handoff

`SchemaReflector` lives inside `synth_engine.modules.ingestion`. Per [ADR-0001], only
`bootstrapper` may orchestrate across modules; no module may import from another module directly.

When downstream modules (T3.4 Subsetting Core, Phase 4 Synthesizer, Profiler) need the
topological ordering or column metadata produced by this module, the following pattern MUST
be used:

1. **Bootstrapper calls** `SchemaReflector.reflect()` and
   `DirectedAcyclicGraph.topological_sort()` at job-initialization time.
2. **Bootstrapper packages** the result into a neutral, stdlib-only data structure (dataclass or
   `TypedDict`) defined in `synth_engine/shared/` -- NOT in `modules/ingestion/`.
3. **Downstream modules receive** the packaged schema topology via constructor injection from
   bootstrapper. They MUST NOT import from `synth_engine.modules.ingestion` directly.

This is the same pattern documented in [ADR-0012] (PostgresIngestionAdapter cross-module gap).
Direct import of `SchemaReflector`, `DirectedAcyclicGraph`, or `CycleDetectionError` by any
module outside `synth_engine.modules.ingestion` will fail the import-linter CI gate.

---

## Consequences

### Positive

- **Correctness:** Topological sort guarantees parents are always synthesised before children.
- **Transparency:** Cycle detection surfaces schema design issues early with actionable error
  messages naming the involved tables.
- **Testability:** `DirectedAcyclicGraph` has no external dependencies (stdlib only), making
  it trivially unit-testable. `SchemaReflector` accepts an `Engine` and is tested via mocked
  `inspect()` calls.
- **Security:** No SQL is executed by either module. All schema interrogation is delegated to
  SQLAlchemy's reflection API via the engine, which has already been validated by T3.1's
  privilege pre-flight check.
- **Boundary compliance:** `graph.py` imports only from the standard library.
  `reflection.py` imports from `sqlalchemy` and `synth_engine.modules.ingestion.graph`.
  Neither module violates import-linter contracts.
- **API symmetry:** Both `add_node()` and `add_edge()` are idempotent, eliminating a class of
  silent correctness bugs in callers that process schemas with redundant FK constraints.

### Negative / Trade-offs

- **No virtual FK support:** Schemas relying on application-enforced FKs (no DB constraints)
  will produce a DAG with no edges between logically related tables. Processing order will be
  arbitrary for those table pairs. This is acceptable for Phase 3; virtual FK support is a
  Phase 4 enhancement.
- **Cycle-breaking is manual:** The engine raises `CycleDetectionError` but does not suggest
  or apply a resolution. Users must supply explicit rules. This is intentional: automatic
  cycle-breaking could produce incorrect output silently.
- **Cross-module handoff requires bootstrapper coordination:** Downstream modules cannot import
  DAG results directly; the bootstrapper must package and inject them. This is the correct
  boundary enforcement but adds an orchestration step.

---

## Alternatives Considered

| Alternative | Reason Rejected |
|---|---|
| NetworkX library for DAG | External dependency with no air-gap approval; stdlib `collections.deque` suffices for Kahn's Algorithm. |
| DFS-only topological sort | Kahn's Algorithm directly integrates cycle detection via in-degree residual check, making it simpler and more readable than DFS with coloring. |
| Inferring virtual FKs from column names | Ambiguous in real-world schemas; deferred to explicit user configuration. |
| Storing `CycleDetectionError` in `shared/` | It is ingestion-specific; placing it in `shared/` would violate the principle of minimal shared surface area. |
| Non-idempotent add_edge() (original) | Produces silent duplicate edges and double-counted in-degrees on composite FK schemas; asymmetric API contract vs. idempotent add_node(). |
