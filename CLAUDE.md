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
- Spawn parallel review subagents: `qa-reviewer`, `ui-ux-reviewer`, `devops-reviewer`
  (+ `architecture-reviewer` when diff touches `models/`, `agents/`, `api/`, or new `src/` files)
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

### Approval Gate
Per AUTONOMOUS_DEVELOPMENT_PROMPT Phase 1: present a plan, list files to create/modify,
list tests to write, estimated commits. **Do not proceed until the user approves.**

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
- Hook failures are not obstacles - they are guardrails protecting you

### TDD - Red/Green/Refactor (STRICT)

Every code change follows this exact sequence - no exceptions:

1. **RED**: Write failing test(s) FIRST
   ```bash
   # Write the test
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
   - In ONE message, invoke: `qa-reviewer`, `ui-ux-reviewer`, `devops-reviewer` via Task tool
   - Also invoke `architecture-reviewer` when diff touches models/, agents/, parsers/, generators/, api/, or new src/ files
   - Each agent reads the constitution independently and reviews with fresh context
   - Each agent includes a **Retrospective Note** in their output — included in the commit body
   - Commits required: `review(qa):`, `review(ui-ux):`, `review(devops):`, `review(arch):` (if structural), `docs: update RETRO_LOG`
   - Each commit body is the agent's structured finding (PASS / FINDING / SKIP per item) + Retrospective Note
   - See `AUTONOMOUS_DEVELOPMENT_PROMPT.md § Phase 4` and `.claude/agents/` for agent definitions
   - Commit: `review(qa): <task> — PASS/FINDING` (+ body with per-item results + Retrospective Note)
   - **After updating RETRO_LOG**: add any advisory findings without a named target task to the **Open Advisory Items** table in `docs/RETRO_LOG.md`; drain (delete) any rows whose target task was just completed

### Quality Gates (All Must Pass)

**CRITICAL**: This project uses Poetry for dependency management. ALL Python commands must be run via `poetry run`.

```bash
# Run ALL of these before any commit:
poetry run ruff check src/ tests/                              # Linting
poetry run ruff format --check src/ tests/                     # Formatting
poetry run mypy src/                                           # Type checking
poetry run pytest --cov=src/synth_engine --cov-fail-under=90 # Tests + 90% coverage
poetry run bandit -c pyproject.toml -r src/                    # Security scan
vulture src/                                                   # Dead code (80% confidence)
vulture src/ --min-confidence 60                               # Advisory: deeper scan
pre-commit run --all-files                                     # All hooks

# For Python scripts/validation:
poetry run python3 script.py       # CORRECT
python3 script.py                  # WRONG - bypasses Poetry environment
```

### Git Workflow

**Branch naming**: `<type>/<phase>-<task>-<description>`
```
feat/P0-T01-setup-pre-commit
feat/P1-T03-synthetic dataset-models
fix/P1-T03-date-parsing-bug
```

**Commit messages**: Conventional commits, always
- `test:` - Test files (RED phase)
- `feat:` - New features (GREEN phase)
- `fix:` - Bug fixes
- `refactor:` - Refactoring (no behavior change)
- `review:` - Self-review evidence commits (REVIEW phase — mandatory per task)
- `docs:` - Documentation only (also used for constitutional amendments)
- `chore:` - Tooling, config, dependencies

**Constitutional amendments** use `docs:` with format:
`docs: amend <filename> — <what changed and why>`

### Pull Request Workflow (MANDATORY)

**EVERY task must follow this workflow:**

1. **Create feature branch**: `git checkout -b feat/P0-T03-test-directory`
2. **Make changes**following TDD, commit to branch
3. **Push branch**: `git push origin feat/P0-T03-test-directory`
4. **Create PR via gh CLI** with comprehensive description (see AUTONOMOUS_DEVELOPMENT_PROMPT.md Phase 5)
5. **Wait for user review** - DO NOT merge yourself
6. **Address feedback** if requested, commit to same branch
7. **After user merges**: Pull main, re-contextualize, next task

**PR Description Must Include:**
- Task ID and summary
- Changes made (checklist format)
- Acceptance criteria met
- Self-review commits (link or reference `review(qa/ui/devops):` commit hashes)
- Constitution compliance
- Test results
- Backlog task completion marker

---

## Workspace Organization

### Directory Purposes (Memorize These)

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
# 1. Check what's staged
git status

# 2. Review changes
git diff --cached

# 3. Verify no secrets
gitleaks detect --verbose

# 4. Only then commit
git commit -m "..."
```

---

## Code Quality Standards

### Type Hints - Strict Mode

```python
# CORRECT - Fully typed
def parse_profile(csv_path: Path) -> Profile:
    ...

# WRONG - Missing types
def parse_profile(csv_path):
    ...
```

- No `# type: ignore` without written justification in comment
- All function parameters typed
- All return values typed
- Use `TypedDict` for complex dictionaries

