# Phase 31 — Code Health & Bus Factor Elimination

**Goal**: Reduce technical debt accumulated over 30 phases of feature delivery. Three
independent work streams: (1) a human developer guide that eliminates the bus factor by
documenting how to operate and contribute to this AI-orchestrated codebase, (2) vulture
whitelist audit to eliminate suppressions that are no longer necessary, and (3) decomposition
of the 981-line `dp_training.py` into focused modules.

**Prerequisite**: Phase 30 merged. Zero open advisories.

**ADR**: None required — no architectural decisions, only internal refactoring and documentation.

---

## T31.1 — Human Developer Guide

**Priority**: P0 — Bus factor mitigation is a project risk, not a feature.

### Context & Constraints

1. The entire codebase was produced by AI agents orchestrated through a governance framework
   (Constitution, CLAUDE.md, agent briefs, ADRs). A human developer onboarding to this project
   today would face two questions: (a) "How does this software work?" and (b) "How do I operate
   the AI orchestration pipeline that produced it?"

2. The guide must be practical — not a restatement of CLAUDE.md or the Constitution. It should
   answer questions like:
   - How do I set up the dev environment?
   - How do I run the full test suite and quality gates?
   - How is the codebase organized and why?
   - How do I add a new module or feature?
   - How does the AI orchestration pipeline work (PM session → subagents → reviews → merge)?
   - How do I operate without the AI pipeline (pure human workflow)?
   - What are the critical invariants I must not violate?

3. The guide lives at `docs/DEVELOPER_GUIDE.md` — a single, comprehensive document.

4. This is a documentation-only task. No source code changes.

### Acceptance Criteria

1. `docs/DEVELOPER_GUIDE.md` created with sections covering:
   - **Environment setup**: Poetry, Python version, Docker Compose, `.env` template
   - **Project architecture**: Modular monolith layout, module responsibilities, import boundaries
   - **Running quality gates**: Every gate command from CLAUDE.md with explanation of what each checks
   - **TDD workflow**: Red/Green/Refactor with concrete examples from this codebase
   - **Adding a new feature**: Step-by-step (backlog task → branch → TDD → reviews → merge)
   - **Adding a new module**: Where files go, import-linter contract, bootstrapper wiring
   - **AI orchestration pipeline**: How the PM session, software-developer agent, and review agents interact
   - **Operating without AI**: How a human developer follows the same workflow manually
   - **Critical invariants**: PII protection, import boundaries, pre-commit hooks, coverage thresholds
   - **Key files reference**: CLAUDE.md, Constitution, ADR index, RETRO_LOG purpose
2. Guide references actual file paths and commands — no placeholders or hypotheticals.
3. Guide is accurate against the current codebase state (Phase 30 complete).
4. `pre-commit run --all-files` passes.

### Testing & Quality Gates

- `pre-commit run --all-files`
- QA reviewer spawned (accuracy check against actual codebase).

### Files to Create/Modify

- `docs/DEVELOPER_GUIDE.md` (new)

---

## T31.2 — Vulture Whitelist Audit & Reduction

**Priority**: P1 — Dead code hygiene.

### Context & Constraints

1. `.vulture_whitelist.py` has 96 entries across 8 categories. A prior analysis categorized them:
   - **68 structurally required** (71%): FastAPI routes, Starlette middleware, Pydantic fields,
     SQLAlchemy protocols, DI factories — these are genuine false positives from framework patterns
     that vulture's static analysis cannot trace. These stay.
   - **19 potentially eliminable**: Entries where the suppressed name might be truly dead code
     or where the code could be restructured to eliminate the need for suppression.
   - **9 test-isolation utilities**: `reset()`, `_reset_fernet_cache()`, `dispose_engines()`, etc.
     — used only in test fixtures. These are legitimate but could potentially be relocated or
     restructured.

2. For each of the 19 "potentially eliminable" entries, the developer must:
   (a) Verify whether the name is actually used at runtime (grep for call sites, check DI wiring).
   (b) If truly dead → delete the dead code and remove the whitelist entry.
   (c) If alive but suppressible via restructuring → restructure and remove the entry.
   (d) If genuinely a false positive that must stay → add a more detailed comment explaining why.

3. For the 9 test-isolation entries: evaluate whether `conftest.py` fixtures or `__all__` exports
   would make them visible to vulture without a whitelist entry.

4. **Constraint**: Removing code that is actually used at runtime is a correctness bug. Every
   removal must be validated by running the full test suite (unit + integration).

5. The whitelist will never reach zero — framework patterns are inherent to FastAPI/Pydantic/
   SQLAlchemy. The goal is to eliminate entries that suppress genuinely dead code.

### Acceptance Criteria

1. Each of the 19 "potentially eliminable" entries has been investigated and either:
   - Removed (dead code deleted + whitelist entry removed), or
   - Retained with an improved comment explaining the runtime usage path.
