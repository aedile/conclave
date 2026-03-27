# Phase 64 — Maintainability Polish

**Goal**: Reduce cognitive load for future maintainers by eliminating
re-export shims, decomposing oversized files, documenting canonical import
paths, and improving RETRO_LOG navigability.

**Prerequisite**: Phase 63 merged.

**Source**: Staff-level production readiness audit (2026-03-27), scored
maintainability 6/10.  Findings: C11 (dual import paths), RETRO_LOG
navigation, rate_limit.py multi-responsibility, import chain depth.

---

## Issues Addressed

| ID | Issue | Source | Impact |
|----|-------|--------|--------|
| C11 | Re-export shims create dual import paths | Audit 2026-03-27 | Developer confusion; bug fixes applied to wrong file |
| — | `rate_limit.py` (475 LOC) mixes rate limiting + Redis fallback + JWT extraction | Audit 2026-03-27 | Multiple responsibilities in single file |
| — | RETRO_LOG has no table-of-contents or domain index | Audit 2026-03-27 | Finding open advisories requires keyword search |
| — | No canonical import path documentation | Audit 2026-03-27 | 10-file import chains; dual paths confuse newcomers |

---

## T64.1 — Eliminate Re-Export Shims

**Priority**: P3 — Maintainability.

### Context & Constraints

1. `modules/synthesizer/storage/models.py` (53 LOC): Exists solely to
   re-export from `artifact.py` and `restricted_unpickler.py`.  Created
   in T58.4 for backward compatibility.
2. `modules/synthesizer/jobs/tasks.py`: Re-exports step classes from
   `job_orchestration.py` so "both old import paths work."
3. Dual import paths mean `grep "class ModelArtifact"` lands on the shim,
   not the real definition.  A developer may fix a bug in the wrong file.
4. Fix: Update all internal callers to use canonical import paths.  Keep
   shim files but add deprecation warnings (`warnings.warn()` at module
   scope).  Set removal deadline for Phase 70.
5. Scan all `from ... import` statements to ensure no internal code uses
   the deprecated paths.

### Acceptance Criteria

1. All internal code uses canonical import paths (not shims).
2. Shim files emit `DeprecationWarning` on import.
3. Deprecation deadline documented in shim module docstrings.
4. `grep -r "from.*storage.models import" src/` returns zero internal hits.
5. Full gate suite passes.

---

## T64.2 — RETRO_LOG Table of Contents

**Priority**: P4 — Documentation navigability.

### Context & Constraints

1. `docs/RETRO_LOG.md` is chronological (most recent first).  Finding
   "open advisories for privacy domain" requires keyword search.
2. Fix: Add a table-of-contents section at the top with:
   - Open advisories by severity (BLOCKER / FINDING / ADVISORY)
   - Open advisories by domain (privacy, synthesis, security, infra)
   - Link to each phase section
3. Keep the chronological body unchanged.

### Acceptance Criteria

1. RETRO_LOG has a TOC section at the top.
2. Open advisories listed by severity and domain.
3. Each phase section reachable by anchor link.
4. Full gate suite passes.

---

## T64.3 — Decompose `rate_limit.py`

**Priority**: P3 — Single responsibility.

### Context & Constraints

1. `bootstrapper/dependencies/rate_limit.py` (475 LOC) contains:
   - Rate limiting middleware dispatch logic
   - Redis-backed counter implementation
   - In-memory fallback counter
   - JWT token extraction for rate-limit identity
   - Tier configuration and endpoint matching
2. Fix: Split into:
   - `rate_limit_middleware.py` — ASGI middleware dispatch
   - `rate_limit_backend.py` — Redis + in-memory counter implementations
   - `rate_limit.py` — configuration, tier definitions, public API
3. Re-export from `rate_limit.py` for backward compatibility (but internal
   code updated to use new paths).

### Acceptance Criteria

1. No file exceeds 200 LOC after split.
2. Single responsibility per file.
3. All existing rate limit tests pass unchanged.
4. Full gate suite passes.

---

## T64.4 — Document Canonical Import Paths

**Priority**: P4 — Developer onboarding.

### Context & Constraints

1. A developer debugging a synthesis job must trace through 10+ files.
   There is no single reference listing "where does each concept live."
2. Fix: Add an "Import Map" section to `docs/DEVELOPER_GUIDE.md` that
   lists every public symbol with its canonical import path, organized
   by domain.
3. Example format:
   ```
   | Symbol | Canonical Import | Domain |
   |--------|-----------------|--------|
   | ModelArtifact | synth_engine.modules.synthesizer.storage.artifact | Synthesizer |
   | DPTrainingWrapper | synth_engine.modules.privacy.dp_engine | Privacy |
   ```

### Acceptance Criteria

1. Import map added to DEVELOPER_GUIDE.md.
2. Every public class, protocol, and factory function listed.
3. No deprecated shim paths listed as canonical.
4. Full gate suite passes.

---

## Task Execution Order

```
T64.2 (RETRO_LOG TOC) ──────────────> trivial, do first
T64.1 (eliminate shims) ────────────> moderate scope
T64.3 (decompose rate_limit.py) ───> moderate scope, parallel with T64.1
T64.4 (import map) ────────────────> after T64.1 (needs canonical paths finalized)
```

---

## Phase 64 Exit Criteria

1. All internal code uses canonical import paths.
2. Re-export shims emit deprecation warnings.
3. `rate_limit.py` decomposed into ≤200 LOC files.
4. RETRO_LOG has navigable TOC with severity/domain index.
5. Import map in DEVELOPER_GUIDE.md covers all public symbols.
6. All quality gates pass.
7. Review agents pass for all tasks.
