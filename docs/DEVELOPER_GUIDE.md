# Conclave Engine — Human Developer Guide

> **Amendment (Phase 56):** File paths updated to reflect synthesizer sub-package decomposition.

**Audience**: Engineer onboarding to this codebase for the first time.

**Purpose**: (a) How does this software work, and (b) how does the AI orchestration pipeline work — and how do you run it manually without AI?

This guide reflects the system after Phase 64. All file paths, commands, and module references are verified against the actual codebase.

---

## Table of Contents

1. [Environment Setup](#1-environment-setup)
2. [Project Architecture](#2-project-architecture)
3. [Running Quality Gates](#3-running-quality-gates)
4. [TDD Workflow](#4-tdd-workflow)
5. [Adding a New Feature](#5-adding-a-new-feature)
6. [Adding a New Module](#6-adding-a-new-module)
7. [AI Orchestration Pipeline](#7-ai-orchestration-pipeline)
8. [Operating Without AI](#8-operating-without-ai)
9. [Critical Invariants](#9-critical-invariants)
10. [Key Files Reference](#10-key-files-reference)
11. [Conditional Imports](#11-conditional-imports)
12. [Import Map — Canonical Paths](#12-import-map--canonical-paths)

---

## 1. Environment Setup

### Python and Poetry

- **Python 3.14** exactly (`python = "^3.14"` in `pyproject.toml`)
- **Poetry 2.2.1** for dependency management. All Python commands must use `poetry run`.

```bash
curl -sSL https://install.python-poetry.org | python3 -
poetry --version                                    # must be 2.2.1
poetry install --with dev,integration               # standard dev setup
poetry install --with dev,integration,synthesizer   # adds GPU/CTGAN/Opacus
```

The `integration` group requires `libpq` on the host. macOS: `brew install postgresql`.

### Pre-commit Hooks

Hooks are mandatory and must never be bypassed. Install once after cloning:

```bash
pre-commit install
```

| Hook | Tool | What it checks |
|------|------|----------------|
| `poetry-check` | Poetry | `pyproject.toml` / `poetry.lock` drift |
| `gitleaks` | gitleaks | Committed secrets / auth material |
| `detect-secrets` | detect-secrets | Secrets against `.secrets.baseline` |
| `ruff` | ruff | Linting (auto-fix enabled) |
| `ruff-format` | ruff | Code formatting |
| `mypy` | mypy | Strict type checking |
| `bandit` | bandit | Security scanning |
| `import-linter` | import-linter | Module boundary contracts |

Install gitleaks separately: https://github.com/gitleaks/gitleaks/releases

### Docker Compose

Base services: `docker-compose.yml`. Dev overrides (hot-reload, Jaeger): `docker-compose.override.yml`.

**Provision Docker secrets before first run:**

```bash
mkdir -p secrets
openssl rand -hex 32 > secrets/app_secret_key.txt
openssl rand -hex 32 > secrets/postgres_password.txt
openssl rand -hex 32 > secrets/grafana_admin_password.txt
echo "conclave-admin"  > secrets/grafana_admin_user.txt
openssl rand -hex 16  > secrets/minio_ephemeral_access_key.txt
openssl rand -hex 32  > secrets/minio_ephemeral_secret_key.txt
chmod 600 secrets/*.txt
```

```bash
docker compose up                   # production-equivalent
docker compose --profile dev up     # with hot-reload, Jaeger UI, local MinIO
```

| Service | Port | Purpose |
|---------|------|---------|
| app | 8000 | FastAPI REST API |
| grafana | 3000 | Grafana dashboards |
| jaeger (dev) | 16686 | Distributed trace UI |
| minio (dev) | 9000/9001 | S3-compatible object store |

### Environment Variables and Make

Copy `.env.example` to `.env` and fill in values. Never commit `.env`. The dev MinIO service uses `.env.dev`.

```bash
make              # show available targets
make build        # build the conclave-engine Docker image
make build-airgap-bundle  # create offline-deployable tar.gz bundle
make ci-local     # run all local CI gates
```

---

## 2. Project Architecture

### Modular Monolith

Conclave Engine is a **Python Modular Monolith** — a single deployable unit with strict internal separation. Chosen over microservices for air-gapped deployments (see `ADR-0001`).

```
src/synth_engine/
├── bootstrapper/          # API entrypoint, DI, middleware, routers, schemas
│   ├── main.py            # FastAPI application factory
│   ├── cli.py             # Click CLI (conclave-subset entrypoint)
│   ├── config_validation.py
│   ├── dependencies/      # FastAPI Depends() providers (auth, db, vault, CSP, licensing)
│   ├── errors.py          # RFC 7807 error middleware
│   ├── factories.py       # Factory functions for dependency-injected components
│   ├── lifecycle.py       # Application startup/shutdown and vault-unseal routes
│   ├── middleware.py      # Security and observability middleware
│   ├── router_registry.py # Declarative router registration
│   ├── routers/           # Route handlers (connections, jobs, licensing, privacy, security, settings)
│   ├── schemas/           # Pydantic v2 request/response models
│   └── sse.py             # Server-Sent Events streaming helper
│
├── modules/               # Business domain modules — mutually independent
│   ├── ingestion/         # PostgreSQL connection adapter and schema validators
│   ├── mapping/           # Schema reflection and FK graph construction
│   ├── masking/           # Format-Preserving Encryption (FPE) and deterministic masking
│   ├── privacy/           # Differential Privacy epsilon/delta budget accountant
│   ├── profiler/          # Statistical distribution profiling
│   ├── subsetting/        # Referentially-intact data subsetting and egress
│   └── synthesizer/       # CTGAN synthesis with Opacus DP-SGD
│
└── shared/                # Cross-cutting utilities (no business logic)
    ├── auth/              # JWT creation, validation, and scope definitions
    ├── db.py              # Async SQLAlchemy engine and session factory
    ├── errors.py          # Shared error value objects
    ├── exceptions.py      # Base exception hierarchy
    ├── middleware/        # Idempotency key middleware
    ├── protocols.py       # Abstract interfaces (storage backend, etc.)
    ├── schema_topology.py # Cross-module data contract (SchemaTopology frozen dataclass)
    ├── security/          # ALE crypto, audit logger, HMAC signing, license, vault, key rotation
    ├── task_queue.py      # Huey task queue singleton
    ├── tasks/             # Background reaper task
    └── telemetry.py       # OpenTelemetry tracer setup
```

### Module Responsibilities

| Module | Responsibility | Key Files |
|--------|----------------|-----------|
| `bootstrapper` | FastAPI app, DI wiring, middleware, route handlers, CLI | `main.py`, `cli.py`, `lifecycle.py`, `factories.py` |
| `modules/ingestion` | PostgreSQL connection validation and adapter | `postgres_adapter.py`, `validators.py` |
| `modules/mapping` | SQLAlchemy schema reflection, FK graph DAG | `reflection.py`, `graph.py` |
| `modules/masking` | FPE-based deterministic masking, Luhn-valid card masking | `deterministic.py`, `algorithms.py`, `registry.py`, `luhn.py` |
| `modules/privacy` | Epsilon/delta accounting, DP engine, privacy ledger | `accountant.py`, `dp_engine.py`, `ledger.py` |
| `modules/profiler` | Statistical column profiling (distributions, nullability) | `profiler.py`, `models.py` |
| `modules/subsetting` | FK-traversal subsetting, CSV egress | `core.py`, `traversal.py`, `egress.py` |
| `modules/synthesizer` | CTGAN model training, DP-SGD discriminator loop, job orchestration | `engine.py`, `dp_training.py`, `dp_discriminator.py`, `job_orchestration.py` |
| `shared` | Auth (JWT), DB session, crypto (ALE, HMAC, vault), audit, telemetry | `security/`, `auth/`, `db.py`, `schema_topology.py` |

### Import Boundary Contracts

Boundaries are enforced at every `git commit` by `import-linter`. Contracts are in `pyproject.toml` under `[tool.importlinter.contracts]`:

1. **Module independence**: `ingestion`, `mapping`, `profiler`, `masking`, `synthesizer`, and `privacy` are fully independent — no cross-module imports.
2. **Subsetting exception**: `subsetting` may import from `mapping` (FK DAG) but no other module and not from `bootstrapper`.
3. **No upward imports**: All modules and `shared` are forbidden from importing `bootstrapper`. Flow is strictly: `bootstrapper` → `modules` → `shared`.
4. **Shared isolation**: `shared` must not import from any module or from `bootstrapper`.

```python
# FORBIDDEN — cross-module import
from synth_engine.modules.ingestion import postgres_adapter  # in masking/deterministic.py
```

```bash
poetry run lint-imports   # verify manually
```

### Cross-Module Communication and Wiring

Modules communicate only through Python interfaces in `shared/protocols.py` or shared frozen dataclasses in `shared/` (e.g., `SchemaTopology`). Cross-module database queries are **forbidden** — if module A needs data owned by module B, the bootstrapper passes it as a function argument.

`bootstrapper/factories.py` constructs and injects module instances. `bootstrapper/dependencies/` provides `Depends()` providers per-request. New modules and injectable components are wired here, not in the module itself.

### Database Access

Two patterns (ADR-0035):

- **Async (`asyncpg`)**: API request handlers, via `AsyncSession` from `shared/db.py`. DSN: `postgresql+asyncpg://`.
- **Sync (`psycopg2`)**: Background Huey tasks and CLI commands where asyncio is unavailable.

Schema migrations: **Alembic**, files in `alembic/versions/`.

### Background Tasks

Long-running synthesis jobs are queued via **Huey** (Redis-backed), singleton in `shared/task_queue.py`. Task functions live in `modules/synthesizer/jobs/tasks.py` and `shared/tasks/reaper.py`. Results stream to clients via SSE (`bootstrapper/sse.py` and the `jobs_streaming` router).

---

## 3. Running Quality Gates

All gates must pass before merge. GitHub Actions CI is active. Run locally with `make ci-local` or individually:

```bash
make ci-local                   # all core + optional stages
bash scripts/ci-local.sh --continue lint test   # only lint + test, collect all failures
```

### Gate Commands

```bash
# Linting
poetry run ruff check src/ tests/
poetry run ruff format --check src/ tests/
poetry run ruff check --fix src/ tests/   # auto-fix linting (not formatting)

# Type checking (strict, src/ only)
poetry run mypy src/

# Security scanning
poetry run bandit -c pyproject.toml -r src/

# Dead code
vulture src/ .vulture_whitelist.py --min-confidence 60

# Module boundaries
poetry run lint-imports

# Dependency vulnerabilities
poetry run pip-audit

# Unit tests (95% coverage required, zero-warning policy)
poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95 -W error

# Integration tests (separate gate — requires live PostgreSQL via pytest-postgresql)
poetry run pytest tests/integration/ -v

# All hooks
pre-commit run --all-files
```

Notes:
- `# type: ignore` requires a written justification in a comment immediately above it.
- `.vulture_whitelist.py` contains documented false-positive suppressions.
- Integration tests require `pg_ctl` on `PATH` (`brew install postgresql`). Unit mocks do not satisfy this gate.

### Test Markers

```bash
poetry run pytest tests/ -m "not synthesizer" -v
```

Synthesizer tests (`@pytest.mark.synthesizer`) require the `synthesizer` group and GPU or `FORCE_CPU=true`.

---

## 4. TDD Workflow

TDD is mandatory and non-negotiable (Constitution Priority 3).

### RED — Write a failing test first

Before any production code, write a test that fails because the implementation does not exist. It must fail for the right reason (`ImportError` or `AssertionError`, not a syntax error).

```python
# tests/unit/test_masking_hash_email.py
def test_hash_email_produces_deterministic_output() -> None:
    from synth_engine.modules.masking.algorithms import hash_email
    assert hash_email("alice@example.com", key=b"testkey") == hash_email("alice@example.com", key=b"testkey")

def test_hash_email_output_is_not_original() -> None:
    from synth_engine.modules.masking.algorithms import hash_email
    assert hash_email("alice@example.com", key=b"testkey") != "alice@example.com"
```

```bash
poetry run pytest tests/unit/test_masking_hash_email.py -v   # confirm failure
git commit -m "test: add failing tests for hash_email masking algorithm"
```

### GREEN — Write the minimum code to pass

Implement only what the failing tests require. Place code in the correct module file.

```bash
poetry run pytest tests/unit/test_masking_hash_email.py -v   # confirm passing
git commit -m "feat: implement hash_email masking algorithm"
```

### REFACTOR — Clean up without changing behavior

Improve names, add Google-style docstrings, tighten type annotations, remove duplication. Re-run tests after each change.

```bash
poetry run pytest tests/unit/ -v --tb=short
git commit -m "refactor: improve hash_email readability and type annotations"
```

### Pre-RED Checklist

For each public method being added:

- Happy path: one test per acceptance criterion item
- Error paths: one test per exception the function can raise
- Edge cases: `None` inputs, empty collections, zero/boundary values, malformed inputs
- Security-critical inputs: any parameter reaching SQL, subprocess, or file I/O needs at least one misuse test

### Commit Type Conventions

| Prefix | When to use |
|--------|-------------|
| `test:` | Adding or updating tests (RED phase) |
| `feat:` | Production code implementing a feature (GREEN phase) |
| `fix:` | Bug fix in production code |
| `refactor:` | Code restructuring with no behavior change |
| `docs:` | Documentation changes only |
| `chore:` | Build scripts, config, tooling |
| `review:` | Consolidated commit from review agent findings |

---

## 5. Adding a New Feature

### Step 1: Create a feature branch

```bash
git checkout main && git pull origin main
git checkout -b feat/P31-T31-2-example-feature
```

### Step 2: Read the task spec

Read `docs/backlog/phase-<N>.md` in full — all four sections. Requirements in Context & Constraints are in scope even if not repeated in AC items. Check `docs/RETRO_LOG.md` for advisories targeting the task's domain.

### Step 3: Identify the correct module

Use the domain table in Section 2. Verify placement against import contracts in `pyproject.toml`. If the class's responsibility doesn't match the module name, it belongs elsewhere or in `shared/`.

### Step 4: TDD — RED, GREEN, REFACTOR

Follow Section 4. After GREEN, run all gates:

```bash
poetry run ruff check src/ tests/
poetry run ruff format --check src/ tests/
poetry run mypy src/
poetry run bandit -c pyproject.toml -r src/
poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95
```

### Step 5: Wire in the bootstrapper

If the feature introduces an injectable component:

1. Add a factory function in `bootstrapper/factories.py`.
2. Add a `Depends()` provider in `bootstrapper/dependencies/`.
3. Wire into the relevant route handler in `bootstrapper/routers/`.

Any IoC hook must be wired to a concrete implementation before the task is complete (CLAUDE.md Rule 8).

### Step 6: Add a `docs:` commit

Every PR branch must have at least one `docs:` commit (Rule 9). If nothing changed:

```bash
git commit --allow-empty -m "docs: no documentation changes required — <justification>"
```

### Step 7: Run pre-commit and push

```bash
pre-commit run --all-files
git push -u origin feat/P31-T31-2-example-feature
gh pr create --title "feat: add example feature (T31.2)" --body "..."
```

PR body must include: Task ID, changes checklist, ACs met, test results, Constitution compliance statements.

---

## 6. Adding a New Module

### Step 1: Create the directory

```bash
mkdir -p src/synth_engine/modules/reporting
touch src/synth_engine/modules/reporting/__init__.py
```

### Step 2: Add import-linter contracts

Add to the independence contract and the "no bootstrapper imports" contract in `pyproject.toml`:

```toml
[[tool.importlinter.contracts]]
name = "Module independence: ingestion, mapping, ..., reporting are independent"
type = "independence"
modules = [
    "synth_engine.modules.ingestion",
    # ... existing modules ...
    "synth_engine.modules.reporting",
]
```

```toml
[[tool.importlinter.contracts]]
name = "Modules must not import from bootstrapper"
type = "forbidden"
source_modules = [
    # ... existing modules ...
    "synth_engine.modules.reporting",
]
forbidden_modules = ["synth_engine.bootstrapper"]
```

Verify before writing code:

```bash
poetry run lint-imports
```

### Step 3: Create the test directory

```bash
mkdir -p tests/unit/reporting
touch tests/unit/reporting/__init__.py
```

### Step 4: TDD, wire in bootstrapper, shared value objects

- Write failing tests in `tests/unit/reporting/` before any implementation.
- Add the module's primary service to `bootstrapper/factories.py` and expose via `bootstrapper/dependencies/`. Add its router (if any) to `bootstrapper/router_registry.py`.
- If the module produces a data structure consumed by another module, place it in `shared/` as a frozen dataclass — not in the producing module. Example: `shared/schema_topology.py`.

### Step 5: Run all quality gates

```bash
pre-commit run --all-files
poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95
poetry run pytest tests/integration/ -v
```

---

## 7. AI Orchestration Pipeline

The entire codebase was produced by AI agents under a defined governance framework.

### The Actors

| Actor | Session type | Role |
|-------|-------------|------|
| **PM** | Main Claude Code session (reads `CLAUDE.md`) | Plans tasks, creates branches, delegates to subagents, runs reviews, creates PRs |
| **software-developer** | Subagent | Writes all code, tests, and commits. Never self-reviews. |
| **qa-reviewer** | Subagent, after GREEN | Reviews correctness, coverage, dead code, edge cases |
| **devops-reviewer** | Subagent, after GREEN | Reviews secrets hygiene, PII, security, dependency risk |
| **architecture-reviewer** | Subagent, when `src/synth_engine/` touched | Reviews module boundaries, ADR alignment |
| **ui-ux-reviewer** | Subagent, when frontend files touched | Reviews accessibility, WCAG 2.1 AA |
| **pr-describer** | Subagent, after reviews pass | Drafts the PR description |
| **pr-reviewer** | Subagent, after review agents pass | Posts GitHub approval |

Agent definitions: `.claude/agents/`.

### Governance Documents

- `CONSTITUTION.md` — absolute priorities (0–9). Security is Priority 0. No agent can override.
- `CLAUDE.md` — workflow rules, TDD mandate, quality gate commands, PII rules.
- `docs/RETRO_LOG.md` — living ledger of retrospective findings and open advisories. PM queries this before every task brief.

### Phase Execution

Work is organized into **phases** (e.g., Phase 30 = Discriminator-Level DP-SGD) containing **tasks** (e.g., T30.1). Specs live in `docs/backlog/phase-<N>.md`.

Typical phase:

1. PM reads task spec and scans `RETRO_LOG.md` for relevant failures.
2. PM creates feature branch and invokes `software-developer` with full spec + failure patterns.
3. `software-developer` executes RED → GREEN → REFACTOR and commits.
4. PM spawns `qa-reviewer` + `devops-reviewer` (always); `architecture-reviewer` (if `src/synth_engine/` touched); `ui-ux-reviewer` (if frontend touched).
5. Any FINDING must be fixed — cannot be labeled advisory and skipped.
6. PM commits all findings in one `review:` commit, updates `RETRO_LOG.md`.
7. PM spawns `pr-describer`, pushes branch, creates PR via `gh pr create`.
8. PM spawns `pr-reviewer` → `gh pr review --approve`.
9. PM merges with `gh pr merge --merge` (never `--squash` — preserves TDD audit trail per Constitution Priority 3).

### Constitutional Enforcement

| Rule | Gate |
|------|------|
| No secrets committed | `gitleaks` + `detect-secrets` in pre-commit |
| Quality gates unbreakable | `ruff`, `mypy`, `pytest`, `pre-commit` cannot be bypassed |
| TDD audit trail | `test:` commit must precede `feat:` — auditable in `git log` |
| 95%+ coverage | `pytest --cov-fail-under=95` in CI |
| Strict typing | `mypy --strict` in pre-commit |
| `docs:` commit required per PR | `docs-gate` stage in `scripts/ci-local.sh` |

---

## 8. Operating Without AI

The AI pipeline is a strict formalization of good engineering practice. To replicate it manually:

1. Read the full task spec in `docs/backlog/phase-<N>.md`.
2. Scan `docs/RETRO_LOG.md` Open Advisory Items for your task domain.
3. Follow Section 5 (Adding a New Feature) exactly. Commit order matters:

```
test: add failing tests for <feature>     ← RED
feat: implement <feature>                 ← GREEN
refactor: improve <feature>               ← REFACTOR (if needed)
docs: <update relevant documentation>     ← required per PR
review: <consolidated findings>           ← after self/peer review
```

### Self-Review Checklists

**QA** (from `.claude/agents/qa-reviewer.md`):
- Coverage ≥ 95% (`pytest --cov=src/synth_engine -q`)
- No dead code (`vulture src/ .vulture_whitelist.py --min-confidence 80`)
- Each new `except` clause: can that exception actually be raised?
- Each new public method: Happy Path test, Error Path test, at least one Edge Case test
- Docstrings describe what the function does as implemented, not aspirationally

**DevOps** (from `.claude/agents/devops-reviewer.md`):
- `gitleaks detect --verbose` before any `git push`
- Zero HIGH/MEDIUM bandit findings
- No `print()` in production code (use `logging.getLogger(__name__)`)
- New `logger.*` calls: would they log PII with real data?
- No `--no-verify` or `SKIP=` flags in the diff

**Architecture** (from `.claude/agents/architecture-reviewer.md`):
- `poetry run lint-imports` — all contracts pass
- New files in the correct module per the domain table in Section 2
- No new cross-module imports
- New ADR-numbered decisions (technology substitutions) documented in `docs/adr/`

Any FINDING from peer review must receive a fix commit before merge.

---

## 9. Critical Invariants

### PII Protection

**Never commit**: `data/`, `output/`, `logs/`, `.env`, `config.local.json`, `secrets/*.txt`

**Safe to commit**: `sample_data/`, `tests/fixtures/` (all fictional data)

`.gitignore` covers most of these; `gitleaks` and `detect-secrets` are the last line of defense.

**Emergency procedure:**

```bash
# Staged, not committed:
git reset HEAD <file>

# Committed, not pushed:
git reset --soft HEAD~1   # then remove file, re-stage, re-commit

# Pushed:
# STOP. Alert team lead. Do not rewrite history without approval.
```

### Pre-commit Hooks Are Never Bypassed

`git commit --no-verify` is forbidden. `SKIP=...` is forbidden. Fix the code if a hook fails.

### Import Boundaries Are Enforced at Commit Time

`import-linter` runs as a pre-commit hook. Rejected commits must fix the import, not add suppressions — there are none in this codebase.

### Coverage Never Drops Below 95%

`pytest --cov-fail-under=95` is enforced in CI and pre-commit. Write the missing tests before pushing.

### No `# type: ignore` Without Justification

Write the justification in a comment immediately above the suppression:

```python
# type: ignore[assignment] — third-party library returns Any; see ADR-0032
result: str = some_lib.get_value()  # type: ignore[assignment]
```

Three documented exceptions in `pyproject.toml` (`[tool.mypy.overrides]`) cover optional synthesizer dependencies (`sdv`, `ctgan`, `opacus`, `huey`) that ship without `py.typed` markers. See ADR-0032.

### Merge Strategy: Always `--merge`, Never `--squash`

Squash merges destroy the TDD audit trail (Constitution Priority 3). Always:

```bash
gh pr merge --merge <PR-number>
```

### Docker Secret Files Are Never Committed

`secrets/*.txt` is gitignored. Generate with the `openssl rand` commands in Section 1 before first `docker compose up`.

---

## 10. Key Files Reference

### Governance and Process

| File | Purpose |
|------|---------|
| `CONSTITUTION.md` | Absolute rules, priority hierarchy (0–9), enforcement table |
| `CLAUDE.md` | PM and developer agent directives, TDD mandate, quality gates, PII rules |
| `docs/RETRO_LOG.md` | Living ledger of phase retrospectives and open advisories |
| `docs/archive/BACKLOG.md` | High-level backlog overview (archived) |
| `docs/backlog/phase-<N>.md` | Per-phase task specifications |
| `docs/index.md` | Central documentation index |

### Architecture Decisions

Key ADRs for onboarding (all in `docs/adr/`):

| ADR | Decision |
|-----|----------|
| `ADR-0001` | Modular monolith over microservices |
| `ADR-0012` | PostgreSQL ingestion adapter design |
| `ADR-0013` | Relational mapping and FK graph |
| `ADR-0014` | Masking engine (FPE, deterministic) |
| `ADR-0017` | Synthesizer DP library selection (CTGAN + Opacus) |
| `ADR-0019` | AI PR review governance |
| `ADR-0029` | Architectural requirements gap analysis |
| `ADR-0031` | PgBouncer image substitution |
| `ADR-0032` | Mypy `ignore_missing_imports` for synthesizer dependencies |
| `ADR-0035` | Dual-driver DB access (asyncpg + psycopg2) |
| `ADR-0036` | Discriminator-level DP-SGD |

### Source Code Entry Points

| File | Purpose |
|------|---------|
| `src/synth_engine/bootstrapper/main.py` | FastAPI application factory — start here |
| `src/synth_engine/bootstrapper/cli.py` | Click CLI (`conclave-subset` entrypoint) |
| `src/synth_engine/bootstrapper/lifecycle.py` | App startup, shutdown, `/unseal` route |
| `src/synth_engine/bootstrapper/factories.py` | Dependency construction and wiring |
| `src/synth_engine/bootstrapper/router_registry.py` | Declarative router registration |
| `src/synth_engine/shared/db.py` | Async database session factory |
| `src/synth_engine/shared/task_queue.py` | Huey task queue singleton |

### Infrastructure

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Production service definitions |
| `docker-compose.override.yml` | Dev overrides (hot-reload, Jaeger, local MinIO) |
| `Dockerfile` | Multi-stage production image |
| `pyproject.toml` | All tool configuration (ruff, mypy, bandit, pytest, import-linter, vulture) |
| `.pre-commit-config.yaml` | Pre-commit hook definitions and versions |
| `alembic.ini` | Alembic migration configuration |
| `alembic/versions/` | Database migration files |

### Scripts

| File | Purpose |
|------|---------|
| `scripts/ci-local.sh` | Local CI runner — replicates GitHub Actions pipeline |
| `scripts/entrypoint.sh` | Docker container entrypoint (drops privileges via gosu) |
| `scripts/build_airgap.sh` | Creates offline deployment bundle |
| `Makefile` | Convenience targets (`build`, `build-airgap-bundle`, `ci-local`) |
| `.vulture_whitelist.py` | Vulture false-positive suppressions (documented) |

### Agent Definitions

| File | Purpose |
|------|---------|
| `.claude/agents/software-developer.md` | Developer agent system prompt |
| `.claude/agents/qa-reviewer.md` | QA reviewer checklist and output format |
| `.claude/agents/devops-reviewer.md` | DevOps/security reviewer checklist |
| `.claude/agents/architecture-reviewer.md` | Architecture boundary reviewer |
| `.claude/agents/ui-ux-reviewer.md` | UI/UX and accessibility reviewer |
| `.claude/agents/pr-describer.md` | PR description drafter |
| `.claude/agents/pr-reviewer.md` | GitHub approval agent |

### Operational Documentation

| File | Purpose |
|------|---------|
| `docs/OPERATOR_MANUAL.md` | Deployment, vault unseal, first-run procedure |
| `docs/PRODUCTION_DEPLOYMENT.md` | Production deployment checklist |
| `docs/DISASTER_RECOVERY.md` | Recovery procedures |
| `docs/TROUBLESHOOTING.md` | Common issues and solutions |
| `docs/LICENSING.md` | Offline license activation workflow |
| `docs/infrastructure_security.md` | LUKS volumes, network isolation, security posture |
| `docs/DEPENDENCY_AUDIT_POLICY.md` | Dependency review requirements and cadence |
| `docs/archive/E2E_VALIDATION.md` | End-to-end validation run record (archived) |

---

## 11. Conditional Imports

### Why the Pattern Exists

Optional dependency groups are not installed in all environments. `poetry install` installs only the core group. Synthesis-specific dependencies (`sdv`, `ctgan`, `opacus`, `torch`) belong to the optional `synthesizer` group:

```bash
poetry install --with synthesizer
```

Unconditional top-level imports would cause `ModuleNotFoundError` at startup in environments without the synthesizer group — breaking health checks, vault-unseal, and all other routes. The solution is **deferred conditional imports** using `try/except ImportError` at module scope, binding to `None` on failure:

```python
try:
    from sdv.single_table import CTGANSynthesizer
except ImportError:  # pragma: no cover — only triggered if synthesizer group is absent
    CTGANSynthesizer = None  # SDV not installed; synthesis unavailable

# At the call site:
if CTGANSynthesizer is None:  # pragma: no cover
    raise ImportError(
        "The 'sdv' package is required for synthesis. "
        "Install it with: poetry install --with synthesizer"
    )
```

The `# pragma: no cover` on the `except` branch is intentional — in the standard test environment (with `--with synthesizer`) this branch is never reached.

### Checking Availability at Runtime

```python
from synth_engine.modules.synthesizer.engine import CTGANSynthesizer

if CTGANSynthesizer is None:
    raise ImportError("Install the synthesizer dependency group to use this feature.")
```

The same pattern applies to `DPCompatibleCTGAN`, `PrivacyEngine`, `torch`, and `MinioStorageBackend`.

### Files Using Deferred Conditional Imports

| File | Names conditionally imported | Optional group |
|------|------------------------------|----------------|
| `modules/synthesizer/training/engine.py` | `CTGANSynthesizer` (sdv), `DPCompatibleCTGAN` | `synthesizer` |
| `modules/synthesizer/training/dp_training.py` | `CTGANSynthesizer`, `CTGAN`, `Generator`, `detect_discrete_columns`, `torch`, `nn`, `DataLoader`, `TensorDataset` | `synthesizer` |
| `modules/synthesizer/training/dp_discriminator.py` | `torch`, `nn` | `synthesizer` |
| `modules/privacy/dp_engine.py` | `PrivacyEngine` (opacus) | `synthesizer` |
| `bootstrapper/factories.py` | `MinioStorageBackend` | `synthesizer` |
| `shared/telemetry.py` | `OTLPSpanExporter` [1] | optional OTEL exporter |

> [1] `shared/telemetry.py` uses a function-scope lazy import inside `_build_exporter()`, not the module-scope `= None` pattern. The name is never bound at module scope; absence is handled at call time.

### Mypy Configuration

These libraries ship without `py.typed` markers. Exceptions declared in `pyproject.toml`:

```toml
[[tool.mypy.overrides]]
module = ["sdv.*", "ctgan.*", "opacus.*", "huey", "huey.*"]
ignore_missing_imports = true
```

See ADR-0032. Adding a new optional dependency without `py.typed` requires an entry here and documentation in ADR-0032.

### Testing Conditional Import Code

Unit tests run with the `synthesizer` group installed. `# pragma: no cover` branches are excluded from the 95% requirement. To test absent-dependency behavior, patch the module-scope name to `None`:

```python
from unittest.mock import patch

def test_train_raises_when_sdv_absent() -> None:
    with patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer", None):
        engine = SynthesisEngine()
        with pytest.raises(ImportError, match="sdv.*synthesizer"):
            engine.train("t", "/path/to/data.parquet")
```

This pattern is used in `tests/unit/synthesizer/test_engine.py`.

---

## 12. Import Map — Canonical Paths

Use these import paths when writing new code or tests. Do not import from
deprecated shim modules (`storage.models`, `jobs.tasks` re-exports); use
canonical paths directly. Shims are preserved for backward compatibility until
Phase 70.

### Application Entry Points

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `create_app` | `synth_engine.bootstrapper.main` | Bootstrapper |
| `ConclaveSettings` | `synth_engine.shared.settings` | Configuration |
| `get_settings` | `synth_engine.shared.settings` | Configuration |

### Middleware

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `RateLimitGateMiddleware` | `synth_engine.bootstrapper.dependencies.rate_limit_middleware` | Infrastructure |
| `AuthenticationGateMiddleware` | `synth_engine.bootstrapper.dependencies.auth_middleware` | Infrastructure |
| `SealGateMiddleware` | `synth_engine.bootstrapper.dependencies.vault` | Infrastructure |
| `LicenseGateMiddleware` | `synth_engine.bootstrapper.dependencies.licensing` | Infrastructure |
| `RequestBodyLimitMiddleware` | `synth_engine.bootstrapper.dependencies.request_limits` | Infrastructure |
| `CSPMiddleware` | `synth_engine.bootstrapper.dependencies.csp` | Infrastructure |
| `HTTPSEnforcementMiddleware` | `synth_engine.bootstrapper.dependencies.https_enforcement` | Infrastructure |
| `IdempotencyMiddleware` | `synth_engine.shared.middleware.idempotency` | Infrastructure |
| `RFC7807Middleware` | `synth_engine.bootstrapper.errors.middleware` | Infrastructure |

### Factory Functions (bootstrapper)

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `build_synthesis_engine` | `synth_engine.bootstrapper.factories` | Synthesizer |
| `build_dp_wrapper` | `synth_engine.bootstrapper.factories` | Privacy |
| `build_ephemeral_storage_client` | `synth_engine.bootstrapper.factories` | Synthesizer |
| `build_spend_budget_fn` | `synth_engine.bootstrapper.factories` | Privacy |

### Synthesizer

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `SynthesisEngine` | `synth_engine.modules.synthesizer.training.engine` | Synthesizer |
| `ModelArtifact` | `synth_engine.modules.synthesizer.storage.artifact` | Synthesizer |
| `RestrictedUnpickler` | `synth_engine.modules.synthesizer.storage.restricted_unpickler` | Synthesizer |
| `SynthesizerModel` | `synth_engine.modules.synthesizer.storage.restricted_unpickler` | Synthesizer |
| `EphemeralStorageClient` | `synth_engine.modules.synthesizer.storage.storage` | Synthesizer |
| `MinioStorageBackend` | `synth_engine.modules.synthesizer.storage.storage` | Synthesizer |
| `StorageBackend` | `synth_engine.modules.synthesizer.storage.storage` | Synthesizer |
| `SynthesisJob` | `synth_engine.modules.synthesizer.jobs.job_models` | Synthesizer |
| `run_synthesis_job` | `synth_engine.modules.synthesizer.jobs.tasks` | Synthesizer |
| `ErasureService` | `synth_engine.modules.synthesizer.lifecycle.erasure` | Synthesizer |
| `DeletionManifest` | `synth_engine.modules.synthesizer.lifecycle.erasure` | Synthesizer |

### Privacy

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `DPTrainingWrapper` | `synth_engine.modules.privacy.dp_engine` | Privacy |
| `PrivacyLedger` | `synth_engine.modules.privacy.ledger` | Privacy |

### Profiler

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `StatisticalProfiler` | `synth_engine.modules.profiler.profiler` | Profiler |
| `ColumnProfile` | `synth_engine.modules.profiler.models` | Profiler |
| `TableProfile` | `synth_engine.modules.profiler.models` | Profiler |
| `ProfileDelta` | `synth_engine.modules.profiler.models` | Profiler |

### Masking

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `MaskingRegistry` | `synth_engine.modules.masking.registry` | Masking |
| `ColumnType` | `synth_engine.modules.masking.registry` | Masking |

### Subsetting

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `SubsettingEngine` | `synth_engine.modules.subsetting.core` | Subsetting |
| `EgressWriter` | `synth_engine.modules.subsetting.egress` | Subsetting |
| `DagTraversal` | `synth_engine.modules.subsetting.traversal` | Subsetting |

### Ingestion & Mapping

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `SchemaReflector` | `synth_engine.modules.mapping.reflection` | Mapping |
| `DirectedAcyclicGraph` | `synth_engine.modules.mapping.graph` | Mapping |
| `PostgresIngestionAdapter` | `synth_engine.modules.ingestion.postgres_adapter` | Ingestion |
| `SchemaInspector` | `synth_engine.modules.ingestion.postgres_adapter` | Ingestion |

### Shared — Cross-Cutting

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `SchemaTopology` | `synth_engine.shared.schema_topology` | Shared |
| `ColumnInfo` | `synth_engine.shared.schema_topology` | Shared |
| `ForeignKeyInfo` | `synth_engine.shared.schema_topology` | Shared |
| `DPWrapperProtocol` | `synth_engine.shared.protocols` | Shared |
| `SpendBudgetProtocol` | `synth_engine.shared.protocols` | Shared |
| `OwnedRecordModel` | `synth_engine.shared.protocols` | Shared |
| `WebhookRegistrationProtocol` | `synth_engine.shared.protocols` | Shared |
| `SynthEngineError` | `synth_engine.shared.exceptions` | Shared |
| `BudgetExhaustionError` | `synth_engine.shared.exceptions` | Shared |
| `ArtifactTamperingError` | `synth_engine.shared.exceptions` | Shared |
| `AuditWriteError` | `synth_engine.shared.exceptions` | Shared |
| `EpsilonMeasurementError` | `synth_engine.shared.exceptions` | Shared |
| `OOMGuardrailError` | `synth_engine.shared.exceptions` | Shared |
| `VaultSealedError` | `synth_engine.shared.exceptions` | Shared |
| `VaultConfigError` | `synth_engine.shared.exceptions` | Shared |
| `LicenseError` | `synth_engine.shared.exceptions` | Shared |
| `TLSCertificateError` | `synth_engine.shared.exceptions` | Shared |
| `DatasetTooLargeError` | `synth_engine.shared.exceptions` | Shared |

### Shared — Security & Audit

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `AuditLogger` | `synth_engine.shared.security.audit_logger` | Security |
| `AuditEvent` | `synth_engine.shared.security.audit_logger` | Security |
| `get_audit_logger` | `synth_engine.shared.security.audit_singleton` | Security |
| `AnchorManager` | `synth_engine.shared.security.audit_anchor` | Security |
| `AnchorBackend` | `synth_engine.shared.security.audit_anchor` | Security |
| `LocalFileAnchorBackend` | `synth_engine.shared.security.audit_anchor` | Security |
| `VaultState` | `synth_engine.shared.security.vault` | Security |
| `LicenseState` | `synth_engine.shared.security.licensing` | Security |

### Shared — Database & Settings

| Symbol | Canonical Import | Domain |
|--------|-----------------|--------|
| `get_engine` | `synth_engine.shared.db` | Database |
| `get_async_engine` | `synth_engine.shared.db` | Database |
| `get_session` | `synth_engine.shared.db` | Database |
| `get_tracer` | `synth_engine.shared.telemetry` | Observability |

### Rate Limiting (with deduplication note)

| Symbol | Canonical Import | Notes |
|--------|-----------------|-------|
| `RateLimitGateMiddleware` | `synth_engine.bootstrapper.dependencies.rate_limit_middleware` | Canonical |
| `RateLimitGateMiddleware` | `synth_engine.bootstrapper.dependencies.rate_limit` | Backward-compat re-export (shim until Phase 70) |
| `_extract_client_ip` | `synth_engine.bootstrapper.dependencies.rate_limit` | Config helper |
| `_extract_operator_id` | `synth_engine.bootstrapper.dependencies.rate_limit` | Config helper |
| `_redis_hit` | `synth_engine.bootstrapper.dependencies.rate_limit_backend` | Backend primitive |
