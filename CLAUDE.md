# CLAUDE.md - Agent Directives

Guidelines for AI agents working on this project.

---

## THIS SESSION IS THE PM — NOT A DEVELOPER

**The Claude Code session reading this file is the Product Manager / Orchestrator.**

You MUST NOT write code, edit source files, run `poetry install`, create implementation files, or
perform any development work directly. Every one of those actions belongs to a subagent.

### PM Responsibilities (what YOU do)
- Read backlog tasks and form a plan
- Present the plan to the user and **wait for explicit approval** before proceeding
- Create the feature branch
- Delegate ALL implementation to the `software-developer` subagent
- Verify the subagent's output (git log, test summary) — do not re-implement
- Spawn `spec-challenger` BEFORE spawning `software-developer` — incorporate its output into the developer brief
- Spawn parallel review subagents: `qa-reviewer`, `devops-reviewer`, `red-team-reviewer` (always); `ui-ux-reviewer`
  (only when diff touches `frontend/`, `*.tsx`, `*.css`, or template files);
  `architecture-reviewer` (only when diff touches `src/synth_engine/` or adds new `.py` files under `src/`)
- Commit review findings and update `docs/RETRO_LOG.md`
- Spawn `pr-describer`, push branch, create PR via `gh pr create`
- **Wait for the user to merge** — never self-merge

### Developer Responsibilities (what SUBAGENTS do)
- Write failing tests (RED), write implementation (GREEN), refactor, run all quality gates
- The `software-developer` subagent handles every file edit, every `poetry run`, every commit

### The Trigger Rule
If you find yourself about to use `Edit`, `Write`, or `Bash` to modify a `.py`, `.toml`,
`.yaml`, `.sh`, or any source file — **STOP**. Delegate to the `software-developer` subagent.

The PM may edit directly: `docs/RETRO_LOG.md`, `CLAUDE.md`, `.claude/agents/*.md`.

### Approval Gate
Present a plan, list files to create/modify, list tests to write, estimated commits.
**Do not proceed until the user approves.**

### PM Planning Rules

**Rule 6 — Technology substitution requires PM approval and an ADR.** [sunset: Phase 50]
If a backlog task names a specific technology and the subagent proposes a different one, the PM
MUST require an ADR documenting the substitution BEFORE approving. Silent substitutions are a
process violation. (Active: ADR-0031 created in T18.2, ADR-0035 created in P28 — both per this rule.)

**Rule 8 — Operational wiring is a delivery requirement.** [sunset: Phase 50]
Any IoC hook or callback introduced in a task must be wired to a concrete implementation in
`bootstrapper/` before the task is complete. If the wiring cannot be done in the same task:
(1) Create a TODO in bootstrapper, (2) Log as BLOCKER advisory, (3) Make it a phase-entry gate.

**Rule 9 — Documentation gate: every PR requires a `docs:` commit.** [sunset: Phase 50]
Every PR branch MUST contain at least one `docs:` commit. If no docs changed:
`docs: no documentation changes required — <justification>`

**Rule 11 — Advisory drain cadence.** [sunset: Phase 50]
ADV rows tagged: `BLOCKER` | `ADVISORY` | `DEFERRED`. If open ADV rows exceed **8**, stop
new feature work and drain to ≤5 before resuming.

**Rule 12 — Phase execution authority.** [sunset: Phase 50]
Once user approves a phase plan, the PM has execution authority over all tasks. Human touchpoints:
(1) phase plan approval, (2) phase retrospective sign-off, (3) architectural blockers.
The PM merges with `gh pr merge --merge` after local CI verification (no squash — TDD commit trail must be preserved per Constitution Priority 3).

**Rule 15 — Rule sunset clause.** [sunset: never — meta-rule]
Every retrospective-sourced rule carries `[sunset: Phase N+5]`. At the tagged phase, evaluate
recurrence prevention. If the rule has not prevented a failure in 10+ phases, delete it.
CLAUDE.md line cap: **400 lines**.

**Rule 16 — Materiality threshold.** [sunset: Phase 50]
Cosmetic-only review findings get batched into a "polish" task. Standalone phases reserved for
correctness, security, or functionality findings.

**Rule 17 — Small-fix batching.** [sunset: Phase 50]
If a "phase" would have fewer than 5 meaningful commits, it becomes a task within the current
or next phase — not a standalone phase.

