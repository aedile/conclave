# Phase 3.5: Technical Debt Sprint — "Back to Solid Ground"

**Goal:** Close all unmet Phase 3 acceptance criteria, resolve the module cohesion architectural
debt, address all standing open advisories, harden the CI/CD supply chain, and deliver a
minimal operational entrypoint — so that Phase 4 starts from a genuinely clean baseline.

**Prerequisite:** All Phase 3.5 tasks must be completed and merged before Phase 4 begins.
This phase is a gate, not a suggestion.

**Advisory Drain:** The following Open Advisory Items are addressed in this phase and should
be drained (deleted) from the RETRO_LOG Open Advisory Items table when their target task merges:
ADV-006, ADV-007, ADV-008, ADV-022, ADV-025, ADV-026, ADV-027, ADV-028, ADV-029, ADV-030, ADV-031.

---

## Task 3.5.1: Supply Chain & CI Hardening
**Assignee:** [Dev A]
**Priority:** BLOCKER — must be first. Unaddressed supply-chain risk since Phase 1.
**Estimated Effort:** 1 day
**Drains Advisories:** ADV-007

### User Story / Agentic Goal
As a DevOps Engineer, I want all GitHub Actions pinned to immutable commit SHAs and a Trivy
container image scan added to CI, so that the supply chain is hardened against third-party
action compromise and container vulnerabilities are caught before deployment.

### Context & Constraints
- GitHub Actions pinned to mutable tags (`@v4`, `@v2`) are a documented supply-chain risk.
  A malicious push to `gitleaks-action@v2` or `snok/install-poetry` would execute in our CI
  without any warning. SHA-pinning prevents this — the action content is frozen to a specific
  commit hash.
- Trivy must scan the production Docker image as a standalone CI job (not blocking unit tests).
- The integration test job must pin a specific PostgreSQL version (`postgresql-16`) and validate
  that `pg_ctl` is discoverable before running tests. Dynamic version discovery via
  `ls /usr/lib/postgresql/ | head -1` is fragile and must be replaced with a pinned version.
- All existing CI behavior must be preserved — this task modifies jobs, it does not remove them.

### Acceptance Criteria
- [ ] All `uses:` lines in `.github/workflows/ci.yml` are pinned to full commit SHAs with a
  comment noting the version tag (e.g., `# v4.1.1`).
- [ ] A new `trivy-scan` job is added to `ci.yml` that scans the production Docker image and
  fails on HIGH or CRITICAL severity CVEs.
- [ ] The `integration-test` job pins `postgresql-16` explicitly in the apt install command
  and sets `PG_BIN=/usr/lib/postgresql/16/bin` as a hard-coded path (not dynamic discovery).
- [ ] CI passes with all existing jobs green.

### Testing & Quality Gates
- Run the full CI pipeline locally (or verify via PR CI run) — all jobs pass.
- Manually verify each SHA corresponds to the intended version tag by checking the action's
  GitHub releases page.

### Files to Create/Modify
- [MODIFY] `.github/workflows/ci.yml`

### Definition of Done (DoD) Checklist
1. **Security Compliance:** All GitHub Actions pinned to SHAs.
2. **Trivy Integration:** Container image scan runs as a CI job.
3. **Pipeline Green:** All existing CI jobs still pass.
4. **Peer Review:** Reviewed by devops-reviewer.
5. **Advisory Drained:** ADV-007 row deleted from RETRO_LOG.

---

## Task 3.5.2: Module Cohesion Refactor — Extract Mapping & Subsetting
**Assignee:** [Dev B]
**Priority:** HIGH — architectural debt; blocks import-linter expansion in Phase 4.
**Estimated Effort:** 2 days
**Drains Advisories:** None directly — resolves Phase 3 Retro finding Q1/Q2.

### User Story / Agentic Goal
As a Software Architect, I want the relational mapping logic (DAG, topological sort, schema
reflection) and the subsetting/egress logic (DagTraversal, SubsettingEngine, EgressWriter)
extracted from `modules/ingestion/` into their own dedicated modules, so that each module has
a single coherent responsibility and import-linter contracts can be expanded to enforce those
boundaries.

