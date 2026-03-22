# Phase 48 — Production-Critical Infrastructure Fixes

**Goal**: Fix five critical infrastructure issues that will cause failures in
horizontally scaled, multi-node, or Kubernetes production deployments.

**Prerequisite**: Phase 47 merged. Zero open advisories.

**ADR**: None required — remediation of existing infrastructure gaps.

**Source**: Production readiness audit, 2026-03-22 — operator-identified issues
that will break under real production conditions.

---

## T48.1 — Redis-Backed Rate Limiting

**Priority**: P0 — Security. In-memory rate limiting is ineffective in
multi-process/multi-node deployments.

### Context & Constraints

1. The current `RateLimitGateMiddleware` uses in-memory counters. In any
   horizontally scaled deployment (multiple uvicorn workers, multiple pods),
   each process maintains its own counter. An attacker's effective rate is
   multiplied by the number of processes.

2. The `/unseal` endpoint brute-force protection is the highest-risk instance —
   an attacker can try N × (number of workers) attempts per window.

3. Redis infrastructure already exists (used by idempotency middleware P45,
   orphan task reaper P45). Moving rate limiting to Redis is a natural fit.

4. The Redis client must use the same graceful degradation pattern established
   in P45: if Redis is unavailable, fall back to in-memory limiting with a
   WARNING log (defense-in-depth, not silent failure).

5. Rate limit keys must be scoped per-IP (unauthenticated) and per-operator
   (authenticated), consistent with the current implementation.

### Acceptance Criteria

1. Rate limiting uses Redis `INCR` + `EXPIRE` (or `SET NX EX` + `INCR`)
   for distributed counting.
2. `/unseal` brute-force protection is Redis-backed and consistent across
   all workers/pods.
3. Graceful degradation: falls back to in-memory when Redis is unavailable,
   with WARNING log.
4. Per-IP and per-operator scoping preserved.
5. Rate limit headers (`X-RateLimit-Remaining`, `Retry-After`) still correct.
6. Existing rate limit tests updated for Redis-backed implementation.
7. New integration test: verify rate limit is shared across simulated workers.
8. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/bootstrapper/dependencies/rate_limit.py`
- Modify: `src/synth_engine/shared/settings.py` (rate limit Redis settings
  if distinct from idempotency)
- Modify: `tests/unit/test_rate_limit.py`
- Create: `tests/integration/test_rate_limit_redis.py`

---

## T48.2 — Connection Pooling for Huey Workers

**Priority**: P0 — Reliability. `NullPool` in Huey workers will exhaust
`max_connections` under concurrent load.

### Context & Constraints

1. Huey workers use `NullPool` (per ADR-0035), meaning each database operation
   opens a fresh connection and discards it. This was chosen to avoid
   connection leaks in task workers.

2. Under concurrent synthesis jobs with overlapping DB operations, fresh
   connections accumulate and hit PostgreSQL's `max_connections` limit,
   causing `OperationalError: FATAL: too many connections`.

3. ADR-0035's own rationale supports `QueuePool(pool_size=1, max_overflow=2)`
   for worker processes — bounded pooling that prevents both leaks and
   exhaustion.

4. The fix must ensure connections are properly returned to the pool after
   each task completes (session cleanup in task wrapper or `finally` block).

5. PgBouncer is in the connection path — pool sizing must account for
   PgBouncer's own `max_client_conn` and `default_pool_size`.

### Acceptance Criteria

1. Huey worker engine uses `QueuePool(pool_size=1, max_overflow=2)`.
2. Sessions properly closed/returned after each task execution.
3. Concurrent job test: 5+ simultaneous jobs do not exhaust connections.
4. ADR-0035 amended to document the pool sizing rationale for workers.
5. Existing worker tests updated.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/db.py` (worker engine factory)
- Modify: `src/synth_engine/shared/tasks/` (session cleanup)
- Amend: `docs/adr/ADR-0035-dual-driver-database-architecture.md`
- Modify: `tests/unit/test_db.py`
- Create: `tests/integration/test_worker_connection_pool.py`

