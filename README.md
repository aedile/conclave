# Conclave

**Conclave** is an enterprise-grade, Air-Gapped Synthetic Data Generation Engine.

It operates on a strict **Bring Your Own Compute (BYOC)** model within zero-trust, physically
isolated environments. Its purpose is to ingest sensitive production data and generate
statistically comparable, relationally intact synthetic datasets using Differential Privacy
(DP-SGD) and deterministic masking — without relying on any external APIs, cloud telemetry,
or internet connectivity.

---

## What It Does

Production databases contain PII that cannot leave the premises — but QA engineers, ML teams,
and developers need realistic data to work with. Conclave solves this by:

1. **Connecting read-only** to a source PostgreSQL database, verifying the ingestion account
   cannot write, and refusing to proceed if it can.
2. **Subsetting** the data relationally — following foreign key graphs to extract a
   surgically precise percentage of records while preserving all parent-child integrity.
3. **Masking deterministically** — the same real name always produces the same fake name,
   preserving referential integrity across joined tables while making PII unrecoverable.
4. **Generating synthetically** (Phase 4) — training GPU-accelerated tabular models
   (SDV/CTGAN) with Differential Privacy (DP-SGD) guarantees, so that individual outlier
   records cannot be reverse-engineered from the synthetic output.
5. **Egressing** the result into a target PostgreSQL database with Saga-pattern rollback — if
   anything fails mid-write, the target is wiped clean.

Everything runs inside Docker with no external network calls, encrypted volumes, and
air-gap artifact bundles for sneaker-net deployment.

---

## Architecture

Conclave is a **Python Modular Monolith** — a single deployable unit with strict internal
module boundaries enforced by `import-linter` contracts.

```
src/synth_engine/
├── bootstrapper/       FastAPI app factory, DI wiring, global middleware
├── modules/
│   ├── ingestion/      PostgreSQL read-only adapter, privilege pre-flight check
│   ├── mapping/        Schema reflection, DAG, Kahn's topological sort         [Phase 3.5]
│   ├── subsetting/     FK traversal, SubsettingEngine, Saga-pattern EgressWriter [Phase 3.5]
│   ├── masking/        Deterministic FPE registry, collision prevention, LUHN
│   ├── profiler/       Statistical distributions, marginal histograms           [Phase 4]
│   ├── synthesizer/    SDV/CTGAN training loop, checkpointing, Huey tasks       [Phase 4]
│   └── privacy/        DP-SGD wiring, OOM guardrails, Epsilon accountant        [Phase 4]
└── shared/             Cross-cutting: Vault, ALE encryption, audit logger, JWT
```

Module boundaries are enforced at CI time via `import-linter`. Modules cannot import from
each other or from `bootstrapper`. Cross-module communication goes through `shared/` value
objects or IoC callbacks injected by the bootstrapper.

---

## Security Posture

Security is Priority Zero — it overrides every other consideration.

| Control | Implementation |
|---------|---------------|
| Read-only ingestion | Pre-flight `SELECT FOR UPDATE` privilege check; superuser → immediate reject |
| PII never in plaintext at rest | Application-Level Encryption (ALE) via Fernet + HKDF-SHA256 from Vault KEK |
| Vault unseal pattern | Operator passphrase derives KEK at runtime; never persisted to disk or env |
| Deterministic masking | HMAC-SHA256 seeded Faker; same input → same output; not reversible |
| Differential Privacy | DP-SGD noise injection with Epsilon/Delta budget accounting (Phase 4) |
| WORM audit log | Cryptographically signed, append-only audit trail |
| Air-gap enforcement | No external network calls; `make build-airgap-bundle` for sneaker-net |
| Supply chain | All GitHub Actions SHA-pinned; Trivy container scan in CI (Phase 3.5) |
| Secret scanning | `gitleaks` + `detect-secrets` on every commit; hooks cannot be bypassed |

---

## Current Development Status

**Active Phase: 3.5 — Technical Debt Sprint**

| Phase | Status | Summary |
|-------|--------|---------|
| 0.6 — Agile Environment | ✅ Complete | ChromaDB, Git worktrees, task queue |
| 0.8 — Technical Spikes | ✅ Complete | ML memory physics, FPE math, topological graphing |
| 1 — CI/CD & Quality Gates | ✅ Complete | Pre-commit hooks, Docker hardening, air-gap bundler |
| 2 — Foundational Architecture | ✅ Complete | FastAPI bootstrapper, PostgreSQL+ALE, JWT, Vault, WORM logger |
| 3 — The "Thin Slice" | ✅ Complete | Ingestion, relational mapping, deterministic masking, subsetting+Saga, E2E tests |
| **3.5 — Tech Debt Sprint** | 🔄 **In Progress** | Module cohesion refactor, Virtual FK, CLI entrypoint, advisory sweep |
| 4 — Generative AI & DP | ⏳ Blocked by 3.5 | SDV/CTGAN training, DP-SGD, Epsilon accountant |
| 5 — Orchestration & UI | ⏳ Pending | Task API, React SPA, offline licensing |
| 6 — Integration & Audit | ⏳ Pending | E2E synthesis tests, NIST erasure, handover |