### Context & Constraints
- Currently `modules/ingestion/` contains: ingestion (correct), schema reflection, graph/DAG,
  topological traversal, subsetting orchestration, and egress writing. This is five distinct
  concerns in one module.
- Target state: three modules with strict single-responsibility:
  - `modules/ingestion/` — postgres_adapter.py, validators.py, SchemaInspector only
  - `modules/mapping/` — graph.py, reflection.py, CycleDetectionError, DirectedAcyclicGraph
  - `modules/subsetting/` — traversal.py, core.py, egress.py, SubsettingEngine, DagTraversal,
    EgressWriter, SubsetResult
- `shared/schema_topology.py` stays in `shared/` — it is a cross-module value object (correct).
- Import-linter contracts must be updated to reflect the new topology:
  - `modules/subsetting` may import from `modules/mapping` (it needs the DAG structure)
  - `modules/mapping` may NOT import from `modules/subsetting` or `modules/ingestion`
  - `modules/ingestion` may NOT import from `modules/mapping` or `modules/subsetting`
  - All three must not import from `bootstrapper`
- All existing tests must pass without modification to test logic (only import paths change).
- All existing ADRs that reference file paths must be updated (ADR-0013, ADR-0015).
- The `__init__.py` files for each module must export the public API so that bootstrapper
  imports from the module name, not from internal files.

### Acceptance Criteria
- [ ] `src/synth_engine/modules/mapping/` exists containing: `__init__.py`, `graph.py`,
  `reflection.py`.
- [ ] `src/synth_engine/modules/subsetting/` exists containing: `__init__.py`, `traversal.py`,
  `core.py`, `egress.py`.
- [ ] `src/synth_engine/modules/ingestion/` contains only: `__init__.py`,
  `postgres_adapter.py`, `validators.py`.
- [ ] `pyproject.toml` import-linter contracts updated with:
  - New independence contract covering `ingestion`, `mapping`, `subsetting`, `masking`,
    `profiler`, `privacy` as mutually independent (with the exception: subsetting may use mapping).
  - Explicit allowed-import contract: `modules.subsetting` may import from `modules.mapping`.
  - Forbidden import contracts updated to include the new modules.
- [ ] `poetry run python -m lint-imports` passes with new contracts.
- [ ] All unit and integration tests pass without changes to test logic.
- [ ] ADR-0013 and ADR-0015 updated to reflect new file paths.

### Testing & Quality Gates
- Run `poetry run python -m pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90`.
  Must pass.
- Run `poetry run pytest tests/integration/ -v --tb=short --no-cov -p pytest_postgresql`.
  Must pass.
- Run `poetry run python -m importlinter`. Must pass with new contracts.
- Run `poetry run python -m vulture src/`. Must be clean.

### Files to Create/Modify
- [NEW] `src/synth_engine/modules/mapping/__init__.py`
- [MOVE] `src/synth_engine/modules/ingestion/graph.py` → `modules/mapping/graph.py`
- [MOVE] `src/synth_engine/modules/ingestion/reflection.py` → `modules/mapping/reflection.py`
- [NEW] `src/synth_engine/modules/subsetting/__init__.py`
- [MOVE] `src/synth_engine/modules/ingestion/traversal.py` → `modules/subsetting/traversal.py`
- [MOVE] `src/synth_engine/modules/ingestion/core.py` → `modules/subsetting/core.py`
- [MOVE] `src/synth_engine/modules/ingestion/egress.py` → `modules/subsetting/egress.py`
- [MODIFY] `src/synth_engine/modules/ingestion/__init__.py`
- [MODIFY] `pyproject.toml` (import-linter contracts)
- [MODIFY] `docs/adr/ADR-0013-relational-mapping.md`
- [MODIFY] `docs/adr/ADR-0015-subsetting-saga.md`
- [MODIFY] All test files that import from `synth_engine.modules.ingestion.*` for moved classes

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Each module has exactly one coherent responsibility.
2. **Import-Linter Green:** All contracts pass with new module topology.
3. **Coverage Gate:** >= 90%.
4. **Pipeline Green:** CI passes.
5. **Peer Review:** Reviewed by architecture-reviewer (mandatory) + qa-reviewer.
6. **ADRs Updated:** ADR-0013 and ADR-0015 reflect new file paths.

