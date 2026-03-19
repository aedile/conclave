# Phase 32 — Dead Module Cleanup & Development Process Documentation

**Goal**: Remove unwired scaffolding code that inflates the codebase without serving any
runtime purpose, roadmap the features they represent, and document the AI-orchestrated
development process that produced this project — both as a README section and as a
standalone case study suitable for external sharing.

**Prerequisite**: Phase 31 merged. Zero open advisories.

**ADR**: None required — no architectural decisions, only cleanup and documentation.

---

## T32.1 — Remove Unwired Scaffolding Modules

**Priority**: P1 — Dead code removal per Constitution Priority 5 (code quality).

### Context & Constraints

1. The panel review identified four modules at 0% test coverage that are **defined but never
   wired** into the application:

   | Module | File | Wired? | Why it exists |
   |--------|------|--------|---------------|
   | JWT auth | `shared/auth/jwt.py` | No — `create_access_token()` never called | Future OAuth2 token issuance |
   | OAuth2 scopes | `shared/auth/scopes.py` | No — `Scope` enum never referenced in routes | Future route-level auth |
   | Idempotency middleware | `shared/middleware/idempotency.py` | No — never added to ASGI stack | Future Redis-backed dedup |
   | Orphan task reaper | `shared/tasks/reaper.py` | No — never registered as Huey periodic task | Future stale-job cleanup |

2. Verification: `grep` for each module's classes/functions across `bootstrapper/` (the wiring
   layer) returns zero results. No route uses `Depends(get_current_user(...))`. No middleware
   stack includes `IdempotencyMiddleware`. No Huey schedule registers `OrphanTaskReaper.reap()`.

3. These modules are whitelisted in `.vulture_whitelist.py` — the whitelist entries must also
   be removed.

4. `shared/auth/__init__.py` and `shared/tasks/__init__.py` may need updating or removal
   if they become empty.

5. `bootstrapper/dependencies/auth.py` imports from `shared/auth/` — this dependency file
   must also be removed or gutted if the auth modules are deleted.

6. Each removed feature must be added to `docs/backlog/deferred-items.md` as a roadmap item
   (TBD-06, TBD-07, TBD-08) with clear trigger conditions for when to implement.

### Acceptance Criteria

1. The following files are deleted:
   - `src/synth_engine/shared/auth/jwt.py`
   - `src/synth_engine/shared/auth/scopes.py`
   - `src/synth_engine/shared/middleware/idempotency.py`
   - `src/synth_engine/shared/tasks/reaper.py`
   - Any `__init__.py` or dependency files that become empty after removal
2. All vulture whitelist entries referencing removed code are deleted.
3. All imports referencing removed modules are cleaned up (no `ImportError` at startup).
4. `docs/backlog/deferred-items.md` updated with TBD-06 (JWT Auth), TBD-07 (Idempotency),
   TBD-08 (Orphan Reaper) — each with trigger condition and acceptance criteria.
5. `vulture src/ .vulture_whitelist.py --min-confidence 60` passes with zero output.
6. `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error` passes.
7. `poetry run pytest tests/integration/ -v` passes.
8. `poetry run mypy src/` passes.
9. import-linter contracts pass.
10. `pre-commit run --all-files` passes.

### Testing & Quality Gates

- Full gate suite (ruff, mypy, bandit, vulture, pytest unit+integration, pre-commit)
- QA + Architecture reviewers spawned.

### Files to Create/Modify

- Delete: `shared/auth/jwt.py`, `shared/auth/scopes.py`, `shared/middleware/idempotency.py`,
  `shared/tasks/reaper.py`, possibly `shared/auth/__init__.py`, `shared/tasks/__init__.py`,
  `bootstrapper/dependencies/auth.py`
- Modify: `.vulture_whitelist.py`, `docs/backlog/deferred-items.md`

---

## T32.2 — README Development Process Section

**Priority**: P1 — Documentation (Constitution Priority 6).

### Context & Constraints

1. The README currently describes what the software does but not how it was built. The panel
   review noted: "The README undersells the automated development system."

2. Add a concise section (10-20 lines) explaining that this codebase was produced by AI agents
   under a Constitutional governance framework, with a link to the full Development Story.

3. Tone: factual, not promotional. State what happened, not what it proves. Let the reader
   draw their own conclusions.

