# Phase 55 — Critical Issues Remediation

**Goal**: Address the comprehensive critical issues list synthesized from the
staff-level architecture review (2026-03-24) and all review agent findings across
Phases 45–53. Prioritized by production impact.

**Prerequisite**: Phase 54 merged.

**Source**: Staff-level architecture review (all tiers: architecture, security,
QA, DevOps, red-team), RETRO_LOG advisory history, and full codebase audit.

---

## Critical Issues — Full List

### Tier 1: Will Break in Production

| ID | Issue | Source | Impact |
|----|-------|--------|--------|
| CI-01 | Per-worker vault state — no cross-worker unseal coordination | Arch review | Requests hit sealed workers randomly (423) |
| CI-02 | Pickle deserialization without class restriction | Red-team review | Full RCE if HMAC key compromised |
| CI-03 | Audit chain restart on process restart — gaps undetectable without anchor | Arch review | Compliance audit failure |
| CI-04 | SSRF fail-open at webhook registration on DNS failure | Red-team review, P45 retro | Internal service access via webhook |
| CI-05 | Single-operator authorization model — no RBAC (ADV-P47-05) | Red-team P47 | Privilege escalation in multi-operator deployment |

### Tier 2: Security Hardening Required

| ID | Issue | Source | Impact |
|----|-------|--------|--------|
| CI-06 | Audit HMAC does not cover `details` field (ADV-P49-02) | Red-team P49 | Audit log detail tampering |
| CI-07 | Rate limiter in-memory fallback multiplied by pod count | Red-team review | Rate limit bypass in multi-pod |
| CI-08 | No CSP nonce strategy for SPA if served from same origin | Arch review | XSS risk if SPA collocated |
| CI-09 | `passlib` dependency unused in `src/` — dead supply chain surface | Dependency audit | Unnecessary attack surface |
| CI-10 | `Any` escape hatches in `main.py` (lines 117, 136) | QA review | Type safety holes at DI boundary |

### Tier 3: Operational Resilience

| ID | Issue | Source | Impact |
|----|-------|--------|--------|
| CI-11 | Bootstrapper `main.py` module-scope side effects — fragile import order | Arch review | Silent task drops on import order change |
| CI-12 | No health endpoint reporting vault seal status per-worker | Arch review | Operator cannot verify cluster-wide unseal |
| CI-13 | Synthesizer module size asymmetry (5,199 LOC, 24 files, 6+ responsibilities) | Arch review | Cognitive load, maintenance burden |
| CI-14 | `test_synthesis_engine_train_raises_on_empty_parquet` ordering-sensitive flaky | QA P47 retro | False failures in CI |
| CI-15 | Redis TLS URL promotion duplicated (ADV-P47-02) | Arch P47 | Maintenance divergence risk |

### Tier 4: Test & Documentation Gaps

| ID | Issue | Source | Impact |
|----|-------|--------|--------|
| CI-16 | 59 test files with `is not None` as sole assertion | QA review | Shallow test coverage |
| CI-17 | Mock assertion concentration in 10 files — transactional tests lack integration coverage | QA review | False confidence in mock-heavy areas |
| CI-18 | Large test files (5 files > 1,000 LOC) | QA review | Maintenance burden |
| CI-19 | Constitution enforcement table has 3 ADVISORY rows without programmatic gates | Arch review | Convention-only enforcement |
| CI-20 | `docker-compose.yml` still mounts `chroma_data` volume (dead reference) | DevOps review | Unnecessary volume provisioning |

---

## T55.1 — Vault State Health Endpoint & Multi-Worker Coordination

**Priority**: P0 — Production reliability.

### Context & Constraints

1. `VaultState` is a class-level singleton. Each Uvicorn worker maintains
   independent vault state. There is no mechanism to verify all workers are
   unsealed or to unseal all workers atomically.
2. The `/health` endpoint currently does not report vault seal status.
3. A load balancer routing to a sealed worker returns 423 to the client with
   no diagnostic information.
4. Fix: Add vault seal status to the health/readiness response. Add a
   `/health/vault` endpoint that reports seal status for the responding worker.
