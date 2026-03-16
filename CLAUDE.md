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
  from RED through GREEN through quality gate verification

### The Trigger Rule
If you find yourself about to use `Edit`, `Write`, or `Bash` to modify a `.py`, `.toml`,
`.yaml`, `.sh`, or any source file for implementation purposes — **STOP**.
That action belongs to the `software-developer` subagent. Delegate it instead.

The only files the PM may edit directly are:
- `docs/RETRO_LOG.md` (review ledger commits)
- `CLAUDE.md` (process amendments like this one)
- `.claude/agents/*.md` (process amendments to agent prompts — these are meta-configuration, not source code)

### Approval Gate
Per the Approval Gate in this CLAUDE.md: present a plan, list files to create/modify,
list tests to write, estimated commits. **Do not proceed until the user approves.**

### PM Planning Rules

**Rule 2 — Cross-task integration matrix.** [sunset: Phase 22]
Before presenting a plan for any task that involves a new dependency, new shared
module, or new infrastructure component, the PM MUST explicitly check whether any
*already-completed* task has a stated integration requirement with this task.
If so, the plan MUST include wiring those systems together — not defer it.
Pattern to check: search backlog for "tie into", "integrate with", "must use",
"wired to" targeting the current component. If found, it is in scope.

**Rule 3 — Integration tests are a separate gate.** [sunset: Phase 22]
Unit tests with mocks or SQLite do NOT satisfy acceptance criteria that specify
integration tests (pytest-postgresql, real Redis, real HTTP server, etc.).
The PM must verify — by inspecting the test files committed — that integration
tests actually exist when the backlog requires them. If they are absent, the
task is NOT done regardless of coverage percentage.

**Rule 4 — Phase-end cross-task integration review.** [sunset: Phase 22]
After the final task of any phase merges, the PM MUST audit every task in that
phase against its backlog acceptance criteria before declaring the phase complete.
Specifically check: (a) are all stated integration tests present? (b) are all
"tie into" integration requirements wired? Failures become P0 debt tasks that
block the next phase from starting.

**Rule 5 — Full backlog spec in agent prompts.** [sunset: Phase 22]
When writing the implementation brief for a `software-developer` subagent, the PM MUST
copy the ENTIRE backlog task spec verbatim — including **Context & Constraints**, not just
**Testing & Quality Gates**. Requirements in Context & Constraints are in scope even if
not repeated in the AC items. The PM must explicitly cross-reference each Context &
Constraints bullet against the AC list before writing the brief. Any gap must be resolved:
either add a matching AC, or explicitly descope with written justification.

**Rule 6 — Technology substitution requires PM approval and an ADR.** [sunset: Phase 22]
If a backlog task spec names a specific technology (library, protocol, driver) and the
software-developer subagent proposes using a different one, the PM MUST:
1. Explicitly acknowledge the substitution in the plan review.
2. Require an ADR (or ADR amendment) documenting the substitution and rationale BEFORE
   approving implementation.
Silent technology substitutions are a process violation.

**Rule 7 — Intra-module cohesion gate.** [sunset: Phase 22]
Before approving any plan that adds new files to an existing module (`modules/X/`), the
PM MUST verify that each new file's responsibility matches the module's domain name.
Rule of thumb: ingestion ingests, masking masks, subsetting subsets, profiling profiles.
A class doing X that lives in module Y is a planning failure.

**Rule 8 — Operational wiring is a delivery requirement, not an advisory.** [sunset: Phase 22]
Any IoC hook, injectable abstraction, or callback interface introduced in a task must be
wired to a concrete implementation in `bootstrapper/` before the task is marked complete.
If the wiring cannot be done in the same task, the PM must:
1. Create an explicit TODO in bootstrapper pointing to the target task.
2. Log it as a BLOCKER advisory (not an informational advisory) in RETRO_LOG.
3. Make it a Phase-entry gate for the phase that includes the wiring task.

**Rule 9 — Documentation gate: every PR requires a `docs:` commit.** [sunset: Phase 22]
Every PR branch MUST contain at least one commit whose message begins with `docs:`. If no
documentation changes were made, the commit message MUST be:
`docs: no documentation changes required — <one-sentence justification>`
This is enforced by the `docs-gate` CI job. The PM makes this commit as the final commit before pushing.

