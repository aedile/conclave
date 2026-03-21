# Conclave Engine — Human Developer Guide

**Audience**: A human engineer onboarding to this codebase for the first time.

**Purpose**: Answer two questions comprehensively: (a) how does this software work, and
(b) how does the AI orchestration pipeline that produced it work — and how do you run that
pipeline manually without AI?

This guide describes the system as it exists after Phase 37. Every file path, command, and
module reference has been verified against the actual codebase.

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

---

## 1. Environment Setup

### Python Version

The project requires **Python 3.14** exactly. The constraint is declared in `pyproject.toml`:

```toml
python = "^3.14"
```

Verify with:

```bash
python3 --version   # must output Python 3.14.x
```

### Poetry

This project uses **Poetry 2.2.1** for dependency management and virtual environment control.
All Python commands must be invoked via `poetry run` — never with a naked `python` or
`pytest` call — to ensure the correct virtual environment is used.

```bash
# Install Poetry if not present
curl -sSL https://install.python-poetry.org | python3 -

# Verify
poetry --version   # must output Poetry (version 2.2.1)

# Install all development dependencies
poetry install --with dev,integration

# Install synthesizer dependencies (GPU/CTGAN/Opacus) — optional, needed for synthesis tests
poetry install --with dev,integration,synthesizer
```

The `integration` group adds `pytest-postgresql` and requires `libpq` (PostgreSQL client
libraries) on the host. On macOS: `brew install postgresql`.

### Pre-commit Hooks

Pre-commit hooks are mandatory and must never be bypassed. Install them once after cloning:

```bash
pre-commit install
```

The hooks defined in `.pre-commit-config.yaml` run on every `git commit`:

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

Gitleaks must be installed separately: https://github.com/gitleaks/gitleaks/releases

### Docker and Docker Compose

The full stack runs with Docker Compose. The base services are defined in `docker-compose.yml`
and developer overrides (hot-reload, Jaeger tracing UI) in `docker-compose.override.yml`.

**Before running `docker compose up` for the first time**, provision Docker secrets:

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

Start the stack:

```bash
# Production-equivalent (no hot-reload)
docker compose up

# Development with hot-reload, Jaeger UI, and local MinIO
docker compose --profile dev up
```

Service ports exposed to the host:

| Service | Port | Purpose |
|---------|------|---------|
| app | 8000 | FastAPI REST API |
| grafana | 3000 | Grafana dashboards |
| jaeger (dev) | 16686 | Distributed trace UI |
| minio (dev) | 9000/9001 | S3-compatible object store |

### Environment Variables

Copy `.env.example` to `.env` and fill in values. The `.env` file is gitignored and must
never be committed. The `docker-compose.override.yml` uses `.env.dev` for the dev MinIO
service.

### Make Targets

```bash
make              # show available targets
make build        # build the conclave-engine Docker image
make build-airgap-bundle  # create offline-deployable tar.gz bundle
make ci-local     # run all local CI gates
```

---

## 2. Project Architecture

### Modular Monolith

Conclave Engine is a **Python Modular Monolith** — a single deployable unit with strict
internal logical separation. This design was chosen deliberately over microservices for
air-gapped deployments (see `docs/adr/ADR-0001-modular-monolith-topology.md`).

The source tree under `src/synth_engine/` has three tiers:

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

Module boundaries are enforced at every `git commit` by `import-linter`. The contracts are
declared in `pyproject.toml` under `[tool.importlinter.contracts]`:

1. **Module independence**: `ingestion`, `mapping`, `profiler`, `masking`, `synthesizer`,
   and `privacy` are fully independent of each other. No cross-module imports are allowed.
2. **Subsetting exception**: `subsetting` may import from `mapping` (it needs the FK DAG)
   but must not import from any other module or from `bootstrapper`.
3. **No upward imports**: All modules and `shared` are forbidden from importing
   `bootstrapper`. Dependency flow is strictly downward: `bootstrapper` → `modules` → `shared`.
4. **Shared isolation**: `shared` must not import from any module or from `bootstrapper`.

Violation example — the following would fail the `import-linter` hook at commit time:

```python
# src/synth_engine/modules/masking/deterministic.py
from synth_engine.modules.ingestion import postgres_adapter  # FORBIDDEN — cross-module import
```

To verify boundaries manually:

```bash
poetry run lint-imports
```

### Cross-Module Communication

Modules communicate only through Python interfaces defined in `shared/protocols.py` or
through shared frozen dataclasses in `shared/` (e.g., `SchemaTopology`). The bootstrapper
wires everything together in `factories.py` and `dependencies/`.

Cross-module database queries are **forbidden**. If module A needs data owned by module B,
it receives it as a function argument provided by the bootstrapper — not by querying B's
tables directly.

### The Bootstrapper as the Wiring Layer

`bootstrapper/factories.py` is where module instances are constructed and injected.
`bootstrapper/dependencies/` provides FastAPI `Depends()` providers that resolve those
instances per-request. When adding a new module or a new injectable component, this is where
the wiring goes — not in the module itself.

### Database Access

The engine uses two database access patterns (ADR-0035):

- **Async (`asyncpg`)**: Used in API request handlers via `AsyncSession` from
  `shared/db.py`. DSN scheme: `postgresql+asyncpg://`.
- **Sync (`psycopg2`)**: Used in background Huey tasks and CLI commands where asyncio is
  not available.

The `shared/db.py` exports `get_async_session` as a FastAPI dependency and
`create_sync_engine` for synchronous contexts.

Schema migrations are managed with **Alembic**. Migration files live in `alembic/versions/`.

### Background Tasks

Long-running synthesis jobs are queued via **Huey** (Redis-backed). The Huey singleton is
defined in `shared/task_queue.py`. Task functions live in `modules/synthesizer/tasks.py`
and `shared/tasks/reaper.py`. Results are streamed to clients via Server-Sent Events (SSE)
defined in `bootstrapper/sse.py` and the `jobs_streaming` router.

---

## 3. Running Quality Gates

All quality gates must pass before any code is merged. GitHub Actions is offline until
2026-03-31 due to budget constraints; run all gates locally. The `make ci-local` target
runs them all in CI order:

```bash
make ci-local                   # run all core + optional stages
bash scripts/ci-local.sh        # equivalent, with more control options
bash scripts/ci-local.sh --continue lint test   # run only lint + test, collect all failures
```

### Individual Gate Commands

Each gate must be run via `poetry run`. Run from the repository root.

**Linting** — ruff enforces PEP 8, import order, and project-specific rules:

```bash
poetry run ruff check src/ tests/
poetry run ruff format --check src/ tests/
```

To auto-fix linting issues (not formatting):

```bash
poetry run ruff check --fix src/ tests/
```

**Type checking** — mypy runs in strict mode on `src/synth_engine` only:

```bash
poetry run mypy src/
```

No `# type: ignore` comments are allowed without a written justification in a comment
immediately above the suppression.

**Security scanning** — bandit scans `src/` and `scripts/`:

```bash
poetry run bandit -c pyproject.toml -r src/
```

The `[tool.bandit]` section in `pyproject.toml` documents which rules are skipped and why.

**Dead code detection** — vulture scans `src/` against the whitelist:

```bash
vulture src/ .vulture_whitelist.py --min-confidence 60
```

The `.vulture_whitelist.py` at the repository root contains documented false-positive
suppressions. If vulture flags a real function as dead, either remove the dead code or add
a suppression entry with a comment explaining the runtime call site.

**Module boundary enforcement** — import-linter checks the contracts in `pyproject.toml`:

```bash
poetry run lint-imports
```

**Dependency vulnerability scan** — pip-audit scans installed packages:

```bash
poetry run pip-audit
```

**Unit tests** — mocks allowed, zero-warning policy enforced:

```bash
poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95 -W error
```

The coverage threshold is **95%**. Branch coverage is enabled in `pyproject.toml`
(`branch = true`). If coverage drops below 95%, the build fails and the diff must include
new tests before merge.

**Integration tests** — require a live PostgreSQL instance (via `pytest-postgresql`):

```bash
poetry run pytest tests/integration/ -v
```