---

## T48.3 — Readiness Probe & External Dependency Health Checks

**Priority**: P0 — Operational. No readiness probe for Kubernetes deployments.

### Context & Constraints

1. The application validates config at startup (`validate_config()`) but does
   not verify Redis connectivity, MinIO availability, or PostgreSQL
   reachability before accepting traffic.

2. In Kubernetes, the pod is marked "Ready" as soon as the HTTP server binds.
   The load balancer routes requests to pods that cannot fulfill them because
   external dependencies are unavailable.

3. Fix: Add a `/ready` endpoint that checks all external dependencies:
   - PostgreSQL: execute `SELECT 1`
   - Redis: execute `PING`
   - MinIO: list buckets or head the configured bucket
   - Return 200 only if all checks pass; 503 with details otherwise.

4. The `/ready` endpoint must be exempt from authentication (it's called by
   infrastructure, not operators) but should NOT be exempt from rate limiting.

5. Add to `AUTH_EXEMPT_PATHS` in the auth middleware.

6. Kubernetes `readinessProbe` and `livenessProbe` configuration should be
   documented (not just the endpoint — the probe YAML).

### Acceptance Criteria

1. `GET /ready` returns 200 when all dependencies are reachable.
2. `GET /ready` returns 503 with structured error when any dependency fails.
3. Individual dependency check results included in response body.
4. Endpoint exempt from authentication but subject to rate limiting.
5. `/ready` added to `AUTH_EXEMPT_PATHS`.
6. Kubernetes probe configuration documented in `PRODUCTION_DEPLOYMENT.md`.
7. Docker Compose healthcheck updated to use `/ready`.
8. Unit tests: each dependency failure returns 503 with correct detail.
9. Integration test: full readiness check with real dependencies.
10. Full gate suite passes.

### Files to Create/Modify

- Create: `src/synth_engine/bootstrapper/routers/health.py`
- Modify: `src/synth_engine/bootstrapper/router_registry.py`
- Modify: `src/synth_engine/bootstrapper/dependencies/auth.py`
  (`AUTH_EXEMPT_PATHS`)
- Modify: `docs/PRODUCTION_DEPLOYMENT.md` (K8s probe config)
- Modify: `docker-compose.yml` (healthcheck)
- Create: `tests/unit/test_readiness_probe.py`
- Create: `tests/integration/test_readiness_probe.py`

---

## T48.4 — Immutable Audit Trail Anchoring

**Priority**: P1 — Compliance. Audit trail can be silently rewritten if
`AUDIT_KEY` is compromised.

### Context & Constraints

1. The HMAC chain in the WORM audit logger provides tamper-detection but not
   tamper-proof storage. If `AUDIT_KEY` is compromised, an attacker can
   rewrite the entire chain and recompute all HMACs.

2. For a compliance-focused product, regulators will question audit trail
   integrity when the signing key is a single point of failure.

3. Fix: Implement periodic hash publication to an immutable external store.
   Options (in order of preference):
   - **Append-only cloud storage** (S3 with Object Lock, GCS with retention
     policy) — simplest, most accessible.
   - **RFC 3161 timestamping** via a public TSA — cryptographic proof of
     existence at a point in time.
   - **Blockchain-based anchoring** (e.g., OpenTimestamps on Bitcoin) — most
     tamper-resistant but adds external dependency.

4. The anchoring mechanism must be pluggable — operators choose their backend.
   Default: local file (for air-gapped deployments) with WARNING that this
   provides no external attestation.

5. Anchoring frequency: configurable, default every 1000 audit entries or
   every 24 hours, whichever comes first.

6. The anchor record must include: chain head hash, entry count, timestamp,
   and the anchoring mechanism used.

### Acceptance Criteria

