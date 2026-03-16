# Phase 17 — Docker Pinning, Dashboard WCAG & Process Cleanup

**Goal**: Close the long-standing ADV-014 Docker base image pinning debt (supply chain
security), fix Dashboard WCAG inconsistencies with Unseal.tsx, document the undocumented
OTEL env var, correct stale process document references, and clean up remaining remote
branches. No new features.

**Prerequisite**: Phase 16 must be complete (all tasks merged, retrospective signed off).

---

## T17.1 — Docker Base Image SHA-256 Pinning (ADV-014)

**Priority**: P0 — Supply chain security (Constitution Priority 0). Open since Phase 1.

### Context & Constraints

1. `Dockerfile` uses tag-only base image references at three stages:
   - Line 8: `FROM node:20-alpine` (frontend build)
   - Line 24: `FROM python:3.14-slim` (Python build)
   - Line 63: `FROM python:3.14-slim` (runtime)
   All three carry `TODO(ADV-014)` comments acknowledging the debt.

2. `docker-compose.yml` uses tag-only references for infrastructure services:
   - Line 119: `redis:7-alpine`
   - Line 149: `postgres:16-alpine`
   Production and development images should be pinned to SHA-256 digests for
   reproducible builds and supply chain auditability.

3. Prometheus (`v2.53.0`), Grafana (`11.3.0`), and MinIO (`RELEASE.2024-01-28...`)
   have version tags but not SHA-256 digests. These are lower priority than the
   Dockerfile stages but should be included for consistency.

4. The air-gap bundler (`make build-airgap-bundle`) relies on deterministic image
   references. Unpinned tags mean the bundle content varies with Docker Hub state.

### Acceptance Criteria

1. `Dockerfile` lines 8, 24, 63 updated to use `@sha256:...` digest references
   alongside version tag comments (e.g., `FROM python:3.14-slim@sha256:abc123 # 3.14-slim`).
2. `docker-compose.yml` service images for `redis`, `postgres` updated to SHA-256
   digest references.
3. `TODO(ADV-014)` comments removed from `Dockerfile` (debt resolved).
4. `docker-compose.yml` Prometheus, Grafana, MinIO images updated to include SHA-256
   digests for consistency (version tags preserved in comments).
5. `docker build .` still succeeds.
6. `docker-compose config` still validates.

### Testing & Quality Gates