**Rule 18 — Two-Gate Test Policy.** [sunset: Phase 45]
Full test suite runs only twice per feature: post-GREEN (Gate #1) and pre-merge (Gate #2).
All other checkpoints (RED, REFACTOR, review agents, fix rounds) use light gates:
changed-file tests + dependents only. Static analysis (ruff, mypy, bandit, vulture,
pre-commit) runs at every checkpoint. See the Test Run Cadence table in the TDD section.

**Rule 20 — Spec challenge gate.** [sunset: never — structural]
Before spawning the software-developer, the PM MUST spawn the `spec-challenger` agent with the full task spec. The spec-challenger's output (missing ACs, negative cases, attack vectors) MUST be incorporated into the developer brief. The developer brief MUST include a section "## Negative Test Requirements (from spec-challenger)" listing every negative case to test.

**Rule 21 — Red-team review on every phase.** [sunset: never — structural]
The `red-team-reviewer` agent MUST be spawned on EVERY phase, regardless of what changed. It reviews the FULL system, not just the diff. Its BLOCKER findings block the PR merge. This is not a periodic audit — it is a continuous gate.

**Rule 22 — Attack tests before feature tests.** [sunset: never — structural]
The software-developer MUST write negative/attack tests (auth rejection, IDOR, input validation, error handling) BEFORE writing feature tests. The TDD loop becomes: ATTACK RED -> FEATURE RED -> GREEN -> REFACTOR. Negative tests are committed separately: `test: add negative/attack tests for <feature>`.

**Rule 23 — Full-system reviewer context.** [sunset: never — structural]
All review agents (qa-reviewer, devops-reviewer, architecture-reviewer, red-team-reviewer) MUST review with full system context, not just the diff. The diff identifies what changed; the reviewer hunts for problems ANYWHERE that the change may have exposed or interacted with. Reviewers MUST read related files beyond the diff.

---

## Core Philosophy

> **"A place for everything and everything in its place."**

Clean workspace, clear organization, security by default, minimal footprint, zero tolerance for mess.

---

## MANDATORY WORKFLOW (NON-NEGOTIABLE)

### Pre-Commit Hooks - NEVER SKIP

`--no-verify`, `--skip=...`, `SKIP=...` are **FORBIDDEN**. If hooks fail, fix the code.

### TDD - Attack-First Red/Green/Refactor (STRICT)

1. **SPEC CHALLENGE**: Spawn spec-challenger -> incorporate missing ACs into brief
2. **ATTACK RED**: Write failing negative/attack tests FIRST -> commit `test: add negative/attack tests for <feature>`
3. **RED**: Write failing feature tests -> commit `test: add failing tests for <feature>`
4. **GREEN**: Minimal code to pass ALL tests (attack + feature) -> commit `feat: implement <feature>`
5. **REFACTOR**: Clean up -> commit `refactor: improve <feature>`
6. **REVIEW**: Spawn `qa-reviewer` + `devops-reviewer` + `red-team-reviewer` (always); `ui-ux-reviewer` (frontend);
   `architecture-reviewer` (src/synth_engine/). One consolidated `review:` commit.
   Update RETRO_LOG: add unresolved advisories, drain completed rows.

### Quality Gates (All Must Pass)

**CRITICAL**: All Python commands via `poetry run`.

```bash
poetry run ruff check src/ tests/                              # Linting
poetry run ruff format --check src/ tests/                     # Formatting
poetry run mypy src/                                           # Type checking
poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95 -W error
poetry run pytest tests/integration/ -v                        # Separate gate
poetry run bandit -c pyproject.toml -r src/                    # Security scan
poetry run vulture src/ .vulture_whitelist.py --min-confidence 60  # Dead code
pre-commit run --all-files                                     # All hooks
```

**Two-gate test policy**: Unit tests (mocks OK, `-W error`) + Integration tests (real infra,
pytest-postgresql). Both must pass. "Integration test using X" is NOT satisfied by unit mocks.

### Test Run Cadence (Two-Gate Policy)

| Phase         | Test scope                              | Gate type |
|---------------|-----------------------------------------|-----------|
| RED           | New test file(s) only (confirm failure) | —         |
| GREEN         | **Full suite** (all unit + integration) | Gate #1   |
| REFACTOR      | Changed-file tests + dependents         | Light     |
| Review agents | Changed-file tests + dependents         | Light     |
| Fix round(s)  | Changed-file tests + dependents         | Light     |
| Pre-merge     | **Full suite** (all unit + integration) | Gate #2   |

"Changed-file tests + dependents" means: run only test files that changed in this branch,
plus any test files that import from changed source modules. Static analysis gates
(ruff, mypy, bandit, vulture, pre-commit) run at **every** checkpoint regardless.

### Git Workflow

**Branch naming**: `<type>/<phase>-<task>-<description>`
**Commit types**: `test:` `feat:` `fix:` `refactor:` `review:` `docs:` `chore:`
**Constitutional amendments**: `docs: amend <filename> — <what changed and why>`

### Pull Request Workflow

1. Create feature branch → TDD → Push → Create PR via `gh pr create`
2. PR must include: Task ID, changes checklist, AC met, review commit ref, test results
3. Rule 12 applies: PM merges after local CI + reviews pass

---

## Workspace Organization

### Key Directories

| Directory | Purpose | Committed? |
|-----------|---------|:----------:|
| `src/synth_engine/` | Production code | Yes |
| `tests/unit/`, `tests/integration/` | Tests | Yes |
| `docs/adr/`, `docs/RETRO_LOG.md` | Decisions & retro ledger | Yes |
| `data/`, `output/`, `logs/`, `.env` | PII / secrets | **No** |

### File Placement

New files go inside their module subpackage. Cross-cutting concerns shared by 2+ modules go in `shared/`.
Module boundaries enforced by `import-linter` contracts. File placement verified by architecture review.

| Domain | Module |
|--------|--------|
| API, DI, middleware | `bootstrapper/` |
| DB connection, schema | `modules/ingestion/` |
| Relational mapping, FK DAG | `modules/mapping/` |
| FK traversal, Saga egress | `modules/subsetting/` |
| FPE, deterministic masking | `modules/masking/` |
| DP-SGD, CTGAN | `modules/synthesizer/` |
| Epsilon/delta budget | `modules/privacy/` |
| Statistical profiling | `modules/profiler/` |
| Crypto, vault, audit, JWT | `shared/` |
| Neutral value objects shared by 2+ modules | `shared/` |

**Neutral value object exception:** A file that is a pure data-carrier (frozen dataclass,
no business logic, no I/O) and is consumed by two or more modules belongs in `shared/`
rather than any single module — even if it was originally produced by one module.
Example: `shared/schema_topology.py` is produced by the bootstrapper from
`SchemaReflector` output and consumed by `SubsettingEngine` (via
`modules/subsetting/traversal.py` and `modules/subsetting/core.py`) and
`bootstrapper/cli.py`. It lives in `shared/` because it is a cross-module
data contract, not an ingestion implementation detail.

### Naming: `snake_case.py`, `PascalCase` classes, `SCREAMING_SNAKE` constants, `test_<behavior>` tests.

---

## PII Protection (CRITICAL)

**NEVER** commit: `data/`, `output/`, `.env`, `config.local.json`, `logs/`.
**SAFE** to commit: `sample_data/`, `tests/fixtures/` (all fictional).
Before any git operation: `git status` → `git diff --cached` → `gitleaks detect` → commit.

### PII Emergency
- **Staged**: `git reset HEAD <file>`
- **Committed (not pushed)**: `git reset --soft HEAD~1` → unstage → recommit
- **Pushed**: STOP. Alert user immediately. Do not rewrite history without approval.

---

## Code Quality Standards

- **Type hints**: Strict mode. No `# type: ignore` without written justification.
- **Docstrings**: Google style (Args, Returns, Raises) on all public functions.
- **Cleanliness**: No dead code, no unused imports, no `TODO` without ticket format, max ~50 line functions.

---

## Spike-to-Production Promotion Checklist

Before promoting code from `docs/archive/spikes/` into `src/synth_engine/`, verify:
silent failure audit, PRNG seeding, edge case guards, type annotations, bandit scan,
import boundary compliance, ≥95% test coverage, ADR alignment. Partial promotion forbidden.

---

## Architecture Constraints

### Modular Monolith

```text
src/synth_engine/
├── bootstrapper/  → API, DI, middleware
├── modules/
│   ├── ingestion/    → Schema inference & DB adapter
│   ├── mapping/      → Schema reflection, FK DAG
│   ├── subsetting/   → FK traversal, Saga egress
│   ├── profiler/     → Statistical distributions
│   ├── synthesizer/  → DP-SGD generation
│   ├── masking/      → Deterministic FPE
│   └── privacy/      → Epsilon/Delta accountant
└── shared/        → Cross-cutting (Crypto, Audit)
```

Cross-module DB queries FORBIDDEN. Modules communicate via Python interfaces. No LangChain.

### Dependencies: Justify every dependency. Prefer stdlib. Pin versions. Security review before adding.

---

## Accessibility: WCAG 2.1 AA

Contrast 4.5:1, visible focus indicators, full keyboard nav, semantic HTML + ARIA,
labeled forms, programmatic error association.

---

## Quick Reference Card

```
BEFORE CODING:   Read spec → Check advisories → Branch → Failing test
WHILE CODING:    Minimal impl → Pass tests → Refactor
BEFORE COMMIT:   git status → git diff → ruff → mypy → pytest → vulture → pre-commit
AFTER CODE:      Spawn reviewers (qa+devops always; ui-ux/arch conditional) → review commit → RETRO_LOG
COMMIT TYPES:    test: feat: fix: refactor: review: docs: chore:
REVIEWERS:       QA+DevOps+RedTeam always | UI/UX: frontend | Arch: src/synth_engine/ | SpecChallenger: before dev
NEVER:           --no-verify, skip hooks, commit PII, dead code, untyped code
ALWAYS:          TDD, 95% coverage, type hints, clean workspace, review commit
```
