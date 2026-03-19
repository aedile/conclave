# Phase 44 — Comprehensive Documentation Audit & Cleanup

**Goal**: Iterate through EVERY markdown document in the repository and verify
it for accuracy, currency, necessity, and cross-reference integrity. Retire
stale documents, fix inaccuracies, update phase references, resolve contradictions
between documents, and establish a document lifecycle index. This is the
definitive documentation quality pass.

**Prerequisite**: Phase 43 merged. Zero open advisories.

**ADR**: None required — documentation maintenance, no architectural decisions.

**Source**: Production Readiness Audit, 2026-03-19 — Documentation Value findings;
user directive to audit every markdown document.

**Special Agent**: This phase uses the `docs-reviewer` agent
(`.claude/agents/docs-reviewer.md`) — a specialized documentation auditor that
verifies factual accuracy, code alignment, cross-reference integrity, phase
currency, necessity, and lifecycle status for each document.

---

## T44.1 — Audit Root-Level Documents

**Priority**: P0 — These are the first documents a new developer or operator sees.
Inaccuracies here erode trust in the entire project.

### Context & Constraints

1. Root-level documents to audit:
   - `README.md` — Project overview, architecture claims, security controls,
     masking evidence, DP maturity claims, quick start
   - `CONSTITUTION.md` — Binding governance framework
   - `CLAUDE.md` — PM/developer directives and workflow rules
   - `CHANGELOG.md` — Phase-by-phase release notes
   - `pyproject.toml` — Dependency versions, tool configurations, project metadata

2. For each document, the `docs-reviewer` agent must:
   - Verify every file path, function name, and class name mentioned
   - Verify phase references match actual git history
   - Verify configuration claims match `ConclaveSettings` fields
   - Check for contradictions with other root-level docs
   - Assess whether content is "Why" (valuable) or "What" (restating code)

3. Known issues to verify:
   - README DP maturity section — does it still say "Benchmarks Pending"?
     (If Phase 42 ran benchmarks, it should be updated)
   - CHANGELOG footer — should reference the most recent phase
   - CLAUDE.md — Rule sunset evaluation should have happened in Phase 43

4. The `docs-reviewer` agent is spawned per document (or in small batches)
   to keep context focused. The PM collects findings and creates fix tasks.

### Acceptance Criteria

1. Every root-level markdown document reviewed by `docs-reviewer` agent.
2. All factual inaccuracies fixed.
3. All stale phase references updated to current.
4. All contradictions between documents resolved.
5. Markdownlint passes on all modified files.

### Files to Audit

- `README.md`
- `CONSTITUTION.md`
- `CLAUDE.md`
- `CHANGELOG.md`

---

## T44.2 — Audit Architecture Decision Records (ADRs)

**Priority**: P1 — ADRs are the authoritative source of architectural decisions.
Stale or contradictory ADRs mislead future developers.

### Context & Constraints