**Rule 10 — Agent learning gate: PM surfaces RETRO_LOG lessons in every brief.** [sunset: Phase 22]
When writing the implementation brief for any `software-developer` subagent, the PM MUST scan
`docs/RETRO_LOG.md` — including the Task Reviews section — for retrospective notes whose domain
matches the current task. These findings are included verbatim under a **"Known Failure Patterns
— Guard Against These"** heading. The agent's first output statement MUST declare which patterns
it is guarding against.

**Rule 11 — Advisory drain cadence.** [sunset: Phase 22]
Every ADV row in `docs/RETRO_LOG.md` MUST be tagged with: `BLOCKER` (must drain before its
target task starts), `ADVISORY` (must drain within the same phase), or `DEFERRED` (accepted
post-launch debt — requires one-sentence PM justification). Open ADV row count is audited at
every phase kickoff. If open ADV rows exceed **12**, the PM MUST stop new feature work and
propose a drain sprint. Drain target: ≤8 before resuming.

**Rule 12 — Phase execution authority.** [sunset: Phase 22]
Once the user approves a phase plan, the PM has execution authority over all tasks in that
phase without per-task human approval. Mandatory human touchpoints: (1) phase plan approval,
(2) phase retrospective sign-off, (3) any PM-raised architectural blocker requiring strategic
input. The PM MUST call `gh pr merge --squash --auto` immediately after `gh pr create` on every PR.

**Rule 13 — PR review automation.** [sunset: Phase 22]
After spawning review agents and after CI is green, the PM MUST spawn the `pr-reviewer`
subagent. The pr-reviewer reads the PR diff, checks CI status, and posts a structured summary
comment via `gh pr comment`. If all gates are green, the pr-reviewer posts `gh pr review --approve`
to satisfy branch protection, at which point auto-merge fires.

**Rule 15 — Rule sunset clause.** [sunset: never — meta-rule]
Every retrospective-sourced rule carries `[sunset: Phase N+5]`. At the tagged phase, the PM
evaluates whether the rule prevented a recurrence. If not, the rule is deleted.
CLAUDE.md line cap: 500 lines. If an amendment would exceed the cap, existing rules must be
consolidated or retired before adding the new rule.

**Rule 16 — Materiality threshold.** [sunset: Phase 22]
Cosmetic-only review findings (formatting, comment wording, doc phrasing) get batched into a
"polish" task within the next feature phase. Standalone phases are reserved for findings that
affect correctness, security, or functionality.

**Rule 17 — Small-fix batching.** [sunset: Phase 22]
If a "phase" would have fewer than 5 meaningful commits, it does not warrant standalone phase
ceremony. Instead, it becomes a task within the current or next phase.

---

## Core Philosophy

> **"A place for everything and everything in its place."**

This project demands:
- **Clean workspace**: No clutter, no orphan files, no dead code
- **Clear organization**: Predictable locations, consistent naming, logical grouping
- **Security by default**: PII protection is not optional, it's foundational
- **Minimal footprint**: Add only what's needed, remove what's not
- **Zero tolerance for mess**: If it doesn't belong, it doesn't exist

---

## MANDATORY WORKFLOW (NON-NEGOTIABLE)

### Pre-Commit Hooks - NEVER SKIP

```bash
# FORBIDDEN - These flags are NEVER acceptable:
git commit --no-verify     # NEVER
git push --no-verify       # NEVER
pre-commit run --skip=...  # NEVER
SKIP=... git commit        # NEVER
```

- Pre-commit hooks **MUST** pass before any commit
- If hooks fail, **FIX THE CODE** - do not bypass, do not work around
- All security scans (bandit, gitleaks, detect-secrets) are mandatory

### TDD - Red/Green/Refactor (STRICT)

Every code change follows this exact sequence - no exceptions:

1. **RED**: Write failing test(s) FIRST
   ```bash
   pytest tests/unit/test_<module>.py -v  # Must FAIL
   ```
   - Commit: `test: add failing tests for <feature>`

2. **GREEN**: Write minimal code to pass
   ```bash
   pytest tests/unit/test_<module>.py -v  # Must PASS
   pytest --cov=src/synth_engine --cov-fail-under=90  # Must PASS
   ```
   - Commit: `feat: implement <feature>`

