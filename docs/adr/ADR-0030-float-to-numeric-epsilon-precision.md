# ADR-0030 — Float to NUMERIC(20, 10) for Epsilon Budget Columns

**Date:** 2026-03-16
**Status:** Accepted
**Deciders:** PM + Architecture Reviewer
**Task:** P16-T16.1
**Resolves:** ADV-050 (floating-point drift in epsilon accounting) and P16-T16.1
migration debt documented in `ledger.py` lines 28–49.

---

## Context

The Conclave Engine tracks differential-privacy epsilon budgets in a PostgreSQL
database via two tables: `privacy_ledger` and `privacy_transaction`.  When these
tables were first created in migration 001 (P4-T4.4), the epsilon columns were
declared as `sa.Float()` — which PostgreSQL stores as `DOUBLE PRECISION` (IEEE
754 64-bit floating point, also known as FLOAT8).

In Phase 8, the ORM models in `src/synth_engine/modules/privacy/ledger.py` were
updated to use `Numeric(precision=20, scale=10)` to prevent floating-point
accumulation drift (ADV-050).  This created a DDL/ORM mismatch:

| Layer | Type |
|-------|------|
| Alembic migration 001 DDL | `FLOAT8` / `DOUBLE PRECISION` |
| SQLModel ORM (`ledger.py`) | `NUMERIC(20, 10)` |

This mismatch means that new deployments via migration apply `FLOAT8` columns
while the ORM writes `Decimal` values, relying on the database driver to coerce
types.  This is a correctness risk: floating-point storage of epsilon values
can introduce sub-epsilon accumulation errors over many budget spending calls.

---

## Decision

**Use `NUMERIC(20, 10)` for all epsilon budget columns.**

Migration 003 (`003_fix_epsilon_column_precision.py`) ALTERs the three affected
columns:

- `privacy_ledger.total_allocated_epsilon`: `FLOAT8` → `NUMERIC(20, 10)`
- `privacy_ledger.total_spent_epsilon`: `FLOAT8` → `NUMERIC(20, 10)`
- `privacy_transaction.epsilon_spent`: `FLOAT8` → `NUMERIC(20, 10)`

---

## Rationale

### Why NUMERIC instead of Float?

IEEE 754 floating-point arithmetic is non-associative: the order of addition
operations affects the result.  For an epsilon budget that is debited hundreds
or thousands of times over the lifetime of a deployment, the accumulated error
can become significant relative to the epsilon values being tracked (typically
in the range 0.01–10.0).

`NUMERIC(20, 10)` provides:

1. **Exact decimal arithmetic** — no rounding at the storage layer.
2. **Sufficient range** — `NUMERIC(20, 10)` holds values up to ±10^10 with 10
   fractional decimal digits.  Realistic privacy budgets never approach this
   ceiling.
3. **ORM/DDL consistency** — the Python `Decimal` type maps exactly to
   `NUMERIC` in PostgreSQL via the psycopg2/asyncpg drivers.  No coercion is
   required.

### Why precision=20, scale=10?

- **Scale 10** preserves sub-epsilon precision (e.g., ε = 0.0000000001 is
  representable).  Practical epsilon values are far larger; scale 10 provides a
  generous safety margin.
- **Precision 20** supports total budget values up to 10^10 — orders of
  magnitude larger than any realistic deployment.
- The choice is consistent with industry practice for monetary decimal columns
  (which also use `NUMERIC` with sufficient precision/scale to prevent drift).

**Note (ADV-074):** Values with more than 10 fractional digits (sub-1e-10)
cannot be stored exactly in `NUMERIC(20, 10)`.  This is an accepted limitation:
no differential-privacy epsilon value meaningful to the Constitution's privacy
guarantee requires more than 10 fractional digits.

### Why a migration instead of a conditional ORM check?

The ORM could detect the mismatch at startup and raise an error, but this would
break existing deployments.  A migration is the correct mechanism: it upgrades
the schema in place, is reversible, and is tracked in the Alembic version chain.

---

## Migration Path

Alembic migration 003 handles both new deployments and existing ones:

- **New deployments**: `alembic upgrade head` applies migrations 001 → 002 → 003
  in sequence.  The tables are created as `FLOAT8` by 001, then ALTERed to
  `NUMERIC(20, 10)` by 003.
- **Existing deployments**: Running `alembic upgrade head` on a database already
  at revision 002 applies only migration 003.  PostgreSQL casts `DOUBLE
  PRECISION` to `NUMERIC(20, 10)` without data loss for all values in the
  epsilon range.

Reverting with `alembic downgrade -1` from revision 003 runs the downgrade
function, restoring `FLOAT8` columns.

---

## Alternatives Considered

### 1. Keep Float8, add application-layer rounding

**Rejected.** Rounding at the application layer is error-prone and does not
prevent drift from concurrent writes.  The database is the authoritative store;
precision must be enforced there.

### 2. Use Float64 with careful accumulation order

**Rejected.** Controlling accumulation order across concurrent transactions is
not feasible without serialising all budget-spend operations.  The pessimistic
lock (`SELECT ... FOR UPDATE`) prevents concurrent overdraw but does not prevent
IEEE 754 rounding errors within a single accumulation.

### 3. Store epsilon as scaled integers (e.g., multiply by 10^10)

**Rejected.**  Scaled integer storage requires custom application logic for
every read and write path, and introduces its own risk of scaling bugs.
`NUMERIC` is the standard relational-database solution for this problem and
requires no custom logic.

---

## Consequences

### Positive

- DDL and ORM are now consistent: no coercion required, no silent type mismatch.
- Epsilon budget values are stored with exact decimal arithmetic, eliminating
  accumulation drift risk (ADV-050).
- The migration is reversible without data loss.

### Negative / Accepted

- Migration 003 applies a DDL ALTER to two tables.  On large tables this can
  require an exclusive lock in PostgreSQL.  Epsilon tables are expected to be
  small (one ledger row, one transaction row per synthesis run); the lock
  duration is negligible.
- Sub-1e-10 epsilon values cannot be stored exactly in `NUMERIC(20, 10)`
  (ADV-074).  This is accepted: such values have no practical meaning in
  differential privacy.

---

## References

- ADV-050: Floating-point epsilon drift finding (Phase 8)
- ADV-074: NUMERIC(20,10) precision limits sub-1e-10 DB storage (Phase 14)
- `alembic/versions/001_add_privacy_ledger_tables.py` — original Float DDL
- `alembic/versions/003_fix_epsilon_column_precision.py` — this migration
- `src/synth_engine/modules/privacy/ledger.py` — ORM definitions
- CLAUDE.md Rule 6 — Technology substitution requires an ADR
