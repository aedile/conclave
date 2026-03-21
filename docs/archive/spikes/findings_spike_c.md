> **HISTORICAL -- DO NOT USE**
> This document is an archived spike findings report. The spike code it describes
> was never promoted to production. It is retained for historical reference only.
> Do not import, adapt, or copy patterns from this document into production code
> without first consulting the relevant ADR and the Spike-to-Production Promotion
> Checklist in `CLAUDE.md`.

---

# Spike C Findings — Topological Subset & Memory-Safe Traversal

**Date:** 2026-03-13
**Spike file:** `spikes/spike_topological_subset.py`
**Outcome:** PASS — All three capabilities demonstrated and memory assertion satisfied.

---

## 1. FK Graph Discovered

The schema inspector read 6 FK edges via `PRAGMA foreign_key_list(<table>)`:

| Child Table | Child Column | Parent Table | Parent Column |
|-------------|-------------|--------------|---------------|
| address | customer_id | customer | customer_id |
| inventory | film_id | film | film_id |
| payment | rental_id | rental | rental_id |
| payment | customer_id | customer | customer_id |
| rental | inventory_id | inventory | inventory_id |
| rental | customer_id | customer | customer_id |

Key observations:
- `payment` has two FK parents (`rental` and `customer`), demonstrating the multi-FK case.
- `rental` also has two FK parents (`customer` and `inventory`).
- `film` and `customer` are root nodes with no FK dependencies.

---

## 2. Topological Sort Order

Kahn's algorithm produced the following parent-first ordering:

```
customer -> film -> address -> inventory -> rental -> payment
```

Edge deduplication ensures that tables with multiple FK columns to the same parent (e.g., `payment -> customer` appears twice in the raw FK list) contribute only one unit of in-degree, producing a correct sort without duplicates in the result.

---

## 3. Rows Extracted for 3-Seed Subset (customer_ids: 1, 50, 100)

| Table | Rows Extracted |
|-------|---------------|
| customer | 3 |
| address | 3 |
| rental | 30 |
| payment | 45 |
| **Total** | **81** |

Notes:
- `film` and `inventory` are not customer-reachable from this seed (no FK path from `customer` to `film` going forward), confirming the reachability algorithm correctly excludes unrelated tables.
- `rental` yields 30 rows: each of the 3 seed customers has 10 rentals (seeding formula: `customer_id = 1 + (rental_id % 10000)` means customer 1 has rental_ids 1, 10001, 20001, ... = 10 rentals, same for customers 50 and 100).
- `payment` yields 45 rows: 15 payments per customer (seeding formula: `rental_id = 1 + (payment_id % 100000)`; the 30 extracted rentals have payments where both `rental_id` AND `customer_id` match via the self-consistent seeding formula).

---

## 4. Generated CTE SQL Snippet

```sql
WITH
  seed_customer AS (
    SELECT * FROM customer
    WHERE customer_id IN (1, 50, 100)
  ),
  seed_address AS (
    SELECT t.* FROM address t
    WHERE EXISTS (
      SELECT 1 FROM seed_customer p
      WHERE p.customer_id = t.customer_id
    )
  ),
  seed_rental AS (
    SELECT t.* FROM rental t
    WHERE EXISTS (
      SELECT 1 FROM seed_customer p
      WHERE p.customer_id = t.customer_id
    )
  ),
  seed_payment AS (
    SELECT t.* FROM payment t
    WHERE EXISTS (
      SELECT 1 FROM seed_rental p
      WHERE p.rental_id = t.rental_id
    )
    AND EXISTS (
      SELECT 1 FROM seed_customer p
      WHERE p.customer_id = t.customer_id
    )
  )
```

Design decisions in the generator:
- **EXISTS over JOIN**: Avoids the ambiguous-column-alias error that arises when a table (e.g., `payment`) has multiple FK parents and both join aliases would be named the same. EXISTS also prevents row duplication from cross-joining multiple parent sets.
- **One CTE per reachable table**: The WITH block is self-contained; each CTE references only previously-defined sibling CTEs, making the query portable and readable.
- **Integer-only WHERE clause**: The seed id list is composed exclusively of `str(int)` literals joined by commas, and all identifiers are sourced from PRAGMA schema metadata — eliminating SQL injection risk.

---

## 5. Memory Profile

| Phase | Current (MB) | Peak (MB) |
|-------|-------------|-----------|
| before_seed | 0.00 | 0.00 |
| after_seed | 0.35 | 29.89 |
| before_extract | 0.37 | 29.89 |
| after_extract (streaming phase only) | 0.38 | **0.38** |

**Flat memory assertion:** Peak during streaming (0.38 MB) < ceiling 59.78 MB (2x post-seed peak of 29.89 MB). PASS.

The streaming peak of 0.38 MB vs. the seeding peak of 29.89 MB demonstrates that the extractor processes 81 rows from a 275,000-row database at constant memory — the SQLite cursor yields one row at a time, and no application-level buffering occurs.

The seeding peak of 29.89 MB reflects Python list construction during the `executemany` bulk insert (lists of 10k-150k tuples). This is a one-time cost at setup and is not representative of the steady-state operation of the subsetting engine.

---

## 6. How to Run

```bash
poetry run python spikes/spike_topological_subset.py
```

No external dependencies. All stdlib only: `sqlite3`, `tracemalloc`, `collections`, `dataclasses`, `collections.abc`.

---

## 7. Recommendation for Phase 3 Subsetting Engine

This spike proves all three required capabilities are feasible with pure stdlib + SQLite:

1. **FK graph inference** is reliable via `PRAGMA foreign_key_list`. The `SchemaInspector` class is a faithful prototype for the Phase 3 `ingestion.schema_inspector` module. The PRAGMA approach works equally well against PostgreSQL via `information_schema.key_column_usage` (with minor query adaptation).

2. **CTE generation** is the correct architectural pattern. The `SubsetQueryGenerator` should be moved into `src/synth_engine/modules/ingestion/` as the core of the Phase 3 subsetting engine. The EXISTS-based approach handles multi-FK tables cleanly and should be retained over JOIN-based generation.

3. **Streaming extraction** with `yield from cursor` is proven memory-safe at this scale (275k rows, streaming peak 0.38 MB). For Phase 3 production use:
   - Replace in-memory SQLite with a PostgreSQL connection (via `psycopg` already in the dependency list).
   - Use server-side cursors (`cursor.itersize`) for very large result sets to avoid fetching all rows into the driver buffer.
   - Consider a configurable `seed_ids` batch size to limit CTE IN-list length for large seed sets.

4. **Topological sort** (Kahn's algorithm with edge deduplication) is the correct foundation for the Phase 3 table ordering logic needed for both subsetting and insertion-order-safe synthetic data output.

---

*Spike C complete. No blockers identified for Phase 3 implementation.*
