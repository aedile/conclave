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
4. **Generating synthetically** — training GPU-accelerated tabular models (SDV/CTGAN) with
   operational Differential Privacy (DP-SGD) guarantees via Opacus, so that individual
   outlier records cannot be reverse-engineered from the synthetic output. Phase 7 DP
   integration is complete: `DPCompatibleCTGAN` with real Opacus `PrivacyEngine` wiring,
   epsilon/delta accounting, and privacy budget enforcement are fully operational.
5. **Orchestrating via API** — a FastAPI task API with Server-Sent Events streams job
   progress to a React SPA dashboard in real time.
6. **Licensing offline** — RS256 JWT hardware-bound license activation for air-gapped
   deployments; no license server call-home required.
7. **Egressing** the result into a target PostgreSQL database with Saga-pattern rollback — if
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
│   ├── mapping/        Schema reflection, DAG, Kahn's topological sort
│   ├── subsetting/     FK traversal, SubsettingEngine, Saga-pattern EgressWriter
│   ├── masking/        Deterministic FPE registry, collision prevention, LUHN
│   ├── profiler/       Statistical distributions, covariance, ProfileDelta
│   ├── synthesizer/    EphemeralStorageClient, OOM guardrails, DPCompatibleCTGAN
│   └── privacy/        DP-SGD wiring, Opacus PrivacyEngine, Epsilon/Delta accountant
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
| Differential Privacy | DP-SGD via Opacus PrivacyEngine; Epsilon/Delta budget enforced per training run |
| WORM audit log | Cryptographically signed, append-only audit trail |
| HMAC artifact signing | Model artifacts signed with HMAC-SHA256; tampering detected at load time |
| Air-gap enforcement | No external network calls; `make build-airgap-bundle` for sneaker-net |
| Supply chain | All GitHub Actions SHA-pinned; Trivy container scan in CI |
| Secret scanning | `gitleaks` + `detect-secrets` on every commit; hooks cannot be bypassed |
| Request body limits | RequestBodyLimitMiddleware: 1 MB body limit, JSON depth 100 |
| Content Security Policy | CSP middleware: `script-src`, `font-src`, `connect-src` all `'self'` |
| OWASP ZAP baseline scan | Automated ZAP scan in CI against the running FastAPI app |
| NIST SP 800-88 erasure | Cryptographic shredding validated against NIST SP 800-88 Rev 1 guidelines |
| Offline license activation | RS256 JWT with hardware binding; no license server call-home required |
| Startup config validation | `validate_config()` at boot; missing required env vars → immediate process exit |

---

## Current Development Status

**Phase 14 — Integration Test Repair & Frontend Lint Fix is in progress.**

| Phase | Status | Summary |
|-------|--------|---------|
| 0.6 — Agile Environment | Complete | ChromaDB, Git worktrees, task queue |
| 0.8 — Technical Spikes | Complete | ML memory physics, FPE math, topological graphing |
| 1 — CI/CD & Quality Gates | Complete | Pre-commit hooks, Docker hardening, air-gap bundler |
| 2 — Foundational Architecture | Complete | FastAPI bootstrapper, PostgreSQL+ALE, JWT, Vault, WORM logger |
| 3 — The "Thin Slice" | Complete | Ingestion, relational mapping, deterministic masking, subsetting+Saga, E2E tests |
| 3.5 — Technical Debt Sprint | Complete | Module cohesion refactor, Virtual FK, CLI entrypoint, advisory sweep |
| 4 — Generative AI & DP | Complete | GPU passthrough, profiler, OOM guardrails, DP-SGD engine, privacy accountant |
| 5 — Orchestration & UI | Complete | Task Orchestration API, React SPA Dashboard, offline licensing, cryptographic shredding |
| 6 — Integration & Audit | Complete | E2E Playwright tests, NIST erasure validation, OWASP ZAP, fuzz testing, production docs |
| 7 — Differential Privacy | Complete | Custom CTGAN training loop, Opacus DP-SGD wiring, quality benchmarks, E2E DP pipeline |
| 8 — Advisory Drain Sprint | Complete | HMAC artifact signing, Alembic migrations, startup config validation, ADR-0017a |
| **9 — Docs & Advisory Drain** | **Complete** | Operator manual refresh, advisory drain, observability |
| **10 — Test Infrastructure Repair & Final Polish** | **Complete** | Stale TODO drain, README updates, test infrastructure repair |
| **11 — Documentation Currency & Workspace Hygiene** | **Complete** | Documentation updates, workspace cleanup, architectural gap ADR |
| **12 — Final Hygiene & Tooling Polish** | **Complete** | Stale branch cleanup, vulture whitelist, README currency |
| **13 — Pre-commit Repair & README Finalization** | **Complete** | Fix ruff/vulture whitelist gate, README Phase 12 completion |
| **14 — Integration Test Repair & Frontend Lint Fix** | **In Progress** | Integration test repair, ESLint 9.x config, nosec justifications |