---

## Task 3.5.3: Data Integrity — SchemaTopology Immutability & Virtual FK Support
**Assignee:** [Dev C]
**Priority:** HIGH — correctness risk (ADV-028) and missing T3.2 acceptance criterion.
**Estimated Effort:** 2 days
**Drains Advisories:** ADV-028

### User Story / Agentic Goal
As a Data Architect, I want `SchemaTopology` to be truly immutable (not just frozen at the
field-assignment level) and the system to support Virtual Foreign Keys (user-defined FK
mappings for databases without physical FK constraints), so that subsetting works correctly
against real-world production schemas where FK constraints are rarely fully defined.

### Context & Constraints
- `SchemaTopology(frozen=True)` prevents field reassignment but does NOT prevent mutation of
  nested dict values. `topology.columns["users"].append("evil_column")` succeeds silently.
  This is a correctness risk if topology instances are shared across concurrent operations.
  Solution: wrap `columns` and `foreign_keys` in `types.MappingProxyType` — a read-only
  view of the dict that raises `TypeError` on mutation attempts.
- Virtual Foreign Keys (VFKs) were specified in T3.2 Context & Constraints but were never
  implemented. A VFK is a user-supplied mapping: "treat column `account_id` in table
  `transactions` as referencing `accounts.id`, even though no FK constraint exists in the DB."
  This is essential for production databases (especially data warehouses and legacy systems)
  where FK constraints are absent for performance reasons.
- VFK support must be additive — it does not change behavior for schemas with physical FKs.
- VFK configuration format: a list of dicts passed to `SchemaReflector`:
  `[{"table": "transactions", "column": "account_id", "references_table": "accounts",
    "references_column": "id"}]`
- VFKs must be merged with physical FKs before the DAG is built, so topological sort and
  traversal work identically for both FK types.
- `MappingProxyType` must not break existing uses of `SchemaTopology` — all read access
  patterns (iteration, key lookup) work identically; only mutation is blocked.

### Acceptance Criteria
- [ ] `SchemaTopology.columns` is a `MappingProxyType` — attempting
  `topology.columns["t"].append("x")` raises `TypeError`.
- [ ] `SchemaTopology.foreign_keys` is a `MappingProxyType` — mutation raises `TypeError`.
- [ ] `SchemaReflector` accepts an optional `virtual_foreign_keys` parameter (list of VFK
  dicts) with the schema above.
- [ ] VFKs are merged with physical FKs before `DirectedAcyclicGraph` edges are added.
- [ ] The merged FK set is reflected in `SchemaTopology.foreign_keys`.
- [ ] `SubsettingEngine` traversal follows VFK edges exactly as it follows physical FK edges.
- [ ] A duplicate VFK (identical to an existing physical FK) is deduplicated gracefully.
- [ ] An invalid VFK (referencing a table not in the schema) raises `ValueError` with a clear
  message identifying the invalid table name.

### Testing & Quality Gates
- Unit test: `SchemaTopology` mutation attempt raises `TypeError` for both `columns` and
  `foreign_keys`.
- Unit test: `SchemaReflector` with a VFK list produces a DAG with the virtual edge.
- Integration test (pytest-postgresql): source DB has no physical FK constraint between
  `transactions` and `accounts`. VFK config is passed to `SchemaReflector`. Subsetting run
  correctly follows the virtual FK edge and produces no orphaned transactions in the target.
- Unit test: duplicate VFK is silently deduplicated (no error, no duplicate edge).
- Unit test: VFK with unknown table raises `ValueError`.

### Files to Create/Modify
- [MODIFY] `src/synth_engine/shared/schema_topology.py` (MappingProxyType wrapping)
- [MODIFY] `src/synth_engine/modules/mapping/reflection.py` (VFK parameter + merge)
- [MODIFY] `tests/unit/test_reflection.py` (VFK tests)
- [MODIFY] `tests/unit/test_subsetting_core.py` (MappingProxyType mutation test)
- [MODIFY] `tests/integration/test_subsetting_integration.py` (VFK integration test)