3. **REFACTOR**: Clean up (only if needed)
   ```bash
   pytest tests/ -v  # Must still PASS
   ```
   - Commit: `refactor: improve <feature>`

4. **REVIEW**: Spawn specialized subagents in parallel (MANDATORY)
   - **Always spawn**: `qa-reviewer`, `devops-reviewer`
   - **Spawn only when diff touches `frontend/`, `*.tsx`, `*.css`, or template files**: `ui-ux-reviewer`
   - **Spawn only when diff touches `src/synth_engine/` or adds new `.py` files under `src/`**: `architecture-reviewer`
   - Each agent reads the constitution independently and reviews with fresh context
   - One consolidated commit required: `review: <task> — QA PASS, DevOps PASS[, Arch PASS][, UI/UX PASS]`
   - Detailed findings go in `docs/RETRO_LOG.md` only, not in commit bodies
   - **After updating RETRO_LOG**: add any advisory findings without a named target task to the
     **Open Advisory Items** table; drain (delete) any rows whose target task was just completed

### Quality Gates (All Must Pass)

**CRITICAL**: This project uses Poetry for dependency management. ALL Python commands must be run via `poetry run`.

```bash
# Run ALL of these before any commit:
poetry run ruff check src/ tests/                              # Linting
poetry run ruff format --check src/ tests/                     # Formatting
poetry run mypy src/                                           # Type checking
poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error  # Unit tests + 90% coverage, zero warnings
poetry run pytest tests/integration/ -v                        # Integration tests (separate gate — must pass independently)
poetry run bandit -c pyproject.toml -r src/                    # Security scan
vulture src/                                                   # Dead code (80% confidence)
vulture src/ .vulture_whitelist.py --min-confidence 60         # Advisory: deeper scan (whitelist suppresses framework false positives)
pre-commit run --all-files                                     # All hooks

# For Python scripts/validation:
poetry run python3 script.py       # CORRECT
python3 script.py                  # WRONG - bypasses Poetry environment
```

**Two-gate test policy:**
- Unit tests (`tests/unit/`) run with `-W error`. Zero warnings tolerated. Mocks and in-memory
  databases are acceptable here.
- Integration tests (`tests/integration/`) run separately without coverage requirement but MUST
  pass. These use real infrastructure (PostgreSQL via pytest-postgresql, real Redis via
  pytest-redis). If `tests/integration/` is empty, that is a finding — not a pass.
- An acceptance criterion that says "integration test using X" is NOT satisfied by a unit test
  using mocks.

### Git Workflow

**Branch naming**: `<type>/<phase>-<task>-<description>`

**Commit messages**: Conventional commits, always
- `test:` - Test files (RED phase)
- `feat:` - New features (GREEN phase)
- `fix:` - Bug fixes
- `refactor:` - Refactoring (no behavior change)
- `review:` - Consolidated review commit (REVIEW phase — mandatory per task)
- `docs:` - Documentation only (also used for constitutional amendments)
- `chore:` - Tooling, config, dependencies

**Constitutional amendments** use `docs:` with format:
`docs: amend <filename> — <what changed and why>`

### Pull Request Workflow (MANDATORY)

1. **Create feature branch**: `git checkout -b feat/P0-T03-test-directory`
2. **Make changes** following TDD, commit to branch
3. **Push branch**: `git push origin feat/P0-T03-test-directory`
4. **Create PR via gh CLI** with comprehensive description
5. **Wait for user review** - DO NOT merge yourself (unless Rule 12 applies)
6. **Address feedback** if requested, commit to same branch
7. **After user merges**: Pull main, re-contextualize, next task

**PR Description Must Include:**
- Task ID and summary
- Changes made (checklist format)
- Acceptance criteria met
- Review commit reference
- Constitution compliance
- Test results
- Backlog task completion marker

---

## Workspace Organization

### Directory Purposes

| Directory | Purpose | Git Status |
|-----------|---------|------------|
| `src/synth_engine/` | Production code only | Committed |
| `tests/unit/` | Unit tests | Committed |
| `tests/integration/` | Integration tests | Committed |
| `tests/fixtures/` | Test data (fictional) | Committed |
| `sample_data/` | Demo Production seed data (fictional) | Committed |
| `docs/adr/` | Architecture decisions | Committed |
| `docs/recontextualization/` | Task-transition checklists | Committed |
| `docs/RETRO_LOG.md` | Living ledger of review retro notes | Committed |
| `docs/retro_archive/` | Archived reviews for phases ≤14 | Committed |
| `.claude/agents/` | Specialized review subagents | Committed |
| `data/` | Real user Production seed data | **GITIGNORED** |
| `output/` | Generated synthetic datasets | **GITIGNORED** |
| `logs/` | Application logs | **GITIGNORED** |

