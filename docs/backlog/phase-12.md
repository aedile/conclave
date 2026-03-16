# Phase 12 — Final Hygiene & Tooling Polish

**Goal**: Address remaining hygiene findings from Roast #2. Prune stale remote branches,
update README to reflect Phase 11 completion, and create a vulture whitelist to eliminate
false positives from advisory dead-code scans. No new features.

**Prerequisite**: Phase 11 must be complete (all tasks merged, retrospective signed off).

---

## T12.1 — Stale Remote Branch Cleanup & README Final Status

**Priority**: P1 — CLAUDE.md "Clean workspace: No clutter" mandate.

### Context & Constraints

1. 70 stale remote feature branches remain on the GitHub remote after PR merges.
   These are all merged branches — every one corresponds to a squash-merged PR.
   They violate the "clean workspace" principle and make `git branch -r` output
   unusable for identifying active work.

2. README.md line 93 still says "Phase 11 — Documentation Currency & Workspace
   Hygiene is in progress." Phase 11 is complete (PRs #69, #70, #71 merged,
   retrospective committed). The status line and phase table need updating.

3. Phase 12 row should be added to the README phase table.

### Acceptance Criteria

1. All merged remote branches pruned (using `gh api` or `git push origin --delete`).
   Only `main` and any active in-progress branches remain.
2. README.md current development status updated to reflect Phase 11 complete /
   Phase 12 current.
3. README.md phase table: Phase 11 row marked "Complete", Phase 12 row added.
4. docs/BACKLOG.md updated to index Phase 12.

### Testing & Quality Gates

- No code changes expected — docs-gate applies.
- All review agents spawned.

---

## T12.2 — Vulture Whitelist for FastAPI False Positives

**Priority**: P2 — Tooling improvement for future advisory scans.

### Context & Constraints

1. `vulture src/ --min-confidence 60` produces 88 findings, nearly all of which are
   FastAPI decorator-registered route handlers, Starlette middleware `dispatch()`
   methods, and bootstrapper factory functions that are called via DI or string-based
   registration patterns that vulture cannot trace.

2. At the standard 80% confidence threshold, vulture returns 0 findings — the
   codebase is clean. But the advisory 60% scan (recommended in CLAUDE.md quality
   gates) is currently pure noise, making it useless for detecting real dead code.

3. A `.vulture_whitelist.py` file is the standard vulture mechanism for suppressing
   known false positives. It contains dummy assignments that tell vulture the
   names are intentionally used.

### Acceptance Criteria

1. `.vulture_whitelist.py` created at project root with entries for all confirmed
   false positives (FastAPI route handlers, middleware dispatch methods, DI factories).
2. `vulture src/ .vulture_whitelist.py --min-confidence 60` produces 0 findings
   OR only findings that are genuinely suspicious (not framework patterns).
3. Any genuinely unused methods discovered during whitelist creation are either
   deleted (if truly dead) or documented with a justification comment.
4. pyproject.toml or CLAUDE.md updated to reference the whitelist in the vulture
   command.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
  (verify no test depends on deleted methods, if any are removed)
- All review agents spawned.

---

## Phase 12 Exit Criteria

- All stale remote branches pruned.
- README current with Phase 11 completion and Phase 12 status.
- Vulture advisory scan produces meaningful output (not pure false positives).
- All quality gates passing.
- Phase 12 end-of-phase retrospective completed.
