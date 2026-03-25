# Dependency Audit — T18.2

> **Staleness notice**: Last verified T18.2 (2026-03-16). Re-audit required before any major dependency change.

**Date:** 2026-03-16 | **Task:** P18-T18.2 | **Auditor:** Software Developer Agent

207 transitive dependencies resolved by Poetry. Direct production deps: 26 (after T18.2 slimming).

**T18.2 changes:** `datamodel-code-generator` placement formalized in dev group. `chromadb` removed (P53 cleanup — feature sunsetted, scripts deleted).

---

## Direct Production Dependencies

| Dependency | Version Range | Purpose | Runtime (`src/`) | Notes |
|------------|--------------|---------|-----------------|-------|
| `click` | `>=8.0,<9` | CLI framework (`conclave-subset`) | `bootstrapper/cli.py` | Required |
| `fastapi` | `>=0.115,<1` | HTTP framework; all API routes | 24 import sites | Required |
| `uvicorn` | `>=0.32,<1` | ASGI server | process start | Required |
| `huey` | `>=2.5,<3` | Redis-backed task queue | `shared/task_queue.py` | Required |
| `redis` | `>=5,<6` | Redis client; huey + direct cache | `shared/task_queue.py` | Required |
| `opentelemetry-sdk` | `>=1.28,<2` | Distributed tracing | `shared/telemetry.py` | Required |
| `opentelemetry-instrumentation-fastapi` | `>=0.49b0,<1` | FastAPI auto-instrumentation | `bootstrapper/main.py` | Required |
| `httpx` | `>=0.27,<1` | Async HTTP client | no direct `src/` imports | Keep — FastAPI TestClient depends on it |
| `sqlmodel` | `>=0.0.21,<1` | ORM (SQLAlchemy + Pydantic) | 14 import sites | Required |
| `alembic` | `>=1.14,<2` | Database migrations | invoked via CLI | Required |
| `psycopg2-binary` | `>=2.9,<3` | Sync PostgreSQL driver | dialect registration | Required — sync driver for subsetting |
| `cryptography` | `>=46.0.5,<47` | AES-GCM, SCRYPT KDF | `shared/security/vault.py`, `licensing.py` | Required |
| `PyJWT` | `>=2.10,<3` | JWT encode/decode | `shared/auth/jwt.py`, `licensing.py` | Required (ADR-0007) |
| ~~`passlib`~~ | ~~`>=1.7.4,<2`~~ | ~~Password hashing~~ | **not in `src/`** | **Removed in T55.5** — superseded by direct `cryptography` pin; zero import sites confirmed. |
| `prometheus-client` | `>=0.21,<1` | Prometheus `/metrics` | `bootstrapper/main.py` | Required |
| `pydantic` | `>=2,<3` | Data validation | 8 import sites | Required |
| `faker` | `^40.11` | Fake data generation | `modules/masking/` | Required |
| `pandas` | `>=2.2,<3` | DataFrame processing | 4 import sites | Required |
| `numpy` | `>=1.26,<3` | Numerical computing | 2 import sites | Required |
| `psutil` | `>=5.9,<8` | System resource monitoring | `shared/` | Required |
| `asyncpg` | `>=0.29,<1` | Async PostgreSQL driver | dialect registration | Retain — DSN scheme throughout; SQLAlchemy async requires it at import |
| `greenlet` | `>=3,<4` | SQLAlchemy async on ARM64 | loaded by SQLAlchemy | Retain — Poetry platform marker misses aarch64 |
| `sse-starlette` | `>=2.1,<4` | Server-Sent Events | `bootstrapper/routers/jobs.py` | Required |
| `qrcode` | `>=8,<9` | QR code for air-gapped license activation | `bootstrapper/routers/licensing.py` | Required |
| `pillow` | `>=12,<13` | PNG rendering for qrcode[pil] | loaded by qrcode | Required — explicit pin per T5.2 supply chain requirement |

---

## Dev Group Dependencies

| Dependency | Purpose | Notes |
|------------|---------|-------|
| `pytest` | Test runner | Required |
| `pytest-cov` | Coverage reporting | Required |
| `pytest-asyncio` | Async test support | Required |
| `ruff` | Linting + formatting | Required |
| `mypy` | Static type checking | Required |
| `bandit` | Security scanner | Required |
| `pip-audit` | CVE audit | Required |
| `vulture` | Dead code detection | Required |
| `import-linter` | Module boundary enforcement | Required |
| `pre-commit` | Git hook runner | Required |
| `cyclonedx-bom` | SBOM generation | Required |
| `detect-secrets` | Secret scanning | Required |
| `pandas-stubs` | Mypy stubs for pandas | Required for strict mypy |
| `types-psutil` | Mypy stubs for psutil | Required for strict mypy |
| `boto3-stubs` | Mypy stubs for boto3 | Required for strict mypy |
| `aiosqlite` | Async SQLite for unit tests | Required for T4.4 async unit tests |
| `datamodel-code-generator` | TypeScript types from OpenAPI | Used in `scripts/generate_ts_types.py`. **Placement formalized in T18.2.** |

---

## Findings Summary

| Finding | Action | Status |
|---------|--------|--------|
| `chromadb` — feature sunsetted | Removed entirely (P53) | **DONE** |
| `datamodel-code-generator` misplaced | Formalized in dev section | **DONE (T18.2)** |
| `pgbouncer/pgbouncer:1.23.1` phantom tag (ADV-015) | Replaced with `edoburu/pgbouncer:v1.23.1-p3@sha256:...` | **DONE (T18.2)** |
| `passlib` has no `src/` imports | Removed entirely (T55.5) | **DONE** |
| `httpx` not directly imported | Retain — FastAPI TestClient depends on it | N/A |

---

## Transitive Count Summary

| Group | Direct Deps | Total (approx) |
|-------|-------------|----------------|
| main | 25 | ~108 |
| dev | 18 | ~180 |
| synthesizer | 5 | ~130 (torch/SDV subtrees; ~1 GB) |
| integration | 1 | ~5 |

---

## References

- `pyproject.toml` — canonical dependency specification
- `docs/adr/ADR-0007-jwt-library-selection.md` — PyJWT + passlib history
- `docs/adr/ADR-0017-ctgan-sdv-synthesizer.md` — synthesizer group rationale
- `docs/adr/ADR-0031-pgbouncer-image-substitution.md` — ADV-015 resolution
- `docs/RETRO_LOG.md` — ADV-015 BLOCKER advisory (resolved)