5. Document the multi-worker unseal procedure in OPERATOR_MANUAL.md.
6. Consider a startup hook that blocks the worker readiness probe until
   unsealed (so load balancers naturally route around sealed workers).

### Acceptance Criteria

1. `/health` response includes `vault_sealed: bool` field.
2. Readiness probe (`/ready`) returns 503 while vault is sealed.
3. Kubernetes readinessProbe can use `/ready` to exclude sealed workers.
4. OPERATOR_MANUAL.md documents multi-worker unseal procedure.
5. Attack tests: sealed worker returns 503 on readiness, unsealed returns 200.
6. Full gate suite passes.

---

## T55.2 — Replace Pickle with Safe Serialization for Model Artifacts

**Priority**: P0 — Security (RCE elimination).

### Context & Constraints

1. `modules/synthesizer/models.py` uses `pickle.dumps` / `pickle.loads` for
   model artifact serialization. HMAC signing prevents tampering but if the
   signing key is compromised, pickle deserialization is arbitrary code execution.
2. Replace pickle with a safe serialization approach. Options:
   a. `safetensors` for model weights + JSON for metadata (preferred if SDV models
      support weight extraction).
   b. Custom JSON + binary format with explicit schema validation.
   c. Restricted unpickler that only allows known model classes (defense-in-depth
      if full replacement is infeasible due to SDV internal state).
3. If SDV's `CTGANSynthesizer` internal state cannot be safely extracted from
   pickle, implement a restricted unpickler as minimum viable fix and document
   the limitation in an ADR.
4. The HMAC signature must still be verified before any deserialization.

### Acceptance Criteria

1. Model artifacts no longer use unrestricted `pickle.loads`.
2. Either: safe serialization format adopted, OR restricted unpickler with
   allowlisted classes implemented.
3. HMAC verification occurs before any deserialization (unchanged).
4. ADR documenting the serialization strategy.
5. Attack test: tampered artifact rejected; arbitrary class in pickle rejected.
6. Existing model artifact tests pass with new serialization.
7. Full gate suite passes.

---

## T55.3 — Audit Chain Continuity Across Restarts

**Priority**: P0 — Compliance.

### Context & Constraints

1. `AuditLogger` singleton resets to `GENESIS_HASH` on process restart.
2. An attacker who causes a crash-restart can start a fresh chain, creating
   an undetectable gap unless anchoring catches it.
3. Fix: Persist the chain head (prev_hash + entry_count) to a durable store
   (database or file) on each anchor. On startup, load the last persisted
   chain head and resume from there.
4. The genesis sentinel should only appear once in a deployment's lifetime.
   A second genesis event should be flagged as a potential tampering indicator.
5. Consider adding a `CHAIN_RESUMED` audit event on startup that records the
   loaded chain head, linking the new process to the previous chain.

### Acceptance Criteria

1. Chain head persisted to durable store on each anchor.
2. On startup, chain resumes from last persisted head (not genesis).
3. `CHAIN_RESUMED` audit event logged on startup with previous chain head.
4. Second genesis event logged as WARNING (potential gap indicator).
5. Attack test: process restart resumes chain (not genesis).
6. Integration test: chain continuity across simulated restart.
7. Full gate suite passes.

---

## T55.4 — SSRF Registration Fail-Closed

**Priority**: P0 — Security.

### Context & Constraints

1. `shared/ssrf.py` line 102-109: DNS resolution failures are fail-open at
   registration time ("treating as safe").
2. This means a webhook registration to an internal hostname succeeds if DNS
   is temporarily unreachable.
3. Fix: fail-closed at registration time (reject if DNS resolution fails).
   The delivery-time check can remain fail-closed as defense-in-depth.
4. Add a `strict` parameter to `validate_callback_url()` — `strict=True`
   (registration) fails on DNS error, `strict=False` (delivery fallback)
   remains fail-open.

### Acceptance Criteria

