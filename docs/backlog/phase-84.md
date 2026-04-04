# Phase 84 — Multi-Database Support

**Tier**: 8 (Enterprise Scale)
**Goal**: Extend ingestion and subsetting beyond PostgreSQL to support MySQL/MariaDB and
SQL Server as source databases.

**Dependencies**: Soft dependency on Phase 79 (T84.4 modifies `connections.py` which P79
also modifies — merge conflict risk if parallel. Recommend P84 starts after P79 merges.)

---

## Prerequisites

### T84.0 — Database Driver ADR (Rule 6)

Select MySQL and SQL Server drivers before implementation begins. ADR must document:
- MySQL: `mysql-connector-python` vs `pymysql` — evaluate pure-Python vs C extension,
  air-gap bundle size, CVE surface, `pip-audit` results
- SQL Server: `pyodbc` vs `pymssql` — evaluate native binary dependencies
  (`pyodbc` requires unixODBC + Microsoft ODBC Driver 18; `pymssql` bundles FreeTDS).
  **Native binary components cannot be scanned by `pip-audit`** — the ADR must document
  native binary provenance audit procedure.
- Air-gap bundle inclusion: how are native binaries bundled in the tarball?
- CI impact: SQL Server Docker image (`mcr.microsoft.com/mssql/server`) is ~1.5GB,
  x86_64 only, requires `ACCEPT_EULA=Y` and `SA_PASSWORD` env vars. Document CI
  runner requirements and expected job time increase (~3-5 minutes).

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
- **Subsetting dialect audit**: Before claiming subsetting parity, audit
  `modules/subsetting/traversal.py` for any raw SQL strings (`text()` with table/column
  name interpolation). SQLAlchemy Core handles dialect quoting automatically, but any
  raw SQL will break across dialects. This audit is a prerequisite for T84.2 and T84.3.
- Air-gap constraint: all database drivers must be bundled in the air-gap tarball.

---

## Tasks

### T84.1 — Database Adapter Interface

**Files to create/modify**:
- `src/synth_engine/modules/ingestion/adapter_protocol.py` (new — place in
  `modules/ingestion/`, not `shared/protocols.py`. Rationale: the protocol is implemented
  only by adapters in `modules/ingestion/` and consumed by the bootstrapper, which can
  import from modules. No other module needs it.)
- `src/synth_engine/modules/ingestion/postgres_adapter.py` (refactor to implement protocol)
- ADR for adapter interface design (per T84.0)

**Acceptance Criteria**:
- [ ] `DatabaseAdapter` Protocol: `reflect_schema()`, `validate_connection()`, `execute_query()`, `get_engine()`
- [ ] Existing `postgres_adapter.py` refactored to implement the protocol
- [ ] All existing tests pass unchanged (backward compatible)
- [ ] Subsetting dialect audit completed: document any raw SQL in `modules/subsetting/traversal.py`
      that needs dialect-specific handling

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
- [ ] CI pipeline updated with MySQL service container

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
- [ ] CI pipeline updated with SQL Server service container (`ACCEPT_EULA=Y`, `SA_PASSWORD`)

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
- [ ] Invalid driver value (e.g., `oracle`, empty string) returns 422 with supported
      driver list in error message. Test required.
- [ ] Default driver: `postgres` (backward compatible)
- [ ] Air-gap bundle smoke test: verify MySQL and MSSQL drivers installable from bundle
      without internet access

---

## Testing & Quality Gates

- Integration tests against real PostgreSQL, MySQL, and SQL Server (all Docker-based)
- Schema reflection parity test: same schema in all 3 databases → same SchemaTopology output
- FK traversal parity test: same seed query → same subset (row count match) across databases
- Attack tests: SQL injection in table/column names across all 3 dialects
- Attack tests: invalid driver value returns 422 (not 500)
- Import boundary: adapters stay inside `modules/ingestion/`, no cross-module imports
- CI pipeline updated with MySQL and SQL Server service containers
- All existing PostgreSQL tests pass unchanged
