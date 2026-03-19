# Phase 43 — Architectural Polish, Code Hygiene & Rule Sunset Evaluation

**Goal**: Address remaining P2/P3 findings from the 2026-03-19 audit: extract
`dp_accounting.py`, consolidate optional import patterns, clean up archive
clutter, add document lifecycle metadata, add request flow documentation,
and evaluate CLAUDE.md rules tagged `[sunset: Phase 40]`.

**Prerequisite**: Phase 42 merged. Zero open advisories.

**ADR**: None required — refactoring, documentation, and governance housekeeping.

**Source**: Production Readiness Audit, 2026-03-19 — P2/P3 items 11-20.

---

## T43.1 — Extract `dp_accounting.py` From `job_orchestration.py`

**Priority**: P2 — Maintainability. `job_orchestration.py` is 579 lines with
`_handle_dp_accounting()` (85 lines) and related constants embedded. Extracting
into a focused module reduces cognitive load.

### Context & Constraints

1. Extract from `job_orchestration.py`:
   - `_handle_dp_accounting()` function (~85 lines, lines 196-281)
   - `_AUDIT_RECONCILIATION_MSG` constant
   - `DpAccountingStep` class
   - Related imports (`AuditWriteError`, `EpsilonMeasurementError`, etc.)

2. New file: `src/synth_engine/modules/synthesizer/dp_accounting.py`

3. `job_orchestration.py` imports from the new module. All public API unchanged.

4. Import-linter boundaries unchanged (stays within `modules/synthesizer/`).

5. Tests in `test_job_steps.py` that test DP accounting should continue to
   work without modification (they import the classes, not the file path).

### Acceptance Criteria

1. `dp_accounting.py` contains all DP accounting logic.
2. `job_orchestration.py` reduced by ~100 lines.
3. All existing tests pass without modification.
4. Import-linter passes.
5. Full gate suite passes.

### Files to Create/Modify

- Create: `src/synth_engine/modules/synthesizer/dp_accounting.py`
- Modify: `src/synth_engine/modules/synthesizer/job_orchestration.py`
- Modify: `src/synth_engine/modules/synthesizer/__init__.py` (export if needed)

---

## T43.2 — Consolidate Optional Import Pattern

**Priority**: P2 — Maintainability. 41 of 89 source files use conditional/deferred
imports for optional synthesizer dependencies. 5 files in `modules/synthesizer/`
repeat the exact same `torch: Any = None` pattern.

### Context & Constraints

1. Files with repeated optional import pattern:
   - `dp_discriminator.py:56-57` — torch, nn
   - `dp_training.py:40-43` — torch, nn, DataLoader, TensorDataset
   - `engine.py:86` — DPCompatibleCTGAN
   - `guardrails.py:71` — torch

2. Create a single `modules/synthesizer/_optional_deps.py` module that:
   - Attempts all optional imports in one place
   - Exports typed references (or None) for each optional dependency
   - Provides a `require_synthesizer()` function that raises a clear
     `ImportError` if the synthesizer group is not installed

3. All synthesizer files import from `_optional_deps` instead of repeating
   the try/except pattern.

4. This reduces `# type: ignore[no-redef]` comments from 5 to 1.

### Acceptance Criteria

1. `_optional_deps.py` centralizes all optional import logic.
2. All synthesizer files import from `_optional_deps`.
3. `# type: ignore[no-redef]` reduced to 1 instance (in `_optional_deps.py`).
4. Clear `ImportError` message when synthesizer group is missing.
5. All existing tests pass.
6. Full gate suite passes.

### Files to Create/Modify

- Create: `src/synth_engine/modules/synthesizer/_optional_deps.py`
- Modify: `src/synth_engine/modules/synthesizer/dp_discriminator.py`
- Modify: `src/synth_engine/modules/synthesizer/dp_training.py`
- Modify: `src/synth_engine/modules/synthesizer/engine.py`
- Modify: `src/synth_engine/modules/synthesizer/guardrails.py`

---

## T43.3 — Add Request Flow Documentation & Architecture Diagram

**Priority**: P2 — Maintainability. No request-to-database flow documentation
exists. A new developer must trace 10 files to understand a single request.

### Context & Constraints

1. Create `docs/REQUEST_FLOW.md` with:
   - Sequence diagram (Mermaid) showing POST /jobs → router → task queue →
     orchestration → training → finalization → audit → response
   - Annotated file list for each step in the flow
   - Async/sync boundary explanation (routes vs Huey workers)
   - Where to look for common modifications (add new masking algorithm,
     add new synthesis model, modify privacy accounting)