1. 38+ ADR files in `docs/adr/`. Each must be checked for:
   - **Status accuracy**: Is the ADR marked Active, Superseded, or Amended?
     Does the status match reality? (e.g., ADR-0002 body says "Spike Not
     Promoted" but header may still say "Accepted")
   - **Code alignment**: Does the ADR describe behavior that still exists?
     If the subject code was removed or refactored, is the ADR amended?
   - **Amendment currency**: Are all amendments listed with phase references?
   - **Cross-references**: Do links to other ADRs resolve?

2. This is the most volume-intensive audit task. Batch ADRs into groups of 5-8
   per `docs-reviewer` agent invocation.

3. Expected findings:
   - ADRs for early phases (0-10) may describe patterns that evolved
   - ADRs referencing `errors.py` should reference `errors/` package (Phase 36)
   - ADRs referencing `config_validation.py` should reference `ConclaveSettings` (Phase 36)

### Acceptance Criteria

1. Every ADR file reviewed.
2. All ADR statuses accurate (Active/Superseded/Amended).
3. ADRs for removed/refactored code marked as Superseded or Amended.
4. Cross-references between ADRs resolve correctly.
5. ADR index (if one exists) is current.

### Files to Audit

- `docs/adr/ADR-0001-*.md` through `docs/adr/ADR-0039-*.md` (and any added in phases 39-43)

---

## T44.3 — Audit Operational & Developer Documentation

**Priority**: P1 — These documents guide deployment and development. Inaccuracies
cause operational failures and developer frustration.

### Context & Constraints

1. Documents to audit:
   - `docs/DEVELOPER_GUIDE.md` — Onboarding, setup, TDD workflow, quality gates
   - `docs/OPERATOR_MANUAL.md` — Deployment, configuration, monitoring
   - `docs/PRODUCTION_DEPLOYMENT.md` — Production setup, Docker, secrets
   - `docs/ARCHITECTURAL_REQUIREMENTS.md` — Full architecture specification
   - `docs/DISASTER_RECOVERY.md` — Recovery procedures
   - `docs/SECURITY_HARDENING.md` — Security configuration (if created in Phase 42)
   - `docs/DATA_COMPLIANCE.md` — Compliance policies (if created in Phase 41)
   - `docs/REQUEST_FLOW.md` — Request flow documentation (if created in Phase 43)
   - `docs/E2E_VALIDATION.md` — End-to-end validation results
   - `docs/DP_QUALITY_REPORT.md` — DP quality benchmarks
   - `docs/BUSINESS_REQUIREMENTS.md` — Executive summary
   - `docs/infrastructure_security.md` — Infrastructure security
   - `docs/LICENSING.md` — License information

2. For each document, verify:
   - All commands shown actually work (e.g., `poetry run ...` commands)
   - All file paths referenced exist
   - All configuration options mentioned exist in `ConclaveSettings`
   - Docker Compose references match actual `docker-compose.yml`
   - Environment variable names match what the code reads

3. Special attention to documents that may have been created in Phases 41-43
   and need cross-referencing with pre-existing docs.

### Acceptance Criteria

1. Every operational/developer document reviewed.
2. All commands verified as working or flagged for fix.
3. All configuration references verified against code.
4. Cross-references between operational docs resolve.
5. Stale documents flagged for update or retirement.

### Files to Audit

- All files listed in Context & Constraints item 1

---

## T44.4 — Audit Backlog, Retrospective & Archive Documents

**Priority**: P2 — Historical documents that should be clearly marked as such.

### Context & Constraints

1. Documents to audit:
   - `docs/backlog/phase-*.md` (39+ files) — Are completed phases marked as complete?
   - `docs/RETRO_LOG.md` — Is it current through the latest phase?
   - `docs/retro_archive/phases-0-to-7.md` — Historical, should be marked
   - `docs/retro_archive/phases-8-to-14.md` — Historical, should be marked
   - `docs/archive/spikes/findings_spike_*.md` — Should be marked HISTORICAL
   - `docs/archive/AUTONOMOUS_DEVELOPMENT_PROMPT.md` — Historical
   - `docs/archive/EXECUTION_PLAN.md` — Historical
   - `docs/prompts/review/*.md` — Reviewer persona prompts (8 files)
   - `docs/index.md` — Document index

2. For backlog files: verify completed phases have all ACs checked off or
   reference the merge commit. Do NOT flag historical content as "stale" —
   backlogs are snapshots of intent at the time.

3. For `docs/index.md`: verify all links resolve. Remove links to retired docs.
   Add links to new docs created in Phases 39-43.

4. For reviewer prompts in `docs/prompts/review/`: verify they reference
   current project structure (e.g., do they still mention `errors.py` instead
   of `errors/` package?).

### Acceptance Criteria

1. All historical documents marked with "HISTORICAL — DO NOT USE" header
   or moved to `docs/archive/`.
2. `docs/index.md` has no broken links and references all current documents.
3. Reviewer prompts reference current project structure.
4. RETRO_LOG is current through the latest phase.
5. Markdownlint passes on all modified files.

### Files to Audit

- `docs/backlog/phase-*.md`
- `docs/RETRO_LOG.md`
- `docs/retro_archive/*.md`
- `docs/archive/**/*.md`
- `docs/prompts/review/*.md`
- `docs/index.md`

---

## T44.5 — Create Document Lifecycle Index

**Priority**: P1 — The audit identified no mechanism for discovering which
documents are active vs historical vs superseded.

### Context & Constraints

1. Create `docs/DOCUMENT_INDEX.md` — a comprehensive index of every markdown
   document in the repository with:
   - File path
   - Lifecycle status (Active | Needs Update | Superseded | Historical | Retired)
   - Audience (Developer | Operator | Architect | PM | All)
   - Last verified date (from this audit)
   - Brief description (1 line)

2. This replaces `docs/index.md` (or supplements it if `index.md` serves
   a different purpose like navigation).

3. The index must be organized by category:
   - Root documents (README, CONSTITUTION, CLAUDE.md, CHANGELOG)
   - Architecture (ADRs)
   - Operational (deployment, security, compliance)
   - Developer (guides, request flow)
   - Historical (backlog, retro archives, spikes)

4. Add a note to `CLAUDE.md` or `DEVELOPER_GUIDE.md`: "When creating or
   retiring a document, update `docs/DOCUMENT_INDEX.md`."

### Acceptance Criteria

1. `docs/DOCUMENT_INDEX.md` lists every markdown file in the repo.
2. Each entry has lifecycle status, audience, and description.
3. Index is organized by category.
4. Process note added to developer guide.
5. Markdownlint passes.

### Files to Create/Modify

- Create: `docs/DOCUMENT_INDEX.md`
- Modify: `docs/DEVELOPER_GUIDE.md` (add index maintenance note)
- Retire or update: `docs/index.md` (if superseded by DOCUMENT_INDEX.md)

---

## Task Execution Order

```
T44.1 (Root docs audit) ─────────> first (establishes baseline accuracy)
T44.2 (ADR audit) ───────────────> parallel with T44.3 (independent doc sets)
T44.3 (Operational docs audit) ──> parallel with T44.2
T44.4 (Archive docs audit) ──────> after T44.1-T44.3 (may reference their findings)
T44.5 (Lifecycle index) ─────────> LAST (incorporates all findings)
```

T44.1 first (root docs set the baseline). T44.2 and T44.3 in parallel.
T44.4 after the main audits. T44.5 last (aggregates all lifecycle statuses).

---

## Phase 44 Exit Criteria

1. Every markdown document in the repository has been reviewed by `docs-reviewer`.
2. All factual inaccuracies fixed.
3. All stale phase references updated.
4. All contradictions between documents resolved.
5. Historical documents clearly marked.
6. `docs/DOCUMENT_INDEX.md` provides a complete lifecycle-aware index.
7. `docs/index.md` links all resolve (or index is retired in favor of DOCUMENT_INDEX).
8. Markdownlint passes on all files.
9. All quality gates pass.
10. Zero open advisories in RETRO_LOG.
11. Review agents pass for all tasks.
