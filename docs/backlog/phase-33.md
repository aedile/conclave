# Phase 33 — Governance Hygiene, Documentation Currency & Codebase Polish

**Goal**: Address all 10 actionable findings from the Panel Roast #2 post-Phase 32.
Evaluate expired governance rules, close the docstring-drift gap with an automated gate,
fix stale documentation, tighten dependency ranges, and remove dead artifacts.

**Prerequisite**: Phase 32 merged. Zero open advisories.

**ADR**: None required — no architectural decisions, only governance evaluation,
documentation, and cleanup.

---

## T33.1 — CLAUDE.md Rule Sunset Evaluation

**Priority**: P0 — Governance integrity. Rules past sunset undermine the sunset mechanism itself.

### Context & Constraints

1. Every CLAUDE.md rule carries `[sunset: Phase N]` per Rule 15 (meta-rule). The purpose is
   to force periodic evaluation: if a rule hasn't prevented a failure in 10+ phases, delete it.

2. All rules from Rule 4 through Rule 17 (excluding Rule 15 itself) are tagged
   `[sunset: Phase 25]`. Phase 32 is complete — these are **7 phases overdue** for evaluation.

3. For each rule, the evaluation must answer:
   - Has this rule prevented a failure since it was introduced? (Check RETRO_LOG for evidence.)
   - Is the failure it guards against still possible given current tooling and process?
   - Should the rule be: **renewed** (new sunset Phase 40), **merged** into another rule,
     **amended**, or **deleted**?

4. Rules to evaluate: 4, 5, 6, 8, 9, 10, 11, 12, 13, 16, 17.

5. Rule 15 (sunset meta-rule) is tagged `[sunset: never]` and is not subject to evaluation.

6. The CLAUDE.md line cap is 400 lines (Rule 15). Pruning rules frees budget for future rules.

7. **Constraint**: Do not change the PM/Developer separation, TDD workflow, or Quality Gates
   sections — those are structural, not retrospective-sourced rules.

### Acceptance Criteria

1. Each of the 11 evaluable rules has a documented verdict: RENEW, MERGE, AMEND, or DELETE.
2. Renewed rules carry `[sunset: Phase 40]`.
3. Deleted rules are removed from CLAUDE.md entirely (no commented-out remnants).
4. CLAUDE.md remains under 400 lines after changes.
5. A `docs:` commit documents the evaluation rationale (which rules were kept/deleted and why).
6. `pre-commit run --all-files` passes.

### Testing & Quality Gates

- `pre-commit run --all-files`
- QA reviewer spawned (verify evaluation logic is sound).

### Files to Create/Modify

- `CLAUDE.md` (modified — rules pruned/renewed)

---

## T33.2 — Docstring Validation Gate

**Priority**: P1 — Recurring failure pattern (3 occurrences in Phases 30, 31, 32).
Constitution Priority 0.5 requires programmatic enforcement for every quality requirement.

### Context & Constraints

1. Docstring-implementation drift is the **most frequent recurring failure** in the RETRO_LOG:
   - Phase 30: `_activate_opacus_proxy` docstring referenced removed `steps_per_epoch` variable
   - Phase 31: dp_training decomposition docstring stale after method extraction
   - Phase 32: test docstrings didn't match Python 3.14 behavior

2. No automated gate currently validates docstring accuracy. Mypy doesn't check docstrings.
   Ruff doesn't cross-reference docstring parameter names against function signatures.
   The only defense is the QA reviewer agent (post-hoc, not pre-commit).

3. Tool options:
   - `pydoclint` — validates Google/NumPy/Sphinx docstring parameter names, types, return types
     against function signatures. Integrates with ruff or standalone.
   - `darglint` — similar but unmaintained since 2022.
   - `ruff` rules `D` (pydocstyle) — style-only, does not validate parameter accuracy.

4. **Recommendation**: `pydoclint` as a pre-commit hook. It catches exactly the class of error
   that keeps recurring: docstring says parameter X exists but the function signature doesn't
   have it (or vice versa).

