# Phase 18 — Type Safety, Dependency Audit & End-to-End Validation

**Goal**: Reduce `type: ignore` suppression count, audit and slim the dependency tree,
and execute a full end-to-end run with realistic sample data to prove the system works
as a complete unit — not just in isolated tests.

**Prerequisite**: Phase 17 must be complete (all tasks merged, retrospective signed off).

---

## T18.1 — Type Ignore Suppression Audit & Reduction

**Priority**: P1 — Code quality (Constitution Priority 5).

### Context & Constraints

1. The codebase has 171 `# type: ignore` suppressions. All are scoped to specific mypy
   error codes (zero bare suppressions), but the volume is high.

2. Distribution: **24 in `src/`** (production), **147 in `tests/`** (test code).

3. Major categories in tests:
   - 36 `[valid-type]` — all from `pytest-postgresql` `factories.postgresql_proc` having
     no exported runtime type. May be fixable with a local type alias or stub.
   - 27 `[arg-type]` — mostly from passing mock factories where typed signatures expect
     real SQLAlchemy sessions. May be fixable with `Protocol` types or `cast()`.
   - 22 `[attr-defined]` — accessing private `_` attributes in tests. Legitimate test
     pattern but could be reduced with test-specific accessor methods.
   - 11 `[import-untyped]` — huey, pyarrow, qrcode, sdv lack `py.typed` markers.
     Not fixable without upstream changes; these are acceptable.

4. In production code (24 suppressions):
   - 5 `[no-redef]` — optional torch/nn imports with `Any` fallback. Legitimate pattern.
   - 4 `[import-untyped]` — huey, pyarrow, qrcode. Not fixable.
   - 4 `[return-value]` / `[arg-type]` — SQLAlchemy inspector return types. May be
     fixable with newer sqlalchemy-stubs or explicit casts.
   - Remainder: various legitimate library gaps.

### Acceptance Criteria

1. Production code (`src/`) `type: ignore` count reduced to ≤15 (from 24).
2. Test code (`tests/`) `type: ignore` count reduced to ≤100 (from 147).
3. Local type alias or stub created for `pytest-postgresql` proc type (eliminates ~36).
4. All remaining `type: ignore` comments have written justification.
5. `poetry run mypy src/` still passes strict mode.
6. No regression in test suite.

### Testing & Quality Gates

- `poetry run mypy src/` — must pass.
- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error` — no regression.
- All review agents spawned.

---

## T18.2 — Dependency Tree Audit & Slimming

**Priority**: P2 — Supply chain surface reduction (Constitution Priority 0).

### Context & Constraints

1. 207 transitive dependencies resolved by Poetry. Direct production deps: ~27.
2. The `synthesizer` group (torch, sdv, opacus, pyarrow, boto3) accounts for the
   majority of transitive weight (~1 GB installed). This is unavoidable for DP-SGD
   but is correctly isolated in an optional dependency group.
3. `chromadb` (used only for retrospective seeding scripts, not production runtime)
   pulls a significant transitive tree. Evaluate whether it belongs in main deps.
4. `datamodel-code-generator` is a dev dep that may be unused after initial scaffolding.
5. `asyncpg` and `greenlet` are listed as direct deps — verify they're actually imported
   at runtime vs being SQLAlchemy optional extras.

### Acceptance Criteria

1. Dependency audit table created in `docs/DEPENDENCY_AUDIT.md` listing each direct
   dependency, its purpose, whether it's used at runtime, and its transitive count.
2. Any unused direct dependencies removed.
3. `chromadb` evaluated: if only used by scripts, moved to dev group.
4. `poetry install` and `poetry install --with dev,synthesizer` both succeed.
5. All quality gates pass.

### Testing & Quality Gates

- `poetry install` — must succeed.
- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error` — no regression.
- `poetry run python -m importlinter` — must pass.
- All review agents spawned.

---

## T18.3 — End-to-End Validation with Sample Data

**Priority**: P0 — System validation (this has never been done).

### Context & Constraints

1. The system has never been run end-to-end with realistic data through Docker Compose.
   All testing uses pytest-postgresql ephemeral instances, mocked APIs, or isolated
   unit tests. The full pipeline (source DB → ingestion → subsetting → masking →
   synthesis → target DB) has not been exercised as a complete system.

2. `sample_data/` directory exists but is empty. The README references it as "Demo
   Production seed data (fictional)."

3. Need to source or generate realistic sample data with PII-like columns (names,
   emails, SSNs, phone numbers, addresses) across multiple related tables with
   foreign key relationships. Options:
   - Generate with Faker (already a dependency) — a seeding script
   - Use a well-known public dataset (e.g., Pagila/Sakila PostgreSQL sample DB)
   - Adapt an existing open dataset with PII-shaped columns

4. The validation must exercise: Docker Compose stack startup, Vault unseal, database
   connection, schema reflection, FK graph traversal, subsetting, deterministic masking,
   CTGAN training (if GPU available; FORCE_CPU=true fallback), synthetic output
   comparison via StatisticalProfiler, and egress to target database.

### Acceptance Criteria

1. Sample data seeding script created (`scripts/seed_sample_data.py`) that populates
   a source PostgreSQL database with realistic multi-table fictional data.
2. `sample_data/` populated with CSV exports of the seed data for reference.
3. `docker-compose up` starts all services successfully.
4. Full pipeline execution documented step-by-step in `docs/E2E_VALIDATION.md`.
5. `conclave-subset` CLI successfully subsets, masks, and egresses sample data.
6. API-driven synthesis job completes (FORCE_CPU=true for CI).
7. Screenshots or terminal recordings of successful runs included in docs.

### Testing & Quality Gates

- `docker-compose up -d` — all services healthy.
- `conclave-subset` CLI — completes without error on sample data.
- `POST /tasks/synthesize` — job completes via SSE.
- All review agents spawned.

---

## Phase 18 Exit Criteria

- `type: ignore` count reduced (src ≤15, tests ≤100).
- Dependency audit completed; unused deps removed.
- End-to-end pipeline validated with sample data through Docker Compose.
- `sample_data/` populated with fictional seed data.
- E2E validation documented with evidence.
- All quality gates passing.
- Phase 18 end-of-phase retrospective completed.