These are a **separate gate** from unit tests. A unit test using mocks does not satisfy an
integration test requirement. Install `pytest-postgresql` via:

```bash
poetry install --with integration
```

The `pytest-postgresql` fixture spins up and tears down a real PostgreSQL process for each
test session. `pg_ctl` must be on `PATH` (install via Homebrew: `brew install postgresql`).

**All hooks together** — run the full pre-commit suite against all files:

```bash
pre-commit run --all-files
```

### Test Markers

Tests are marked with `@pytest.mark.unit`, `@pytest.mark.integration`, or
`@pytest.mark.synthesizer`. Run a specific marker subset:

```bash
poetry run pytest tests/ -m "not synthesizer" -v
```

Synthesizer tests require the `synthesizer` dependency group (`sdv`, `torch`, `opacus`) and
GPU or `FORCE_CPU=true`.

---

## 4. TDD Workflow

Test-Driven Development is mandatory and non-negotiable (Constitution Priority 3).

### The Three Phases

**RED — Write a failing test first.**

Before writing a single line of production code, write a test that defines the requirement
and fails because the implementation does not exist yet. The test must fail for the right
reason — an `ImportError` or an `AssertionError` — not a syntax error.

Example: adding a new masking algorithm called `hash_email`:

```bash
# Create the test file first
# tests/unit/test_masking_hash_email.py

def test_hash_email_produces_deterministic_output() -> None:
    from synth_engine.modules.masking.algorithms import hash_email
    result1 = hash_email("alice@example.com", key=b"testkey")
    result2 = hash_email("alice@example.com", key=b"testkey")
    assert result1 == result2

def test_hash_email_output_is_not_original() -> None:
    from synth_engine.modules.masking.algorithms import hash_email
    result = hash_email("alice@example.com", key=b"testkey")
    assert result != "alice@example.com"
```

Run the test to confirm it fails:

```bash
poetry run pytest tests/unit/test_masking_hash_email.py -v
```

Commit the failing test:

```bash
git add tests/unit/test_masking_hash_email.py
git commit -m "test: add failing tests for hash_email masking algorithm"
```

**GREEN — Write the minimum code to make the test pass.**

Implement only what is needed to make the failing test pass. Do not add functionality that
is not yet tested. Place the new code in the correct module file — in this example,
`src/synth_engine/modules/masking/algorithms.py`.

Run the tests to confirm they pass:

```bash
poetry run pytest tests/unit/test_masking_hash_email.py -v
```

Commit the implementation:

```bash
git add src/synth_engine/modules/masking/algorithms.py
git commit -m "feat: implement hash_email masking algorithm"
```

**REFACTOR — Clean up without changing behavior.**

After tests pass, clean up: improve variable names, add a Google-style docstring, tighten
type annotations, remove duplication. Run all tests again after each change to ensure
nothing broke.

```bash
poetry run pytest tests/unit/ -v --tb=short
```

Commit the refactor if substantive:

```bash
git commit -m "refactor: improve hash_email readability and type annotations"
```

### TDD Checklist Before Committing RED

For each public method (no leading underscore) being added:

- Happy path: at least one test per acceptance criterion item.
- Error paths: at least one test for each exception the function can raise.
- Edge cases: `None` inputs, empty collections, zero/boundary values, malformed inputs.
- Security-critical inputs: any parameter that reaches SQL, subprocess, or file I/O needs
  at least one misuse test.

### Commit Type Conventions

| Prefix | When to use |
|--------|-------------|
| `test:` | Adding or updating tests (RED phase) |
| `feat:` | Production code that implements a feature (GREEN phase) |
| `fix:` | Bug fix in production code |
| `refactor:` | Code restructuring with no behavior change |
| `docs:` | Documentation changes only |
| `chore:` | Build scripts, config, tooling |
| `review:` | Consolidated commit from review agent findings |

---

## 5. Adding a New Feature

This is the step-by-step process for adding a feature end-to-end, following the same
workflow as the AI agents.

### Step 1: Create a Feature Branch

Branch naming convention: `<type>/<phase>-<task>-<brief-description>`

```bash
git checkout main
git pull origin main
git checkout -b feat/P31-T31-2-example-feature
```

