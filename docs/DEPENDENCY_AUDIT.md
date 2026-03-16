# Dependency Audit — T18.2

**Date:** 2026-03-16
**Task:** P18-T18.2 — Dependency Tree Audit & Slimming
**Auditor:** Software Developer Agent

---

## Overview

207 transitive dependencies resolved by Poetry. Direct production deps: 26 (after T18.2 slimming).

This audit covers every direct dependency in `[tool.poetry.dependencies]` (main group). Each entry
records purpose, runtime usage, transitive count, and group recommendation.

**Changes applied in T18.2:**
- `chromadb` moved from `[tool.poetry.dependencies]` → `[tool.poetry.group.dev.dependencies]`
- `datamodel-code-generator` placement formalized inside `[tool.poetry.group.dev.dependencies]`
  (it was syntactically in dev due to TOML ordering but lacked an explicit section comment)

---

## Direct Production Dependency Audit Table

| Dependency | Version Range | Purpose | Used at Runtime (`src/`) | Transitive Direct Deps | Group | Notes |
|------------|--------------|---------|--------------------------|----------------------|-------|-------|
| `click` | `>=8.0,<9` | CLI framework for `conclave-subset` entrypoint | Yes — `bootstrapper/cli.py` | 0 | main | Required |
| `fastapi` | `>=0.115,<1` | HTTP framework; all API routes | Yes — 24 import sites | ~6 | main | Required |
| `uvicorn` | `>=0.32,<1` | ASGI server for FastAPI; started by bootstrapper | Yes — process start | ~5 | main | Required |
| `huey` | `>=2.5,<3` | Redis-backed task queue for background jobs | Yes — `shared/task_queue.py` | ~3 | main | Required |
| `redis` | `>=5,<6` | Redis client; used by huey and direct cache ops | Yes — `shared/task_queue.py` | ~2 | main | Required |
| `opentelemetry-sdk` | `>=1.28,<2` | Distributed tracing / span management | Yes — `shared/telemetry.py` | ~8 | main | Required |
| `opentelemetry-instrumentation-fastapi` | `>=0.49b0,<1` | Auto-instrumentation for FastAPI routes | Yes — `bootstrapper/main.py` | ~4 | main | Required |
| `httpx` | `>=0.27,<1` | Async HTTP client (health probes, license activation) | Transitive via FastAPI; direct: 0 src imports | ~4 | main | Keep — FastAPI testclient depends on it |
| `sqlmodel` | `>=0.0.21,<1` | ORM layer combining SQLAlchemy + Pydantic | Yes — 14 import sites | ~3 | main | Required |
| `alembic` | `>=1.14,<2` | Database migration management | Not directly imported — invoked via CLI | ~2 | main | Required for migrations |
| `psycopg2-binary` | `>=2.9,<3` | Synchronous PostgreSQL driver for `postgresql+psycopg2://` DSN | Not directly imported — registered as SQLAlchemy dialect | 0 | main | Required — sync driver for subsetting |
| `cryptography` | `>=46.0.5,<47` | AES-GCM encryption, SCRYPT KDF in vault/licensing | Yes — `shared/security/vault.py`, `shared/security/licensing.py` | ~2 | main | Required |
| `PyJWT` | `>=2.10,<3` | JWT token encode/decode for auth | Yes — `shared/auth/jwt.py`, `shared/security/licensing.py` | 0 | main | Required (ADR-0007) |
| `passlib` | `>=1.7.4,<2` | Password hashing framework | **Not directly imported** in `src/` | 1 (`bcrypt`) | main | **Candidate for future removal.** Historically present per ADR-0007 as the source of the `cryptography` extra. Now `cryptography` is pinned directly. No `src/` import found. Retained in this phase pending explicit removal ADR. |
| `prometheus-client` | `>=0.21,<1` | Prometheus metrics exposition (`/metrics` endpoint) | Yes — `bootstrapper/main.py` | 0 | main | Required |
| `pydantic` | `>=2,<3` | Data validation and serialization | Yes — 8 import sites | ~2 | main | Required |
| `faker` | `^40.11` | Synthetic/fake data generation for sample datasets | Yes — `modules/masking/` | ~2 | main | Required |
| `pandas` | `>=2.2,<3` | DataFrame processing for profiler and synthesizer | Yes — 4 import sites | ~4 | main | Required |
| `numpy` | `>=1.26,<3` | Numerical computing for profiler distributions | Yes — 2 import sites | 0 | main | Required |
| `psutil` | `>=5.9,<8` | System resource monitoring (memory, CPU) | Yes — `shared/` | ~0 | main | Required |
| `asyncpg` | `>=0.29,<1` | Async PostgreSQL driver for `postgresql+asyncpg://` DSN | **Not directly imported** — registered as SQLAlchemy dialect | 0 | main | **Retain.** Used as DSN scheme string throughout production DB config and integration tests. SQLAlchemy async engine requires it at import time to register the dialect. |
| `greenlet` | `>=3,<4` | Required by SQLAlchemy async extension on ARM64/aarch64 | **Not directly imported** — loaded by SQLAlchemy | 0 | main | **Retain.** Platform guard for ARM64 where Poetry's platform marker misses aarch64. |
| `sse-starlette` | `>=2.1,<4` | Server-Sent Events for job status streaming | Yes — `bootstrapper/routers/jobs.py` | ~2 | main | Required |
| `qrcode` | `>=8,<9` | QR code rendering for air-gapped license activation | Yes — `bootstrapper/routers/licensing.py` | ~2 | main | Required |
| `pillow` | `>=12,<13` | Image rendering for qrcode[pil] PNG output | Not directly imported — loaded by qrcode[pil] | ~0 | main | Required — explicit pin per T5.2 supply chain requirement |