---

## What's Working Right Now

The Phase 3 "Thin Slice" pipeline is fully operational at the library level:

- **`PostgresIngestionAdapter`** — connects read-only, runs pre-flight privilege check
- **`SchemaReflector`** + **`DirectedAcyclicGraph`** — reflects schema, builds FK topology
- **`DeterministicMaskingEngine`** — masks Names, Emails, SSNs, Credit Cards, Phone Numbers deterministically with collision prevention and LUHN compliance
- **`SubsettingEngine`** — traverses FK graph from a seed query, applies masking via injected callback, writes to target with Saga rollback
- **Integration tests** — pytest-postgresql ephemeral source/target DBs, FK integrity verified, masking determinism verified

A minimal CLI entrypoint (`conclave-subset`) is in progress as part of Phase 3.5.

---

## Quality Gates (All Must Pass)

Every commit passes all of the following before it can merge:

```bash
poetry run ruff check src/ tests/                          # linting
poetry run ruff format --check src/ tests/                 # formatting
poetry run mypy src/                                       # strict type checking
poetry run bandit -c pyproject.toml -r src/               # security scan
poetry run pytest tests/unit/ --cov=src/synth_engine \
    --cov-fail-under=90 -W error                           # unit tests + 90% coverage
poetry run pytest tests/integration/ -v --no-cov \
    -p pytest_postgresql                                   # integration tests (separate gate)
poetry run python -m importlinter                          # module boundary enforcement
pre-commit run --all-files                                 # all hooks
```

Coverage is enforced at 90% minimum. Integration tests are a separate gate — unit tests with
mocks do not substitute for integration tests that specify real infrastructure.

---

## Running Locally

### Prerequisites

- Docker + Docker Compose
- Python 3.14
- Poetry
- PostgreSQL client (`libpq`) for integration tests

### Setup

```bash
# Install dependencies
poetry install --with dev,integration

# Start local services (PostgreSQL, Redis, MinIO, Jaeger)
docker-compose up -d

# Unseal the vault (development mode — sets ALE_KEY from .env)
# Copy .env.example to .env and fill in values
cp .env.example .env

# Run unit tests
poetry run pytest tests/unit/ -v

# Run integration tests (requires PostgreSQL on PATH)
poetry run pytest tests/integration/ -v --no-cov -p pytest_postgresql
```

### Air-Gap Bundle (for offline deployment)

```bash
make build-airgap-bundle
```

Produces a deterministic, versioned tarball containing all Python wheels, Docker images, and
configuration for deployment on an isolated network with no internet access.

---

## Development Process

This project runs an autonomous TDD workflow governed by `CONSTITUTION.md` and `CLAUDE.md`:

- **Red → Green → Refactor** — tests are written before implementation, always
- **4-parallel-reviewer pattern** — every task is reviewed by QA, DevOps, Architecture, and
  UI/UX agents in parallel before merge
- **90% coverage gate** — enforced in CI; cannot be bypassed
- **Living retrospective log** — `docs/RETRO_LOG.md` captures every review finding and
  open advisory item
- **ADR-driven decisions** — architectural decisions are documented in `docs/adr/`

See `CLAUDE.md` for the full PM/developer workflow and `CONSTITUTION.md` for the binding
priority hierarchy.

---

## Project Structure

```
├── src/synth_engine/       Production source code (see Architecture above)
├── tests/
│   ├── unit/               Fast isolated unit tests (90% coverage gate)
│   └── integration/        pytest-postgresql integration tests (separate CI gate)
├── docs/
│   ├── adr/                Architecture Decision Records
│   ├── backlog/            Phase-by-phase task backlog
│   ├── RETRO_LOG.md        Living review ledger and advisory tracking
│   └── EXECUTION_PLAN.md   Full project Gantt and dependency map
├── .claude/agents/         Specialized AI reviewer definitions
├── scripts/                Utility scripts (ChromaDB seeding, etc.)
├── spikes/                 Exploratory prototypes (not production code)
├── docker-compose.yml      Local development services
└── Makefile                Build targets including air-gap bundle
```

---

## License

Proprietary. All rights reserved.