2. Each of the 9 test-isolation entries has been investigated for conftest/`__all__` alternatives.
3. Net reduction of at least 5 whitelist entries (conservative — actuals may be higher or lower
   depending on what is truly dead).
4. `vulture src/ .vulture_whitelist.py --min-confidence 60` passes with zero output.
5. `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error` passes.
6. `poetry run pytest tests/integration/ -v` passes.
7. No import-linter violations.

### Testing & Quality Gates

- `vulture src/ .vulture_whitelist.py --min-confidence 60`
- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/ -v`
- `poetry run mypy src/`
- `pre-commit run --all-files`
- QA + Architecture reviewers spawned.

### Files to Create/Modify

- `.vulture_whitelist.py` (reduced)
- Various `src/` files (dead code removal, if any)
- Possibly `tests/conftest.py` (if test-isolation utilities are relocated)

---

## T31.3 — dp_training.py Decomposition

**Priority**: P1 — Maintainability. 981 lines with a 218-line method is a code smell.

### Context & Constraints

1. `src/synth_engine/modules/synthesizer/dp_training.py` is 981 lines with 12 methods.
   The primary decomposition target is `_train_dp_discriminator` (218 lines, L407–L624),
   which handles:
   - Batch size capping and DataLoader construction
   - Generator and Discriminator instantiation
   - Opacus PrivacyEngine wrapping
   - The full GAN training loop (epoch → batch → discriminator step → generator step)
   - Per-epoch budget enforcement
   - State storage for sampling

2. Secondary targets:
   - `_activate_opacus_proxy` (112 lines, L625–L736) — the fallback proxy model path
   - `fit` (91 lines, L780–L870) — orchestration method
   - `_sample_from_dp_generator` (59 lines, L923–L981)

3. **Decomposition strategy**: Extract sub-responsibilities into private helper methods or a
   separate module (`dp_training_loop.py`). The public API (`DPCompatibleCTGAN.fit()`,
   `.sample()`) must not change. No behavioral changes — pure refactor.

4. **Import boundary**: All extracted code stays within `modules/synthesizer/`. No new
   cross-module dependencies.

5. **Test stability**: This is a refactor — all existing tests must continue to pass without
   modification. If tests break, the refactor is wrong, not the tests.

6. Candidate extractions from `_train_dp_discriminator`:
   - `_cap_batch_size(n_samples, batch_size) -> int` — batch size clamping logic
   - `_build_gan_models(data_dim, sdv_synth) -> tuple[Generator, Discriminator]` — model construction
   - `_wrap_discriminator_with_opacus(discriminator, optimizer, dataloader, dp_wrapper) -> optimizer` — Opacus integration point
   - `_run_gan_training_loop(generator, discriminator, dataloader, ...) -> None` — the epoch/batch loop
   - `_store_dp_training_state(generator, ...) -> None` — post-training state storage for sampling

7. The module docstring (currently 164 lines, L1–L165) is documentation, not code. It stays.

### Acceptance Criteria

1. `_train_dp_discriminator` reduced to ≤80 lines (from 218) by extracting sub-methods.
2. No method in `dp_training.py` exceeds 100 lines.
3. All existing unit tests pass without modification.
4. All existing integration tests pass without modification.
5. `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error` passes.
6. `poetry run mypy src/` passes.
7. `vulture src/ .vulture_whitelist.py --min-confidence 60` passes.
8. No new whitelist entries added (extracted methods are called by the parent method, not framework magic).
9. import-linter contracts pass.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/ -v`
- `poetry run mypy src/`
- `vulture src/ .vulture_whitelist.py --min-confidence 60`
- `pre-commit run --all-files`
- QA + Architecture reviewers spawned.

### Files to Create/Modify

- `src/synth_engine/modules/synthesizer/dp_training.py` (refactored — same public API)
- Possibly `src/synth_engine/modules/synthesizer/dp_training_loop.py` (new — if extraction warrants a separate module)

---

## Task Execution Order

```
T31.1 (Developer Guide)  ─────────────────────────> parallel
T31.2 (Vulture Audit)    ─────────────────────────> parallel
T31.3 (dp_training decomp) ───────────────────────> parallel
```

All three tasks are independent — no dependencies between them. They can be executed in
parallel by separate subagent instances.

---

## Phase 31 Exit Criteria

1. `docs/DEVELOPER_GUIDE.md` exists and covers all sections from T31.1 AC.
2. Vulture whitelist reduced by ≥5 entries with zero vulture output.
3. No method in `dp_training.py` exceeds 100 lines.
4. All quality gates pass (unit tests ≥90% coverage, integration tests, mypy, ruff, bandit, vulture, pre-commit).
5. Zero open advisories in RETRO_LOG.
6. Review agents pass for all three tasks.
