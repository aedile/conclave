# Phase 11 — Documentation Currency & Workspace Hygiene

**Goal**: Close the documentation-to-reality gap identified in the Phase 10 end-of-phase
roast. Update stale project indices, clean workspace artifacts, and document architectural
requirement deviations. No new features.

**Prerequisite**: Phase 10 must be complete (all tasks merged, retrospective signed off).

---

## T11.1 — Documentation Currency (README, BACKLOG.md)

**Priority**: P1 — Constitution Priority 6 (Documentation).

### Context & Constraints

1. `README.md` line 93 and line 109 still say Phase 10 "In Progress" — Phase 10 is
   complete (both PRs merged, retrospective committed).

2. `docs/BACKLOG.md` only indexes Phases 0.5–6. Phases 7, 8, 9, 10, and 11 exist as
   individual files in `docs/backlog/` but are not linked from the master index. A new
   developer would not discover them from the top-level entry point.

3. The README phase table should mark Phase 10 as complete and reflect current state.

### Acceptance Criteria

1. README.md current development status updated to reflect Phase 10 complete / Phase 11
   current.
2. README.md phase table: Phase 10 row marked "Complete".
3. `docs/BACKLOG.md` updated to index Phases 7, 8, 9, 10, and 11.
4. No code changes — documentation only.

### Testing & Quality Gates

- No code changes expected — docs-gate applies.
- All review agents spawned.

---

## T11.2 — Workspace Hygiene (Worktrees, Spikes, .gitignore)

**Priority**: P1 — CLAUDE.md "Clean workspace: No clutter, no orphan files" mandate.

### Context & Constraints

1. `.claude/worktrees/` contains 19 stale agent worktrees consuming ~13 GB of disk.
   These are untracked (gitignored) but violate the "clean workspace" principle and
   waste significant disk space.

2. `spikes/` directory contains 6 files (3 Python spike scripts + 3 findings documents)
   from Phase 0.8 technical spikes. These spikes were consumed during Phases 3–4; the
   code has been promoted or rejected. The spike files serve no further purpose and
   should be archived to `docs/retired/spikes/` to preserve history while cleaning the
   workspace.

3. `.coverage` file is untracked in the working directory — should be added to
   `.gitignore`.

### Acceptance Criteria

1. All stale worktrees under `.claude/worktrees/` removed (using `git worktree remove`
   or manual cleanup for orphaned directories).
2. `spikes/` files moved to `docs/retired/spikes/` (git mv to preserve history).
3. `.coverage` added to `.gitignore`.
4. Working directory clean: `git status` shows no untracked clutter.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
  (verify no test depends on spike files)
- All review agents spawned.

---

## T11.3 — Architectural Requirements Gap ADR

**Priority**: P2 — Constitution Priority 6 (Documentation) + Architecture compliance.

### Context & Constraints

The Phase 10 roast identified a significant delta between `docs/ARCHITECTURAL_REQUIREMENTS.md`
and the implemented system. The following requirements from the architecture spec are
NOT implemented:

1. **Internal Event Bus / Pub-Sub** — spec says "Internal In-Memory Event Bus
   (Publisher/Subscriber)" for cross-module communication. Actual: IoC callbacks.
2. **Webhook callbacks** — spec says webhook push for long-running task completion.
   Actual: SSE only.
3. **`llms.txt`** — spec says serve an `llms.txt` for agentic AI integration.
   Not implemented.
4. **Model Context Protocol (MCP)** — spec says native MCP support. Not implemented.
5. **`datamodel-code-generator` in CI** — spec says Pydantic models must be generated
   from OpenAPI spec in CI. Actual: hand-written Pydantic models.
6. **Rate limiting & circuit breakers** — spec says required for agentic DDoS protection.
   Not implemented.
7. **mTLS inter-container** — spec says all inter-container communication over mTLS.
   Actual: plain TCP.
8. **Custom Prometheus business metrics** — spec says "Milliseconds per Synthesized Row"
   and "Epsilon Spent per Request". Not implemented.
9. **OTEL trace context into Huey workers** — spec says explicit trace ID injection
   into async task arguments. Not implemented.

These are not bugs — they are unimplemented requirements that need a documented decision.
CLAUDE.md Rule 6 requires technology/design deviations to have ADR documentation.

### Acceptance Criteria

1. `docs/adr/ADR-0029-architectural-requirements-gap-analysis.md` created, documenting
   each gap with one of:
   - **Implemented differently** — what was done instead and why (e.g., IoC callbacks
     instead of Event Bus)
   - **Deferred** — to which future phase, with justification
   - **Descoped** — with written rationale for why the requirement is not applicable
     to the current deployment stage
2. No code changes — ADR documentation only.

### Testing & Quality Gates

- No code changes expected — docs-gate applies.
- All review agents spawned.

---

## Phase 11 Exit Criteria

- README and BACKLOG.md current with all phases.
- Workspace clean: no stale worktrees, no orphan spike files.
- All architectural requirement deviations documented in ADR-0029.
- All quality gates passing.
- Phase 11 end-of-phase retrospective completed.