### Definition of Done (DoD) Checklist
1. **Correctness:** `SchemaTopology` is genuinely immutable at runtime.
2. **Feature Completeness:** VFK support satisfies the T3.2 missing AC.
3. **Coverage Gate:** >= 90%.
4. **Pipeline Green:** CI passes.
5. **Peer Review:** Reviewed.
6. **Advisory Drained:** ADV-028 row deleted from RETRO_LOG.

---

## Task 3.5.4: Operational Correctness — Bootstrapper Wiring & Minimal CLI Entrypoint
**Assignee:** [Dev D]
**Priority:** HIGH — closes the theoretical-vs-operational gap; delivers the T3.5 missing AC.
**Estimated Effort:** 2 days
**Drains Advisories:** ADV-031, ADV-022
**Blocked by:** T3.5.2 (needs correct module paths)

### User Story / Agentic Goal
As a QA Engineer, I want a CLI command I can actually run to subset a source database into
a target database with deterministic masking applied, so that Phase 3's "Rapid ROI" promise
is operationally real and not just theoretically demonstrated in test files.

### Context & Constraints
- The `row_transformer` IoC hook in `SubsettingEngine` is currently only exercised in
  integration tests. The bootstrapper never wires masking into the engine. This makes Phase
  3's core value proposition (masked subsetting) untestable without writing test code.
- A minimal CLI entrypoint is required to close T3.5 AC2: "test invokes the Subsetting API
  endpoint (or CLI entrypoint) representing a complete user job."
