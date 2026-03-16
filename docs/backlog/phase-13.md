# Phase 13 — Pre-commit Repair & README Finalization

**Goal**: Fix the broken pre-commit ruff gate caused by `.vulture_whitelist.py` (Constitution
Priority 1 violation) and finalize README to reflect Phase 12 completion. No new features.

**Prerequisite**: Phase 12 must be complete (all tasks merged, retrospective signed off).

---

## T13.1 — Fix Vulture Whitelist Ruff Compliance & README Final Status

**Priority**: P0 — Constitution Priority 1 violation (quality gates broken on main).

### Context & Constraints

1. `.vulture_whitelist.py` (created in P12-T12.2, PR #74) produces 161 ruff errors:
   - F821 (undefined names) — expected in vulture whitelists, which use bare identifiers
   - B018 (useless expressions) — same root cause; bare identifiers are statements
   These are **false positives specific to the vulture whitelist idiom**. The standard
   vulture whitelist pattern uses bare identifiers intentionally — they exist only so
   vulture's AST walker sees the names as "used".

2. `pre-commit run --all-files` **fails** on `main` because the ruff hook scans
   `.vulture_whitelist.py`. This blocks all developers from making clean commits.

3. README.md line 93 still says "Phase 12 — Final Hygiene & Tooling Polish is in progress."
   Phase 12 retrospective is committed. Line 111 in the phase table also says "In Progress".

4. Phase 13 row should be added to the README phase table and docs/BACKLOG.md.

### Acceptance Criteria

1. `.vulture_whitelist.py` excluded from ruff F821 and B018 checks via `per-file-ignores`
   in `pyproject.toml`.
2. `pre-commit run --all-files` passes cleanly (zero failures).
3. `poetry run ruff check src/ tests/` still passes (no regression).
4. README.md line 93 updated: "is complete" (not "is in progress").
5. README.md phase table: Phase 12 row marked "Complete", Phase 13 row added as "In Progress".
6. `docs/BACKLOG.md` updated to index Phase 13.

### Testing & Quality Gates

- `pre-commit run --all-files` — must pass (this is the primary gate).
- `poetry run ruff check src/ tests/` — must still pass.
- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error` — no regression.
- All review agents spawned.

---

## Phase 13 Exit Criteria

- Pre-commit hooks pass cleanly on main.
- README current with Phase 12 completion and Phase 13 status.
- All quality gates passing.
- Phase 13 end-of-phase retrospective completed.