---

## What's Working Right Now

The full Conclave platform is operational across all completed phases:

**Phase 3 pipeline — operational**
- **`PostgresIngestionAdapter`** — connects read-only, runs pre-flight privilege check
- **`SchemaReflector`** + **`DirectedAcyclicGraph`** — reflects schema, builds FK topology
- **`DeterministicMaskingEngine`** — masks Names, Emails, SSNs, Credit Cards, Phone Numbers
  deterministically with collision prevention and LUHN compliance
- **`SubsettingEngine`** — traverses FK graph from a seed query, applies masking via injected
  callback, writes to target with Saga rollback
- **`conclave-subset` CLI** — fully operational; connects read-only to source PostgreSQL,
  subsets relationally, masks deterministically, egresses with Saga rollback

**Phase 4 generative AI — operational**
- **`StatisticalProfiler`** — profiles DataFrames (histograms, covariance matrices,
  nullability rates); `compare()` produces `ProfileDelta` for drift detection
- **`EphemeralStorageClient`** — uploads/downloads Parquet files to MinIO ephemeral bucket
  via injectable `StorageBackend` Protocol; `FORCE_CPU` fallback tested and operational
- **`OOM Guardrail`** — `check_memory_feasibility()` pre-flight check rejects jobs that
  would exhaust available RAM before training starts
- **`CTGANSynthesizer`** — SDV/CTGAN tabular model training operational
- **`EpsilonAccountant`** — tracks per-table Epsilon/Delta privacy budget consumption;
  rejects training runs that would exceed configured budget limits

**Phase 5 orchestration — operational**
- **Task Orchestration API** — FastAPI + Huey/Redis task queue; `POST /tasks/synthesize`
  enqueues jobs; `GET /tasks/{id}/status` streams progress via Server-Sent Events (SSE)
- **React SPA Dashboard** — React 18 + TypeScript + Vite; real-time job status via SSE;
  accessible (WCAG 2.1 AA); offline-capable static build
- **Offline License Activation** — RS256 JWT hardware binding; QR code challenge/response
  workflow for air-gapped activation; no call-home required
- **Cryptographic Shredding** — `CryptographicShredder` destroys ALE key material,
  rendering encrypted PII permanently unrecoverable

**Phase 6 validation — operational**
- **Playwright E2E tests** — full browser automation across the React SPA + FastAPI backend
- **NIST SP 800-88 erasure validation** — automated tests verifying cryptographic shredding
  meets NIST SP 800-88 Rev 1 media sanitization guidelines
- **OWASP ZAP baseline scan** — automated in CI against the running application
- **Security fuzz tests** — property-based fuzzing of API endpoints and masking primitives

**Phase 7 DP-SGD integration — operational**
- **`DPCompatibleCTGAN`** — custom CTGAN training loop with Opacus DP-SGD integration;
  drop-in replacement for SDV's `CTGANSynthesizer` with real per-sample gradient clipping
  and Gaussian noise injection via Opacus `PrivacyEngine`
- **`DPTrainingWrapper`** — configurable wrapper for `max_grad_norm` and `noise_multiplier`;
  `epsilon_spent(delta)` returns real epsilon after training; `check_budget()` enforces
  per-run privacy budget with `BudgetExhaustionError` on exhaustion
- **`build_dp_wrapper()` bootstrapper factory** — sole entry point for constructing
  `DPTrainingWrapper`; wires DP into `SynthesisEngine.train(dp_wrapper=...)` via DI