- The CLI does not need to be polished (that is T5.1's job). It needs to be functional:
  accept `--source`, `--target`, `--seed-table`, `--seed-query`, `--mask/--no-mask` flags
  and invoke `SubsettingEngine.run()` with the masking registry wired as `row_transformer`.
- The CLI entrypoint must live in `src/synth_engine/cli.py` and be registered in
  `pyproject.toml` as a `[tool.poetry.scripts]` entry.
- Bootstrapper wiring: `bootstrapper/main.py` must instantiate `SubsettingEngine` via
  `Depends()` with the masking registry callback injected as `row_transformer`. This is the
  canonical Phase 4 cross-module wiring point for the masking↔subsetting integration.
- ADV-022: `bootstrapper/main.py` must also intercept `CycleDetectionError` (from
  `modules/mapping/`) and return RFC 7807 error format with HTTP 422 — not a 500.
- The `EgressWriter.commit()` no-op method must be either: (a) removed entirely (preferred),
  or (b) converted to raise `NotImplementedError` with a clear docstring explaining it is
  intentionally absent and why. A silent no-op public method named `commit()` on a
  database-facing class is a semantic trap.
- The T3.5 E2E integration test (`test_e2e_subsetting.py`) must be updated to invoke the
  CLI entrypoint (via `subprocess.run` or `click.testing.CliRunner`) rather than calling
  `SubsettingEngine.run()` directly.

### Acceptance Criteria
- [ ] `src/synth_engine/cli.py` exists with a `subset` command accepting:
  `--source TEXT`, `--target TEXT`, `--seed-table TEXT`, `--seed-query TEXT`,
  `--mask / --no-mask` (default: `--mask`).
- [ ] CLI is registered in `pyproject.toml` under `[tool.poetry.scripts]` as
  `conclave-subset = "synth_engine.cli:subset"`.
- [ ] Running `poetry run conclave-subset --help` succeeds without errors.
- [ ] `bootstrapper/main.py` wires `SubsettingEngine` via FastAPI `Depends()` with the
  masking registry callback injected as `row_transformer`.
- [ ] `bootstrapper/main.py` handles `CycleDetectionError` with a 422 RFC 7807 response.
- [ ] `EgressWriter.commit()` is either removed or raises `NotImplementedError` with
  a docstring explaining why commit is not needed (auto-commit via connection context).
- [ ] `tests/integration/test_e2e_subsetting.py` invokes the CLI via
  `click.testing.CliRunner` (or equivalent) rather than calling `SubsettingEngine` directly.
- [ ] All existing integration and unit tests continue to pass.

### Testing & Quality Gates
- CLI unit test: `CliRunner` invokes `subset` with valid args on a mock engine; assert exit
  code 0 and correct output.
- CLI unit test: invalid `--seed-query` (non-SELECT) returns exit code 1 with clear error.
- CLI unit test: `--source` with invalid connection string returns exit code 1.
- Integration test: updated `test_e2e_subsetting.py` — tests call the CLI via `CliRunner`
  and assert final DB state. This satisfies T3.5 AC2.
- Bootstrapper unit test: `CycleDetectionError` raised by subsetting engine returns 422
  with RFC 7807 body.

### Files to Create/Modify
- [NEW] `src/synth_engine/cli.py`
- [MODIFY] `pyproject.toml` (poetry scripts entry)
- [MODIFY] `src/synth_engine/bootstrapper/main.py` (SubsettingEngine DI, CycleDetectionError handler)
- [MODIFY] `src/synth_engine/modules/subsetting/egress.py` (remove or fix commit() no-op)
- [MODIFY] `tests/integration/test_e2e_subsetting.py` (CLI invocation via CliRunner)
- [NEW] `tests/unit/test_cli.py`

### Definition of Done (DoD) Checklist
1. **Operational:** A real user can run `poetry run conclave-subset --help` today.
2. **T3.5 AC Closed:** Integration test invokes CLI, not internal Python API.
3. **Bootstrapper Wired:** Masking → Subsetting connection is real in production code path.
4. **Coverage Gate:** >= 90%.
5. **Pipeline Green:** CI passes.
6. **Peer Review:** Reviewed.
7. **Advisories Drained:** ADV-031 and ADV-022 rows deleted from RETRO_LOG.

---

## Task 3.5.5: Advisory Sweep — Remaining Open Items
**Assignee:** [Dev A]
**Priority:** MEDIUM — these are individually small but collectively risky.
**Estimated Effort:** 1.5 days
**Blocked by:** T3.5.2 (module paths must be stable before touching these files)
**Drains Advisories:** ADV-006, ADV-008, ADV-025, ADV-026, ADV-027, ADV-029, ADV-030

### User Story / Agentic Goal
As a Software Engineer, I want all standing open advisory items addressed in a single sweep
so that the RETRO_LOG Open Advisory Items table is clean entering Phase 4, and no inherited
debt creates surprise blockers during synthesizer or privacy accountant work.

### Context & Constraints
This task addresses multiple targeted fixes. Each is small in isolation; grouped here to
avoid per-advisory PRs.

**ADV-006:** `scripts/seed_chroma.py` references `docs/ARCHITECTURAL_REQUIREMENTS.md` in its
`SEEDING_MANIFEST`. The file exists at `docs/ARCHITECTURAL_REQUIREMENTS.md` — verify the path
in the script is correct. If the script path is wrong, fix it. If it is correct, close the
advisory. Run `poetry run python scripts/seed_chroma.py --dry-run` (if such a flag exists)
or trace the code path to confirm it won't `sys.exit(1)` at runtime.

**ADV-008:** `spikes/spike_ml_memory.py` — `_process_chunk()` uses `except ValueError: pass`
(silent swallow). Before any spike code is promoted to `src/synth_engine/modules/synthesizer/`
in Phase 4, this pattern must be eliminated. Add `logger.warning(...)` to the except block.
Also: the numpy fast path uses unseeded `np.random.normal` — replace with
`np.random.default_rng(seed).normal(...)` where `seed` is passed as a parameter. These fixes
are in spike code but must be done now to prevent the pattern from propagating into Phase 4.

**ADV-025:** The masking engine has a `luhn_check` function that lives in `algorithms.py` but
should be in a dedicated `luhn.py` file per the naming convention established in the backlog
(`masking/luhn.py`). Move `luhn_check` and related Luhn algorithm code to
`src/synth_engine/modules/masking/luhn.py`. Update imports in `algorithms.py`.

**ADV-026:** `deterministic_hash()` in `algorithms.py` has no guard on output length against
the column's `max_length` constraint. Add a `max_length: int | None = None` parameter. If
`max_length` is provided and the generated value exceeds it, truncate deterministically (not
randomly — use the first N characters of the hash output, not a random slice).

**ADV-027:** HMAC salt in `algorithms.py` is predictable (uses table name or column name as
salt). Evaluate whether the salt should incorporate a per-deployment secret. If the current
design is intentional (deterministic across deployments without a secret), document it in an
ADR note. If it should use a secret, wire `ALE_KEY` or a dedicated `MASKING_SALT` env var.
Either resolve or document — do not leave it as an open advisory.

**ADV-029:** `EgressWriter.rollback()` logs at DEBUG level. A Saga rollback is a significant
operational event — data was partially written and then wiped. This must log at WARNING level
with the list of tables that were truncated and the row counts that were lost. Update the
rollback implementation.

**ADV-030:** `SchemaReflector._create_database` (if it exists) uses Python string formatting
for database identifiers. Replace with `sqlalchemy.sql.quoted_name` or `text()` with bound
parameters. No dynamic SQL construction from user-supplied identifiers without quoting.

### Acceptance Criteria
- [ ] ADV-006 verified and closed (either script path fixed or confirmed correct).
- [ ] ADV-008: `_process_chunk()` logs at WARNING on ValueError; numpy uses seeded RNG.
- [ ] ADV-025: `luhn.py` exists in `modules/masking/`; `luhn_check` imported from there.
- [ ] ADV-026: `deterministic_hash()` accepts `max_length` and truncates deterministically.
- [ ] ADV-027: HMAC salt design is either changed or documented in an ADR/code comment.
- [ ] ADV-029: `EgressWriter.rollback()` logs at WARNING with table names and row counts.
- [ ] ADV-030: No string-formatted SQL identifiers in reflection code.
- [ ] All unit and integration tests pass.
- [ ] RETRO_LOG Open Advisory Items table has zero rows for ADV-006 through ADV-030 at end
  of this task.

### Testing & Quality Gates
- Unit test for ADV-026: `deterministic_hash("input", max_length=10)` returns a string of
  length <= 10 that is deterministic across calls.
- Unit test for ADV-029: mock the logger in `EgressWriter`; call `rollback()`; assert
  `logger.warning` was called with table names.
- Run `poetry run python -m pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90`.
- Run `poetry run python -m ruff check src/ tests/`.

### Files to Create/Modify
- [VERIFY/MODIFY] `scripts/seed_chroma.py` (ADV-006)
- [MODIFY] `spikes/spike_ml_memory.py` (ADV-008)
- [NEW] `src/synth_engine/modules/masking/luhn.py` (ADV-025)
- [MODIFY] `src/synth_engine/modules/masking/algorithms.py` (ADV-025, ADV-026, ADV-027)
- [MODIFY] `src/synth_engine/modules/subsetting/egress.py` (ADV-029)
- [MODIFY] `src/synth_engine/modules/mapping/reflection.py` (ADV-030, if applicable)
- [MODIFY] `tests/unit/test_masking_algorithms.py` (ADV-026 test)
- [MODIFY] `tests/unit/test_subsetting_egress.py` (ADV-029 test)

### Definition of Done (DoD) Checklist
1. **Advisory Table Clean:** All 7 target advisories drained from RETRO_LOG.
2. **No New Advisories:** Sweep does not introduce new findings.
3. **Coverage Gate:** >= 90%.
4. **Pipeline Green:** CI passes.
5. **Peer Review:** Reviewed.

---

## Phase 3.5 Exit Criteria

Before declaring Phase 3.5 complete and unblocking Phase 4, the PM MUST verify ALL of the
following:

| # | Criterion | Verified By |
|---|-----------|-------------|
| 1 | All GitHub Actions SHA-pinned; Trivy job running | CI green, devops-reviewer PASS |
| 2 | `modules/mapping/` and `modules/subsetting/` exist; `modules/ingestion/` is clean | import-linter green, arch-reviewer PASS |
| 3 | `SchemaTopology` mutation raises `TypeError`; VFK support tested E2E | Unit + integration tests pass |
| 4 | `poetry run conclave-subset --help` works; T3.5 E2E test calls CLI | Manual verification + CI |
| 5 | RETRO_LOG Open Advisory Items table has zero rows | PM visual inspection |
| 6 | All Phase 3.5 tasks have `review(qa):`, `review(arch):`, `review(devops):` commits | Git log |
| 7 | Unit test coverage >= 90% | CI coverage gate |
| 8 | Integration tests pass independently | CI integration-test job |

Only when all 8 criteria are met does Phase 3.5 close and Phase 4 open.