### File Placement Rules

New files belong **inside their Epic subpackage**. Only create a top-level
subpackage when a concern is shared by two or more Epics.

| What | Where | Example |
|------|-------|---------|
| API Entrypoints | `src/synth_engine/bootstrapper/` | `bootstrapper/main.py` |
| Data I/O & Subsetting | `src/synth_engine/modules/ingestion/` | `ingestion/mapper.py` |
| Format-preserving rules | `src/synth_engine/modules/masking/` | `masking/luhn.py` |
| DP-SGD generation | `src/synth_engine/modules/synthesizer/` | `synthesizer/gan.py` |
| Epsilon tracking | `src/synth_engine/modules/privacy/` | `privacy/accountant.py` |
| Cross-cutting Utils | `src/synth_engine/shared/` | `shared/audit_logger.py` |

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Modules | `snake_case.py` | `parser_agent.py` |
| Classes | `PascalCase` | `ParserAgent` |
| Functions | `snake_case` | `parse_positions` |
| Constants | `SCREAMING_SNAKE` | `MAX_UPLOAD_SIZE` |
| Test files | `test_<module>.py` | `test_parser_agent.py` |
| Test functions | `test_<behavior>` | `test_parses_valid_csv` |

---

## PII Protection (CRITICAL)

### Golden Rules

1. **NEVER** check PII into git - `data/`, `output/`, `config.local.json`, `.env`
2. **ALWAYS** review `git status` and `git diff` before `git add`
3. **ASK TWICE** before any git operation involving potentially sensitive files
4. **VERIFY** with `gitleaks detect` if uncertain about a file

### What Contains PII

| File/Directory | Contains PII | Action |
|---------------|--------------|--------|
| `data/*.csv` | YES - real Production seed data | Never commit |
| `output/*` | YES - generated synthetic datasets | Never commit |
| `config.local.json` | YES - contact info | Never commit |
| `.env` | YES - API keys | Never commit |
| `logs/*.log` | MAYBE - sanitize before review | Never commit |
| `sample_data/*.csv` | NO - fictional data | Safe to commit |
| `tests/fixtures/*` | NO - fictional data | Safe to commit |

### Before Any Git Operation

```bash
git status          # 1. Check what's staged
git diff --cached   # 2. Review changes
gitleaks detect --verbose  # 3. Verify no secrets
git commit -m "..." # 4. Only then commit
```

---

## Code Quality Standards

### Type Hints - Strict Mode

- No `# type: ignore` without written justification in comment
- All function parameters typed, all return values typed
- Use `TypedDict` for complex dictionaries

### Docstrings - Google Style

Use Google-style docstrings with Args, Returns, and Raises sections on all public functions.

### Code Cleanliness

- **No dead code**: Delete it, don't comment it out
- **No unused imports**: Remove them immediately
- **No `TODO` without ticket**: Use `TODO(P1-T03):` format
- **No magic numbers**: Define constants with clear names
- **No long functions**: Max ~50 lines, extract helpers
- **No deep nesting**: Max 3 levels, refactor if deeper

---

## Spike-to-Production Promotion Checklist
(ADV-011 — Added Phase 4 Kickoff 2026-03-14)

Before any code from `docs/retired/spikes/` is promoted into `src/synth_engine/`, the promoting
developer MUST verify ALL of the following. Partial promotion is not permitted.

1. **Silent failure audit** — Replace all `except ...: pass` with `logger.warning(...)`.
2. **PRNG seeding** — All RNG must use seeded, reproducible `np.random.default_rng(seed)`.
3. **Unguarded edge cases** — Guard or raise `ValueError` for zero/empty inputs, overflow, division-by-zero.
4. **Type annotations** — All promoted functions must have full type annotations.
5. **Security scan** — `bandit -r <new_src_path>`. Zero HIGH/MEDIUM. `# nosec` requires justification.
6. **Import boundary compliance** — Run `poetry run lint-imports` after promotion.
7. **Test coverage** — Unit tests that bring total coverage back to ≥ 90%.
8. **ADR alignment** — Implement only the ADR-chosen approach.