- **DP quality benchmarks** — epsilon vs. quality degradation curves documented in
  `docs/DP_QUALITY_REPORT.md`; recommended epsilon ranges by use case
- **Full E2E DP pipeline** — Parquet → `DPCompatibleCTGAN` training → `sample()` →
  `StatisticalProfiler.compare()` → `ProfileDelta` validation; all integration-tested

**Phase 8 security hardening — operational**
- **HMAC artifact signing** — `ModelArtifact.save()` / `load()` sign and verify artifacts
  with `ARTIFACT_SIGNING_KEY`; tampering raises `SecurityError` at load time
- **Alembic migrations** — database schema managed via Alembic; `alembic upgrade head`
  applies all pending migrations before first start and after updates
- **Startup config validation** — `validate_config()` runs at boot; missing required
  environment variables cause an immediate, descriptive process exit rather than silent
  misconfiguration
- **ADR-0017a** — Opacus `secure_mode` decision documented; `filterwarnings` suppression
  for `UserWarning: Secure RNG turned off` is ADR-backed

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
- Node.js (for the React SPA)
- PostgreSQL client (`libpq`) for integration tests

### Backend Setup

```bash
# Install dependencies (include synthesizer group for DP-SGD training)
poetry install --with dev,integration,synthesizer

# Start local services (PostgreSQL, Redis, MinIO, Jaeger)
docker-compose up -d

# Apply database migrations
export DB_USER=conclave
export DB_PASSWORD=postgres
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=conclave
poetry run alembic upgrade head

# Unseal the vault (development mode — sets ALE_KEY from .env)
# Copy .env.example to .env and fill in values
cp .env.example .env

# Run unit tests
poetry run pytest tests/unit/ -v

# Run integration tests (requires PostgreSQL on PATH)
poetry run pytest tests/integration/ -v --no-cov -p pytest_postgresql
```

### Frontend Setup

```bash
cd frontend && npm ci && npm run dev
```

The SPA will be available at `http://localhost:5173` and proxies API calls to the FastAPI
backend at `http://localhost:8000`.

### Air-Gap Bundle (for offline deployment)

```bash
make build-airgap-bundle
```

Produces a deterministic, versioned tarball containing all Python wheels, Docker images, and
configuration for deployment on an isolated network with no internet access.

---

## Development Process

This project runs an autonomous TDD workflow governed by a two-layer governance model:

- **`CONSTITUTION.md`** — The binding priority hierarchy for all agents. Security is Priority 0. Every directive has a programmatic enforcement mechanism (Priority 0.5). Immutable except by explicit ratification.
- **`CLAUDE.md`** — The operational PM/developer workflow. Defines how the PM orchestrates subagents, delegates implementation, manages the backlog, and runs the review cycle. Living document; amended after retrospectives.

The workflow itself:

- **Red → Green → Refactor** — tests are written before implementation, always
- **4-parallel-reviewer pattern** — every task is reviewed by QA, DevOps, Architecture, and
  UI/UX agents in parallel before merge
- **90% coverage gate** — enforced in CI; cannot be bypassed
- **docs-gate** — every PR must contain at least one `docs:` commit (Constitution Priority 6)
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
│   ├── integration/        pytest-postgresql integration tests (separate CI gate)
│   └── security/           NIST erasure validation and security fuzz tests
├── frontend/               React 18 SPA (Vite, TypeScript, Playwright E2E)
├── docs/
│   ├── adr/                Architecture Decision Records
│   ├── backlog/            Phase-by-phase task backlog
│   ├── RETRO_LOG.md        Living review ledger and advisory tracking
│   ├── OPERATOR_MANUAL.md  Production deployment and operations guide
│   ├── DP_QUALITY_REPORT.md  DP-SGD epsilon vs. quality benchmarks
│   ├── DISASTER_RECOVERY.md  Incident response and recovery procedures
│   ├── LICENSING.md        Offline license activation and hardware binding guide
│   └── retired/            Retired documents and archived spike files
├── .claude/agents/         Specialized AI reviewer definitions
├── alembic/                Database migration scripts (Alembic)
├── scripts/                Utility scripts (ChromaDB seeding, type generation, etc.)
├── docker-compose.yml      Local development services
└── Makefile                Build targets including air-gap bundle
```

---

## License

Proprietary. All rights reserved.