1. Webhook registration rejects URLs when DNS resolution fails.
2. `validate_callback_url(url, strict=True)` raises on DNS failure.
3. Delivery-time check uses `strict=False` (existing behavior preserved).
4. Attack test: registration with unresolvable hostname fails.
5. Existing SSRF tests pass (delivery path unchanged).
6. Full gate suite passes.

---

## T55.5 — Eliminate Dead Dependencies and Type Safety Holes

**Priority**: P5 — Code quality.

### Context & Constraints

1. `passlib` is in production dependencies but has zero imports in `src/`.
   It was superseded by direct `cryptography` usage. Remove from main deps.
2. `main.py:117` uses `backend_cls: Any` to bypass mypy on conditional import.
   Replace with proper type narrowing.
3. `main.py:136` returns `Any` instead of `Callable[[int, str], None]`.
   Add proper return type annotation.
4. Remove `chromadb` from dev dependencies (P53 cleanup — scripts deleted).
5. Remove chromadb-related pytest ignore entries from `pyproject.toml`.
6. Remove chromadb warning filters from `tests/conftest.py`.
7. Remove `chroma_data` volume from `docker-compose.yml` and
   `docker-compose.override.yml`.
8. Remove ChromaDB section from `.env.example`.
9. Delete `scripts/seed_chroma.py`, `scripts/seed_chroma_retro.py`,
   `scripts/init_chroma.py`.
10. Delete `tests/unit/test_seed_chroma.py`, `tests/unit/test_init_chroma.py`.
11. Update `tests/unit/test_dependency_audit.py` to remove chromadb assertions.
12. Update `tests/unit/test_ci_infrastructure.py` to remove chromadb reference.
13. Update `scripts/setup_agile_env.sh` to remove chromadb references.

### Acceptance Criteria

1. `passlib` removed from main dependency group.
2. `chromadb` removed from dev dependency group.
3. All chromadb scripts deleted.
4. All chromadb test files deleted.
5. `Any` escape hatches in `main.py` replaced with proper types.
6. `docker-compose.yml` and `docker-compose.override.yml` have no `chroma_data`.
7. `.env.example` has no ChromaDB section.
8. `pyproject.toml` has no chromadb references (deps, warning filters, ignores).
9. `tests/conftest.py` has no chromadb warning filter.
10. All chromadb-referencing tests updated or removed.
11. `poetry lock` regenerated.
12. Full gate suite passes.

---

## T55.6 — Flaky Test Resolution

**Priority**: P4 — Test reliability.

### Context & Constraints

1. `test_synthesis_engine_train_raises_on_empty_parquet` is documented as
   ordering-sensitive (P47 retro). It passes individually but fails in
   certain suite orderings due to state pollution.
2. Investigate the root cause (likely Prometheus registry state or module-scope
   singleton bleed) and fix.
3. Scan for other ordering-sensitive tests using `pytest --randomly-seed=...`
   runs.

### Acceptance Criteria

1. Root cause of flaky test identified and fixed.
2. Test passes reliably in any ordering (`pytest --randomly-seed` verified).
3. No new flaky tests introduced.
4. Full gate suite passes.

---

## Task Execution Order

```
T55.5 (dead deps + chromadb cleanup) ──> foundation (unblocks CI)
T55.1 (vault health) ────────────────┐
T55.2 (pickle replacement) ──────────┼──> parallel after T55.5
T55.3 (audit chain continuity) ──────┤
T55.4 (SSRF fail-closed) ───────────┘
T55.6 (flaky test) ──────────────────> independent, any time
```

---

## Phase 55 Exit Criteria

1. Vault seal status reported in health endpoint; sealed workers excluded by
   readiness probe.
2. Model artifacts no longer use unrestricted `pickle.loads`.
3. Audit chain resumes from persisted head on process restart.
4. Webhook registration fails when DNS resolution fails.
5. `passlib` and `chromadb` removed from dependencies.
6. Type safety holes in `main.py` eliminated.
7. `chroma_data` volume removed from Docker Compose.
8. Flaky test fixed and verified with random ordering.
9. All open Tier 1 and Tier 2 critical issues resolved.
10. All quality gates pass.
11. Review agents pass for all tasks.