- `docker build . --no-cache` — must succeed with pinned digests.
- `docker-compose config` — must validate without errors.
- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error` — no regression.
- All review agents spawned.

---

## T17.2 — Dashboard WCAG Form Accessibility Parity

**Priority**: P1 — Accessibility (Constitution Priority 9). Dashboard form inputs lack
`aria-required` and `aria-invalid` attributes that Unseal.tsx correctly implements.

### Context & Constraints

1. `frontend/src/routes/Unseal.tsx` correctly implements:
   - Line 253: `aria-invalid={error !== null}`
   - Line 254: `aria-required="true"`
   - Line 235: visible asterisk with `aria-hidden="true"`
   These are the WCAG 1.3.1 (Info and Relationships) and 3.3.1 (Error Identification)
   patterns the project has already established.

2. `frontend/src/routes/Dashboard.tsx` has 4 form inputs (`table_name`, `parquet_path`,
   `total_epochs`, `checkpoint_every_n`) at lines 390-471 that:
   - Use `required` HTML attribute but NOT `aria-required="true"`
   - Have client-side validation (lines 243-255) with `setFormValidationError()` but
     do NOT set `aria-invalid="true"` on the failing input
   - Use visible asterisks but without the `aria-hidden="true"` pattern from Unseal

3. The Dashboard also has `OTEL_EXPORTER_OTLP_ENDPOINT` undocumented in `.env.example`.
   This is the OpenTelemetry exporter endpoint read by `src/synth_engine/shared/telemetry.py:66`.
   Operators deploying with observability have no template guidance.

### Acceptance Criteria

1. All 4 Dashboard form inputs have `aria-required="true"` matching Unseal.tsx pattern.
2. `total_epochs` and `checkpoint_every_n` inputs set `aria-invalid="true"` when
   client-side validation fails, matching Unseal.tsx pattern.
3. Visible asterisks on Dashboard form labels wrapped with `aria-hidden="true"`.
4. `.env.example` updated to document `OTEL_EXPORTER_OTLP_ENDPOINT` with explanatory
   comments about optional observability configuration.
5. Existing tests updated or new tests added to verify aria attribute presence.

### Testing & Quality Gates

- `cd frontend && npm run lint` — must pass.
- `cd frontend && npm run test:coverage` — must pass (90%+ all thresholds).
- `cd frontend && npm run type-check` — must still pass.
- `poetry run bandit -c pyproject.toml -r src/` — must pass.
- All review agents spawned.

---

## T17.3 — CLAUDE.md Stale References, Backlog Spec Fix & Branch Cleanup

**Priority**: P2 — Documentation currency (Constitution Priority 6) + process accuracy
(Priority 8) + workspace hygiene.

### Context & Constraints

1. `CLAUDE.md` contains 4 references to `AUTONOMOUS_DEVELOPMENT_PROMPT.md` (lines 43,
   205, 267, 417) which was retired to `docs/retired/` in Phase 3.5 (task 3.5.0, commit
   in git history). These references are misleading — any agent reading CLAUDE.md may
   attempt to reference a retired document.

2. `docs/backlog/phase-16.md` says "Alembic Migration 002" in the task title (line 11),
   acceptance criteria (lines 38, 42-44), and exit criteria (line 146). The actual
   migration created was 003 (migration 002 already existed). The Phase 16 retrospective
   documents this as a known discrepancy but the spec was never corrected.

3. 5 stale remote branches remain on origin:
   - `origin/feat/P15-T15.2-readme-cleanup`
   - `origin/feat/P16-T16.1-alembic-epsilon-precision`
   - `origin/feat/P16-T16.2-supply-chain-nosec-envexample`
   - `origin/feat/P16-T16.3-skip-nav-readme-cleanup`
   - `origin/fix/P16-T16.3-uiux-review-fixes`
   GitHub auto-delete was enabled during T16.3 but these branches were created before
   the setting took effect.

4. 4 ADRs (0015, 0016, 0023, 0024) use `**Status**: Accepted` instead of `**Status:**
   Accepted` — minor format inconsistency with the other 26 ADRs.

### Acceptance Criteria

1. `CLAUDE.md` references to `AUTONOMOUS_DEVELOPMENT_PROMPT.md` replaced with current
   equivalents:
   - Line 43: reference the Approval Gate section of CLAUDE.md itself
   - Line 205: reference `.claude/agents/` directly
   - Line 267: reference CLAUDE.md PR workflow section
   - Line 417: update to note the document is fully retired
2. `docs/backlog/phase-16.md` corrected: "Migration 002" → "Migration 003" in title,
   ACs, and exit criteria.
3. All 5 stale remote branches deleted.
4. ADR format inconsistency fixed in ADR-0015, 0016, 0023, 0024 (`**Status**:` →
   `**Status:**`).
5. README.md updated: Phase 16 → Complete, Phase 17 row added as In Progress.
6. `docs/BACKLOG.md` updated to index Phase 17.

### Testing & Quality Gates

- No code changes expected — docs-gate applies.
- All review agents spawned.

---

## T17.4 — Process Governance Slimming

**Priority**: P1 — Process sustainability (no Constitutional priority — this is meta-governance).

### Context & Constraints

1. The governance system has mechanisms to ADD process (retrospective findings become
   rules, advisory items become tasks, review findings become fix commits) but ZERO
   mechanisms to REMOVE process. Every failure adds a rule. No success removes one.
   CLAUDE.md has grown from ~200 lines to 603 lines across 17 phases.

2. The UI/UX reviewer is spawned on every task but SKIPs ~60% of the time (18 of ~83
   review commits are SKIPs). Each SKIP costs 13K+ tokens and wall-clock time for a
   predictable outcome on backend/infra/docs tasks.

3. The Architecture reviewer similarly gets spawned on tasks with no `src/synth_engine/`
   changes despite CLAUDE.md already defining a trigger rule for when it should fire.

4. RETRO_LOG is 2,633 lines. Rule 10 requires the developer agent to scan it for known
   failure patterns before every task. Ancient Phase 2 review details are irrelevant to
   Phase 17+ work but consume tokens on every brief.

5. CLAUDE.md Rules 1 and 5 overlap (Rule 5 supersedes Rule 1). Rule 14 (ChromaDB seeding)
   adds overhead to every task for a learning system whose consumption has not been validated.

6. Review commits use 4 separate commits per task (qa, devops, arch, ui-ux). On a docs-only
   task, that's 3 commits for findings that could be one line each.

7. Phases 10-15 were each 1-3 commits fixing problems the previous phase introduced.
   Full phase ceremony (backlog file, README update, BACKLOG.md index, retrospective)
   on a 2-commit fix is disproportionate overhead.

### Acceptance Criteria

1. **Conditional reviewer spawning**: PM only spawns UI/UX reviewer when diff touches
   `frontend/`, `*.tsx`, `*.css`, or template files. PM only spawns Architecture reviewer
   when diff touches `src/synth_engine/` or adds new `.py` files under `src/`. CLAUDE.md
   updated to encode these as explicit file-path trigger rules.

2. **RETRO_LOG rolling window**: Detailed per-task review sections older than 3 phases
   archived to `docs/retro_archive/phase-N.md`. Open Advisory Items table stays in
   live file permanently. Target: RETRO_LOG under 800 lines.

3. **CLAUDE.md rule consolidation**: Rules 1 and 5 merged. Rule 14 (ChromaDB seeding)
   evaluated and either justified with evidence of consumption or deleted. Target:
   CLAUDE.md under 500 lines.

4. **Rule sunset clause added**: New rule added to CLAUDE.md: every retrospective-sourced
   rule carries `[sunset: Phase N+5]`. At the tagged phase, PM evaluates whether the rule
   prevented a recurrence. If not, rule is deleted. CLAUDE.md line cap: 400 lines.
   If an amendment would exceed the cap, existing rules must be consolidated or retired.

5. **Consolidated review commits**: Replace 4 separate `review(qa/devops/arch/ui-ux):`
   commits with one `review: <task> — QA PASS, DevOps PASS, ...` commit. Detailed
   findings in RETRO_LOG only.

6. **Materiality threshold for roast findings**: Cosmetic-only roast findings (formatting,
   comment wording, doc phrasing) get batched into a single "polish" task within the next
   feature phase — not a standalone phase. Standalone phases reserved for findings that
   affect correctness, security, or functionality.

7. **Small-fix batching**: If a "phase" would have fewer than 5 meaningful commits, it
   doesn't warrant standalone phase ceremony. Instead, it becomes a task within the
   current or next phase.

### Testing & Quality Gates

- CLAUDE.md must be under 500 lines after consolidation.
- RETRO_LOG must be under 800 lines after archival.
- `pre-commit run --all-files` must pass.
- All review agents spawned (using new conditional rules — this task is the test case).

---

## Phase 17 Exit Criteria

- Docker base images pinned to SHA-256 digests (Dockerfile + docker-compose.yml).
- ADV-014 TODO comments removed.
- Dashboard form inputs have aria-required and aria-invalid attributes.
- OTEL_EXPORTER_OTLP_ENDPOINT documented in .env.example.
- CLAUDE.md stale references removed.
- Phase 16 backlog spec corrected (migration 002 → 003).
- All stale remote branches cleaned.
- ADR format consistency restored.
- README current with Phase 16 completion and Phase 17 status.
- CLAUDE.md under 500 lines with rule sunset clause.
- RETRO_LOG under 800 lines with rolling window archival.
- Review spawning is conditional on file-path triggers.
- Review commits consolidated to one per task.
- Materiality threshold and small-fix batching rules in place.
- All quality gates passing.
- Phase 17 end-of-phase retrospective completed.