2. Add a "Conditional Imports" section to `DEVELOPER_GUIDE.md`:
   - Why the pattern exists (optional synthesizer group)
   - How to check dependency availability at runtime
   - List of files using deferred imports

### Acceptance Criteria

1. `docs/REQUEST_FLOW.md` contains sequence diagram and annotated file list.
2. `DEVELOPER_GUIDE.md` has conditional imports section.
3. Mermaid diagrams render correctly in GitHub.
4. Markdownlint passes.

### Files to Create/Modify

- Create: `docs/REQUEST_FLOW.md`
- Modify: `docs/DEVELOPER_GUIDE.md`

---

## T43.4 — Code Hygiene Polish Batch

**Priority**: P3 — Cosmetic. Batched per Rule 16 (materiality threshold).

### Context & Constraints

1. **Add inline justification comments** to exception handlers at:
   - `job_orchestration.py:232` — `except Exception as exc` (missing comment)
   - `job_orchestration.py:274` — `except Exception as exc` (missing comment)
   - `dp_training.py:421` — `except Exception as exc` (missing comment)

2. **Document Prometheus label strategy** for `EPSILON_SPENT_TOTAL` counter
   in `privacy/accountant.py`. Add a docstring or comment explaining label
   semantics and cardinality expectations.

3. **Freeze or document `ModelArtifact` mutability** in `synthesizer/models.py`.
   Either make it `frozen=True` or add a comment explaining why mutability
   is intentional (e.g., fields are set incrementally during job finalization).

4. **Clean up `docs/archive/spikes/`** — Add "HISTORICAL — DO NOT USE" header
   to each spike findings document, or move to `docs/retired/` if that
   directory is the designated archive location.

5. **ADR-0002 deprecation marker** — Add explicit "Status: Superseded" to the
   ADR header. Currently says "Spike Not Promoted" in body text but header
   still shows "Accepted".

### Acceptance Criteria

1. Exception handlers at 3 locations have inline justification comments.
2. Prometheus counter has documented label strategy.
3. `ModelArtifact` immutability documented or enforced.
4. Archive spike docs marked as historical.
5. ADR-0002 header shows "Superseded".
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/synthesizer/job_orchestration.py`
- Modify: `src/synth_engine/modules/synthesizer/dp_training.py`
- Modify: `src/synth_engine/modules/privacy/accountant.py`
- Modify: `src/synth_engine/modules/synthesizer/models.py`
- Modify: `docs/archive/spikes/findings_spike_a.md` (and b, c)
- Modify: `docs/adr/ADR-0002-chromadb-spike.md`

---

## T43.5 — Evaluate CLAUDE.md Rule Sunset (Phase 40 Rules)

**Priority**: P2 — Governance. Rules 4, 5, 6, 8, 9, 10, 11, 12, 16, 17 all
carry `[sunset: Phase 40]`. Per Rule 15, evaluate whether each rule has
prevented a failure in the last 10 phases. Delete rules that haven't fired;
extend rules that have.

### Context & Constraints

1. Review `docs/RETRO_LOG.md` phases 30-43 for evidence of each rule preventing
   a failure or being invoked.

2. For each rule:
   - If it prevented a failure in phases 30-40: extend sunset to Phase 50.
   - If it has NOT prevented a failure: delete from CLAUDE.md.
   - Document the evaluation in RETRO_LOG.

3. CLAUDE.md line cap is 400 lines. Deleting expired rules creates headroom.

4. This is a governance task, not a code task. PM can execute directly.

### Acceptance Criteria

1. Each `[sunset: Phase 40]` rule evaluated with evidence.
2. Rules that prevented failures extended to Phase 50.
3. Rules that never fired deleted from CLAUDE.md.
4. CLAUDE.md remains under 400 lines.
5. RETRO_LOG documents the evaluation.

### Files to Create/Modify

- Modify: `CLAUDE.md`
- Modify: `docs/RETRO_LOG.md`

---

## Task Execution Order

```
T43.1 (Extract dp_accounting) ────> parallel
T43.2 (Consolidate imports) ──────> parallel
T43.3 (Request flow docs) ────────> parallel
T43.4 (Code hygiene batch) ───────> parallel
T43.5 (Rule sunset evaluation) ──> parallel (governance, PM-only)
```

All five tasks are independent.

---

## Phase 43 Exit Criteria

1. `dp_accounting.py` extracted; `job_orchestration.py` reduced.
2. Optional imports consolidated into single `_optional_deps.py`.
3. Request flow documentation complete with sequence diagram.
4. All P3 hygiene items resolved.
5. CLAUDE.md rules evaluated at sunset; expired rules deleted.
6. All quality gates pass.
7. Zero open advisories in RETRO_LOG.
8. Review agents pass for all tasks.