### Step 2: Read the Relevant Backlog Task

Every task is in `docs/backlog/phase-<N>.md`. Read it in full — all four sections:
Context & Constraints, Acceptance Criteria, Testing & Quality Gates, and Files to
Create/Modify. Requirements stated in Context & Constraints are in scope even if they are
not repeated in the AC items.

Check `docs/RETRO_LOG.md` Open Advisory Items for any rows targeting the current task.

### Step 3: Check the Open Advisory Table

```bash
head -30 /path/to/docs/RETRO_LOG.md
```

If any open advisory targets the task's domain, address it during implementation.

### Step 4: Identify the Correct Module

Using the domain table from Section 2, identify which module directory owns the new
functionality. Verify the placement does not violate import boundary contracts by checking
the contracts in `pyproject.toml` under `[tool.importlinter.contracts]`.

Ask: does this class's responsibility match the module name? If not, the file belongs in a
different module or in `shared/`.

### Step 5: TDD — RED, GREEN, REFACTOR

Follow the TDD workflow described in Section 4. Run each gate after the GREEN phase:

```bash
poetry run ruff check src/ tests/
poetry run ruff format --check src/ tests/
poetry run mypy src/
poetry run bandit -c pyproject.toml -r src/
poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95
```

Fix any failures before proceeding.

### Step 6: Wire the Feature in the Bootstrapper

If the new feature introduces an injectable component (a new service, engine, or dependency):

1. Add a factory function in `bootstrapper/factories.py`.
2. Add a `Depends()` provider in the appropriate file under `bootstrapper/dependencies/`.
3. Wire the dependency into the relevant route handler in `bootstrapper/routers/`.

Rule 8 from `CLAUDE.md`: any IoC hook introduced in a task must be wired to a concrete
implementation in `bootstrapper/` before the task is complete.

### Step 7: Add a `docs:` Commit

Every PR branch must contain at least one `docs:` commit (Constitution Priority 6,
Rule 9 from `CLAUDE.md`). If the feature required no documentation changes, add:

```bash
git commit --allow-empty -m "docs: no documentation changes required — <one-sentence justification>"
```

If the feature touches user-facing behavior, update the relevant docs file (e.g.,
`docs/OPERATOR_MANUAL.md`, `docs/index.md`).

### Step 8: Run Pre-commit on All Files

```bash
pre-commit run --all-files
```

Fix all hook failures before pushing.

### Step 9: Push and Raise a PR

```bash
git push -u origin feat/P31-T31-2-example-feature
gh pr create --title "feat: add example feature (T31.2)" --body "..."
```

The PR body must include: Task ID, changes checklist, acceptance criteria met, test results,
and Constitution compliance statements.

---

## 6. Adding a New Module

Adding a fully new business domain (e.g., a `reporting` module) requires the following
steps. Each step has a corresponding quality gate.

### Step 1: Create the Module Directory

```bash
mkdir -p src/synth_engine/modules/reporting
touch src/synth_engine/modules/reporting/__init__.py
```

### Step 2: Add an Import-Linter Contract

Add the new module to the independence contract in `pyproject.toml`:

```toml
[[tool.importlinter.contracts]]
name = "Module independence: ingestion, mapping, ..., reporting are independent"
type = "independence"
modules = [
    "synth_engine.modules.ingestion",
    # ... existing modules ...
    "synth_engine.modules.reporting",   # <-- add here
]
```

And to the "modules must not import from bootstrapper" forbidden contract:

```toml
[[tool.importlinter.contracts]]
name = "Modules must not import from bootstrapper"
type = "forbidden"
source_modules = [
    # ... existing modules ...
    "synth_engine.modules.reporting",   # <-- add here
]
forbidden_modules = ["synth_engine.bootstrapper"]
```

Verify the contract works before writing any code:

```bash
poetry run lint-imports
```

### Step 3: Create a Corresponding Test Directory

```bash
mkdir -p tests/unit/reporting
touch tests/unit/reporting/__init__.py
```

### Step 4: Follow TDD

Write failing tests in `tests/unit/reporting/` before any implementation.

