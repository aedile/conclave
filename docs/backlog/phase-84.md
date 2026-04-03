# Phase 84 — Multi-Database Support

**Tier**: 8 (Enterprise Scale)
**Goal**: Extend ingestion and subsetting beyond PostgreSQL to support MySQL/MariaDB and
SQL Server as source databases.

**Dependencies**: None (can run in parallel with Phases 80-83)

---

## Context & Constraints

- Currently: PostgreSQL only. All SQL in `modules/ingestion/` and `modules/subsetting/`
  uses PostgreSQL-specific syntax and psycopg2 driver.
- Enterprise data lives in mixed database estates. PostgreSQL-only limits adoption.
- The modular monolith already separates ingestion/subsetting from other modules via
  import-linter contracts. The adapter pattern fits naturally.
- Target database support: PostgreSQL (existing), MySQL/MariaDB, SQL Server (MSSQL).
- Schema reflection, FK graph construction, and subsetting traversal must work across
  all three databases. Masking and synthesis operate on Parquet files and are DB-agnostic.
- ADR required: adapter interface design, driver selection (mysql-connector vs pymysql,
  pyodbc vs pymssql).
- Air-gap constraint: all database drivers must be bundled in the air-gap tarball.

---

## Tasks

### T84.1 — Database Adapter Interface

**Files to create/modify**:
- `src/synth_engine/modules/ingestion/adapter_protocol.py` (new)
- `src/synth_engine/modules/ingestion/postgres_adapter.py` (refactor to implement protocol)
- ADR for adapter interface design

**Acceptance Criteria**:
- [ ] `DatabaseAdapter` Protocol: `reflect_schema()`, `validate_connection()`, `execute_query()`, `get_engine()`
- [ ] Existing `postgres_adapter.py` refactored to implement the protocol
- [ ] All existing tests pass unchanged (backward compatible)
- [ ] Protocol defined in `shared/protocols.py` or `modules/ingestion/` (ADR decides placement)
- [ ] ADR documents adapter interface, driver selection rationale, and dialect-specific SQL handling

### T84.2 — MySQL/MariaDB Adapter

**Files to create**:
- `src/synth_engine/modules/ingestion/mysql_adapter.py` (new)
- `tests/integration/test_mysql_adapter.py` (new)

**Acceptance Criteria**:
- [ ] Schema reflection: tables, columns, types, PKs, FKs via `information_schema`
- [ ] FK graph construction works identically to PostgreSQL adapter
- [ ] Subsetting traversal works with MySQL-specific quoted identifiers (backticks)
- [ ] Data type mapping: MySQL types → Parquet-compatible types
- [ ] Connection validation with privilege pre-flight check
- [ ] Integration test against real MySQL (Docker, pytest fixture)
- [ ] Driver: `mysql-connector-python` or `pymysql` (ADR decides)

### T84.3 — SQL Server Adapter

**Files to create**:
- `src/synth_engine/modules/ingestion/mssql_adapter.py` (new)
- `tests/integration/test_mssql_adapter.py` (new)

**Acceptance Criteria**:
- [ ] Schema reflection: tables, columns, types, PKs, FKs via `INFORMATION_SCHEMA`
- [ ] FK graph construction works identically to other adapters
- [ ] Subsetting traversal works with SQL Server-specific quoted identifiers (`[brackets]`)
- [ ] Data type mapping: SQL Server types → Parquet-compatible types
- [ ] Connection validation with privilege pre-flight check
- [ ] Integration test against real SQL Server (Docker `mcr.microsoft.com/mssql/server`)
- [ ] Driver: `pyodbc` or `pymssql` (ADR decides)

### T84.4 — Adapter Selection & Connection Config

**Files to modify**:
- `bootstrapper/routers/connections.py`
- `bootstrapper/schemas/connections.py`
- `shared/settings.py`

**Acceptance Criteria**:
- [ ] Connection model gains `driver` field: `postgres`, `mysql`, `mssql`
- [ ] `POST /api/v1/connections` accepts driver parameter
- [ ] Adapter selected based on driver field at connection time
- [ ] Connection test endpoint validates connectivity with the selected adapter
- [ ] DSN format validated per driver (PostgreSQL URI vs MySQL URI vs MSSQL URI)
- [ ] Default driver: `postgres` (backward compatible)

---

## Testing & Quality Gates

- Integration tests against real PostgreSQL, MySQL, and SQL Server (all Docker-based)
- Schema reflection parity test: same schema in all 3 databases → same SchemaTopology output
- FK traversal parity test: same seed query → same subset (row count match) across databases
- Attack tests: SQL injection in table/column names across all 3 dialects
- Import boundary: adapters stay inside `modules/ingestion/`, no cross-module imports