4. Include key metrics from the git history (commit count, timeline, coverage, ADR count)
   as evidence, not claims.

### Acceptance Criteria

1. `README.md` contains a new section (suggested title: "How This Was Built" or similar).
2. Section includes: timeline (9 days), methodology (Constitutional AI orchestration),
   key metrics (515 commits, 98% coverage, 36 ADRs), and a link to `docs/DEVELOPMENT_STORY.md`.
3. No exaggeration — every number is verifiable from the git history.
4. `pre-commit run --all-files` passes.

### Files to Create/Modify

- `README.md`

---

## T32.3 — Development Story Case Study

**Priority**: P0 — The primary deliverable of this phase.

### Context & Constraints

1. This is a case study / blog-post-style document telling the story of how a human
   specification became a deployable system in 9 days through AI-orchestrated development.

2. **Audience**: External — developers, engineering leaders, and anyone interested in
   AI-augmented software development. Must be readable by someone with no prior context
   about this project.

3. **Tone**: Honest case study. No BS, no stretching the truth. State what worked, what
   didn't, what was surprising. Let the evidence speak.

4. **Evidence sources** (the developer agent MUST use these, not fabricate data):
   - `git log` — commit count, date range, commit type distribution, TDD pairs
   - `git shortlog` — author breakdown
   - `gh pr list --state all` — PR count and timeline
   - `docs/RETRO_LOG.md` — failure patterns, what was learned
   - `docs/adr/` — decision count and topics
   - `CONSTITUTION.md` — governance framework
   - `CLAUDE.md` — PM/developer separation, agent ecosystem
   - `.claude/agents/` — agent definitions
   - Test results — coverage numbers, test counts
   - `docs/backlog/deferred-items.md` — what was deliberately NOT built

5. **Suggested structure** (developer agent should use judgment):
   - The Problem: Why build a synthetic data engine?
   - The Approach: Constitutional AI orchestration — what is it?
   - The Timeline: Day-by-day progression with git evidence
   - The Governance Framework: Constitution, CLAUDE.md, agent ecosystem
   - The TDD Discipline: How AI agents follow Red/Green/Refactor
   - The Review System: QA, DevOps, Architecture, UI/UX agents
   - The Learning Loop: RETRO_LOG and institutional memory
   - What Went Wrong: Honest failures (proxy model compromise, guard parity, docstring drift)
   - What Went Right: Coverage, security, documentation corpus
   - The Numbers: Final metrics table
   - Conclusion: What this experiment demonstrates (and what it doesn't)

6. Every claim must be backed by a specific git commit, PR number, file path, or metric
   that a reader could verify. No hand-waving.

7. The document should acknowledge limitations honestly: the frontend is minimal, some
   modules were scaffolding that had to be removed (T32.1), the AI agents sometimes stalled
   on large contexts, etc.

### Acceptance Criteria

1. `docs/DEVELOPMENT_STORY.md` created (target: 800-1500 lines).
2. Every factual claim is backed by verifiable evidence (commit hash, PR number, file path,
   or command output).
3. Document includes at least 3 "what went wrong" examples from RETRO_LOG.
4. Document includes the commit type distribution table with actual numbers.
5. Document includes the timeline (day-by-day or phase-by-phase) with actual dates.
6. Document acknowledges limitations honestly.
7. Tone is case study, not marketing copy. No superlatives without evidence.
8. `pre-commit run --all-files` passes.

### Files to Create/Modify

- `docs/DEVELOPMENT_STORY.md` (new)

---

## Task Execution Order

```
T32.1 (Dead module cleanup)  ─────────────────────> parallel
T32.2 (README section)       ─────────────────────> parallel
T32.3 (Development Story)    ─────────────────────> parallel
```

All three tasks are independent. T32.2 links to the output of T32.3, but the link target
path (`docs/DEVELOPMENT_STORY.md`) is known in advance.

---

## Phase 32 Exit Criteria

1. Zero unwired scaffolding modules in `src/synth_engine/`.
2. Removed features roadmapped in `deferred-items.md` (TBD-06/07/08).
3. README includes development process section with link to story.
4. `docs/DEVELOPMENT_STORY.md` exists as an honest, evidence-backed case study.
5. All quality gates pass.
6. Zero open advisories in RETRO_LOG.
7. Review agents pass for all tasks.