### Step 5: Wire in the Bootstrapper

Add the module's primary service to `bootstrapper/factories.py` and expose it via a
`Depends()` provider in `bootstrapper/dependencies/`. Add the module's router (if it
has one) to `bootstrapper/router_registry.py`.

### Step 6: Write a Neutral Value Object in `shared/` If Needed

If the new module produces a data structure consumed by another module, that structure is a
cross-module data contract. Place it in `shared/` as a frozen dataclass with no business
logic and no I/O — not in the module that produces it.

Example pattern: `shared/schema_topology.py` is produced by `bootstrapper` from
`mapping.reflection.SchemaReflector` output, then consumed by `subsetting` and `cli`.
It lives in `shared/` because it is cross-module.

### Step 7: Run All Quality Gates

```bash
pre-commit run --all-files
poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95
poetry run pytest tests/integration/ -v
```

---

## 7. AI Orchestration Pipeline

The entire codebase has been produced by AI agents operating under a defined governance
framework. Understanding this pipeline is essential for extending or maintaining it.

### The Actors

| Actor | Claude session type | Role |
|-------|--------------------|----|
| **PM (Product Manager)** | Main Claude Code session, reads `CLAUDE.md` | Plans tasks, creates branches, delegates to subagents, runs reviews, creates PRs |
| **software-developer** | Subagent (`claude` CLI invocation) | Writes all code, tests, and commits. Never self-reviews. |
| **qa-reviewer** | Subagent, spawned after GREEN | Reviews for correctness, coverage, dead code, edge cases |
| **devops-reviewer** | Subagent, spawned after GREEN | Reviews for secrets hygiene, PII, security, dependency risk |
| **architecture-reviewer** | Subagent, spawned when `src/synth_engine/` is touched | Reviews module boundaries, ADR alignment |
| **ui-ux-reviewer** | Subagent, spawned when frontend files are touched | Reviews accessibility, UX, WCAG 2.1 AA compliance |
| **pr-describer** | Subagent, spawned after reviews pass | Drafts the PR description |
| **pr-reviewer** | Subagent, spawned after review agents pass | Posts the GitHub approval |

Agent definitions live in `.claude/agents/` and are loaded by the Claude Code framework.

### The Governance Documents

The agents are constrained by:

- `CONSTITUTION.md` — absolute priorities (0–9). Security is Priority 0. These cannot be
  overridden by any agent.
- `CLAUDE.md` — workflow rules (PM vs. developer responsibilities, TDD mandate,
  quality gate commands, branch naming, PII rules).
- `docs/RETRO_LOG.md` — living ledger of retrospective findings and open advisories.
  The PM queries this before every task brief to include "known failure patterns" in the
  developer agent's instructions.

### The Phase and Task Structure

Work is organized into **phases** (e.g., Phase 30 = Discriminator-Level DP-SGD). Each
phase contains **tasks** (e.g., T30.1, T30.2). Task specifications live in
`docs/backlog/phase-<N>.md`.

A typical phase execution:

1. PM reads the task spec from `docs/backlog/`.
2. PM scans `docs/RETRO_LOG.md` for relevant historical failures.
3. PM creates a feature branch (`feat/P<N>-T<N>.<M>-description`).
4. PM invokes `software-developer` subagent with the full task spec + failure patterns.
5. `software-developer` executes RED → GREEN → REFACTOR and commits.
6. PM spawns `qa-reviewer` and `devops-reviewer` (always). If `src/synth_engine/` was
   touched, also spawns `architecture-reviewer`. If frontend files were touched, spawns
   `ui-ux-reviewer`.
7. Review agents return findings. Any FINDING must be fixed — it cannot be labeled advisory
   and skipped (per the memory file `feedback_review_findings_must_be_fixed.md`).
8. PM commits all review findings and fixes in one `review:` commit.
9. PM updates `docs/RETRO_LOG.md` with the phase retrospective.
10. PM spawns `pr-describer`, pushes the branch, creates a PR via `gh pr create`.
11. PM spawns `pr-reviewer`, which posts `gh pr review --approve`.
12. PM merges with `gh pr merge --merge` (never `--squash` — preserves TDD audit trail per
    Constitution Priority 3 and `feedback_no_squash_merges.md`).