1. Periodic audit chain anchor published to configurable external store.
2. At least two backend implementations: local file + S3 Object Lock.
3. Anchor record includes chain head hash, entry count, timestamp.
4. Anchoring frequency configurable (entry count and time interval).
5. Verification utility: `scripts/verify-audit-chain.sh` or Python CLI
   that validates the chain against published anchors.
6. ADR documenting the anchoring architecture and threat model.
7. Unit tests: anchor generation, frequency triggers, backend abstraction.
8. Full gate suite passes.

### Files to Create/Modify

- Create: `src/synth_engine/shared/security/audit_anchor.py`
- Modify: `src/synth_engine/shared/security/audit.py` (trigger anchoring)
- Modify: `src/synth_engine/shared/settings.py` (anchoring settings)
- Create: `scripts/verify-audit-chain.py`
- Create: `docs/adr/ADR-0045-audit-trail-anchoring.md`
- Create: `tests/unit/test_audit_anchor.py`

---

## T48.5 — ALE Vault Dependency Enforcement

**Priority**: P1 — Security. ALE key fallback to environment variable when
vault is sealed creates a decryption window.

### Context & Constraints

1. When the vault is sealed, ALE operations fall back to reading the
   encryption key from an environment variable. This means encrypted
   connection credentials are decryptable without vault authentication —
   the vault seal provides no actual protection.

2. This undermines the entire vault architecture: if the key is always
   available via env var fallback, the vault is security theater.

3. Fix: Remove the env var fallback for ALE keys. When the vault is sealed:
   - Reject any request that requires ALE decryption with 423 Locked.
   - Log the rejection to the audit trail.
   - The `SealGateMiddleware` already returns 423 for most endpoints, but
     ALE decryption can happen in background tasks (Huey workers) that
     bypass the middleware.

4. Huey tasks that need ALE decryption must check vault status before
   attempting decryption and fail gracefully if sealed.

5. The startup sequence must still work: the vault must be unsealed before
   the application can process any requests that involve encrypted data.

### Acceptance Criteria

1. ALE operations fail with clear error when vault is sealed (no env var
   fallback).
2. Background tasks (Huey) check vault status before ALE operations.
3. Startup validation warns if vault is sealed (but doesn't prevent startup
   — operator unseals via API).
4. Existing SealGateMiddleware behavior preserved for API requests.
5. Audit trail entry for every ALE operation rejected due to sealed vault.
6. Unit tests: ALE fails when sealed, Huey task fails gracefully when sealed,
   unsealed operations work normally.
7. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/security/ale.py` (remove env var fallback)
- Modify: `src/synth_engine/shared/security/vault.py` (seal check helper)
- Modify: `src/synth_engine/shared/tasks/` (vault check before ALE)
- Modify: `src/synth_engine/bootstrapper/config_validation.py` (startup
  warning)
- Modify: `tests/unit/test_ale.py`
- Create: `tests/unit/test_ale_vault_dependency.py`

---

## Task Execution Order

```
T48.1 (Redis rate limiting) ──────┐
T48.2 (Worker connection pooling) ┼──> parallel (infrastructure fixes)
T48.3 (Readiness probe) ──────────┘
                                     ↓ infrastructure fixes complete
T48.4 (Audit trail anchoring) ──┐
T48.5 (ALE vault enforcement) ──┼──> parallel (security hardening)
                                 ┘
```

T48.1, T48.2, and T48.3 are independent infrastructure fixes that can run in
parallel. T48.4 and T48.5 are security hardening tasks that can also run in
parallel with each other but should follow the infrastructure fixes.

---

## Phase 48 Exit Criteria

1. Rate limiting is Redis-backed and consistent across all workers/pods.
2. Huey workers use bounded connection pooling.
3. `/ready` endpoint checks all external dependencies.
4. Audit trail publishes periodic anchors to immutable external store.
5. ALE operations require vault unseal — no env var fallback.
6. All quality gates pass.
7. Zero open advisories in RETRO_LOG.
8. Review agents pass for all tasks.
