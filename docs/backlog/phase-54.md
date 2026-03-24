# Phase 54 — Full E2E DP Synthesis Validation (Pagila)

**Goal**: Prove the complete pipeline end-to-end with a real multi-table PostgreSQL
dataset (Pagila), real CTGAN training with DP-SGD, and validated synthetic output.
This is the first real-data validation of the system.

**Prerequisite**: Phase 53 merged.

**Source**: Staff-level architecture review, 2026-03-24 — identified that the system
has never been proven end-to-end with real data through the full pipeline.

---

## T54.1 — Pagila Dataset Provisioning

**Priority**: P6 — Infrastructure setup for validation.

### Context & Constraints

1. Pagila is the PostgreSQL port of MySQL's Sakila sample database. It provides
   ~15 tables with a rich FK graph (customer → rental → inventory → film, etc.),
   varied column types (timestamps, numerics, text, booleans), ~46K rentals,
   ~16K customers, ~1K films.
2. The dataset contains no real PII — all data is fictional DVD rental records.
   Column names (`first_name`, `last_name`, `email`, `address`) exercise the
   masking registry naturally.
3. Pagila SQL dumps are publicly available from the official PostgreSQL wiki
   and GitHub mirrors. Use the official `pagila-data.sql` and `pagila-schema.sql`.
4. The dataset must be loaded into a local PostgreSQL instance accessible to the
   engine's ingestion adapter.
5. Include a `scripts/provision_pagila.sh` helper that downloads the Pagila SQL
   files, creates the `pagila` database, and loads the schema + data.
6. Add a `sample_data/pagila/` directory with a README documenting the dataset
   source, license (PostgreSQL License), and table count.

### Acceptance Criteria

1. `scripts/provision_pagila.sh` created — downloads Pagila, creates DB, loads data.
2. Script is idempotent (drops and recreates if DB exists).
3. `sample_data/pagila/README.md` documents dataset source, license, table list.
4. Pagila loads cleanly into PostgreSQL 16+ with all FK constraints satisfied.
5. Script validates row counts after load (customers ≥ 500, rentals ≥ 40000).

### Files to Create/Modify

- Create: `scripts/provision_pagila.sh`
- Create: `sample_data/pagila/README.md`

---

## T54.2 — Full Pipeline Validation Script

**Priority**: P4 — Production validation.

### Context & Constraints

1. This script exercises the COMPLETE production pipeline end-to-end:
   - Schema reflection (ingestion adapter → FK DAG)
   - Subsetting (FK-aware row selection from Pagila)
   - Masking (deterministic FPE on PII-like columns)
   - Statistical profiling (distribution detection)
   - CTGAN training WITH DP-SGD wrapper (real Opacus, real epsilon accounting)
   - FK post-processing (orphan elimination)
   - Output validation (statistical comparison, FK integrity check)
2. This is NOT an automated test suite fixture. It is a standalone validation
   script in `scripts/` that an operator runs manually to prove the system works.
   It does NOT run as part of `pytest` or CI.
3. The script must accept configuration via CLI arguments or environment variables:
   - Database connection string (default: local Pagila from T54.1)
   - Subset size (default: 500 rows from root table)
   - Epsilon budget (default: 10.0 — generous for validation)
   - Number of CTGAN epochs (default: 50 — enough for validation, not production)
   - Output directory for synthetic Parquet files
4. The script MUST use the actual production code paths — not test doubles,
   not mocks, not `DummyMLSynthesizer`. It imports from `src/synth_engine/`
   and uses the real `SynthesisEngine`, real `DPTrainingWrapper`, real
   `StatisticalProfiler`, real `DeterministicMaskingEngine`.
5. Requires the `synthesizer` dependency group (`poetry install --with synthesizer`).
   CPU-only is acceptable — set `FORCE_CPU=true` if no GPU available.
6. The validation script should produce a structured report (JSON or markdown)
   documenting:
   - Tables processed, row counts (source vs synthetic)
   - FK integrity check results (orphan count per FK column)
   - Epsilon budget consumed vs allocated
   - Per-column distribution comparison (KS statistic or similar)
   - Masking verification (no unmasked PII-like values in output)
   - Wall-clock time per pipeline stage
7. Select a representative subset of Pagila tables for the validation (not all 15).
   Recommended: `customer`, `address`, `rental`, `inventory`, `film` — a 5-table
   linear FK chain that exercises the subsetting engine's topological traversal.

### Acceptance Criteria

1. `scripts/validate_full_pipeline.sh` (or `.py`) created.
2. Script runs the complete pipeline: reflect → subset → mask → profile →
   train (DP-CTGAN) → generate → FK post-process → validate.
3. Uses real production code paths — no mocks, no test doubles.
4. Produces a structured validation report with:
   - Table/row counts (source vs synthetic)
   - FK integrity results (zero orphans after post-processing)
   - Epsilon accounting (budget consumed < allocated)
   - Per-column statistical comparison
   - Masking verification
   - Timing breakdown
5. Report is written to `output/validation-report-<timestamp>.json` (gitignored).
6. Script exits 0 on success (all validations pass), non-zero on any failure.
7. Runs successfully on CPU-only with `FORCE_CPU=true`.
8. Validated on the 5-table Pagila subset (customer → address → rental →
   inventory → film).
9. Full quality gates pass (the script itself must pass ruff, mypy, bandit).

### Files to Create/Modify

- Create: `scripts/validate_full_pipeline.py`
- Modify: `.gitignore` (ensure `output/` is excluded — should already be)
- Modify: `Makefile` (add `validate-pipeline` target)

---

## T54.3 — Validation Run & Results Documentation

**Priority**: P6 — Documentation.

### Context & Constraints

1. Execute the validation script from T54.2 against the Pagila dataset from T54.1.
2. Capture the full output report.
3. Document the results in `docs/E2E_VALIDATION_RESULTS.md` — not the archived
   `docs/archive/E2E_VALIDATION.md` (which is from an earlier, less comprehensive run).
4. Include: hardware specs, Python version, dependency versions, wall-clock times,
   epsilon budget accounting, FK integrity verification, statistical comparison
   summary, and any anomalies observed.
5. If any validation check fails, document the failure and create an advisory.

### Acceptance Criteria

1. Validation script executed successfully against Pagila.
2. `docs/E2E_VALIDATION_RESULTS.md` created with full results.
3. All FK integrity checks pass (zero orphans).
4. Epsilon consumed < epsilon allocated.
5. Masking verification passes (no unmasked PII-like values).
6. Any anomalies documented with analysis.
7. `docs/index.md` updated with link to new results document.

### Files to Create/Modify

- Create: `docs/E2E_VALIDATION_RESULTS.md`
- Modify: `docs/index.md`

---

## Task Execution Order

```
T54.1 (Pagila provisioning) ──> T54.2 (validation script) ──> T54.3 (run & document)
```

Sequential — each task depends on the previous.

---

## Phase 54 Exit Criteria

1. Pagila dataset loads cleanly into local PostgreSQL.
2. Full pipeline runs end-to-end with real CTGAN + DP-SGD (no mocks).
3. FK integrity verified — zero orphans in synthetic output.
4. Epsilon accounting verified — budget not exceeded.
5. Masking verified — no unmasked PII-like values in output.
6. Validation results documented with statistical comparison.
7. All quality gates pass.
8. Review agents pass for all tasks.