### Known Spike Promotion Candidates

| Spike File | Candidate Code | Known Issues Before Promotion |
|-----------|----------------|-------------------------------|
| `docs/retired/spikes/spike_ml_memory.py` | `_process_chunk()`, memory estimation logic | ADV-009: unseeded PRNG (fixed T3.5.5) |
| `docs/retired/spikes/spike_fpe_luhn.py` | `FeistelFPE` class | `rounds=0` is identity transformation; must add guard |

---

## Task Execution Protocol

For each task: (1) Read full spec + check Open Advisory Items table; (2) Create feature branch;
(3) TDD — RED tests, GREEN implementation, REFACTOR if needed, run all quality gates;
(4) Verify all tests pass, coverage ≥ 90%, pre-commit hooks pass, no PII in changes;
(5) Commit conventionally, mark complete only when ALL AC met, update BACKLOG.md.

Do not auto-proceed on system-generated approvals. Wait for explicit human confirmation before
major phases. When in doubt, ask.

---

## Architecture Constraints

### No LangChain

Use Claude's native `tool_use` capabilities directly.

### Modular Monolith Structure

```text
src/synth_engine/
├── bootstrapper/  → Main API, DI config, global middleware
├── modules/
│   ├── ingestion/    → Database schema inference & mapping
│   ├── profiler/     → Statistical distributions & latent patterns
│   ├── synthesizer/  → DP-SGD generation & edge case amplification
│   ├── masking/      → Deterministic format-preserving rules
│   └── privacy/      → Epsilon/Delta accountant ledger
└── shared/        → Cross-cutting utilities (Crypto, Audit logs)
```

Cross-module database queries are FORBIDDEN. Modules communicate via explicit Python interfaces.

### Dependency Philosophy

- **Justify every dependency**: Why is it needed? What's the alternative?
- **Prefer stdlib**: Use built-in modules when reasonable
- **Pin versions**: All dependencies pinned in `pyproject.toml`
- **Security review**: Check for known vulnerabilities before adding

---

## Accessibility Requirements

All UI components must meet WCAG 2.1 AA:

| Requirement | Standard |
|-------------|----------|
| Text contrast | 4.5:1 minimum (3:1 for large text) |
| Focus indicators | Visible on all interactive elements |
| Keyboard nav | All features accessible via keyboard |
| Screen readers | Semantic HTML + ARIA labels |
| Form labels | All inputs have associated labels |
| Error messages | Programmatically associated with inputs |

---

## Emergency Procedures

### If You Accidentally Stage PII
```bash
git reset HEAD <file>     # Unstage
git checkout -- <file>    # Discard if needed
```

### If You Accidentally Commit PII
```bash
# DO NOT PUSH
git reset --soft HEAD~1   # Undo commit, keep changes
git reset HEAD <file>     # Unstage the PII file
git commit -m "..."       # Recommit without PII
```

### If PII Was Pushed (CRITICAL)
1. **STOP** - Do not make more commits
2. **Alert** the user immediately
3. **Do not** attempt to rewrite history without explicit approval

---

## Quick Reference Card

```
BEFORE CODING:   Read task spec → Check Open Advisory Items → Create branch → Write failing test
WHILE CODING:    Minimal implementation → Pass tests → Refactor
BEFORE COMMIT:   git status → git diff → ruff → mypy → pytest → vulture → pre-commit
AFTER CODE:      Spawn qa/devops reviewers (+ ui-ux if frontend, + arch if src/) in ONE message
                 → ONE review: commit → RETRO_LOG update → drain completed advisory rows
COMMIT MESSAGE:  type: description (test:, feat:, fix:, refactor:, review:, docs:, chore:)
REVIEWERS:       QA + DevOps: always | UI/UX: frontend/tsx/css only | Arch: src/synth_engine/ only
NEVER:           --no-verify, skip hooks, commit PII, dead code, untyped code
ALWAYS:          TDD, 90% coverage, type hints, docstrings, clean workspace, review commit
AMEND PROCESS:   docs: amend <filename> — <what> (use after retrospectives or review findings)
```