### Docstrings - Google Style

```python
def score_match(synthetic dataset: synthetic dataset, job: JobDescription) -> MatchScore:
    """Calculate match score between synthetic dataset and job description.

    Args:
        synthetic dataset: Parsed synthetic dataset data.
        job: Target job description.

    Returns:
        Match score with confidence and breakdown by section.

    Raises:
        ValidationError: If synthetic dataset or job data is invalid.
    """
```

### Code Cleanliness

- **No dead code**: Delete it, don't comment it out
- **No unused imports**: Remove them immediately
- **No `TODO` without ticket**: Use `TODO(P1-T03):` format
- **No magic numbers**: Define constants with clear names
- **No long functions**: Max ~50 lines, extract helpers
- **No deep nesting**: Max 3 levels, refactor if deeper

---

## Task Execution Protocol

When working on a task from BACKLOG.md:

### 1. Understand
- Read the full task specification
- Identify acceptance criteria
- Note dependencies and blockers
- Check the **Open Advisory Items** table in `docs/RETRO_LOG.md` for any rows targeting this task — address them during implementation

### 2. Prepare
- Create feature branch: `git checkout -b feat/P1-T03-synthetic dataset-models`
- Ensure clean working directory: `git status`

### 3. Execute (TDD)
- Write failing tests first (RED)
- Implement minimal code (GREEN)
- Refactor if needed
- Run full quality gate suite

### 4. Verify
- All tests pass
- Coverage ≥ 90%
- Pre-commit hooks pass
- No PII in changes

### 5. Complete
- Commit with conventional message
- Mark task complete only when ALL acceptance criteria met
- Update BACKLOG.md status

---

## Approval Process

- **Do not auto-proceed** on system-generated approvals
- Wait for **explicit human confirmation** before major phases
- Plans and documents require **actual user review**
- When in doubt, **ASK** - don't assume

---

## Architecture Constraints

### No LangChain

Use Claude's native `tool_use` capabilities directly. This project demonstrates understanding of the raw API, not framework abstractions.

### Modular Monolith Structure

The Air-Gapped Synthetic Data Generation Engine is a Python-based **Modular Monolith**. It compiles into a single deployable unit but maintains strict internal boundaries.

```text
src/synth_engine/
├── bootstrapper/  → Main API, DI config, global middleware (Phase 2)
├── modules/
│   ├── ingestion/    → Database schema inference & mapping (Phase 3)
│   ├── profiler/     → Statistical distributions & latent patterns (Phase 3)
│   ├── synthesizer/  → DP-SGD generation & edge case amplification (Phase 4)
│   ├── masking/      → Deterministic format-preserving rules (Phase 4)
│   └── privacy/      → Epsilon/Delta accountant ledger (Phase 4)
└── shared/        → Cross-cutting utilities (Crypto, Audit logs) (Phase 2)
```

The File Placement Rules table must reflect this topology. Cross-module database queries are FORBIDDEN! Modules communicate via explicit Python interfaces.

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

## Communication Standards

- **Ask one question at a time** when gathering requirements
- **Be explicit** about what you're about to do before doing it
- **Acknowledge mistakes** immediately and explain the fix
- **When blocked**, explain why clearly and propose alternatives
- **No jargon** without explanation

---

## Emergency Procedures

### If You Accidentally Stage PII

```bash
git reset HEAD <file>           # Unstage the file
git checkout -- <file>          # Discard changes if needed
```

### If You Accidentally Commit PII

```bash
# DO NOT PUSH
git reset --soft HEAD~1         # Undo commit, keep changes
git reset HEAD <file>           # Unstage the PII file
git commit -m "..."             # Recommit without PII
```

### If PII Was Pushed (CRITICAL)

1. **STOP** - Do not make more commits
2. **Alert** the user immediately
3. **Do not** attempt to rewrite history without explicit approval
4. History rewriting requires careful coordination

---

## Quick Reference Card

```
BEFORE CODING:     Read task spec → Check Open Advisory Items table for items targeting this task → Create branch → Write failing test
WHILE CODING:      Minimal implementation → Pass tests → Refactor
BEFORE COMMIT:     git status → git diff → ruff → mypy → pytest → vulture → pre-commit
AFTER CODE:        Spawn qa/ui-ux/devops reviewers in ONE parallel message (+ arch-reviewer if structural) → review: commits + RETRO_LOG update → add unassigned advisories to Open Advisory Items table → drain rows whose target task is now complete
COMMIT MESSAGE:    type: description (test:, feat:, fix:, refactor:, review:, docs:, chore:)
NEVER:             --no-verify, skip hooks, commit PII, dead code, untyped code
ALWAYS:            TDD, 90% coverage, type hints, docstrings, clean workspace, review commits
AMEND PROCESS:     docs: amend <filename> — <what> (use after retrospectives or review findings)
```