---

## Dev Group Dependency Audit

| Dependency | Purpose | Group | Notes |
|------------|---------|-------|-------|
| `pytest` | Test runner | dev | Required |
| `pytest-cov` | Coverage reporting | dev | Required |
| `pytest-asyncio` | Async test support | dev | Required |
| `ruff` | Linting + formatting | dev | Required |
| `mypy` | Static type checking | dev | Required |
| `bandit` | Security scanner | dev | Required |
| `pip-audit` | CVE audit | dev | Required |
| `vulture` | Dead code detection | dev | Required |
| `import-linter` | Module boundary enforcement | dev | Required |
| `pre-commit` | Git hook runner | dev | Required |
| `cyclonedx-bom` | SBOM generation | dev | Required |
| `detect-secrets` | Secret scanning | dev | Required |
| `pandas-stubs` | Mypy stubs for pandas | dev | Required for strict mypy |
| `types-psutil` | Mypy stubs for psutil | dev | Required for strict mypy |
| `boto3-stubs` | Mypy stubs for boto3 | dev | Required for strict mypy |
| `aiosqlite` | Async SQLite for unit tests | dev | Required for T4.4 async unit tests |
| `chromadb` | Vector DB for retrospective seeding scripts | dev | **Moved from main in T18.2.** Only used in `scripts/seed_chroma.py`, `scripts/seed_chroma_retro.py`, `scripts/init_chroma.py`. Not used at production runtime. Moving reduces main install size by ~25 transitive packages (including onnxruntime, kubernetes, grpcio). |
| `datamodel-code-generator` | TypeScript type generation from OpenAPI schema | dev | Used in `scripts/generate_ts_types.py` and `generate-ts-types` CLI. Dev scaffolding tool. **Placement formalized in T18.2** — was syntactically in dev group but lacked an explicit section comment. |

---

## Findings Summary

### Changes Applied

| Finding | Action | Status |
|---------|--------|--------|
| `chromadb` in main deps — only used in `scripts/` | Moved to dev group | **DONE (T18.2)** |
| `datamodel-code-generator` misplaced comment context | Formalized in dev section with comment | **DONE (T18.2)** |
| `pgbouncer/pgbouncer:1.23.1` phantom tag (ADV-015) | Replaced with `edoburu/pgbouncer:v1.23.1-p3@sha256:...` | **DONE (T18.2)** |

### Deferred Items

| Finding | Recommendation | Deferred To |
|---------|---------------|-------------|
| `passlib` has no `src/` imports | Evaluate for removal — may have been superseded by direct `cryptography` pin. Requires ADR update for ADR-0007 if removed. | Future phase |
| `httpx` not directly imported in `src/` | Keep — FastAPI's `TestClient` depends on it transitively; its absence breaks test infrastructure. | N/A — retain |

---

## Transitive Count Summary

| Group | Direct Deps | Total Resolved (approx) |
|-------|-------------|-------------------------|
| main (production) | 26 → 25 after chromadb move | ~108 |
| dev | 16 → 18 after chromadb + datamodel-codegen formalization | ~180 |
| synthesizer | 5 | ~130 (largely torch/SDV subtrees) |
| integration | 1 | ~5 |

Note: Poetry resolves a single unified dependency graph; counts above reflect approximate unique packages
contributed by each group. The synthesizer group accounts for ~1 GB of installed size (torch, SDV, ONNX).

---

## References

- `pyproject.toml` — canonical dependency specification
- `docs/adr/ADR-0007-jwt-library-selection.md` — PyJWT + passlib history
- `docs/adr/ADR-0017-ctgan-sdv-synthesizer.md` — synthesizer group rationale
- `docs/adr/ADR-0031-pgbouncer-image-substitution.md` — ADV-015 resolution
- `docs/RETRO_LOG.md` — ADV-015 BLOCKER advisory (now resolved)