5. **Scope**: Apply to `src/synth_engine/` only (not tests — test docstrings are descriptive,
   not API contracts).

6. Expect initial violations — the tool may flag existing docstrings that are technically
   inaccurate. All violations must be fixed before the gate can be enforced.

### Acceptance Criteria

1. `pydoclint` (or equivalent) added to `pyproject.toml` dev dependencies.
2. Pre-commit hook configured to run `pydoclint` on `src/synth_engine/`.
3. All existing violations in `src/synth_engine/` fixed.
4. `pre-commit run --all-files` passes with the new hook.
5. `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error` passes.
6. `poetry run mypy src/` passes.

### Testing & Quality Gates

- Full gate suite (ruff, mypy, bandit, vulture, pytest unit+integration, pre-commit)
- QA reviewer spawned.

### Files to Create/Modify

- `pyproject.toml` (new dev dependency)
- `.pre-commit-config.yaml` (new hook)
- Various `src/synth_engine/` files (docstring fixes for existing violations)

---

## T33.3 — Documentation Currency & Gaps

**Priority**: P1 — Documentation accuracy (Constitution Priority 6).

### Context & Constraints

1. **Stale metrics** (Finding 4): README says "515 commits" and DEVELOPMENT_STORY says the same,
   but the repo now has 523+. These numbers will continue to drift. Fix by using "at time of
   Phase 32" language with a pinned commit hash, or by using a range ("500+").

2. **No CHANGELOG** (Finding 5): For a security tool destined for air-gapped deployment,
   operators need structured release notes. Create `CHANGELOG.md` with phase-based entries.
   Use `git log` to reconstruct key changes per phase. Keep it concise — not a copy of
   RETRO_LOG, but a user-facing "what changed" document.

3. **Missing backlog files for Phases 21-28** (Finding 9): These phases were executed but have
   no backlog documents in `docs/backlog/`. Backfill with lightweight summaries (PR title,
   date merged, key deliverables) — not full specs. These are historical records, not planning
   documents. Use `gh pr list` output as the source of truth.

4. **No static API reference** (Finding 11): The only API docs are FastAPI's auto-generated
   Swagger/ReDoc, which require a running server. Export the OpenAPI schema to
   `docs/api/openapi.json` and generate a static markdown reference. This is especially
   valuable for air-gapped deployments where the server may not be accessible during
   development planning.

5. **ADR-0002 ChromaDB relevance** (Finding 10): ADR-0002 describes ChromaDB as a runtime
   dependency for an "Agile Brain" semantic memory layer. This was a Phase 0.8 spike that was
   never promoted to production. ChromaDB is currently a dev-only optional dependency. The ADR
   should be updated to status "Superseded" or "Withdrawn" with a note explaining the spike
   was not promoted.

### Acceptance Criteria

1. README and DEVELOPMENT_STORY metrics pinned to "at time of Phase 32 completion" with
   commit hash reference (no more drifting absolute numbers).
2. `CHANGELOG.md` created at project root with phase-based entries from Phase 1 through 32.
   Each entry: phase number, date, 2-3 bullet points of key changes.
3. `docs/backlog/phase-21.md` through `phase-28.md` created as lightweight historical summaries.
4. `docs/api/openapi.json` exported from the running FastAPI app.
5. `docs/api/API_REFERENCE.md` generated from the OpenAPI schema (or a human-readable
   equivalent documenting all endpoints, methods, request/response schemas).
6. ADR-0002 updated with amended status and explanation.
7. `pre-commit run --all-files` passes.

### Testing & Quality Gates

- `pre-commit run --all-files`
- QA reviewer spawned (verify historical accuracy of CHANGELOG and backlog summaries).

### Files to Create/Modify

- `README.md` (metric pinning)
- `docs/DEVELOPMENT_STORY.md` (metric pinning)
- `CHANGELOG.md` (new)
- `docs/backlog/phase-21.md` through `phase-28.md` (new — lightweight summaries)
- `docs/api/openapi.json` (new)
- `docs/api/API_REFERENCE.md` (new)
- `docs/adr/ADR-0002-chromadb-runtime-dependency.md` (amended)

