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
- Spawn parallel review subagents: `qa-reviewer`, `devops-reviewer` (always); `ui-ux-reviewer`
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

**Rule 4 — Phase-end cross-task integration review.** [sunset: Phase 25]
After the final task of any phase merges, the PM MUST audit every task against its backlog AC.
Check: (a) are all stated integration tests present? (b) are all integration requirements wired?
Failures become P0 debt tasks blocking the next phase.

**Rule 5 — Full backlog spec in agent prompts.** [sunset: Phase 25]
The PM MUST copy the ENTIRE backlog task spec verbatim into the software-developer brief —
including **Context & Constraints**. Cross-reference each C&C bullet against the AC list.
Gaps must be resolved: add a matching AC, or explicitly descope with written justification.

**Rule 6 — Technology substitution requires PM approval and an ADR.** [sunset: Phase 25]
If a backlog task names a specific technology and the subagent proposes a different one, the PM
MUST require an ADR documenting the substitution BEFORE approving. Silent substitutions are a
process violation. (Active: ADR-0031 created in T18.2 per this rule.)

**Rule 8 — Operational wiring is a delivery requirement.** [sunset: Phase 25]
Any IoC hook or callback introduced in a task must be wired to a concrete implementation in
`bootstrapper/` before the task is complete. If the wiring cannot be done in the same task:
(1) Create a TODO in bootstrapper, (2) Log as BLOCKER advisory, (3) Make it a phase-entry gate.

**Rule 9 — Documentation gate: every PR requires a `docs:` commit.** [sunset: Phase 25]
Every PR branch MUST contain at least one `docs:` commit. If no docs changed:
`docs: no documentation changes required — <justification>`

**Rule 10 — Agent learning gate.** [sunset: Phase 25]
The PM MUST scan `docs/RETRO_LOG.md` for retrospective notes matching the current task domain
and include them under **"Known Failure Patterns — Guard Against These"** in the brief.

**Rule 11 — Advisory drain cadence.** [sunset: Phase 25]
ADV rows tagged: `BLOCKER` | `ADVISORY` | `DEFERRED`. If open ADV rows exceed **12**, stop
new feature work and drain to ≤8 before resuming.

**Rule 12 — Phase execution authority.** [sunset: Phase 25]
Once user approves a phase plan, the PM has execution authority over all tasks. Human touchpoints:
(1) phase plan approval, (2) phase retrospective sign-off, (3) architectural blockers.
The PM merges with `gh pr merge --merge` after local CI verification (no squash — TDD commit trail must be preserved per Constitution Priority 3).
(Until 2026-03-31: GitHub CI offline due to budget. Local execution is constitutional.)

**Rule 13 — PR review automation.** [sunset: Phase 25]
After review agents pass and local CI gates pass, spawn the `pr-reviewer` subagent. If all
gates green, pr-reviewer posts `gh pr review --approve` and PM merges with `gh pr merge --merge`.

**Rule 15 — Rule sunset clause.** [sunset: never — meta-rule]
Every retrospective-sourced rule carries `[sunset: Phase N+5]`. At the tagged phase, evaluate
recurrence prevention. If the rule has not prevented a failure in 10+ phases, delete it.
CLAUDE.md line cap: **400 lines**.

**Rule 16 — Materiality threshold.** [sunset: Phase 25]
Cosmetic-only review findings get batched into a "polish" task. Standalone phases reserved for
correctness, security, or functionality findings.

**Rule 17 — Small-fix batching.** [sunset: Phase 25]
If a "phase" would have fewer than 5 meaningful commits, it becomes a task within the current
or next phase — not a standalone phase.

---

## Core Philosophy

> **"A place for everything and everything in its place."**

Clean workspace, clear organization, security by default, minimal footprint, zero tolerance for mess.

---

## MANDATORY WORKFLOW (NON-NEGOTIABLE)

### Pre-Commit Hooks - NEVER SKIP

`--no-verify`, `--skip=...`, `SKIP=...` are **FORBIDDEN**. If hooks fail, fix the code.

### TDD - Red/Green/Refactor (STRICT)

1. **RED**: Write failing tests FIRST → commit `test: add failing tests for <feature>`
2. **GREEN**: Minimal code to pass → commit `feat: implement <feature>`
3. **REFACTOR**: Clean up if needed → commit `refactor: improve <feature>`
4. **REVIEW**: Spawn `qa-reviewer` + `devops-reviewer` (always); `ui-ux-reviewer` (frontend);
   `architecture-reviewer` (src/synth_engine/). One consolidated `review:` commit.
   Update RETRO_LOG: add unresolved advisories, drain completed rows.

### Quality Gates (All Must Pass)

**TEMPORARY (until 2026-03-31)**: GitHub Actions offline. All gates run **locally** before merge.

**CRITICAL**: All Python commands via `poetry run`.

```bash
poetry run ruff check src/ tests/                              # Linting
poetry run ruff format --check src/ tests/                     # Formatting
poetry run mypy src/                                           # Type checking
poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95 -W error
poetry run pytest tests/integration/ -v                        # Separate gate
poetry run bandit -c pyproject.toml -r src/                    # Security scan
vulture src/ .vulture_whitelist.py --min-confidence 60         # Dead code
pre-commit run --all-files                                     # All hooks
```

**Two-gate test policy**: Unit tests (mocks OK, `-W error`) + Integration tests (real infra,
pytest-postgresql). Both must pass. "Integration test using X" is NOT satisfied by unit mocks.

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

Before promoting code from `docs/retired/spikes/` into `src/synth_engine/`, verify:
silent failure audit, PRNG seeding, edge case guards, type annotations, bandit scan,
import boundary compliance, ≥95% test coverage, ADR alignment. Partial promotion forbidden.

---

## Architecture Constraints

### Modular Monolith

```text
src/synth_engine/
├── bootstrapper/  → API, DI, middleware
├── modules/
│   ├── ingestion/    → Schema inference & mapping
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
REVIEWERS:       QA+DevOps always | UI/UX: frontend | Arch: src/synth_engine/
NEVER:           --no-verify, skip hooks, commit PII, dead code, untyped code
ALWAYS:          TDD, 95% coverage, type hints, clean workspace, review commit
```