### The ChromaDB Retrospective Store

The `software-developer` agent is instructed to query a ChromaDB collection called
"Retrospectives" before reading any task spec. This semantic search surfaces relevant past
failures without requiring the agent to read the full `RETRO_LOG.md`.

Seeds are managed by `scripts/seed_chroma.py` and `scripts/seed_chroma_retro.py`.

### Constitutional Enforcement Mechanisms

Every Constitutional rule has a programmatic gate (Priority 0.5). This prevents drift where
rules only exist as honor-system expectations:

| Rule | Gate |
|------|------|
| No secrets committed | `gitleaks` + `detect-secrets` in pre-commit |
| Quality gates unbreakable | `ruff`, `mypy`, `pytest`, `pre-commit` cannot be bypassed |
| TDD audit trail | `test:` commit must precede `feat:` commit — auditable in `git log` |
| 95%+ coverage | `pytest --cov-fail-under=95` in CI |
| Strict typing | `mypy --strict` in pre-commit |
| `docs:` commit required per PR | `docs-gate` stage in `scripts/ci-local.sh` |

---

## 8. Operating Without AI

A human developer can follow the exact same workflow. The AI pipeline is a strict
formalization of good engineering practice — not magic. Here is how to replicate it manually.

### Planning a Task

1. Read the full task spec in `docs/backlog/phase-<N>.md`.
2. Read `docs/RETRO_LOG.md` Open Advisory Items. Address any that target your task domain.
3. Scan previous phase retrospectives for failure patterns related to your work.
4. Fill in the pre-RED checklist from Section 4 before writing any test.

### Executing the Task

Follow Section 5 (Adding a New Feature) exactly as written. The order of commits matters:

```
test: add failing tests for <feature>      ← RED phase
feat: implement <feature>                  ← GREEN phase
refactor: improve <feature>                ← REFACTOR phase (if needed)
docs: <update relevant documentation>      ← required for every PR
review: <consolidated review findings>     ← after self-review or peer review
```

### Self-Review Checklist

In the absence of dedicated review agents, a human developer should work through each
reviewer's checklist manually before raising a PR:

**QA checklist** (from `.claude/agents/qa-reviewer.md`):

- Run `poetry run pytest tests/unit/ --cov=src/synth_engine -q` and verify coverage is
  95%+.
- Run `poetry run vulture src/ .vulture_whitelist.py --min-confidence 80` and fix any
  dead code findings.
- For each new `except` clause: can that exception actually be raised by the guarded code?
- For each new public method: does it have a Happy Path test, an Error Path test, and at
  least one Edge Case test?
- Do docstrings accurately describe what the function does — not aspirationally, but as
  implemented?

**DevOps checklist** (from `.claude/agents/devops-reviewer.md`):

- Run `gitleaks detect --verbose` before any `git push`.
- Run `poetry run bandit -c pyproject.toml -r src/` and ensure zero HIGH/MEDIUM findings.
- Verify no `print()` calls exist in production code (use `logging.getLogger(__name__)`).
- Check any new `logger.*` call — would it log PII if real data were passed through?
- Verify no `--no-verify` or `SKIP=` flags appear anywhere in the diff.

**Architecture checklist** (from `.claude/agents/architecture-reviewer.md`):

- Run `poetry run lint-imports` and verify all contracts pass.
- Confirm new files are placed in the correct module per the domain table in Section 2.
- Confirm no new cross-module imports were introduced.
- If a new ADR-numbered decision was made (new technology, architectural substitution),
  write the ADR in `docs/adr/`.

### Peer Review

This project was designed for AI-agent review, but the same standards apply to human peer
review. Use the output format from each reviewer's `.claude/agents/*.md` file as a PR
comment template. Any FINDING in that review must receive a fix commit before merge.

---

## 9. Critical Invariants

These are rules that, if violated, compromise either the security of the system or the
integrity of the development process. They must be treated as absolute.

### PII Protection