---

## T33.4 — Codebase Cleanup

**Priority**: P2 — Code hygiene. No correctness or security impact, but reduces noise.

### Context & Constraints

1. **Empty `shared/middleware/` package** (Finding 3): After Phase 32 removed
   `idempotency.py`, `shared/middleware/__init__.py` contains only a docstring noting the
   removal. The package has no inhabitants. Remove the package entirely — if TBD-07
   (idempotency) is ever implemented, it can recreate the directory.

2. **Scaffolding-removal tests will rot** (Finding 7): `tests/unit/test_t32_1_scaffolding_removal.py`
   contains 14 negative tests asserting deleted modules are not importable. These tests pass
   forever and add no ongoing value. Add a `# SUNSET: Phase 38 — evaluate for removal` comment
   at the top, and add a pytest marker `@pytest.mark.sunset_phase_38` so they can be filtered
   in future cleanup.

3. **Wide dependency version ranges** (Finding 8): `pyproject.toml` has ranges like
   `fastapi = ">=0.115.0,<1.0.0"`, `pydantic = ">=2.0.0,<3.0.0"`. These span unreleased
   major versions. Tighten to current minor version ranges based on `poetry.lock` resolved
   versions (e.g., `fastapi = ">=0.115.0,<0.116.0"` or `"~0.115"`). The `poetry.lock` still
   pins exact versions, but tighter ranges prevent surprising resolution changes.

4. When tightening ranges, do NOT tighten ranges for dependencies that are already at their
   latest published version — only tighten where the upper bound spans an unreleased major
   version.

### Acceptance Criteria

1. `src/synth_engine/shared/middleware/` directory deleted (including `__init__.py` and
   `__pycache__/`).
2. No import references to `shared.middleware` remain in the codebase.
3. `test_t32_1_scaffolding_removal.py` has sunset comment and marker.
4. Key dependency ranges in `pyproject.toml` tightened (at minimum: fastapi, pydantic,
   sqlmodel, cryptography, alembic).
5. `poetry lock --no-update` succeeds with tightened ranges (no resolution changes).
6. `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error` passes.
7. `poetry run pytest tests/integration/ -v` passes.
8. `poetry run mypy src/` passes.
9. `vulture src/ .vulture_whitelist.py --min-confidence 60` passes.
10. `pre-commit run --all-files` passes.

### Testing & Quality Gates

- Full gate suite (ruff, mypy, bandit, vulture, pytest unit+integration, pre-commit)
- QA + Architecture reviewers spawned.

### Files to Create/Modify

- Delete: `src/synth_engine/shared/middleware/__init__.py`, `shared/middleware/__pycache__/`
- Modify: `pyproject.toml` (dependency ranges)
- Modify: `tests/unit/test_t32_1_scaffolding_removal.py` (sunset comment + marker)
- Modify: `.vulture_whitelist.py` (remove any middleware references if present)

---

## Task Execution Order

```
T33.1 (Rule sunset evaluation) ──────────────────> parallel
T33.2 (Docstring validation gate) ───────────────> parallel
T33.3 (Documentation currency) ──────────────────> parallel
T33.4 (Codebase cleanup) ────────────────────────> parallel
```

All four tasks are independent. T33.1 modifies CLAUDE.md, T33.2 modifies pre-commit config,
T33.3 creates/modifies docs, T33.4 modifies source and config. No cross-task dependencies.

---

## Phase 33 Exit Criteria

1. All CLAUDE.md rules evaluated — expired rules deleted or renewed with Phase 40 sunset.
2. Docstring validation gate enforced in pre-commit with zero violations in `src/`.
3. README and DEVELOPMENT_STORY metrics pinned (no more drifting numbers).
4. CHANGELOG.md exists with phase-based release notes.
5. Phase 21-28 backlog summaries backfilled.
6. Static API reference exported and documented.
7. ADR-0002 status updated.
8. Empty `shared/middleware/` package removed.
9. Dependency version ranges tightened.
10. All quality gates pass.
11. Zero open advisories in RETRO_LOG.
12. Review agents pass for all tasks.