**Never commit**:
- `data/` — real database dumps
- `output/` — synthesis output files
- `logs/` — application logs (may contain sensitive data)
- `.env` — environment variable files with secrets
- `config.local.json` — local configuration overrides
- `secrets/*.txt` — Docker secret files

**Safe to commit**:
- `sample_data/` — fictional sample data only
- `tests/fixtures/` — all fictional data, no real names or real emails

The `.gitignore` enforces most of these, but `gitleaks` and `detect-secrets` are the last
line of defense. Always run `git status` and `gitleaks detect` before pushing.

**PII emergency procedure**:

```bash
# If PII was staged but not committed:
git reset HEAD <file>

# If PII was committed but not pushed:
git reset --soft HEAD~1
# Remove the file, re-stage everything else, re-commit

# If PII was pushed:
STOP. Do not rewrite history without explicit approval.
Alert the team lead immediately.
```

### Pre-commit Hooks Must Never Be Bypassed

`git commit --no-verify` is forbidden. `SKIP=...` is forbidden. If a hook fails, fix
the underlying code. Do not find workarounds.

### Import Boundaries Are Enforced at Commit Time

`import-linter` runs as a pre-commit hook. If it fails, the commit is rejected. The
contracts in `pyproject.toml` define what is and is not allowed. Do not add
`# noqa: import-boundary` suppressions — there are none in this codebase.

### Coverage Never Drops Below 95%

`pytest --cov-fail-under=95` is enforced in CI and in the pre-commit `test:` gate. If a
commit would reduce coverage below 95%, write the missing tests first.

### No `# type: ignore` Without Justification

Mypy runs in strict mode. If a `# type: ignore` is needed, the justification must be
written in a comment immediately above the suppression:

```python
# type: ignore[assignment] — third-party library returns Any; see ADR-0032
result: str = some_lib.get_value()  # type: ignore[assignment]
```

There are currently three documented exceptions in `pyproject.toml`
(`[tool.mypy.overrides]`) for optional synthesizer dependencies (`sdv`, `ctgan`, `opacus`,
`huey`) that ship without `py.typed` markers. See ADR-0032 for the full rationale.

### Merge Strategy: Always `--merge`, Never `--squash`

Squash merges destroy the TDD audit trail (Constitution Priority 3). The `test:` → `feat:`
commit sequence must be preserved on `main`. Use:

```bash
gh pr merge --merge <PR-number>
```

Never `gh pr merge --squash`.

### Docker Secret Files Are Never Committed

`secrets/*.txt` is gitignored. The `docker-compose.yml` requires them to exist on the host
before `docker compose up`. Generate them with the `openssl rand` commands shown in
Section 1.

---

## 10. Key Files Reference

### Governance and Process

| File | Purpose |
|------|---------|
| `CONSTITUTION.md` | Absolute project rules, priority hierarchy (0–9), enforcement table |
| `CLAUDE.md` | PM and developer agent directives, TDD mandate, quality gate commands, PII rules |
| `docs/RETRO_LOG.md` | Living ledger of phase retrospectives and open advisory items |
| `docs/BACKLOG.md` | High-level backlog overview |
| `docs/backlog/phase-<N>.md` | Per-phase task specifications |
| `docs/index.md` | Central documentation index (92 documents) |

### Architecture Decisions

All ADRs live in `docs/adr/`. Key ones for onboarding:

| ADR | Decision |
|-----|----------|
| `ADR-0001` | Modular monolith over microservices — rationale and topology |
| `ADR-0012` | PostgreSQL ingestion adapter design |
| `ADR-0013` | Relational mapping and FK graph |
| `ADR-0014` | Masking engine (FPE, deterministic) |
| `ADR-0017` | Synthesizer DP library selection (CTGAN + Opacus) |
| `ADR-0019` | AI PR review governance |
| `ADR-0029` | Architectural requirements gap analysis — nine deviations from spec |
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
| `docs/E2E_VALIDATION.md` | End-to-end validation run record (Phase 28+) |

---

## 11. Conditional Imports

### Why the Pattern Exists

Several optional dependency groups are not installed in all environments. The default
`poetry install` installs only the core group. Synthesis-specific dependencies
(`sdv`, `ctgan`, `opacus`, `torch`) belong to the `synthesizer` group, which is
optional:

```bash
poetry install --with synthesizer  # enables synthesis and DP training
```

If a module imported these libraries unconditionally at the top of the file,
`ModuleNotFoundError` would prevent the FastAPI application from starting in
environments where the synthesizer group is absent — even if no synthesis endpoint
was called. This would break health checks, the vault-unseal route, and all other
unrelated routes.

The solution is **deferred conditional imports** using `try/except ImportError` at
module scope, binding the name to `None` on failure. Code that actually uses the
name then guards at the call site:

```python
try:
    from sdv.single_table import CTGANSynthesizer
except ImportError:  # pragma: no cover — only triggered if synthesizer group is absent
    CTGANSynthesizer = None  # SDV not installed; synthesis unavailable

# ... later, at the call site:
if CTGANSynthesizer is None:  # pragma: no cover
    raise ImportError(
        "The 'sdv' package is required for synthesis. "
        "Install it with: poetry install --with synthesizer"
    )
```

The `# pragma: no cover` marker on the `except` branch is intentional: in the
standard test environment (with `--with synthesizer`), this branch is never reached
and cannot be covered. The marker prevents a spurious coverage failure.

### How to Check Dependency Availability at Runtime

A caller can detect whether synthesis is available before attempting to use it:

```python
from synth_engine.modules.synthesizer.engine import CTGANSynthesizer

if CTGANSynthesizer is None:
    # synthesis group not installed — raise a user-friendly error or skip
    raise ImportError("Install the synthesizer dependency group to use this feature.")
```

The same pattern applies to `DPCompatibleCTGAN`, `PrivacyEngine`, `torch`, and
`MinioStorageBackend`.

### Files Using Deferred Conditional Imports

Every file in this list follows the `try/except ImportError` pattern described above.
All synthesizer-group names are bound to `None` when the group is absent.

| File | Names conditionally imported | Optional group |
|------|------------------------------|----------------|
| `modules/synthesizer/engine.py` | `CTGANSynthesizer` (sdv), `DPCompatibleCTGAN` | `synthesizer` |
| `modules/synthesizer/dp_training.py` | `CTGANSynthesizer`, `CTGAN`, `Generator`, `detect_discrete_columns` (sdv/ctgan), `torch`, `nn`, `DataLoader`, `TensorDataset` | `synthesizer` |
| `modules/synthesizer/dp_discriminator.py` | `torch`, `nn` | `synthesizer` |
| `modules/privacy/dp_engine.py` | `PrivacyEngine` (opacus) | `synthesizer` |
| `bootstrapper/main.py` | `MinioStorageBackend` | `synthesizer` |
| `shared/telemetry.py` | `OTLPSpanExporter` (opentelemetry-exporter-otlp) | optional OTEL exporter |

### Mypy Configuration

Because these libraries ship without `py.typed` markers, mypy strict mode cannot
verify their type stubs. The exceptions are declared in `pyproject.toml` under
`[tool.mypy.overrides]`:

```toml
[[tool.mypy.overrides]]
module = ["sdv.*", "ctgan.*", "opacus.*", "huey.*"]
ignore_missing_imports = true
```

See ADR-0032 for the full rationale. Adding a new optional dependency that lacks
`py.typed` requires a corresponding entry here plus documentation in ADR-0032.

### Testing Conditional Import Code

Unit tests for code that uses conditional imports run with the `synthesizer` group
installed. The `# pragma: no cover` branches that fire when the group is absent are
excluded from the 95% coverage requirement.

To test the behavior when a dependency is absent, patch the module-scope name to `None`:

```python
from unittest.mock import patch

def test_train_raises_when_sdv_absent() -> None:
    with patch("synth_engine.modules.synthesizer.engine.CTGANSynthesizer", None):
        engine = SynthesisEngine()
        with pytest.raises(ImportError, match="sdv.*synthesizer"):
            engine.train("t", "/path/to/data.parquet")
```

This pattern is used in `tests/unit/synthesizer/test_engine.py` to achieve
meaningful coverage of the guarded call sites without requiring two separate
dependency environments.
