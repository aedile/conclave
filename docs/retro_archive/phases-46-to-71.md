# Archived Phase Specifications — Phases 46 to 71

Archived during T74.6 documentation cleanup. These phase specifications
are retained for historical reference but are no longer active backlog items.

---

# Phase 46 — mTLS Inter-Container Communication

**Goal**: Implement mutual TLS for all inter-container communication paths,
enabling secure multi-host and Kubernetes deployments where traffic traverses
shared network infrastructure.

**Prerequisite**: Phase 45 merged. Zero open advisories.

**ADR**: ADR-0042 — mTLS Inter-Container Communication Architecture (new, required).
Must document: certificate issuance strategy, rotation mechanism, deployment
topology requirements, and fallback behavior during certificate renewal.

**Source**: Deferred Items (ADR-0029 Gap Analysis) — TBD-03.

---

## T46.1 — Internal Certificate Authority & Certificate Issuance

**Priority**: P1 — Infrastructure prerequisite for all mTLS connections.

### Context & Constraints

1. `mTLS Inter-Container Communication` was deferred in ADR-0029 (Gap 7) because
   single-host Docker Compose deployments use kernel-level network isolation.
   Multi-host deployments (Kubernetes, Docker Swarm) require mTLS to protect
   traffic traversing shared infrastructure.

2. Implement an internal CA or integrate with cert-manager for automatic
   certificate issuance to each container identity:
   - API server (synth-engine)
   - PostgreSQL (via PgBouncer)
   - Redis
   - Huey worker(s)

3. Certificates must include SAN entries matching container hostnames used in
   Docker Compose and Kubernetes service names.

4. Certificate storage must use the existing `secrets/` directory pattern
   (gitignored, operator-provisioned) for Docker Compose, and Kubernetes
   Secrets or cert-manager for K8s deployments.

5. The CA private key must be protected with the same security posture as
   the vault KEK — never committed, never logged, operator-provisioned.

### Acceptance Criteria

1. Internal CA script or cert-manager integration generates per-container certs.
2. CA root certificate distributed to all containers as a trust anchor.
3. Certificates include correct SANs for both Docker Compose and K8s hostnames.
4. Certificate generation documented in operator manual.
5. Unit tests: certificate generation, SAN validation.
6. Full gate suite passes.

### Files to Create/Modify

- Create: `scripts/generate-mtls-certs.sh` (internal CA + cert generation)
- Create: `src/synth_engine/shared/tls/` (TLS configuration helpers)
- Modify: `docs/OPERATOR_MANUAL.md` (mTLS setup section)
- Modify: `docs/PRODUCTION_DEPLOYMENT.md` (certificate provisioning steps)
- Create: `tests/unit/test_tls_config.py`

---

## T46.2 — Wire mTLS on All Container-to-Container Connections

**Priority**: P1 — Core mTLS implementation.

### Context & Constraints

1. All container-to-container connections must use mutual TLS:
   - **API → PostgreSQL** (via PgBouncer): Configure `sslmode=verify-full`
     in SQLAlchemy connection string. PgBouncer must present a server cert
     and verify the API client cert.
   - **API → Redis**: Configure Redis TLS with client certificate
     authentication. Update Huey and idempotency middleware (Phase 45)
     Redis clients.
   - **API → Huey worker**: Huey uses Redis as the message broker — this
     path is covered by Redis mTLS.

2. Docker Compose must support both plaintext (development) and mTLS
   (production) modes via environment variable toggle:
   `MTLS_ENABLED=true|false` (default: `false` for backward compatibility).

3. PgBouncer configuration (`pgbouncer/pgbouncer.ini`) must be updated to
   support TLS server and client certificate verification.

4. Redis configuration must be updated to require TLS when `MTLS_ENABLED=true`.

5. Connection string construction in `ConclaveSettings` must conditionally
   include TLS parameters based on the `MTLS_ENABLED` flag.

### Acceptance Criteria

1. API → PostgreSQL connection uses `sslmode=verify-full` when mTLS enabled.
2. API → Redis connection uses TLS with client cert when mTLS enabled.
3. PgBouncer configured for TLS server cert and client cert verification.
4. Redis configured for TLS with client authentication.
5. `MTLS_ENABLED` toggle in `ConclaveSettings` with `false` default.
6. Docker Compose override file for mTLS-enabled deployment.
7. Plaintext connections rejected when mTLS is enabled (smoke test).
8. Unit tests: connection string construction with/without TLS parameters.
9. Integration test: full connection through mTLS-enabled PgBouncer.
10. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/settings.py` (mTLS settings)
- Modify: `src/synth_engine/bootstrapper/factories.py` (TLS connection params)
- Modify: `src/synth_engine/bootstrapper/dependencies/redis.py` (TLS Redis client)
- Modify: `pgbouncer/pgbouncer.ini` (TLS configuration)
- Create: `docker-compose.mtls.yml` (mTLS override)
- Modify: `docker-compose.yml` (conditional TLS volume mounts)
- Create: `tests/unit/test_mtls_settings.py`
- Create: `tests/integration/test_mtls_connections.py`

---

## T46.3 — Certificate Rotation Without Downtime

**Priority**: P1 — Operational requirement for production mTLS.

### Context & Constraints

1. Certificates have finite lifetimes (recommended: 90 days for leaf certs,
   1 year for internal CA). Rotation must not cause service downtime.

2. Implement certificate rotation strategy:
   - **Docker Compose**: Operator replaces cert files in `secrets/` and
     sends SIGHUP to containers (or restarts with rolling strategy).
   - **Kubernetes**: cert-manager handles automatic renewal; containers
     watch for cert file changes and reload.

3. The API server (uvicorn) must support TLS certificate reload without
   full process restart. If uvicorn doesn't support dynamic reload,
   document the rolling restart procedure.

4. PgBouncer supports `RELOAD` command for certificate refresh.

5. Redis supports `CONFIG SET tls-cert-file` for dynamic cert reload.

### Acceptance Criteria

1. Certificate rotation procedure documented for Docker Compose deployment.
2. Certificate rotation procedure documented for Kubernetes deployment.
3. PgBouncer cert reload verified via `RELOAD` command.
4. Redis cert reload verified via `CONFIG SET`.
5. No client connection drops during certificate rotation (or documented
   reconnection behavior with retry).
6. Monitoring: certificate expiry metric exposed via `/metrics` endpoint.
7. Unit tests: certificate expiry detection, metric emission.
8. Full gate suite passes.

### Files to Create/Modify

- Create: `scripts/rotate-mtls-certs.sh` (rotation helper)
- Modify: `src/synth_engine/shared/telemetry.py` (cert expiry metric)
- Modify: `docs/OPERATOR_MANUAL.md` (rotation procedures)
- Modify: `docs/DISASTER_RECOVERY.md` (cert loss recovery)
- Create: `tests/unit/test_cert_expiry_metric.py`

---

## T46.4 — Network Policy Enforcement & Documentation

**Priority**: P2 — Defense-in-depth for Kubernetes deployments.

### Context & Constraints

1. For Kubernetes deployments, provide NetworkPolicy manifests that enforce
   mTLS-only communication paths between pods.

2. Document the threat model: what mTLS protects against (network sniffing,
   MITM on shared infrastructure) and what it does not (compromised container,
   kernel exploit).

3. Update ADR-0029 to mark Gap 7 (mTLS) as DELIVERED with Phase 46 reference.

### Acceptance Criteria

1. Kubernetes NetworkPolicy manifests for all inter-container paths.
2. Threat model documented in ADR-0042.
3. Smoke test: plaintext connections rejected when mTLS enforced.
4. ADR-0029 updated with Phase 46 assignment for Gap 7.
5. `docs/backlog/deferred-items.md` TBD-03 marked DELIVERED with Phase 46.
6. Full gate suite passes.

### Files to Create/Modify

- Create: `k8s/network-policies/` (NetworkPolicy manifests)
- Create: `docs/adr/ADR-0042-mtls-inter-container-communication.md`
- Modify: `docs/adr/ADR-0029-architectural-requirements-gap-analysis.md`
- Modify: `docs/backlog/deferred-items.md`
- Modify: `docs/infrastructure_security.md` (mTLS section)

---

## Task Execution Order

```
T46.1 (Internal CA & Certs) ──────> first (prerequisite for all connections)
T46.2 (Wire mTLS connections) ───> after T46.1 (needs certificates)
T46.3 (Certificate rotation) ────> after T46.2 (needs working mTLS)
T46.4 (Network policy & docs) ──> LAST (documents everything)
```

Sequential execution — each task builds on the previous.

---

## Phase 46 Exit Criteria

1. Internal CA generates per-container certificates with correct SANs.
2. All container-to-container connections use mTLS when enabled.
3. Plaintext connections rejected when mTLS is enforced.
4. Certificate rotation documented and tested for both Docker Compose and K8s.
5. Certificate expiry metric exposed via `/metrics`.
6. Kubernetes NetworkPolicy manifests provided.
7. ADR-0042 documents the mTLS architecture and threat model.
8. ADR-0029 updated with Phase 46 assignment.
9. TBD-03 marked DELIVERED in deferred items.
10. All quality gates pass.
11. Zero open advisories in RETRO_LOG.
12. Review agents pass for all tasks.


---

# Phase 47 — Auth Gap Remediation, Safety Hardening & Operational Fixes

**Goal**: Close critical authentication gaps on security/privacy/settings endpoints,
harden model artifact verification, add memory safety bounds, fix shutdown resource
leaks, and tighten operational observability for production readiness.

**Prerequisite**: Phase 46 merged. Zero open advisories.

**ADR**: None required — remediation of existing endpoints and operational hardening.

**Source**: Production Readiness Audit, 2026-03-21 — P0 items 1-6, P1 items 7-9, P2 items 10-11.

---

## T47.1 — Add Authentication to `/security` Endpoints

**Priority**: P0 — Security. `/security/shred` and `/security/keys/rotate` have no
`Depends(get_current_operator)`. Any anonymous request can zeroize the vault KEK
(permanently destroying all ALE-encrypted data) or trigger key rotation.

### Context & Constraints

1. `src/synth_engine/bootstrapper/routers/security.py:69` — the `/security/shred`
   endpoint has no auth dependency. An unauthenticated caller can invoke cryptographic
   shredding, permanently destroying all ALE-encrypted data.

2. `src/synth_engine/bootstrapper/routers/security.py:128` — the `/security/keys/rotate`
   endpoint has no auth dependency. An unauthenticated caller can trigger key rotation.

3. Fix: Add `current_operator: str = Depends(get_current_operator)` to both endpoint
   signatures. These are the most destructive operations in the system and must require
   authenticated operator access.

4. Both endpoints should additionally require an elevated scope (e.g., `security:admin`)
   to prevent accidental invocation by regular authenticated users.

### Acceptance Criteria

1. `/security/shred` requires `Depends(get_current_operator)`.
2. `/security/keys/rotate` requires `Depends(get_current_operator)`.
3. Both endpoints return 401 for unauthenticated requests.
4. Both endpoints return 403 for authenticated users without `security:admin` scope.
5. Existing authenticated callers with correct scope can still invoke both endpoints.
6. New tests: unauthenticated shred returns 401, unauthenticated rotate returns 401,
   wrong scope returns 403, correct scope succeeds.
7. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/bootstrapper/routers/security.py`
- Create: `tests/unit/test_security_router_auth.py`

---

## T47.2 — Add Authentication to `/privacy/budget` Endpoints

**Priority**: P0 — Security. `/privacy/budget` GET and POST endpoints have no auth
dependency. Anyone can read or refresh the differential privacy epsilon budget.

### Context & Constraints

1. `src/synth_engine/bootstrapper/routers/privacy.py:183,213` — both the budget read
   and budget refresh endpoints lack `Depends(get_current_operator)`.

2. The epsilon budget is a finite privacy resource. Unauthorized reads leak privacy
   posture; unauthorized refreshes could reset spend tracking, violating DP guarantees.

3. Fix: Add `current_operator: str = Depends(get_current_operator)` to both endpoint
   signatures.

### Acceptance Criteria

1. `/privacy/budget` GET requires authentication.
2. `/privacy/budget` POST requires authentication.
3. Unauthenticated requests return 401.
4. Authenticated requests work as before.
5. New tests: unauthenticated budget read returns 401, unauthenticated budget refresh
   returns 401, authenticated calls succeed.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/bootstrapper/routers/privacy.py`
- Create: `tests/unit/test_privacy_router_auth.py`

---

## T47.3 — Add Authentication to All `/settings` CRUD Endpoints

**Priority**: P0 — Security. All four `/settings` CRUD endpoints have no auth
dependency. Anyone can read or modify application configuration including encryption
keys and database URLs.

### Context & Constraints

1. `src/synth_engine/bootstrapper/routers/settings.py:31,49,79,106` — GET, POST, PUT,
   DELETE endpoints all lack `Depends(get_current_operator)`.

2. Settings may contain sensitive values (database URLs, encryption keys, feature flags).
   Unauthenticated modification could compromise the entire system.

3. Fix: Add `current_operator: str = Depends(get_current_operator)` to all four
   endpoint signatures. Write endpoints (POST, PUT, DELETE) should additionally require
   an elevated scope (e.g., `settings:write`).

### Acceptance Criteria

1. All four `/settings` endpoints require authentication.
2. Write endpoints (POST, PUT, DELETE) require `settings:write` scope.
3. Unauthenticated requests return 401.
4. Authenticated read-only users can GET but not POST/PUT/DELETE (403).
5. New tests: unauthenticated CRUD returns 401, unauthorized write returns 403,
   authorized calls succeed.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/bootstrapper/routers/settings.py`
- Create: `tests/unit/test_settings_router_auth.py`

---

## T47.4 — Fail-Fast on Empty `JWT_SECRET_KEY`

**Priority**: P0 — Security. Empty `JWT_SECRET_KEY` silently disables ALL
authentication. Middleware returns pass-through, `get_current_operator()` returns `""`.
Only a WARNING log is emitted. No fail-fast.

### Context & Constraints

1. `src/synth_engine/bootstrapper/dependencies/auth.py:219-222,329-340` — when
   `JWT_SECRET_KEY` is empty or unset, the auth middleware becomes a no-op and
   `get_current_operator()` returns an empty string, effectively disabling all
   authentication silently.

2. This is a silent security failure. A misconfigured production deployment has zero
   authentication with no obvious error.

3. Fix: In production mode (`is_production() == True`), raise a `RuntimeError` at
   startup if `JWT_SECRET_KEY` is empty or unset. In development mode, log a WARNING
   but allow startup (for local development convenience).

4. Update `config_validation.py` startup checks to include `JWT_SECRET_KEY` validation.

### Acceptance Criteria

1. Empty `JWT_SECRET_KEY` in production mode raises `RuntimeError` at startup.
2. Empty `JWT_SECRET_KEY` in development mode logs WARNING but allows startup.
3. Non-empty `JWT_SECRET_KEY` works normally in both modes.
4. Startup validation message clearly states the issue and remediation.
5. New tests: empty key in production raises, empty key in dev warns, valid key passes.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/bootstrapper/dependencies/auth.py`
- Modify: `src/synth_engine/bootstrapper/config_validation.py`
- Create: `tests/unit/test_jwt_secret_validation.py`

---

## T47.5 — Validate `OPERATOR_CREDENTIALS_HASH` at Startup

**Priority**: P0 — Security. `config_validation.py` validates DB_URL, SIGNING_KEY,
MASKING_SALT but NOT operator credentials. The app starts in production with no way to
authenticate.

### Context & Constraints

1. `src/synth_engine/bootstrapper/config_validation.py:71-101` validates several
   critical configuration values at startup but omits `OPERATOR_CREDENTIALS_HASH`.

2. Without a valid credentials hash, the operator login endpoint has nothing to verify
   against, making the authentication system non-functional even when JWT is configured.

3. Fix: Add `OPERATOR_CREDENTIALS_HASH` to the startup validation checklist. In
   production mode, fail-fast if the value is empty, unset, or not a valid bcrypt/argon2
   hash format. In development mode, log a WARNING.

### Acceptance Criteria

1. Missing `OPERATOR_CREDENTIALS_HASH` in production mode raises `RuntimeError`.
2. Invalid hash format in production mode raises `RuntimeError`.
3. Development mode logs WARNING but allows startup.
4. Valid hash passes validation in both modes.
5. New tests: missing hash raises, invalid format raises, valid hash passes.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/bootstrapper/config_validation.py`
- Create: `tests/unit/test_operator_credentials_validation.py`

---

## T47.6 — Harden Model Artifact Signature Verification

**Priority**: P1 — Security. `_looks_signed()` uses a byte-offset heuristic that can
be fooled by a crafted 32-byte preamble, bypassing unsigned pickle detection.

### Context & Constraints

1. `src/synth_engine/modules/synthesizer/models.py:68-90,254` — the `_looks_signed()`
   method checks for a signature by inspecting the first 32 bytes. A crafted file with
   a valid-looking 32-byte preamble followed by a malicious pickle payload would pass
   this check.

2. Fix: Replace the byte-offset heuristic with cryptographic verification:
   - Extract the key ID and HMAC from the preamble.
   - Verify the HMAC against the artifact body using the corresponding signing key.
   - Reject artifacts where the HMAC does not verify (treat as unsigned).
   - Fall back to legacy verification for pre-versioning artifacts (per T42.1).

3. Log all verification failures to the WORM audit trail.

### Acceptance Criteria

1. `_looks_signed()` replaced with `_verify_signature()` that performs cryptographic
   verification, not heuristic inspection.
2. Crafted preamble without valid HMAC is rejected.
3. Legitimately signed artifacts still verify correctly.
4. Legacy (pre-versioning) artifacts handled per T42.1 backward compatibility rules.
5. Verification failures logged to audit trail.
6. New tests: valid signature passes, crafted preamble fails, legacy artifact passes,
   tampered artifact fails.
7. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/synthesizer/models.py`
- Create: `tests/unit/test_artifact_signature_verification.py`

---

## T47.7 — Add Memory Bounds to Parquet Loading

**Priority**: P1 — Safety. `pd.read_parquet()` loads entire file into memory with no
size cap. A large dataset causes OOM kill.

### Context & Constraints

1. `src/synth_engine/modules/synthesizer/engine.py:345` and `storage.py:282` call
   `pd.read_parquet()` without any file size or row count limit.

2. A maliciously large or unexpectedly large Parquet file will exhaust available memory
   and OOM-kill the process, affecting all concurrent jobs.

3. Fix: Add configurable size limits:
   - Check file size before loading (default: 2 GiB, configurable via
     `MAX_PARQUET_FILE_SIZE`).
   - Use `read_parquet(..., columns=...)` to load only required columns.
   - Add a row count limit check after loading (default: 10M rows, configurable via
     `MAX_PARQUET_ROW_COUNT`).
   - Raise `DatasetTooLargeError` (new exception in shared hierarchy) if limits
     exceeded.

### Acceptance Criteria

1. File size checked before `pd.read_parquet()` call.
2. Row count checked after loading.
3. Both limits configurable via settings.
4. `DatasetTooLargeError` raised when limits exceeded.
5. Error message includes actual size/count and configured limit.
6. Normal-sized files load without impact.
7. New tests: oversized file raises, over-row-count raises, normal file succeeds,
   custom limits respected.
8. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/synthesizer/engine.py`
- Modify: `src/synth_engine/modules/synthesizer/storage.py`
- Modify: `src/synth_engine/shared/settings.py`
- Modify: `src/synth_engine/shared/exceptions.py`
- Create: `tests/unit/test_parquet_memory_bounds.py`

---

## T47.8 — Add Shutdown Cleanup to Lifespan Hook

**Priority**: P1 — Operational. Lifespan hook validates at startup but performs no
cleanup on shutdown. Connection pool resources leak.

### Context & Constraints

1. `src/synth_engine/bootstrapper/lifecycle.py:48-67` — the lifespan context manager
   runs validation at startup but has no `finally` or post-`yield` cleanup logic.

2. Without explicit `dispose_engines()` on shutdown, SQLAlchemy connection pools,
   Redis connections, and other resources are not cleanly released. This causes:
   - PostgreSQL `max_connections` exhaustion during rolling deploys.
   - Redis connection count drift.
   - File descriptor leaks in long-running containers.

3. Fix: Add post-`yield` cleanup in the lifespan hook:
   - Call `dispose_engines()` to close all SQLAlchemy engine pools.
   - Close Redis connection pools.
   - Log shutdown completion to audit trail.
   - Use `try/finally` to ensure cleanup runs even if shutdown is interrupted.

### Acceptance Criteria

1. Lifespan hook calls `dispose_engines()` on shutdown.
2. Redis connections closed on shutdown.
3. Shutdown logged to audit trail.
4. Cleanup runs in `finally` block (survives interrupted shutdown).
5. Startup behavior unchanged.
6. New tests: shutdown triggers cleanup, cleanup logged, interrupted shutdown still
   cleans up.
7. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/bootstrapper/lifecycle.py`
- Create: `tests/unit/test_lifecycle_shutdown.py`

---

## T47.9 — Scrub Budget Values From Exception Messages

**Priority**: P2 — Privacy. `BudgetExhaustionError` includes epsilon values that could
leak to API clients via error responses.

### Context & Constraints

1. `src/synth_engine/modules/privacy/accountant.py:177-182` — the exception message
   includes the current epsilon value and the requested epsilon value. If this exception
   propagates to an API error response, it leaks the privacy budget state.

2. Fix: Replace specific epsilon values in the exception message with a generic
   "budget exhausted" message. Store the detailed values as structured attributes on the
   exception (accessible to internal logging) but keep them out of `str(exception)`.

3. Ensure the error handler in `bootstrapper/errors.py` does not serialize exception
   attributes into the API response body.

### Acceptance Criteria

1. `str(BudgetExhaustionError(...))` does not contain epsilon values.
2. Exception object retains `.requested_epsilon` and `.remaining_epsilon` attributes
   for internal logging.
3. API error response for budget exhaustion contains generic message only.
4. Internal logs still capture the detailed epsilon values.
5. New tests: exception string has no epsilon, attributes accessible, API response
   generic.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/privacy/accountant.py`
- Modify: `src/synth_engine/bootstrapper/errors.py`
- Create: `tests/unit/test_budget_error_scrubbing.py`

---

## T47.10 — Add Redis Health Check to Docker Compose

**Priority**: P2 — Operational. Redis service in `docker-compose.yml` has no
healthcheck, unlike PostgreSQL which uses `pg_isready`.

### Context & Constraints

1. `docker-compose.yml` — the `postgres` service defines a healthcheck using
   `pg_isready`. The `redis` service has no equivalent healthcheck.

2. Without a healthcheck, dependent services may start before Redis is ready, causing
   connection errors on startup.

3. Fix: Add a healthcheck to the Redis service using `redis-cli ping`:
   ```yaml
   healthcheck:
     test: ["CMD", "redis-cli", "ping"]
     interval: 10s
     timeout: 5s
     retries: 5
   ```

4. Ensure services that depend on Redis use `depends_on` with `condition:
   service_healthy`.

### Acceptance Criteria

1. Redis service has a `healthcheck` in `docker-compose.yml`.
2. Healthcheck uses `redis-cli ping`.
3. Dependent services wait for Redis to be healthy before starting.
4. `docker compose config` validates without errors.
5. Full gate suite passes.

### Files to Create/Modify

- Modify: `docker-compose.yml`

---

## Task Execution Order

```
T47.1 (Security endpoint auth) ───┐
T47.2 (Privacy endpoint auth) ────┤
T47.3 (Settings endpoint auth) ───┼──> parallel (auth gap remediation)
T47.4 (JWT secret fail-fast) ─────┤
T47.5 (Operator creds validation) ┘
                                     ↓ auth tasks complete
T47.6 (Artifact signature hardening) ──┐
T47.7 (Parquet memory bounds) ─────────┼──> parallel (safety hardening)
T47.8 (Shutdown cleanup) ─────────────┘
                                     ↓ safety tasks complete
T47.9 (Budget error scrubbing) ────┐
T47.10 (Redis healthcheck) ────────┼──> parallel (operational fixes)
                                   ┘
```

Auth gap remediation (P0) runs first. Safety hardening (P1) follows. Operational
fixes (P2) run last. Within each group, tasks are independent and parallelizable.

---

## Phase 47 Exit Criteria

1. All `/security`, `/privacy/budget`, and `/settings` endpoints require authentication.
2. Destructive security endpoints require elevated `security:admin` scope.
3. Empty `JWT_SECRET_KEY` fails fast in production mode.
4. Missing `OPERATOR_CREDENTIALS_HASH` fails fast in production mode.
5. Model artifact signatures use cryptographic verification, not heuristics.
6. Parquet loading has configurable memory bounds.
7. Shutdown cleanup releases all connection pool resources.
8. Budget exhaustion errors do not leak epsilon values to API clients.
9. Redis service has a health check in Docker Compose.
10. All quality gates pass.
11. Zero open advisories in RETRO_LOG.
12. Review agents pass for all tasks.


---

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


---

# Phase 49 — Test Quality Hardening

**Goal**: Systematically remediate test suite quality issues identified in the
architecture review. The test suite is 74,290 LOC with ~30% of tests that would
pass even if the implementation were broken. This phase fixes the tests, not the
production code.

**Prerequisite**: Framework amendments PR merged (Constitution assertion quality
gate and mutation testing requirement established).

**ADR**: ADR-0047 (Mutation Testing Gate) governs T49.5.

**Source**: Staff-level architecture review, 2026-03-22 — test efficacy analysis.

---

## T49.1 — Assertion Hardening: Security-Critical Tests

**Priority**: P0 — Security. Shallow assertions on security features mean defects
go undetected.

### Context & Constraints

1. `test_download_hmac_signing.py` has only 4 tests for a security-critical
   feature. Missing: signature forgery, replay attacks, key rotation, wrong
   algorithm.
2. `test_audit.py:138-161` uses `assert field in parsed` — only checks key
   presence, not value validity. An audit event with `timestamp: null` passes.
3. `test_dp_accounting.py` logs budget deduction failures but tests don't verify
   the failure propagates correctly.
4. `test_ale.py` asserts `ciphertext is not None` — would pass if encryption
   returns empty bytes.

### Acceptance Criteria

1. `test_download_hmac_signing.py` expanded to >=12 tests: add forgery (tampered
   signature), replay (old sig on new data), key rotation handling, wrong hash
   algorithm (SHA1 vs SHA256), empty payload, oversized payload.
2. `test_audit.py` field-presence assertions replaced with value validity: type
   check + non-empty + format validation for timestamp, event_type, actor.
3. `test_dp_accounting.py` gains tests verifying that unknown exceptions from
   `spend_budget_fn` propagate (not just log). At least 3 negative test cases for
   budget failure paths.
4. `test_ale.py` assertions replaced: `assert len(ciphertext) > 0`, round-trip
   decrypt equals original plaintext, different plaintexts produce different
   ciphertexts.
5. All amended tests pass. No coverage regression.

### Files to Create/Modify

- Modify: `tests/unit/test_download_hmac_signing.py`
- Modify: `tests/unit/test_audit.py`
- Modify: `tests/unit/test_dp_accounting.py`
- Modify: `tests/unit/test_application_level_encryption.py`

---

## T49.2 — Assertion Hardening: Masking & Subsetting Tests

**Priority**: P1 — Quality. Determinism tests would pass if salt parameter is
completely ignored.

### Context & Constraints

1. 8+ masking determinism tests repeat the same pattern:
   `assert mask_X(val, salt) == mask_X(val, salt)`. This would pass if the
   function ignores the salt and returns a constant.
2. These 8 tests should be parametrized into 1 test with
   `@pytest.mark.parametrize`.
3. `test_subsetting_core.py` mocks 100% of SQLAlchemy. Missing negative cases:
   circular FK reference during traversal, egress writer mid-stream failure, DB
   disconnect.
4. `test_settings_router.py` — 5 tests in 266 lines, assertions are exclusively
   `isinstance()`. No field value assertions.

### Acceptance Criteria

1. Every masking determinism test also asserts:
   `mask_X(val, salt_a) != mask_X(val, salt_b)` — different salt produces
   different output.
2. 8 duplicate determinism functions parametrized into 1
   `@pytest.mark.parametrize` test.
3. `test_subsetting_core.py` gains >=3 negative test cases: circular FK,
   mid-stream egress failure, connection loss.
4. `test_settings_router.py` assertions replaced with specific field value checks.
5. Net test function count may decrease (parametrization). Coverage must not
   regress.

### Files to Create/Modify

- Modify: `tests/unit/test_masking_determinism.py` (or whichever files contain
  the determinism tests)
- Modify: `tests/unit/test_masking_algorithms.py`
- Modify: `tests/unit/test_subsetting_core.py` (or equivalent test file for
  subsetting)
- Modify: `tests/unit/test_settings_router.py`

---

## T49.3 — Mock Reduction Pass

**Priority**: P2 — Reliability. 100% mocked tests won't catch API version drift.

### Context & Constraints

1. `test_dp_engine.py` mocks 100% of Opacus. Zero actual Opacus invocations.
   Would not detect Opacus API version break.
2. `test_synthesizer_guardrails.py` mocks `psutil.virtual_memory()` and
   `torch.cuda` completely. Missing: psutil raising exception, torch.cuda failure,
   memory=0 edge case.
3. Mock helpers (`_make_engine()`, `_make_topology()`, `_mock_vmem()`) are
   duplicated across 4+ test files.
4. monkeypatch environment variable boilerplate repeated across 10+ files (3-5
   lines of identical setup per test).

### Acceptance Criteria

1. `test_dp_engine.py` gains >=1 integration-style test using real Opacus (tiny
   model, 10 rows) to verify API compatibility. Mark with
   `@pytest.mark.synthesizer` for optional CI.
2. `test_synthesizer_guardrails.py` gains 3 edge case tests: psutil exception,
   torch.cuda exception, available memory = 0.
3. Shared mock helpers moved to `tests/unit/conftest.py` or `tests/fixtures/`.
   Duplicates removed from individual test files.
4. Shared environment variable fixture created for JWT/auth test setup.
   Deduplicate across 10+ files.
5. No coverage regression. Mock count may decrease.

### Files to Create/Modify

- Modify: `tests/unit/test_dp_engine.py`
- Modify: `tests/unit/test_synthesizer_guardrails.py`
- Modify: `tests/unit/conftest.py` (add shared fixtures)
- Modify: Multiple test files (deduplicate env var setup)
- Create: `tests/fixtures/mock_helpers.py` (if not using conftest)

---

## T49.4 — Test Organization Cleanup

**Priority**: P3 — Maintainability. Large files and missing docs increase
cognitive load.

### Context & Constraints

1. `test_synthesizer_tasks.py` is 2,738 lines — largest test file, hard to
   navigate, mixes unit and integration patterns.
2. 30+ test files lack module docstrings explaining what they test.
3. Copy-paste test patterns identified across masking, auth, and subsetting tests.

### Acceptance Criteria

1. `test_synthesizer_tasks.py` split into <=3 focused files (by concern: task
   lifecycle, error handling, integration).
2. Module docstrings added to all test files that lack them (brief: one line
   stating what module/feature is under test).
3. No copy-paste test blocks (>5 lines identical) across files. Shared patterns
   extracted to fixtures or parametrized.
4. All tests pass. No coverage regression.

### Files to Create/Modify

- Split: `tests/unit/test_synthesizer_tasks.py` -> multiple files
- Modify: 30+ test files (add docstrings)
- Modify: Various test files (deduplicate patterns)

---

## T49.5 — Mutation Testing Baseline

**Priority**: P1 — Quality. Establish the mutation testing gate required by
ADR-0047.

### Context & Constraints

1. ADR-0047 mandates mutation testing on `shared/security/` and
   `modules/privacy/`.
2. `mutmut` must be added to dev dependencies.
3. Initial threshold: 60% mutation score.
4. Surviving mutants in security-critical code must be fixed (new tests or
   hardened assertions).

### Acceptance Criteria

1. `mutmut` added to `pyproject.toml` dev dependencies.
2. Mutation testing configured in `pyproject.toml` for `shared/security/` and
   `modules/privacy/`.
3. Baseline mutation score documented.
4. Surviving mutants in `shared/security/vault.py`,
   `shared/security/hmac_signing.py`, and `modules/privacy/accountant.py` killed
   (new tests written).
5. Mutation score >=60% on target modules.
6. CI gate configured (can be advisory-only initially if full enforcement blocks).

### Files to Create/Modify

- Modify: `pyproject.toml` (add mutmut dependency + config)
- Create: Tests to kill surviving mutants (locations TBD after baseline run)
- Modify: CI config if applicable

---

## Task Execution Order

```
T49.1 (Security assertion hardening) ──┐
T49.2 (Masking assertion hardening) ───┼──> parallel (assertion hardening)
                                        ┘
                                          ↓ assertion tasks complete
T49.3 (Mock reduction) ────────────────┐
T49.4 (Test organization cleanup) ─────┼──> parallel (structural cleanup)
                                        ┘
                                          ↓ structural cleanup complete
T49.5 (Mutation testing baseline) ─────> sequential (requires hardened tests)
```

T49.1 and T49.2 are independent assertion hardening tasks that can run in
parallel. T49.3 and T49.4 are structural cleanup tasks that can run in parallel
but benefit from hardened assertions being in place first. T49.5 runs last because
mutation scores are more meaningful after assertions are hardened.

---

## Phase 49 Exit Criteria

1. Security-critical tests have deep assertions (not just existence/type checks).
2. Masking determinism tests verify salt sensitivity, not just self-equality.
3. Shared mock helpers deduplicated into conftest or fixtures.
4. Largest test file split into focused modules.
5. All test files have module docstrings.
6. Mutation testing baseline established at >=60% on target modules.
7. All quality gates pass.
8. Zero open advisories in RETRO_LOG.
9. Review agents pass for all tasks.


---

# Phase 50 — Production Security Fixes

**Goal**: Fix five critical production defects identified in the architecture
review that will cause failures, compliance violations, or security bypasses in
production deployments.

**Prerequisite**: Phase 48 merged (infrastructure fixes). Phase 49 preferred but
not blocking — these are production code fixes with their own test requirements.

**ADR**: T50.1 and T50.2 require new ADRs. T50.5 may require an ADR if
serialization format changes.

**Source**: Staff-level architecture review, 2026-03-22 — penetration test
findings and production readiness audit.

---

## T50.1 — DP Budget Deduction: Fail Closed

**Priority**: P0 — Security/Compliance. Silent budget failure means privacy
guarantee is violated.

### Context & Constraints

1. `dp_accounting.py:140` catches `Exception` broadly when calling
   `spend_budget_fn`. If the budget deduction fails for any reason, the job
   continues. The epsilon ledger is now wrong.
2. The system's value proposition is (epsilon, delta)-DP guarantees. A failed budget
   deduction means the reported epsilon is lower than the actual privacy cost. This
   is a compliance-critical defect.
3. `BudgetExhaustionError` and `EpsilonMeasurementError` already exist in the
   exception hierarchy.
4. The fix is narrow: catch only the expected exceptions, let everything else
   propagate and abort the job.

### Acceptance Criteria

1. `dp_accounting.py` broad `except Exception` replaced with specific catches:
   `BudgetExhaustionError`, `EpsilonMeasurementError`.
2. Any other exception from `spend_budget_fn` propagates, marking the job FAILED.
3. New test: inject an unexpected exception into `spend_budget_fn` -> verify job
   status is FAILED (not COMPLETE).
4. New test: `BudgetExhaustionError` from `spend_budget_fn` -> verify budget
   exhaustion is handled gracefully (existing behavior preserved).
5. ADR documenting the fail-closed decision and compliance rationale.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/synthesizer/dp_accounting.py`
- Create: `docs/adr/ADR-0048-dp-budget-fail-closed.md`
- Modify: `tests/unit/test_dp_accounting.py`

---

## T50.2 — Multi-Pod Audit Chain Integrity

**Priority**: P0 — Security. WORM audit trail tamper-evidence broken in multi-pod
deployment.

### Context & Constraints

1. `shared/security/audit.py` uses `threading.Lock` for hash chain integrity.
   This is process-scoped.
2. In Kubernetes (2+ pods), each pod independently computes chain hashes. Two pods
   writing audit entries produce independent chains with potentially overlapping
   sequence numbers.
3. The tamper-evident property (hash chain) is the core security guarantee of WORM
   logging.
4. Options: (a) Database-backed sequence (PostgreSQL SERIAL + advisory lock),
   (b) Singleton audit writer sidecar, (c) Per-pod chain prefix with centralized
   verification.
5. Option (a) is simplest and aligns with existing PostgreSQL infrastructure.

### Acceptance Criteria

1. Audit chain sequence numbers are globally unique across pods (not just
   per-process).
2. Hash chain integrity is verifiable across entries from multiple pods.
3. Audit write performance does not degrade by more than 2x (database round-trip
   acceptable, but not blocking).
4. New integration test: simulate 2 concurrent "pods" (2 threads with separate
   AuditLogger instances) writing interleaved entries. Verify combined chain is
   valid.
5. ADR documenting chosen approach and trade-offs.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/security/audit.py`
- Create: `docs/adr/ADR-0049-multi-pod-audit-chain.md`
- Modify: `tests/unit/test_audit_logger.py`
- Create: `tests/integration/test_audit_chain_concurrency.py`

---

## T50.3 — Default to Production Mode

**Priority**: P0 — Security. Missing env var silently disables authentication.

### Context & Constraints

1. Empty `JWT_SECRET_KEY` disables authentication entirely (dev convenience).
2. Production mode check depends on `CONCLAVE_ENV=production`. If this env var is
   missing, the system defaults to dev mode.
3. A fresh deployment with no `.env` boots with no auth — the exact opposite of
   secure-by-default.
4. Fix: default `CONCLAVE_ENV` to `production`. Dev mode must be explicitly opted
   into.

### Acceptance Criteria

1. `CONCLAVE_ENV` defaults to `"production"` when not set (not `"development"`).
2. Dev mode requires explicit `CONCLAVE_ENV=development`.
3. Startup logs WARNING when running in dev mode: "Authentication disabled —
   development mode active. Set CONCLAVE_ENV=production for production use."
4. Missing `JWT_SECRET_KEY` in production mode -> startup fails with clear error
   (existing behavior, just verify).
5. New test: no `CONCLAVE_ENV` set -> production mode enforced, auth required.
6. New test: `CONCLAVE_ENV=development` -> dev mode, auth disabled with warning.
7. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/settings.py` (default CONCLAVE_ENV)
- Modify: `src/synth_engine/bootstrapper/config_validation.py`
- Modify: `tests/unit/test_startup_validate_config.py`
- Modify: `tests/unit/test_missing_env_var_startup_check.py`

---

## T50.4 — Pickle TOCTOU Mitigation

**Priority**: P1 — Security. Time-of-check-to-time-of-use gap in artifact
verification.

### Context & Constraints

1. `modules/synthesizer/models.py`: Model artifacts are HMAC-signed at creation
   and verified at load. The verification and deserialization are separate
   operations.
2. If ephemeral storage (MinIO) is compromised between sign-time and load-time, a
   malicious pickle can be substituted.
3. MinIO is on tmpfs (good), but any pod with network access to
   `minio-ephemeral:9000` can write to it.
4. Fix: Verify HMAC immediately before `pickle.loads()` in the same atomic
   function call — no window between verify and load.
5. Longer-term: evaluate migration to safetensors or ONNX (may warrant separate
   ADR).

### Acceptance Criteria

1. HMAC verification and `pickle.loads()` occur in the same function, with no
   external calls between them.
2. The bytes verified by HMAC are the exact same bytes passed to `pickle.loads()`
   (read once, verify, then deserialize from the same buffer).
3. New test: tamper with artifact bytes after signing -> verify load rejects with
   `ArtifactTamperingError`.
4. Close ADV-P47-07.
5. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/synthesizer/models.py`
- Modify: `tests/unit/test_artfact_signing.py` (note: existing typo in filename)

---

## T50.5 — Centralize Remaining Hardcodes

**Priority**: P2 — Maintainability. Hardcoded values require code changes for
environment-specific configuration.

### Context & Constraints

1. Phase 36 centralized 14 env vars into `ConclaveSettings`. These were missed:
   - `_MINIO_ENDPOINT = "http://minio-ephemeral:9000"` (main.py:86)
   - `_EPHEMERAL_BUCKET = "synth-ephemeral"` (main.py:92)
   - `_SECRETS_DIR = Path("/run/secrets")` (main.py:92)
   - Service hostname allowlist in tls/config.py:74-90 (10 hardcoded hostnames)
   - OOM calculation params in job_orchestration.py:91-94 (overhead factor, min
     samples, chunk size, max retries)
2. The pattern exists — just extend `ConclaveSettings` with new fields and replace
   hardcodes with settings reads.
3. Defaults should match current hardcoded values (no behavior change without
   explicit configuration).

### Acceptance Criteria

1. All 5 hardcoded value groups moved to `ConclaveSettings` with sensible defaults
   matching current values.
2. Each new setting is readable from environment variables.
3. `config_validation.py` validates new settings where appropriate (e.g.,
   MINIO_ENDPOINT must be a valid URL).
4. Tests: verify each setting is read from env, defaults match previous hardcoded
   values.
5. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/settings.py`
- Modify: `src/synth_engine/bootstrapper/main.py`
- Modify: `src/synth_engine/shared/tls/config.py`
- Modify: `src/synth_engine/modules/synthesizer/job_orchestration.py`
- Modify: `src/synth_engine/bootstrapper/config_validation.py`
- Modify: `tests/unit/test_startup_validate_config.py`

---

## Task Execution Order

```
T50.1 (DP budget fail-closed) ─────────┐
T50.2 (Multi-pod audit chain) ─────────┼──> parallel (P0 security fixes)
T50.3 (Default to production mode) ────┘
                                          ↓ P0 fixes complete
T50.4 (Pickle TOCTOU mitigation) ──────┐
T50.5 (Centralize remaining hardcodes) ┼──> parallel (P1/P2 hardening)
                                        ┘
```

T50.1, T50.2, and T50.3 are independent P0 security fixes that can run in
parallel. T50.4 and T50.5 are lower-priority hardening tasks that can also run in
parallel with each other but should follow the P0 fixes.

---

## Phase 50 Exit Criteria

1. DP budget deduction fails closed on unexpected exceptions.
2. Audit chain integrity maintained across multiple pods.
3. Missing `CONCLAVE_ENV` defaults to production mode (secure-by-default).
4. Pickle deserialization is atomic with HMAC verification (no TOCTOU window).
5. All remaining hardcoded values centralized in `ConclaveSettings`.
6. ADRs written for fail-closed budget (ADR-0048) and multi-pod audit (ADR-0049).
7. All quality gates pass.
8. Zero open advisories in RETRO_LOG.
9. Review agents pass for all tasks.


---

# Phase 52 — Demo & Benchmark Suite

**Goal**: Produce runnable Jupyter notebook demos with real, reproducible benchmark
results. Parameterized epsilon curve generation with rigorous statistical methodology.
Two audience-specific notebooks (data architects, AI/ML builders). All results committed
as versioned artifacts with honest analysis — results that look bad stay in.

**Prerequisite**: Phase 50 merged (security fixes resolve expired advisories). Phase 51
recommended if specced (release engineering — not yet in backlog).

**ADR**: None required — no architectural changes. Demo dependency group is additive and
isolated.

**Source**: Portfolio review and go-to-market planning, 2026-03-23.

---

## T52.1 — Benchmark Infrastructure

**Priority**: P1 — Foundation for all subsequent tasks.

### Context & Constraints

1. No plotting libraries exist in `pyproject.toml`. No Jupyter notebooks exist in the repo.
2. `scripts/benchmark_dp_quality.py` exists but runs at 500 rows / 10 epochs — acknowledged
   in `docs/archive/DP_QUALITY_REPORT.md` as insufficient for quality assessment (GAN
   hasn't converged).
3. `scripts/e2e_load_test.py` exists as a load test harness but produces no visualizations.
4. Demo dependencies (matplotlib, seaborn, jupyter, scikit-learn) MUST NOT appear in the
   production dependency tree or Docker image. They belong in a
   `[tool.poetry.group.demos]` optional group only.
5. Production modules (`src/synth_engine/`) MUST NOT import from demo dependencies.
6. The benchmark harness must be idempotent — skip already-completed parameter combinations
   to allow resume after crash.
7. All benchmark runs must use a fixed random seed strategy (torch manual seed, numpy seed,
   Python random seed) for reproducibility. Limitations (cuDNN non-determinism on GPU) must
   be documented in results metadata.
8. `nbstripout` must be added as a pre-commit hook to prevent credential/PII leaks from
   executed notebook cell outputs.

### Acceptance Criteria

1. `[tool.poetry.group.demos]` dependency group added to `pyproject.toml` with:
   `matplotlib`, `seaborn`, `jupyter`, `scikit-learn`, `nbstripout`.
2. `nbstripout` added to `.pre-commit-config.yaml` with a pinned `rev` tag (e.g.,
   `v0.7.1`) to strip all notebook cell outputs before commit. Consistent with the
   project's supply chain hardening policy — `HEAD` or branch refs are forbidden.
3. `demos/` top-level directory created. `.gitignore` updated to exclude generated output
   (`demos/results/*.csv` and `demos/figures/`) while allowing committed versioned JSON
   artifacts via negation rules (`!demos/results/*_v1.json`,
   `!demos/results/grid_config.json`). Files `demos/*.ipynb`, `demos/*.py`, and
   `demos/README.md` are committed normally.
4. `scripts/benchmark_epsilon_curves.py` created — parameterized harness that:
   - Accepts: PostgreSQL connection string, table name, parameter grid config (JSON/YAML).
   - Parameter grid config YAML parsing MUST use `yaml.safe_load()` only. `yaml.load()`
     without SafeLoader is forbidden. Bandit B506 enforces this.
   - Trains CTGAN at configurable noise multipliers x epoch counts x sample sizes.
   - Records per-run: actual epsilon (from Opacus RDP accountant), wall time (start = first
     training epoch, stop = final sample generation), KS statistic per numeric column,
     chi-squared p-value per categorical column, mean absolute error per column, correlation
     matrix delta, FK orphan rate.
   - Uses delta value matching production constant `_DP_EPSILON_DELTA` (currently `1e-5`).
   - Sets fixed random seeds (torch, numpy, Python) per run for reproducibility.
   - Outputs structured JSON + CSV to configurable output directory.
   - Is idempotent — skips completed parameter combinations on resume.
   - Records failure rows (with error type and message) for any grid cell that errors —
     never silently omits.
   - Includes `schema_version` field in all output artifacts.
   - Includes hardware metadata (CPU model, RAM, core count, OS, GPU if available) in
     results.
   - Sanitizes all artifact filenames from parameter grid config, not from dataset schema
     column names.
   - Has a configurable per-run timeout (default: 30 minutes) — writes TIMEOUT result row
     and continues.
5. All artifact filenames derived from parameter grid configuration, not from dataset column
   names (path traversal prevention).
6. `demos/conclave_demo.py` convenience wrapper created for notebook use — orchestrates
   synthesis via direct Python imports (not API calls), using an isolated SQLite or fresh
   PostgreSQL instance for privacy budget (never touches production ledger). The wrapper
   MUST require and pass the artifact signing key to `ModelArtifact.load()`; it MUST NOT
   call `load()` without a signing key.

### Files to Create/Modify

- Modify: `pyproject.toml` (add demos dependency group)
- Modify: `.pre-commit-config.yaml` (add nbstripout hook)
- Create: `demos/` directory structure
- Create: `scripts/benchmark_epsilon_curves.py`
- Create: `demos/conclave_demo.py`

### Negative Test Requirements (from spec-challenger)

- `test_demo_dependencies_not_imported_in_production_modules` — import every module in
  `src/synth_engine/` without demos group; assert no ImportError.
- `test_benchmark_harness_rejects_run_without_dataset_fixture` — run harness with no
  dataset; verify clear error.
- `test_benchmark_epsilon_delta_matches_production_constant` — assert benchmark delta
  equals production `_DP_EPSILON_DELTA`.
- `test_benchmark_run_produces_identical_metrics_given_fixed_seed` — run twice with same
  seed on CPU; assert metrics match within tolerance. Test MUST be marked
  `@pytest.mark.cpu_only` or skip on GPU (cuDNN non-determinism documented in results
  metadata).
- `test_benchmark_harness_records_failure_row_on_run_error` — inject failure; verify
  failure row recorded, not omitted.
- `test_results_artifact_contains_schema_version_field` — parse results; assert
  schema_version present.
- `test_committed_results_contain_no_real_column_names` — assert column identifiers match
  fixture schema only.
- `test_parameter_grid_is_committed_alongside_results` — grid config must be an artifact.
- `test_benchmark_harness_rejects_malicious_yaml_config` — pass a YAML document with
  `!!python/object/apply:os.system` payload; assert it is rejected (safe_load must raise
  or refuse to deserialize the object).

---

## T52.2 — Execute Benchmarks (Real Results)

**Priority**: P1 — Produces the raw data for all notebooks.

### Context & Constraints

1. Benchmarks MUST run against `sample_data/` fixtures (publicly committable, all fictional
   Faker-generated data) — never against production data.
2. `scripts/seed_sample_data.py` already supports `--customers` and `--orders` CLI flags
   (from T18.3). The actual work is to verify it scales to 50K rows per table and add any
   missing table support (e.g., `--order-items`, `--payments` if not present). 100K deferred
   to GPU-available hardware.
3. Parameter grid for `customers` table (most PII-dense): noise multipliers
   (0.5, 1.0, 2.0, 5.0, 10.0) x epoch counts (50, 100, 200) x sample sizes
   (1K, 10K, 50K) = 45 cells.
4. Reduced grid for `orders` table: noise multipliers (1.0, 5.0, 10.0) x epochs
   (100, 200) x sample sizes (10K, 50K) = 12 cells.
5. All runs on documented hardware. Estimated total wall time: 8-16 hours on CPU
   (16 GB RAM, no GPU). Grid designed to complete within this envelope.
6. Results committed to `demos/results/` as versioned JSON/CSV artifacts with parameter
   grid config alongside.
7. Benchmarks run against an isolated database instance — fresh PostgreSQL with its own
   privacy ledger. The production ledger is never touched.
8. CPU-only is the supported and documented path. GPU acceleration detected at runtime and
   recorded in metadata but not required.

### Acceptance Criteria

1. Verify existing `scripts/seed_sample_data.py` scales to 50K rows per table. Add missing
   table flags if needed. No rewrite required if existing script handles the scale.
2. Full parameter grid executed for `customers` (45 cells) and `orders` (12 cells).
3. Every grid cell has a result row — no omissions. Failed cells have failure rows with
   error details.
4. Results committed as `demos/results/benchmark_customers_v1.json`,
   `demos/results/benchmark_orders_v1.json` with `schema_version: "1.0"`.
5. Parameter grid config committed alongside results as `demos/results/grid_config.json`.
6. Hardware metadata present and non-empty in all result files.
7. FK orphan rate is 0 for all successful synthesis runs.
8. Wall time field present and positive for all result rows.
9. All committed artifacts reference only `sample_data/` fixture column names — no
   production schema names.

### Files to Create/Modify

- Modify (if needed): `scripts/seed_sample_data.py`
- Create: `demos/results/benchmark_customers_v1.json`
- Create: `demos/results/benchmark_orders_v1.json`
- Create: `demos/results/grid_config.json`

### Negative Test Requirements (from spec-challenger)

- `test_results_manifest_contains_all_parameter_grid_cells` — parse results; assert every
  grid tuple has a result row.
- `test_fk_orphan_rate_is_zero_for_well_formed_fixture` — assert FK metric is 0 for
  successful runs.
- `test_wall_time_field_present_and_positive_in_all_result_rows` — measurement
  completeness.
- `test_results_hardware_metadata_present_and_non_empty` — hardware documentation gate.

---

## T52.3 — Epsilon Curve Notebook

**Priority**: P1 — The rigorous benchmark notebook.

### Context & Constraints

1. This notebook is for people who care about the math. Methodology must be defensible in
   a peer review.
2. All charts generated from committed raw results in `demos/results/` — no live training
   in the notebook itself.
3. Epsilon values are post-hoc measured by Opacus RDP accountant, not configured targets.
4. Results that look bad stay in. The committed results artifact MUST contain a result row
   for every cell in the parameter grid — the notebook MUST NOT filter out unfavorable
   results.
5. Notebook must execute cleanly with `Run All` from a fresh kernel — no hidden state
   dependencies.
6. Figures saved as SVG (publication quality) to `demos/figures/` via a documented
   regeneration script.

### Acceptance Criteria

1. `demos/epsilon_curves.ipynb` created with sections:
   - **Methodology**: Hardware, software versions, seed strategy, Opacus RDP accountant,
     delta value, dataset description, parameter grid, wall-time measurement scope (first
     epoch to final sample), limitations.
   - **Epsilon vs. Noise Multiplier**: For each sample size, sigma on x-axis, measured
     epsilon on y-axis. Expected inverse relationship annotated.
   - **Epsilon vs. Statistical Fidelity**: Epsilon on x-axis, mean KS statistic on y-axis.
     Annotate sweet spot IF one exists — do not manufacture one.
   - **Epsilon vs. Dataset Size**: Fixed sigma, varying row counts. Demonstrates
     subsampling amplification.
   - **Correlation Preservation Heatmaps**: Side-by-side source vs. synthetic correlation
     matrices at three epsilon levels (strong/moderate/weak).
   - **FK Integrity Verification**: Table showing orphan count = 0 for all runs.
   - **Honest Limitations**: CTGAN architecture constraints, epoch count vs. convergence,
     what these numbers mean and don't mean.
2. Every chart has: axis labels with units, legend, one-sentence interpretation, figure
   title.
3. Notebook executes cleanly via `jupyter nbconvert --execute` from fresh kernel with no
   errors.
4. Pre-rendered SVG figures committed to `demos/figures/` and referenced in
   `demos/README.md`.
5. `demos/generate_figures.py` script regenerates all figures from committed raw results.

### Files to Create/Modify

- Create: `demos/epsilon_curves.ipynb`
- Create: `demos/generate_figures.py`
- Create: `demos/figures/` (pre-rendered SVGs)

### Negative Test Requirements (from spec-challenger)

- `test_notebooks_execute_cleanly_from_fresh_kernel` — run notebook via nbconvert; assert
  zero cell errors.
- `test_figures_are_regenerable_from_committed_results` — run generate_figures.py; assert
  output matches committed figures.
- `test_notebook_epsilon_curve_runs_without_network_access` — notebook must not pull data
  at runtime.

---

## T52.4 — Quick-Start Notebook

**Priority**: P1 — The data architect demo.

### Context & Constraints

1. Target audience: data architects who need to see results fast. Three cells: connect,
   synthesize, compare.
2. Uses `demos/conclave_demo.py` wrapper from T52.1 for clean interface.
3. The notebook MUST NOT contain hardcoded database credentials. Connection strings use
   environment variables or localhost defaults for the Docker Compose stack.
4. `nbstripout` (from T52.1) prevents accidental credential commits from executed cell
   output.
5. Notebooks load model artifacts ONLY through the verified production code path
   (`ModelArtifact.load()`) — never via raw `pickle.load()`.
6. The output of Cell 3 (comparison plots) is the screenshot you send people.

### Acceptance Criteria

1. `demos/quickstart.ipynb` created with three primary cells:
   - **Cell 1 (Connect)**: Connect to PostgreSQL via env var or localhost default. Print
     discovered tables, row counts, FK relationships.
   - **Cell 2 (Synthesize)**: Generate synthetic data for 2-3 tables with DP enabled.
     Print summary: table, rows generated, epsilon, duration.
   - **Cell 3 (Compare)**: Side-by-side distribution overlays (real vs synthetic),
     correlation heatmaps, FK integrity check ("FK orphans: 0").
2. No hardcoded credentials in notebook source cells.
3. Notebook executes cleanly from fresh kernel against the Docker Compose PostgreSQL
   instance seeded with sample data.
4. All model artifact loading MUST call `ModelArtifact.load(path, signing_key=...)` with
   `ARTIFACT_SIGNING_KEY` from the environment. Unsigned loading (`signing_key=None`) is
   forbidden in demo code. Never use raw `pickle.load()`.
5. `demos/README.md` includes setup instructions (docker compose, seed data, install demos
   group, run notebook).

### Files to Create/Modify

- Create: `demos/quickstart.ipynb`
- Modify: `demos/README.md`

### Negative Test Requirements (from spec-challenger)

- `test_notebooks_execute_cleanly_from_fresh_kernel` — (shared with T52.3, covers all
  notebooks).
- `test_demo_readme_links_resolve_to_existing_files` — all links in demos/README.md point
  to existing files.

---

## T52.5 — AI Builder Notebook

**Priority**: P2 — The ML training data demo.

### Context & Constraints

1. Target audience: AI developers/founders who want to train models on synthetic data.
2. Demonstrates "train on synthetic, test on real" methodology — the key value proposition
   for ML use cases.
3. Downstream task: binary classification (payment method prediction from order amount +
   customer features) using scikit-learn LogisticRegression (simple, reproducible, no GPU
   needed).
4. Evaluation metric: ROC-AUC (handles class imbalance better than accuracy).
5. Train/test split: 80/20 stratified on the target variable. Holdout set is ALWAYS real
   data.
6. Comparison protocol:
   - Baseline: Train on real, test on real (upper bound).
   - Synthetic: Train on synthetic (various epsilon levels), test on real.
   - Augmented: Train on real + synthetic combined, test on real.
7. scikit-learn is in the `demos` dependency group — not in production.
8. All model artifact loading MUST call `ModelArtifact.load(path, signing_key=...)` with
   `ARTIFACT_SIGNING_KEY` from the environment — not in unsigned mode. Never use raw
   `pickle.load()`.

### Acceptance Criteria

1. `demos/training_data.ipynb` created with sections:
   - **Model Selection**: Documents model class (LogisticRegression), metric (ROC-AUC),
     train/test split (80/20 stratified), dataset, and rationale.
   - **Generate Synthetic Data**: Using conclave_demo wrapper, generate synthetic datasets
     at 3 epsilon levels.
   - **Train on Real (Baseline)**: Train LogisticRegression, report ROC-AUC on holdout.
   - **Train on Synthetic**: Train on synthetic data at each epsilon level, report ROC-AUC
     on same holdout.
   - **Utility Curve**: Plot epsilon on x-axis, downstream ROC-AUC on y-axis. Shows
     practical privacy-utility tradeoff.
   - **Augmentation**: Train on real + synthetic combined, report ROC-AUC.
   - **Privacy Guarantee Explanation**: What epsilon=0.17, epsilon=2.0, epsilon=10.0 mean
     in plain language.
   - **Honest Limitations**: Synthetic data typically underperforms real data on downstream
     tasks — state this explicitly. Simple model may not capture all effects.
2. Every chart has axis labels, units, legend, interpretation.
3. Notebook executes cleanly from fresh kernel.
4. Fixed random seed for train/test split and model training (reproducible results).

### Files to Create/Modify

- Create: `demos/training_data.ipynb`

### Negative Test Requirements (from spec-challenger)

- `test_notebooks_execute_cleanly_from_fresh_kernel` — (shared, covers all notebooks).
- `test_ai_builder_notebook_documents_model_selection_rationale` — assert notebook
  contains Model Selection markdown cell with model name, metric, split, dataset.

---

## T52.6 — Published Results & README Updates

**Priority**: P1 — Makes the demos discoverable.

### Context & Constraints

1. Pre-rendered figures from T52.3 committed as SVGs for people who won't run notebooks.
2. `demos/README.md` is the entry point — how to run, hardware requirements, expected
   runtimes.
3. Top-level `README.md` updated with a "Demos & Benchmarks" section linking to notebooks
   and key figures.
4. The README section should include 1-2 inline figures (epsilon curve, correlation
   heatmap) as compelling visual evidence.
5. All links in both READMEs must resolve to existing files.

### Acceptance Criteria

1. `demos/README.md` created with: overview, prerequisites (Docker Compose, demos
   dependency group), setup instructions, per-notebook descriptions with expected runtimes,
   hardware requirements, methodology summary.
2. Top-level `README.md` updated with "Demos & Benchmarks" section between "Validated
   Scale" and "Quality and Development Process" sections. Includes:
   - 1-2 key figures inline (epsilon vs fidelity curve, correlation heatmap).
   - Links to all three notebooks.
   - Link to `demos/README.md` for full setup.
   - Brief methodology note ("all epsilon values post-hoc measured by Opacus RDP
     accountant, not configured targets").
3. All links in `demos/README.md` resolve to existing committed files.
4. All links in updated `README.md` resolve to existing committed files.
5. "How This Was Built" section metrics updated to current values (commits, PRs, ADRs,
   LOC).

### Files to Create/Modify

- Create: `demos/README.md`
- Modify: `README.md` (add Demos & Benchmarks section, update metrics)

### Negative Test Requirements (from spec-challenger)

- `test_demo_readme_links_resolve_to_existing_files` — all README links point to existing
  files.

---

## Task Execution Order

```
T52.1 (benchmark infrastructure + deps) ──────> foundation
                                                    |
                                                    v
T52.2 (execute benchmarks, commit results) ────> raw data
                                                    |
                                                    v
T52.3 (epsilon curves notebook) ───┐
T52.4 (quick-start notebook) ─────┼──> parallel (notebooks)
T52.5 (AI builder notebook) ──────┘
                                      |
                                      v notebooks complete
T52.6 (published results + READMEs) ──> documentation
```

T52.1 must complete first (infrastructure). T52.2 depends on T52.1 (needs harness).
T52.3/T52.4/T52.5 can run in parallel after T52.2 produces results. T52.6 depends on all
notebooks being complete.

---

## Phase 52 Exit Criteria

1. Benchmark harness produces reproducible results with fixed seeds.
2. Full parameter grid executed — every cell has a result row (no omissions).
3. All three notebooks execute cleanly from fresh kernel via `nbconvert --execute`.
4. Pre-rendered SVG figures committed and regenerable from raw results.
5. `nbstripout` pre-commit hook active — no cell outputs in committed notebooks.
6. Demo dependency group isolated — production modules do not import from it.
7. Benchmark runs use isolated database — production privacy ledger untouched.
8. All committed artifacts reference only `sample_data/` fixture column names.
9. README updated with Demos & Benchmarks section, current metrics, inline figures.
10. All quality gates pass.
11. Review agents pass for all tasks.


---

# Phase 53 — Mutation Testing & Advisory Drain

**Goal**: Establish working mutation testing on Python 3.14, close the
Constitution Priority 4 programmatic gate gap, and drain all actionable open
advisories.

**Prerequisite**: Phase 50 merged (security fixes). Phase 52 is independent
(demo/benchmark suite) and does not block this phase.

**ADR**: T53.1 requires a new ADR (cosmic-ray adoption or dual-interpreter
strategy — technology substitution per Rule 6). T53.2 may amend ADR-0048
(audit trail anchoring) if signature format changes.

**Source**: Staff-level architecture review, 2026-03-23 — mutation testing
gap identified as highest-priority remediation. ADR-0052 re-evaluation
trigger (c): "An alternative mutation tool is evaluated and found compatible
with Python 3.14."

---

## T53.1 — Mutation Testing: Evaluate cosmic-ray, Adopt or Fallback

**Priority**: P0 — Constitution Priority 4 / Priority 0.5. The mutation
testing gate (ADR-0047) is non-functional. ADR-0052 accepted the gap
temporarily; this task closes it.

### Context & Constraints

1. `mutmut 3.x` crashes with SIGSEGV on CPython 3.14 due to its in-process
   trampoline mechanism. All 200 mutants exit with signal -11. Mutation scores
   are meaningless (ADR-0052).
2. `cosmic-ray 8.4.4` (latest, Feb 2026) uses subprocess isolation per mutant
   — no trampoline. It mutates at the AST level and runs each mutant in a
   fresh subprocess. This architecture avoids the SIGSEGV root cause entirely.
3. cosmic-ray does not declare Python 3.14 in its PyPI classifiers (up to
   3.13), but its subprocess-based approach has no known 3.14 incompatibility.
   The spike must verify this empirically.
4. If cosmic-ray works on 3.14: adopt it, wire into CI, supersede ADR-0052.
   Rule 6 requires an ADR for the tool substitution.
5. If cosmic-ray does NOT work on 3.14: fall back to running mutmut under a
   Python 3.13 interpreter for mutation runs only, while the rest of the
   project remains on 3.14. This requires a `tox` or `nox` configuration to
   manage the dual-interpreter setup. An ADR is still required.
6. Either path must produce a real, verifiable mutation score for
   `shared/security/` and `modules/privacy/`. The ADR-0047 threshold (60%,
   targeting 70% by Phase 55) must be enforceable.
7. The CI gate must block merges when mutation score drops below threshold.
8. The existing 19 manual hardening tests (`test_mutation_hardening_t49_5.py`)
   remain as defense-in-depth regardless of which tool is adopted.

### Acceptance Criteria

1. **Spike**: cosmic-ray installed in dev dependencies. Run against
   `shared/security/audit.py` (medium complexity, known mutant surface).
   Record: mutant count, kill count, surviving mutants, wall time, exit codes.
   Document whether all mutants execute cleanly (no SIGSEGV).
2. **If cosmic-ray works** (no SIGSEGV, mutation score computable):
   - Replace `mutmut` with `cosmic-ray` in `pyproject.toml` dev dependencies.
   - Configure cosmic-ray to target `shared/security/` and `modules/privacy/`.
   - Mutation score meets or exceeds 60% threshold (ADR-0047).
   - Wire `cosmic-ray` into `.github/workflows/ci.yml` as a blocking gate.
   - ADR documenting substitution: mutmut → cosmic-ray, rationale (Python 3.14
     compatibility), configuration, and threshold enforcement.
   - Update ADR-0052 status to `Superseded by ADR-XXXX`.
3. **If cosmic-ray fails** (SIGSEGV or other 3.14 incompatibility):
   - Configure dual-interpreter: `tox` or `nox` environment pinned to Python
     3.13 for mutation testing only.
   - `mutmut` runs under 3.13 interpreter against source code that targets
     3.14. Document any 3.13/3.14 syntax incompatibilities and mitigations.
   - Wire the dual-interpreter mutation gate into CI.
   - ADR documenting the dual-interpreter strategy, rationale, and risks.
   - Update ADR-0052 status to `Superseded by ADR-XXXX`.
4. Constitution enforcement table updated: Priority 4 mutation score row
   changes from `[ADVISORY — no programmatic gate]` to the actual CI gate
   command.
5. Full gate suite passes.

### Files to Create/Modify

- Modify: `pyproject.toml` (dependency swap or addition)
- Modify: `.github/workflows/ci.yml` (add mutation testing gate)
- Modify: `CONSTITUTION.md` (update enforcement table)
- Create: `docs/adr/ADR-XXXX-mutation-tool-adoption.md`
- Modify: `docs/adr/ADR-0052-mutmut-python-314-gap.md` (status → Superseded)
- Possibly create: `tox.ini` or `noxfile.py` (fallback path only)

---

## T53.2 — Audit HMAC: Include Details Field in Signature

**Priority**: P0 — Security. Closes ADV-P49-02.

### Context & Constraints

1. `shared/security/audit.py` computes HMAC-SHA256 signatures over a canonical
   representation of audit events. The `details` dict is excluded from the
   signed payload.
2. An attacker with write access to the WORM log store could modify `details`
   without invalidating the HMAC signature. The chain hash covers `details`
   transitively but is re-computable by an attacker who controls the store.
3. Fix: include a canonical serialization of `details` in the HMAC input.
   Use `json.dumps(details, sort_keys=True, separators=(",", ":"))` for
   deterministic serialization.
4. **Migration**: existing audit events have signatures computed without
   `details`. The verification routine must support both formats:
   - Events with a signature version prefix (e.g., `v2:`) verify with
     details included.
   - Events without the prefix verify with the legacy (no-details) format.
   - New events always use the v2 format.
5. This is a signature format change. Document in ADR-0048 amendment or a new
   ADR if the change is substantial enough.

### Acceptance Criteria

1. New audit events include `details` in HMAC computation.
2. Signature format includes a version discriminator (`v1:` legacy,
   `v2:` with details).
3. `verify_event()` correctly verifies both v1 (legacy) and v2 signatures.
4. New test: create event, tamper with `details` field only → v2 signature
   verification fails.
5. New test: legacy v1 event (no details in HMAC) → still verifies correctly.
6. New test: v2 event with matching details → verifies correctly.
7. New test: v2 event with `details=None` vs `details={}` produce different
   signatures (edge case).
8. Close ADV-P49-02 in RETRO_LOG.
9. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/security/audit.py`
- Modify: `tests/unit/test_audit.py`
- Modify or amend: `docs/adr/ADR-0048-audit-trail-anchoring.md`
- Modify: `docs/RETRO_LOG.md` (close ADV-P49-02)

---

## T53.3 — Programmatic Auth Coverage Gate

**Priority**: P0 — Constitution Priority 0.5. Closes the `[ADVISORY — no
programmatic gate]` annotation on the auth coverage row in the Constitution
enforcement table.

### Context & Constraints

1. CONSTITUTION.md line 107 documents: `[ADVISORY — no programmatic gate:
   test_all_routes_require_auth() does not exist]`.
2. The system has `AUTH_EXEMPT_PATHS` in `_exempt_paths.py`. Every route NOT
   in this list must require authentication.
3. A programmatic gate: enumerate all registered FastAPI routes, subtract
   the exempt paths, assert each remaining route returns 401 without a valid
   Bearer token.
4. This test must be an integration test (real FastAPI app, real ASGI
   transport) to catch middleware ordering bugs.
5. The test must be self-maintaining: if a new route is added without auth,
   the test fails. No manual route list to update.

### Acceptance Criteria

1. `tests/integration/test_all_routes_require_auth.py` created.
2. Test enumerates all routes from the FastAPI app's `app.routes`.
3. Test subtracts `AUTH_EXEMPT_PATHS` and health/readiness probe paths.
4. For each remaining route, sends a request with no Bearer token → asserts
   401 Unauthorized.
5. For each remaining route, sends a request with an invalid Bearer token →
   asserts 401 Unauthorized.
6. Test is self-maintaining: adding a new route without auth causes this test
   to fail (no manual route list).
7. Constitution enforcement table updated: auth coverage row changes from
   `[ADVISORY — no programmatic gate]` to
   `test_all_routes_require_auth() in tests/integration/`.
8. Full gate suite passes.

### Files to Create/Modify

- Create: `tests/integration/test_all_routes_require_auth.py`
- Modify: `CONSTITUTION.md` (update enforcement table)

---

## T53.4 — Redis TLS Promotion Deduplication

**Priority**: P2 — Maintainability. Closes ADV-P47-02.

### Context & Constraints

1. `_promote_redis_url_to_tls` logic is duplicated between
   `shared/tls/config.py` and the bootstrapper init. Both implementations
   rewrite `redis://` to `rediss://` when mTLS is enabled.
2. The duplication was intentional during T46.2 (mTLS implementation) to
   minimize cross-module coupling during a security-critical change.
3. Now that mTLS is stable and tested, consolidate into a single utility in
   `shared/tls/config.py` and have the bootstrapper import it.
4. This is a pure refactor — no behavior change. Existing tests must continue
   to pass without modification.

### Acceptance Criteria

1. Single `promote_redis_url_to_tls()` function in `shared/tls/config.py`.
2. Bootstrapper imports and calls the shared function instead of duplicating
   the logic.
3. No behavior change — existing TLS promotion tests pass without
   modification.
4. New test: verify the shared function handles edge cases (already `rediss://`,
   `None` URL, URL with port, URL with auth credentials).
5. Close ADV-P47-02 in RETRO_LOG.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/tls/config.py`
- Modify: `src/synth_engine/bootstrapper/main.py` (or wherever the duplicate
  lives)
- Modify: `tests/unit/test_mtls_wiring.py` or `test_tls_config.py`
- Modify: `docs/RETRO_LOG.md` (close ADV-P47-02)

---

## Task Execution Order

```
T53.1 (mutation testing spike + adoption) ──────> foundation (longest task)
                                                    |
T53.2 (audit HMAC details) ────────────┐            |
T53.3 (auth coverage gate) ────────────┼──> parallel with T53.1
T53.4 (Redis TLS dedup) ──────────────┘
```

T53.1 is the longest task (spike + evaluation + CI wiring) and should start
immediately. T53.2, T53.3, and T53.4 are independent of each other and of
T53.1 — all four tasks can run in parallel.

---

## Phase 53 Exit Criteria

1. Mutation testing produces a real, verifiable score on Python 3.14.
2. Mutation score meets ADR-0047 threshold (≥60%) on `shared/security/` and
   `modules/privacy/`.
3. Mutation testing gate wired into CI and blocks merges below threshold.
4. Constitution enforcement table has zero `[ADVISORY — no programmatic gate]`
   rows for Priority 0 items.
5. Audit HMAC covers `details` field with backward-compatible versioned
   signatures.
6. All registered routes (except explicit exemptions) return 401 without auth
   — verified by self-maintaining integration test.
7. Redis TLS promotion logic consolidated into single shared utility.
8. ADV-P49-02 and ADV-P47-02 closed in RETRO_LOG.
9. ADR-0052 superseded by new mutation tool ADR.
10. All quality gates pass.
11. Review agents pass for all tasks.


---

# Phase 54 — Full E2E DP Synthesis Validation (Pagila)

**Goal**: Prove the complete pipeline end-to-end with a real multi-table PostgreSQL
dataset (Pagila), real CTGAN training with DP-SGD, and validated synthetic output.
This is the first real-data validation of the system.

**Prerequisite**: Phase 53 merged.

**Source**: Staff-level architecture review, 2026-03-24 — identified that the system
has never been proven end-to-end with real data through the full pipeline.

---

## T54.1 — Pagila Dataset Provisioning

**Priority**: P6 — Infrastructure setup for validation.

### Context & Constraints

1. Pagila is the PostgreSQL port of MySQL's Sakila sample database. It provides
   ~15 tables with a rich FK graph (customer → rental → inventory → film, etc.),
   varied column types (timestamps, numerics, text, booleans), ~46K rentals,
   ~16K customers, ~1K films.
2. The dataset contains no real PII — all data is fictional DVD rental records.
   Column names (`first_name`, `last_name`, `email`, `address`) exercise the
   masking registry naturally.
3. Pagila SQL dumps are publicly available from the official PostgreSQL wiki
   and GitHub mirrors. Use the official `pagila-data.sql` and `pagila-schema.sql`.
4. The dataset must be loaded into a local PostgreSQL instance accessible to the
   engine's ingestion adapter.
5. Include a `scripts/provision_pagila.sh` helper that downloads the Pagila SQL
   files, creates the `pagila` database, and loads the schema + data.
6. Add a `sample_data/pagila/` directory with a README documenting the dataset
   source, license (PostgreSQL License), and table count.

### Acceptance Criteria

1. `scripts/provision_pagila.sh` created — downloads Pagila, creates DB, loads data.
2. Script is idempotent (drops and recreates if DB exists).
3. `sample_data/pagila/README.md` documents dataset source, license, table list.
4. Pagila loads cleanly into PostgreSQL 16+ with all FK constraints satisfied.
5. Script validates row counts after load (customers ≥ 500, rentals ≥ 40000).

### Files to Create/Modify

- Create: `scripts/provision_pagila.sh`
- Create: `sample_data/pagila/README.md`

---

## T54.2 — Full Pipeline Validation Script

**Priority**: P4 — Production validation.

### Context & Constraints

1. This script exercises the COMPLETE production pipeline end-to-end:
   - Schema reflection (ingestion adapter → FK DAG)
   - Subsetting (FK-aware row selection from Pagila)
   - Masking (deterministic FPE on PII-like columns)
   - Statistical profiling (distribution detection)
   - CTGAN training WITH DP-SGD wrapper (real Opacus, real epsilon accounting)
   - FK post-processing (orphan elimination)
   - Output validation (statistical comparison, FK integrity check)
2. This is NOT an automated test suite fixture. It is a standalone validation
   script in `scripts/` that an operator runs manually to prove the system works.
   It does NOT run as part of `pytest` or CI.
3. The script must accept configuration via CLI arguments or environment variables:
   - Database connection string (default: local Pagila from T54.1)
   - Subset size (default: 500 rows from root table)
   - Epsilon budget (default: 10.0 — generous for validation)
   - Number of CTGAN epochs (default: 50 — enough for validation, not production)
   - Output directory for synthetic Parquet files
4. The script MUST use the actual production code paths — not test doubles,
   not mocks, not `DummyMLSynthesizer`. It imports from `src/synth_engine/`
   and uses the real `SynthesisEngine`, real `DPTrainingWrapper`, real
   `StatisticalProfiler`, real `DeterministicMaskingEngine`.
5. Requires the `synthesizer` dependency group (`poetry install --with synthesizer`).
   CPU-only is acceptable — set `FORCE_CPU=true` if no GPU available.
6. The validation script should produce a structured report (JSON or markdown)
   documenting:
   - Tables processed, row counts (source vs synthetic)
   - FK integrity check results (orphan count per FK column)
   - Epsilon budget consumed vs allocated
   - Per-column distribution comparison (KS statistic or similar)
   - Masking verification (no unmasked PII-like values in output)
   - Wall-clock time per pipeline stage
7. Select a representative subset of Pagila tables for the validation (not all 15).
   Recommended: `customer`, `address`, `rental`, `inventory`, `film` — a 5-table
   linear FK chain that exercises the subsetting engine's topological traversal.

### Acceptance Criteria

1. `scripts/validate_full_pipeline.sh` (or `.py`) created.
2. Script runs the complete pipeline: reflect → subset → mask → profile →
   train (DP-CTGAN) → generate → FK post-process → validate.
3. Uses real production code paths — no mocks, no test doubles.
4. Produces a structured validation report with:
   - Table/row counts (source vs synthetic)
   - FK integrity results (zero orphans after post-processing)
   - Epsilon accounting (budget consumed < allocated)
   - Per-column statistical comparison
   - Masking verification
   - Timing breakdown
5. Report is written to `output/validation-report-<timestamp>.json` (gitignored).
6. Script exits 0 on success (all validations pass), non-zero on any failure.
7. Runs successfully on CPU-only with `FORCE_CPU=true`.
8. Validated on the 5-table Pagila subset (customer → address → rental →
   inventory → film).
9. Full quality gates pass (the script itself must pass ruff, mypy, bandit).

### Files to Create/Modify

- Create: `scripts/validate_full_pipeline.py`
- Modify: `.gitignore` (ensure `output/` is excluded — should already be)
- Modify: `Makefile` (add `validate-pipeline` target)

---

## T54.3 — Validation Run & Results Documentation

**Priority**: P6 — Documentation.

### Context & Constraints

1. Execute the validation script from T54.2 against the Pagila dataset from T54.1.
2. Capture the full output report.
3. Document the results in `docs/E2E_VALIDATION_RESULTS.md` — not the archived
   `docs/archive/E2E_VALIDATION.md` (which is from an earlier, less comprehensive run).
4. Include: hardware specs, Python version, dependency versions, wall-clock times,
   epsilon budget accounting, FK integrity verification, statistical comparison
   summary, and any anomalies observed.
5. If any validation check fails, document the failure and create an advisory.

### Acceptance Criteria

1. Validation script executed successfully against Pagila.
2. `docs/E2E_VALIDATION_RESULTS.md` created with full results.
3. All FK integrity checks pass (zero orphans).
4. Epsilon consumed < epsilon allocated.
5. Masking verification passes (no unmasked PII-like values).
6. Any anomalies documented with analysis.
7. `docs/index.md` updated with link to new results document.

### Files to Create/Modify

- Create: `docs/E2E_VALIDATION_RESULTS.md`
- Modify: `docs/index.md`

---

## Task Execution Order

```
T54.1 (Pagila provisioning) ──> T54.2 (validation script) ──> T54.3 (run & document)
```

Sequential — each task depends on the previous.

---

## Phase 54 Exit Criteria

1. Pagila dataset loads cleanly into local PostgreSQL.
2. Full pipeline runs end-to-end with real CTGAN + DP-SGD (no mocks).
3. FK integrity verified — zero orphans in synthetic output.
4. Epsilon accounting verified — budget not exceeded.
5. Masking verified — no unmasked PII-like values in output.
6. Validation results documented with statistical comparison.
7. All quality gates pass.
8. Review agents pass for all tasks.


---

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


---

# Phase 56 — Refactoring Priorities

**Goal**: Execute the highest-yield refactoring items synthesized from the
staff-level architecture review (2026-03-24) and all review agent findings
across Phases 45–53. Prioritized by cognitive load reduction and maintainability.

**Prerequisite**: Phase 55 merged.

**Source**: Staff-level architecture review (all tiers), RETRO_LOG advisory
history, test quality analysis, and full codebase audit.

---

## Refactoring Items — Full List

### Tier 1: Highest-Yield Structural Changes

| ID | Refactor | Source | Yield |
|----|----------|--------|-------|
| RF-01 | Decompose `modules/synthesizer/` into sub-packages | Arch review | Reduce 24-file, 5,199-LOC module to 4 focused packages |
| RF-02 | Extract bootstrapper wiring from `main.py` | Arch review | Eliminate fragile module-scope side effects |
| RF-03 | Consolidate large test files (5 files > 1,000 LOC) | QA review | Reduce per-file cognitive load |

### Tier 2: Code Quality Improvements

| ID | Refactor | Source | Yield |
|----|----------|--------|-------|
| RF-04 | Strengthen shallow test assertions (59 files with `is not None` only) | QA review, Constitution P4 | Improve mutation kill rate |
| RF-05 | Migrate mock-heavy transactional tests to integration tests | QA review | Improve confidence in egress/rollback paths |
| RF-06 | Eliminate `# noqa: F401, E402` density in `main.py` | Arch review | Clean import structure |

### Tier 3: Documentation & Governance Cleanup

| ID | Refactor | Source | Yield |
|----|----------|--------|-------|
| RF-07 | Add programmatic gates for remaining ADVISORY Constitution rows | Arch review, P49 retro | Complete enforcement table |
| RF-08 | RETRO_LOG archival — move phases 15–45 to `retro_archive/` | QA review | Reduce token load (54,646 tokens → ~10,000) |
| RF-09 | ADR status audit — verify all ADR statuses match current code | Phase boundary auditor | Eliminate stale ADR references |

### Tier 4: Infrastructure Modernization

| ID | Refactor | Source | Yield |
|----|----------|--------|-------|
| RF-10 | Evaluate Python 3.13 downgrade for ecosystem compatibility | DevOps review | Unblock mutmut, reduce supply chain risk |
| RF-11 | Add `Makefile` targets for common multi-step workflows | DevOps review | Reduce onboarding friction |
| RF-12 | Consolidate `docker-compose*.yml` overlays | DevOps review | Reduce deployment configuration surface |

---

## T56.1 — Decompose Synthesizer Module

**Priority**: P5 — Architecture.

### Context & Constraints

1. `modules/synthesizer/` contains 24 files and 5,199 LOC — 10x the size of
   every other module. It owns at least 6 distinct responsibilities: ML training,
   job orchestration, storage, retention, webhook delivery, and reaper lifecycle.
2. Decompose into focused sub-packages:
   ```
   modules/synthesizer/
   ├── __init__.py          # Re-exports for backward compatibility
   ├── training/            # engine.py, dp_training.py, dp_discriminator.py,
   │                        # dp_accounting.py, training_strategies.py, ctgan_*.py
   ├── jobs/                # job_models.py, job_orchestration.py, job_steps.py,
   │                        # job_finalization.py, tasks.py
   ├── storage/             # storage.py, models.py (ModelArtifact), shred.py,
   │                        # erasure.py
   └── lifecycle/           # retention.py, retention_tasks.py, reaper_repository.py,
                            # reaper_tasks.py, webhook_delivery.py, guardrails.py
   ```
3. This is a pure refactor — no behavior change. All existing tests must pass
   without modification (use `__init__.py` re-exports for backward compatibility).
4. Import-linter contracts may need updating if sub-packages are treated as
   separate modules. Evaluate whether sub-packages should be independent or
   whether the existing synthesizer contract is sufficient.
5. Move `_optional_deps.py` to the synthesizer package root (shared by all
   sub-packages).

### Acceptance Criteria

1. Synthesizer module decomposed into 4 sub-packages.
2. All existing tests pass without modification.
3. Import-linter contracts pass.
4. `__init__.py` re-exports preserve backward compatibility.
5. No file exceeds 500 LOC in the new structure.
6. Module docstrings updated to reflect new sub-package structure.
7. Full gate suite passes.

---

## T56.2 — Extract Bootstrapper Wiring Module

**Priority**: P5 — Maintainability.

### Context & Constraints

1. `bootstrapper/main.py` lines 245–255 contain module-scope import side
   effects that register Huey tasks and inject DI factories. These are
   guarded by `# noqa: F401, E402` comments.
2. The import order is fragile — if a circular import appears, tasks will
   silently fail to register.
3. Extract to `bootstrapper/wiring.py` with explicit registration functions:
   ```python
   def wire_task_registrations() -> None:
       """Register all Huey tasks and inject DI factories."""
       ...
   ```
4. Call `wire_task_registrations()` from `create_app()` (explicit) rather
   than relying on module-scope side effects (implicit).
5. This is a pure refactor — no behavior change.

### Acceptance Criteria

1. `bootstrapper/wiring.py` created with explicit registration functions.
2. Module-scope side effects removed from `main.py`.
3. `create_app()` calls wiring functions explicitly.
4. All `# noqa: F401, E402` comments in `main.py` eliminated or reduced
   to genuine re-exports only.
5. All existing tests pass without modification.
6. Full gate suite passes.

---

## T56.3 — Test File Consolidation & Assertion Hardening

**Priority**: P4 — Test quality.

### Context & Constraints

1. Five test files exceed 1,000 LOC:
   - `test_auth_gap_remediation.py` (1,369)
   - `test_bootstrapper_errors.py` (1,205)
   - `test_full_pipeline_e2e.py` (1,155)
   - `test_authorization.py` (1,151)
   - `test_job_steps.py` (1,146)
2. Split each by logical grouping (e.g., `test_auth_gap_remediation.py` →
   `test_auth_gap_scope.py`, `test_auth_gap_jwt.py`, etc.). Preserve all
   test functions — zero test deletion.
3. Separately, scan all 59 files with `is not None` as sole assertion.
   Replace with specific value assertions where possible. Flag cases where
   `is not None` is genuinely the right assertion (e.g., factory return type
   verification) — these are acceptable if documented.
4. This is a pure refactor — no behavior change. Test count must not decrease.

### Acceptance Criteria

1. No test file exceeds 600 LOC after splitting.
2. All tests preserved — zero test deletion.
3. `is not None` sole-assertion tests reduced by ≥50%.
4. Remaining `is not None` assertions documented with inline justification.
5. All tests pass without modification to production code.
6. Full gate suite passes.

---

## T56.4 — RETRO_LOG Archival

**Priority**: P6 — Documentation.

### Context & Constraints

1. `docs/RETRO_LOG.md` is 54,646 tokens. Agent context consumption at
   phase start is excessive.
2. Archive phases 15–45 to `docs/retro_archive/phases-15-to-45.md`.
3. Retain phases 46+ in the active RETRO_LOG (recent, actionable context).
4. Update the Open Advisory Items table (stays in active RETRO_LOG).
5. Update `docs/index.md` retro archive table.

### Acceptance Criteria

1. Phases 15–45 moved to `docs/retro_archive/phases-15-to-45.md`.
2. Active RETRO_LOG contains only phases 46+ and Open Advisory Items.
3. Active RETRO_LOG is under 15,000 tokens.
4. `docs/index.md` updated.
5. No content lost — all historical records preserved in archive.

---

## T56.5 — ADR Status Audit

**Priority**: P6 — Documentation accuracy.

### Context & Constraints

1. Phase boundary auditor has flagged potential ADR staleness in multiple
   phases. Several ADRs reference classes, functions, or modules that may
   have been renamed or removed.
2. Audit all 53 ADRs against current codebase:
   - Verify referenced classes/functions still exist (grep for each).
   - Verify ADR status matches reality (e.g., ADR-0002 was already
     Superseded but the index said Accepted until P53 cleanup).
   - Flag any ADR whose decision has been silently reversed without an
     amendment.
3. Update statuses and add supersession notices where needed.

### Acceptance Criteria

1. All 53 ADRs audited against current code.
2. Any ADR referencing deleted/renamed code updated with amendment notice.
3. ADR status in `docs/index.md` matches the ADR file header for all entries.
4. No ADR references a non-existent class, function, or module without a
   deprecation notice.
5. Findings documented in a summary table in the PR description.

---

## Task Execution Order

```
T56.1 (synthesizer decomposition) ──> largest refactor, start first
T56.2 (bootstrapper wiring) ─────────> can parallel with T56.1
T56.3 (test consolidation) ──────────> can parallel with T56.1/T56.2
T56.4 (RETRO_LOG archival) ──────┐
T56.5 (ADR status audit) ────────┼──> docs tasks, parallel with code tasks
```

---

## Phase 56 Exit Criteria

1. Synthesizer module decomposed into 4 focused sub-packages.
2. Bootstrapper wiring extracted from module-scope side effects.
3. No test file exceeds 600 LOC.
4. `is not None` sole-assertion tests reduced by ≥50%.
5. RETRO_LOG active section under 15,000 tokens.
6. All 53 ADRs audited and statuses corrected.
7. Zero test deletions — all refactoring preserves existing test count.
8. All quality gates pass.
9. Review agents pass for all tasks.


---

# Phase 57 — Critical Audit Findings Remediation

**Goal**: Address the 3 critical production issues and the security-adjacent
findings identified in the 2026-03-26 staff-level security audit. These are
items that will break or compromise the system in a production deployment.

**Prerequisite**: Phase 56 merged.

**Source**: Staff-level security audit (2026-03-26), justified scoring across
7 categories. All findings cite specific file:line references.

---

## Critical Issues — Full List

| ID | Issue | Location | Impact |
|----|-------|----------|--------|
| CA-01 | JWT pass-through on empty secret key | `auth.py:228-233` | Full authentication bypass in misconfigured production |
| CA-02 | `assert` in production code | `main.py:105-107` | `AssertionError` crash in partial-install deployments |
| CA-03 | Empty defaults for required fields | `settings.py:180-189` | Silent no-DB/no-audit operation if `validate_config()` missed |
| CA-04 | Epsilon budget logged at INFO on success path | `accountant.py:203-210` | Privacy budget state leaked to log consumers (T47.9 gap) |
| CA-05 | Silent genesis fallback on anchor config error | `audit.py:698` | Audit chain continuity silently broken |
| CA-06 | Dual env/conclave_env confusion | `settings.py:248-258` | Production mode bypass via conflicting env vars |
| CA-07 | Erasure path silently swallows errors | `lifecycle/erasure.py:217` | GDPR right-to-erasure failure undetected |

---

## T57.1 — JWT Authentication Hard-Fail in Production

**Priority**: P0 — Security.

### Context & Constraints

1. `bootstrapper/dependencies/auth.py:228-233`: When `jwt_secret_key` is empty,
   `get_current_operator` returns `""` as operator identity with only a WARNING log.
2. In production mode, this effectively disables authentication on all endpoints.
3. Fix: In `is_production()` mode, raise `AuthenticationError` when JWT secret
   is not configured. Pass-through mode is only valid in development/test.

### Acceptance Criteria

1. `get_current_operator` raises `AuthenticationError` when JWT secret is empty
   AND `settings.is_production()` is True.
2. Pass-through mode preserved for `conclave_env != "production"`.
3. Attack test: production mode + empty JWT key → 401 on all protected endpoints.
4. Feature test: dev mode + empty JWT key → pass-through still works.
5. Full gate suite passes.

---

## T57.2 — Replace Production Assert with Descriptive Error

**Priority**: P1 — Reliability.

### Context & Constraints

1. `bootstrapper/main.py:105-107`: `assert MinioStorageBackend is not None`
   crashes with unhelpful `AssertionError` when the synthesizer dependency
   group is absent.
2. Fix: Replace with `RuntimeError` containing install instructions.

### Acceptance Criteria

1. `build_ephemeral_storage_client()` raises `RuntimeError` with actionable
   message when `MinioStorageBackend` is None.
2. No `assert` statements remain in production code paths (scan `src/` for
   bare `assert` that isn't in test code).
3. Full gate suite passes.

---

## T57.3 — Production-Mode Validation for Required Settings

**Priority**: P0 — Reliability.

### Context & Constraints

1. `settings.py:180-189`: `database_url` and `audit_key` default to empty
   strings with no construction-time validation.
2. If `validate_config()` is not called (e.g., new integration path), the app
   runs without a database and without audit signing.
3. Fix: Add Pydantic `@field_validator` that rejects empty values when
   `conclave_env == "production"`. Dev/test mode allows empty (for unit tests).

### Acceptance Criteria

1. `ConclaveSettings` raises `ValidationError` when `database_url` is empty
   AND `conclave_env == "production"`.
2. Same for `audit_key`.
3. Dev/test mode (`conclave_env != "production"`) allows empty values.
4. Attack test: production mode + empty database_url → ValidationError.
5. Full gate suite passes.

---

## T57.4 — Epsilon Budget Logging Scrub

**Priority**: P1 — Compliance.

### Context & Constraints

1. `accountant.py:203-210`: Epsilon values (total_spent, remaining) logged at
   INFO on the success path. T47.9 scrubbed these from `BudgetExhaustionError`
   messages, but the success-path log was missed.
2. Fix: Reduce to DEBUG level. Epsilon values should not be visible in default
   log configurations.

### Acceptance Criteria

1. Epsilon budget values logged at DEBUG, not INFO.
2. No epsilon numeric values appear in INFO or WARNING log messages from
   `accountant.py`.
3. Full gate suite passes.

---

## T57.5 — Narrow Exception Handling in Audit Logger Singleton

**Priority**: P1 — Reliability.

### Context & Constraints

1. `audit.py:698`: `get_audit_logger()` catches bare `Exception` on
   `anchor_file_path` retrieval and silently falls back to `None`.
2. A misconfigured anchor path starts the chain from genesis with no alert.
3. Fix: Narrow to specific exceptions (`AttributeError`, `KeyError`,
   `TypeError`). Log at WARNING when fallback occurs.

### Acceptance Criteria

1. `get_audit_logger()` catches only expected exception types on anchor path.
2. WARNING logged when anchor path retrieval fails.
3. Unexpected exceptions propagate (programming errors surface).
4. Full gate suite passes.

---

## T57.6 — Unify Environment Configuration

**Priority**: P2 — Maintainability.

### Context & Constraints

1. `settings.py:248-258`: `conclave_env` defaults to `"production"` but `env`
   defaults to `""`. The `is_production()` method uses `conclave_env`, but the
   dual field creates confusion.
2. Fix: Remove the redundant `env` field or alias it to `conclave_env`.
   Document the single source of truth.

### Acceptance Criteria

1. Single environment field determines production/development mode.
2. `is_production()` uses the unified field.
3. No ambiguity about which env var controls production mode.
4. Full gate suite passes.

---

## T57.7 — Erasure Error Handling Hardening

**Priority**: P1 — Compliance.

### Context & Constraints

1. `lifecycle/erasure.py:217`: `except Exception` silently swallows erasure
   errors. A GDPR right-to-erasure failure going undetected is a compliance risk.
2. Fix: Log at ERROR and re-raise or return a failure status that the caller
   can act on. Erasure failures must be visible to operators.

### Acceptance Criteria

1. Erasure failures logged at ERROR with the affected data subject/table.
2. Erasure function returns a result indicating success/failure.
3. Caller can distinguish partial erasure from complete erasure.
4. Full gate suite passes.

---

## Task Execution Order

```
T57.1 (JWT hard-fail) ──────────┐
T57.3 (settings validation) ────┼──> security-critical, do first
T57.2 (assert replacement) ─────┤
T57.4 (epsilon scrub) ──────────┘
T57.5 (audit exception narrowing) ──> reliability
T57.6 (env unification) ────────────> maintainability
T57.7 (erasure hardening) ──────────> compliance
```

---

## Phase 57 Exit Criteria

1. JWT authentication refuses empty keys in production mode.
2. No `assert` in production code paths.
3. Required settings validated at construction time in production mode.
4. Epsilon values not in INFO/WARNING logs.
5. Audit logger fallback logged at WARNING.
6. Single environment configuration field.
7. Erasure failures visible to operators.
8. All quality gates pass.
9. Review agents pass for all tasks.


---

# Phase 58 — Refactoring & Quality Hardening

**Goal**: Address the structural refactoring priorities and test/documentation
quality findings from the 2026-03-26 staff-level audit. These items reduce
cognitive load, improve type safety, and strengthen test efficacy.

**Prerequisite**: Phase 57 merged.

**Source**: Staff-level security audit (2026-03-26), justified scoring across
7 categories — maintainability (7/10), test efficacy (8/10), documentation
value (7/10), hidden technical debt (7/10).

---

## Refactoring Items — Full List

### Tier 1: Type Safety & Code Quality

| ID | Refactor | Location | Yield |
|----|----------|----------|-------|
| RQ-01 | Replace `Any` types with TYPE_CHECKING Protocols | `dp_engine.py:138-150` | Type safety at DP boundary |
| RQ-02 | Eliminate double JWT decode | `auth.py:338-357` | Performance + reduced complexity |
| RQ-03 | Replace `inspect.getsource()` tests | `test_ssrf_fail_closed.py:148-185` | Refactor-resilient tests |
| RQ-04 | Replace structural pass-with-pass tests | `test_bootstrapper_wiring.py:32-76` | Meaningful behavioral coverage |

### Tier 2: File Decomposition

| ID | Refactor | Location | Yield |
|----|----------|----------|-------|
| RQ-05 | Split audit.py (721 LOC) | `shared/security/audit.py` | Reduce per-file cognitive load |
| RQ-06 | Split models.py (694 LOC) | `synthesizer/storage/models.py` | Separate artifact from unpickler |
| RQ-07 | Group ConclaveSettings into nested sub-models | `shared/settings.py` | Discoverability, 40+ fields → grouped |

### Tier 3: Documentation Cleanup

| ID | Refactor | Location | Yield |
|----|----------|----------|-------|
| RQ-08 | Deduplicate settings.py class docstring | `settings.py:89-165` | Remove 76 lines of duplication |
| RQ-09 | Compress verbose module docstrings | `auth.py:1-58`, `health.py:1-78`, `dp_engine.py:55-108` | Reduce scroll-past noise |
| RQ-10 | Move response schemas from docstrings to OpenAPI | `health.py:37-46` | Single source of truth |

### Tier 4: Test Infrastructure

| ID | Refactor | Location | Yield |
|----|----------|----------|-------|
| RQ-11 | Add Hypothesis property-based tests | HMAC signing, SSRF validation | Edge case coverage |
| RQ-12 | Track `# type: ignore` reduction | 35 across 22 files | Systematic type safety improvement |

---

## T58.1 — Replace Any Types in DPTrainingWrapper

**Priority**: P3 — Type safety.

### Context & Constraints

1. `dp_engine.py:138,144,148-150`: Five `Any` types for `_privacy_engine`,
   `wrapped_module`, `_wrapped_optimizer`, `_wrapped_dataloader`, and related
   PyTorch/Opacus objects.
2. These are optional dependencies — not always installed.
3. Fix: Use `TYPE_CHECKING` guard with `from __future__ import annotations`:
   ```python
   if TYPE_CHECKING:
       from opacus import PrivacyEngine
       from torch.nn import Module
       from torch.optim import Optimizer
       from torch.utils.data import DataLoader
   ```
4. Type as `PrivacyEngine | None`, `Module | None`, etc.

### Acceptance Criteria

1. Zero `Any` types for PyTorch/Opacus objects in `dp_engine.py`.
2. `mypy src/` passes.
3. No runtime dependency on opacus/torch for type checking.
4. Full gate suite passes.

---

## T58.2 — Eliminate Double JWT Decode

**Priority**: P3 — Performance + clarity.

### Context & Constraints

1. `auth.py:338-357`: `require_scope()` re-decodes the JWT that
   `get_current_operator` already decoded and verified.
2. Fix: Store decoded claims on `request.state.jwt_claims` in
   `get_current_operator`. Read from `request.state` in `require_scope`.
3. This eliminates redundant HMAC verification per scope-protected request.

### Acceptance Criteria

1. JWT decoded exactly once per request, not twice.
2. `request.state.jwt_claims` populated by `get_current_operator`.
3. `require_scope` reads claims from `request.state`, not by re-decoding.
4. All auth tests pass unchanged (behavioral equivalence).
5. Full gate suite passes.

---

## T58.3 — Replace Fragile Source-Inspection Tests

**Priority**: P4 — Test quality.

### Context & Constraints

1. `test_ssrf_fail_closed.py:148-185`: Uses `inspect.getsource()` to verify
   specific strings in production source code. Breaks on any formatting change.
2. `test_bootstrapper_wiring.py:32-76`: Six tests assert `callable()` and
   `__name__` — pass with `def fn(): pass`.
3. `test_bootstrapper_wiring.py:104-114`: Idempotency tests verify no exception
   but not correctness of registration state.
4. Fix: Replace source-inspection with behavioral mocks. Replace structural
   tests with behavioral assertions.

### Acceptance Criteria

1. Zero `inspect.getsource()` assertions in test suite.
2. SSRF strict/lenient verified by mocking `validate_callback_url` and checking
   call arguments (strict=True at registration, strict=False at delivery).
3. Wiring structural tests replaced with behavioral tests that verify IoC
   registration state after `wire_all()`.
4. Zero test function deletion (replace, don't delete).
5. Full gate suite passes.

---

## T58.4 — Split audit.py and models.py

**Priority**: P5 — Maintainability.

### Context & Constraints

1. `shared/security/audit.py` (721 LOC): Covers v1/v2/v3 signatures, chain
   management, anchor resume, singleton, and key loading.
2. `synthesizer/storage/models.py` (694 LOC): Covers RestrictedUnpickler,
   SynthesizerModel Protocol, ModelArtifact, signing key validation, format
   detection, and Prometheus counters.
3. Fix:
   - Split `audit.py` → `audit_logger.py` (chain + events), `audit_signatures.py`
     (v1/v2/v3 signing/verification), `audit_singleton.py` (get/reset).
   - Split `models.py` → `artifact.py` (ModelArtifact), `restricted_unpickler.py`
     (RestrictedUnpickler + allowlists + SynthesizerModel Protocol).
4. Re-export from `__init__.py` for backward compatibility.

### Acceptance Criteria

1. No file exceeds 400 LOC after split.
2. All existing imports continue to work (re-exports).
3. All tests pass without modification.
4. Full gate suite passes.

---

## T58.5 — Group ConclaveSettings into Sub-Models

**Priority**: P5 — Maintainability.

### Context & Constraints

1. `settings.py` has 40+ fields in a single flat class.
2. Fix: Group into nested Pydantic sub-models:
   - `TLSSettings` (tls_cert_path, tls_key_path, mtls_*)
   - `RateLimitSettings` (general_limit, burst_limit, etc.)
   - `RetentionSettings` (job_retention_days, artifact_retention_days, etc.)
   - `WebhookSettings` (webhook_delivery_timeout_seconds, etc.)
   - `AnchorSettings` (anchor_backend, anchor_file_path, anchor_every_*)
3. Fields accessed via `settings.tls.cert_path` instead of `settings.tls_cert_path`.

### Acceptance Criteria

1. Settings grouped into 5+ sub-models.
2. All call sites updated.
3. `.env.example` still works (Pydantic nested model env var prefix).
4. Full gate suite passes.

---

## T58.6 — Documentation Deduplication

**Priority**: P6 — Documentation.

### Context & Constraints

1. `settings.py:89-165`: Class docstring duplicates all Field descriptions.
2. `auth.py:1-58`: 58-line module docstring.
3. `health.py:1-78`: 78-line module docstring with embedded JSON schema.
4. `dp_engine.py:55-108`: Constructor params duplicated.
5. Fix: Remove field-by-field restatement from class docstrings. Move response
   schemas to OpenAPI metadata. Compress module docstrings to security-relevant
   rationale only.

### Acceptance Criteria

1. No class docstring restates Field descriptions.
2. Response schemas in OpenAPI, not Python comments.
3. Module docstrings ≤30 lines (security rationale only).
4. Constructor param docs appear once, not twice.
5. Full gate suite passes.

---

## T58.7 — Property-Based Testing (Hypothesis)

**Priority**: P4 — Test quality.

### Context & Constraints

1. HMAC signing: arbitrary field content (including pipe characters, null bytes,
   Unicode, multi-GB strings) should never produce collisions in v3 format.
2. SSRF validation: arbitrary IP addresses in blocked ranges should always be
   rejected; arbitrary safe IPs should always pass.
3. Fix: Add `hypothesis` to dev dependencies. Write property-based tests for
   HMAC v3 signing and SSRF validation.

### Acceptance Criteria

1. `hypothesis` added to dev dependencies.
2. Property test: v3 HMAC signing with arbitrary st.text() fields never
   produces collisions when fields differ.
3. Property test: SSRF validation rejects all RFC 1918, loopback, link-local
   addresses regardless of encoding (IPv4, IPv6, mapped).
4. Full gate suite passes.

---

## Task Execution Order

```
T58.1 (Any types) ──────────────────┐
T58.2 (JWT double-decode) ──────────┼──> quick wins, parallel
T58.3 (fragile tests) ─────────────┘
T58.4 (file splits) ───────────────> depends on T58.1 (dp_engine types)
T58.5 (settings sub-models) ───────> independent
T58.6 (doc dedup) ─────────────────> independent, any time
T58.7 (Hypothesis tests) ──────────> independent, any time
```

---

## Phase 58 Exit Criteria

1. Zero `Any` types in `dp_engine.py`.
2. JWT decoded exactly once per request.
3. Zero `inspect.getsource()` in tests.
4. `audit.py` and `models.py` each split into ≤400 LOC files.
5. Settings grouped into nested sub-models.
6. Docstring duplication eliminated.
7. Property-based tests for HMAC and SSRF.
8. Exception handler boilerplate replaced with data-driven loop.
9. `extra="ignore"` replaced with `extra="forbid"` or warning.
10. Failed v1/v2 HMAC verification attempts logged.
11. `ClassVar` annotations on VaultState class attributes.
12. Broad `except Exception` in wiring.py narrowed.
13. All quality gates pass.
14. Review agents pass for all tasks.


---

# Phase 59 — Production Readiness & v1.0 Release Preparation

**Goal**: Bring the system to production-deployment-grade quality suitable for
a v1.0 release. This means API versioning, load testing with realistic data
volumes, and operational polish that demonstrates production engineering maturity.

**Prerequisite**: Phase 57 merged. Phase 58 (refactoring) deferred to post-v1.0
as structural polish — it is not release-blocking.

**Source**: Staff-level review recommendations, PM release planning discussion.

---

## Production Readiness Items

| ID | Item | Category | Impact |
|----|------|----------|--------|
| PR-01 | API versioning (/api/v1/ prefix) | API stability | Contract stability for consumers |
| PR-02 | Load test with realistic data volumes | Operational validation | Performance baseline under load |
| PR-03 | API documentation (OpenAPI enrichment) | Developer experience | Self-documenting endpoints |
| PR-04 | Release infrastructure (tag, changelog, SBOM) | Release engineering | Auditable release artifact |

---

## T59.1 — API Versioning

**Priority**: P2 — API stability.

### Context & Constraints

1. All routes currently live at the root (`/jobs`, `/connections`, `/webhooks`,
   `/settings`, `/security/shred`, `/auth/token`, etc.).
2. A v1.0 release implies API contract stability. Without versioning, any
   future breaking change requires a major version bump on the entire system.
3. Fix: Add `/api/v1/` prefix to all business-logic routes. Infrastructure
   routes (`/health`, `/ready`, `/health/vault`, `/unseal`, `/metrics`,
   `/docs`, `/redoc`, `/openapi.json`) stay at the root.
4. Use FastAPI's `APIRouter(prefix="/api/v1")` pattern.
5. The OpenAPI docs should reflect the versioned paths.
6. Update all test fixtures that hit endpoints to use the `/api/v1/` prefix.

### Acceptance Criteria

1. All business-logic routes prefixed with `/api/v1/`.
2. Infrastructure routes remain at root (no prefix).
3. OpenAPI spec reflects versioned paths.
4. All integration tests updated.
5. `test_all_routes_require_auth.py` updated for new paths.
6. Full gate suite passes.

---

## T59.2 — Load Test with Realistic Data Volumes

**Priority**: P3 — Operational validation.

### Context & Constraints

1. The Pagila E2E validation used 200 rows / 3 tables / 6 seconds.
2. A production-grade validation should test with:
   - 5,000+ rows from the root table
   - All 5 target tables (customer, address, rental, inventory, film)
   - Real DP-SGD with epsilon ≤ 10.0
   - Wall-clock time measurement per stage
   - Memory usage profiling (peak RSS)
3. The `address` and `film` tables diverged under DP-SGD with small samples.
   With 5,000+ rows, convergence is more likely. If they still diverge,
   document the hyperparameter tuning needed.
4. Create `scripts/load_test.py` that runs the pipeline with configurable
   parameters and produces a performance report.
5. Run on the local machine (Apple M4, 24 GB RAM) and document results.

### Acceptance Criteria

1. `scripts/load_test.py` created with configurable row count, epochs, epsilon.
2. Successfully runs with 5,000 rows from customer root table.
3. Performance report includes: wall-clock per stage, peak memory (RSS),
   epsilon spent, rows/second throughput.
4. Results documented in `docs/LOAD_TEST_RESULTS.md`.
5. All 5 tables attempted. Divergent tables documented with analysis.
6. Full gate suite passes.

---

## T59.3 — OpenAPI Documentation Enrichment

**Priority**: P4 — Developer experience.

### Context & Constraints

1. FastAPI auto-generates OpenAPI docs, but the default descriptions are
   minimal. Production APIs need rich descriptions, example values, and
   error response schemas.
2. Add `summary`, `description`, `response_model`, and `responses` to all
   route decorators.
3. Add `tags_metadata` to the app for organized documentation.
4. Add example request/response bodies using Pydantic `model_config` with
   `json_schema_extra`.
5. Move response schemas from module docstrings (flagged by audit) into
   OpenAPI metadata.

### Acceptance Criteria

1. Every business-logic endpoint has a summary and description.
2. Error responses (400, 401, 403, 404, 409, 422, 423, 503) documented
   with RFC 7807 problem-detail schema.
3. Example request/response bodies in OpenAPI spec.
4. Tag descriptions for all route groups.
5. `/docs` page renders a production-quality API reference.
6. Full gate suite passes.

---

## T59.4 — Release Infrastructure

**Priority**: P2 — Release engineering.

### Context & Constraints

1. Tag `v1.0.0` on main after all tasks merge.
2. Create `CHANGELOG.md` summarizing all 59 phases.
3. Generate SBOM (already in CI — verify it produces a valid artifact).
4. Create a GitHub Release with:
   - Changelog summary
   - SBOM attachment
   - Link to E2E validation results
   - Link to load test results
5. Update `pyproject.toml` version to `1.0.0`.
6. Update README badges and project description for v1.0.

### Acceptance Criteria

1. `pyproject.toml` version set to `1.0.0`.
2. `CHANGELOG.md` created with phase-by-phase summary.
3. GitHub Release created with tag `v1.0.0`.
4. SBOM attached to release.
5. README updated for v1.0 (badges, description, quick start).
6. Full gate suite passes.

---

## Task Execution Order

```
T59.1 (API versioning) ────────> foundation for all other tasks
T59.2 (load test) ─────────────> independent, can parallel with T59.3
T59.3 (OpenAPI enrichment) ────> depends on T59.1 (versioned paths)
T59.4 (release infrastructure) > last, after all other tasks merge
```

---

## Phase 59 Exit Criteria

1. All business-logic routes versioned at `/api/v1/`.
2. Load test completed with 5,000+ rows, results documented.
3. OpenAPI spec production-quality with examples and error schemas.
4. v1.0.0 tagged, CHANGELOG created, GitHub Release published.
5. All quality gates pass.
6. Review agents pass for all tasks.


---

# Phase 60 — Bootstrapper Decomposition

**Goal**: Reduce cognitive load in the bootstrapper package by extracting
multi-responsibility files into focused, single-purpose modules. Pure
refactoring — zero behavioral changes.

**Prerequisite**: Phase 58 merged.

**Source**: Architect analysis (2026-03-26) — bootstrapper is 9,137 LOC across
47 files with 4 multi-responsibility files identified.

---

## Decomposition Items

| ID | Change | Source File | Risk | Effort |
|----|--------|------------|------|--------|
| BD-01 | Extract AuthenticationGateMiddleware | `dependencies/auth.py` (534 LOC) | Low | 1 hr |
| BD-02 | Move inline routes from lifecycle.py | `lifecycle.py` (217 LOC) | Medium | 2 hrs |
| BD-03 | Move build_ephemeral_storage_client | `main.py` (212 LOC) | Low | 30 min |
| BD-04 | Extract domain transaction logic | `factories.py` (311 LOC) | Medium | 3 hrs |
| BD-05 | Move UnsealRequest to schemas | `lifecycle.py` | Low | 15 min |

---

## T60.1 — Extract AuthenticationGateMiddleware

**Priority**: P3 — Maintainability.

### Context & Constraints

1. `bootstrapper/dependencies/auth.py` (534 LOC) combines JWT utility functions,
   FastAPI dependencies (`get_current_operator`, `require_scope`), and a Starlette
   middleware class (`AuthenticationGateMiddleware`).
2. The middleware is architecturally distinct from the dependencies: dependencies
   are injected per-route, the middleware runs on every request.
3. Split into:
   - `dependencies/auth.py` (keep) — `create_token`, `verify_token`,
     `verify_operator_credentials`, `get_current_operator`, `require_scope` (~300 LOC)
   - `dependencies/auth_middleware.py` (new) — `AuthenticationGateMiddleware`,
     `_build_401_response` (~120 LOC)
4. `AUTH_EXEMPT_PATHS` stays in `_exempt_paths.py` (already there).
5. `auth_middleware.py` imports `verify_token` from `auth.py` (one-way).

### Acceptance Criteria

1. `AuthenticationGateMiddleware` in its own file.
2. `auth.py` reduced to ≤350 LOC.
3. All existing imports continue to work (re-exports if needed).
4. All tests pass without modification.
5. `middleware.py` import updated to reference new file.
6. Full gate suite passes.

---

## T60.2 — Move Inline Routes from lifecycle.py

**Priority**: P4 — Maintainability.

### Context & Constraints

1. `lifecycle.py` (217 LOC) defines the lifespan hook AND inline route handlers
   for `/health` (liveness) and `/unseal`.
2. `/health` liveness probe duplicates purpose with `routers/health.py` (which
   has `/ready` and `/health/vault`).
3. Move `/health` liveness to `routers/health.py`.
4. Move `/unseal` to a new `routers/vault.py` or keep in lifecycle (it's a
   single ops route tightly coupled to the lifespan).
5. `lifecycle.py` becomes purely the lifespan hook (~80 LOC).
6. CRITICAL: `/health` and `/unseal` are in `COMMON_INFRA_EXEMPT_PATHS` and
   must remain at root (not under `/api/v1/`). Verify exempt-path matching
   still works after the move.

### Acceptance Criteria

1. `/health` liveness probe in `routers/health.py`.
2. `/unseal` either in `routers/vault.py` or documented as staying in lifecycle.
3. `lifecycle.py` ≤100 LOC.
4. All exempt-path tests pass.
5. All tests pass without modification (or with minimal path updates).
6. Full gate suite passes.

---

## T60.3 — Move build_ephemeral_storage_client to factories.py

**Priority**: P5 — Code organization.

### Context & Constraints

1. `main.py` contains `build_ephemeral_storage_client` (lines 91-131) — a
   factory function that logically belongs with the other factories in
   `factories.py`.
2. Move to `factories.py` and add a re-export in `main.py` for backward
   compatibility with test patches.

### Acceptance Criteria

1. `build_ephemeral_storage_client` in `factories.py`.
2. Re-export in `main.py` preserves test patch targets.
3. `main.py` reduced by ~40 LOC.
4. All tests pass without modification.
5. Full gate suite passes.

---

## T60.4 — Extract Domain Transaction Logic from factories.py

**Priority**: P3 — Architecture (boundary violation).

### Context & Constraints

1. `factories.py` `build_spend_budget_fn` (lines 158-311) contains a full
   pessimistic-locking database transaction inside the `_sync_wrapper` closure.
   This is domain-level budget accounting logic living in the bootstrapper layer.
2. Extract the transaction body into `modules/privacy/` as a
   `sync_spend_budget()` function.
3. `build_spend_budget_fn` retains the sync engine construction (URL demotion,
   NullPool) and returns a closure that calls `sync_spend_budget(engine, ...)`.
4. This moves domain logic to its correct module while keeping IoC wiring in
   the bootstrapper.
5. Import-linter: `modules/privacy/` is independent. The new function must not
   import from bootstrapper.

### Acceptance Criteria

1. Transaction logic in `modules/privacy/sync_budget.py` (or similar).
2. `factories.py` `build_spend_budget_fn` delegates to the new function.
3. Import-linter passes (no boundary violation).
4. All budget-related tests pass.
5. `factories.py` reduced by ~80 LOC.
6. Full gate suite passes.

---

## T60.5 — Move UnsealRequest to schemas

**Priority**: P5 — Code organization.

### Context & Constraints

1. `UnsealRequest` (a Pydantic model) is defined in `lifecycle.py` and
   re-exported from `main.py`. All other Pydantic schemas are in `schemas/`.
2. Move to `schemas/vault.py`.
3. Update imports in `lifecycle.py` and re-export in `main.py`.

### Acceptance Criteria

1. `UnsealRequest` in `schemas/vault.py`.
2. Import in `lifecycle.py` updated.
3. Re-export in `main.py` preserved.
4. All tests pass without modification.
5. Full gate suite passes.

---

## Task Execution Order

```
T60.5 (UnsealRequest move) ──────────> trivial, first
T60.3 (ephemeral storage move) ──────> trivial, parallel with T60.5
T60.1 (auth middleware extraction) ──> low risk
T60.2 (inline routes to routers) ───> medium risk, after T60.5
T60.4 (domain logic extraction) ────> medium risk, last (most architectural)
```

---

## Phase 60 Exit Criteria

1. `dependencies/auth.py` ≤350 LOC with middleware extracted.
2. `lifecycle.py` ≤100 LOC with inline routes moved.
3. `build_ephemeral_storage_client` in `factories.py`.
4. Budget transaction logic in `modules/privacy/`.
5. `UnsealRequest` in `schemas/vault.py`.
6. Zero behavioral changes — all existing tests pass.
7. Import-linter passes.
8. All quality gates pass.
9. Review agents pass for all tasks.


---

# Phase 61 — Test Quality Elevation

**Goal**: Raise mutation kill rate by eliminating shallow assertions, test
duplication, and infrastructure test sprawl.  The 3.9:1 test-to-code ratio
(98 K test LOC vs 25 K source LOC) is inflated by ~6,500 LOC of scaffolding
tests and 153 shallow `is not None` sole assertions across 48 files.

**Prerequisite**: Phase 60 merged.

**Source**: Staff-level production readiness audit (2026-03-27), scored
test efficacy 6/10. Findings: C7 (shallow assertions), test duplication in
DP suite, infrastructure test sprawl, missing real DP integration test.

---

## Critical Issues Addressed

| ID | Issue | Source | Impact |
|----|-------|--------|--------|
| C7 | 153 shallow `is not None` sole assertions across 48 test files | Audit 2026-03-27 | Low mutation kill rate; false confidence in coverage |
| — | 20+ copy-paste tests in `test_synthesizer_tasks_dp.py` | Audit 2026-03-27 | Maintenance burden; 80% code duplication |
| — | ~6,500 LOC infrastructure tests mixed with business logic tests | Audit 2026-03-27 | Inflated test ratio; unclear coverage signal |
| — | No integration test exercises real DP-SGD training pipeline | Audit 2026-03-27 | Mock-only DP validation; real behavior untested |

---

## T61.1 — Replace Shallow `is not None` Assertions with Semantic Assertions

**Priority**: P2 — Test quality.

### Context & Constraints

1. 153 occurrences of `assert X is not None` as the sole assertion across 48
   test files.  These pass for any non-None value, including wrong types,
   empty collections, or stale data.
2. Top offenders by count:
   - `test_benchmark_infrastructure.py` (16)
   - `test_shred_endpoint.py` (16)
   - `test_reaper_stale_jobs.py` (12)
   - `test_migration_007_encrypt_connection_metadata.py` (12)
   - `test_synthesizer_tasks_errors.py` (10)
   - `test_authorization_idor_jobs.py` (6)
   - `test_jobs_router.py` (6)
   - `test_dp_budget_fail_closed.py` (5)
3. Fix: Replace each `is not None` with a semantic assertion that validates
   the actual value — type, shape, content, or business invariant.
4. Do NOT delete assertions; replace them.  Each replacement must assert
   something that a mutation would break.

### Acceptance Criteria

1. Zero `assert X is not None` as the sole assertion in any test function.
2. Every replacement asserts a business-meaningful property (value, type +
   content, structure, or invariant).
3. No test functions deleted.
4. Full gate suite passes.

---

## T61.2 — Parameterize DP Task Tests

**Priority**: P3 — Test maintainability.

### Context & Constraints

1. `tests/unit/test_synthesizer_tasks_dp.py` contains 20+ tests following an
   identical pattern: create mock session → create mock engine → call
   `_run_synthesis_job_impl` → assert `mock.call_args`.
2. 80% code duplication.  Each test varies only in: DP enabled/disabled,
   epsilon value, wrapper presence.
3. Fix: Collapse into ~5 parameterized tests using `@pytest.mark.parametrize`.
   Each parametrized case must retain a descriptive ID string.
4. Estimated LOC reduction: ~500 lines.

### Acceptance Criteria

1. `test_synthesizer_tasks_dp.py` reduced by at least 40% LOC.
2. All original test scenarios preserved as parameterized cases with IDs.
3. No behavioral coverage lost (same assertions, same edge cases).
4. Full gate suite passes.

---

## T61.3 — Separate Infrastructure Tests into Dedicated Suite

**Priority**: P4 — Test organization.

### Context & Constraints

1. ~15 test files (~6,500 LOC) validate infrastructure, scaffolding, and
   documentation rather than production business logic:
   - `test_validate_pipeline_infrastructure.py` (706 LOC)
   - `test_readme_links.py` (317 LOC)
   - `test_notebook_infrastructure.py` (404 LOC)
   - `test_benchmark_infrastructure.py` (542 LOC)
   - `test_dead_dependency_cleanup.py` (402 LOC)
   - `test_mutation_testing_infrastructure.py` (323 LOC)
   - `test_ai_builder_notebook.py` (399 LOC)
   - `test_pagila_provisioning.py` (442 LOC)
   - `test_release_workflow.py` (561 LOC) [if in tests/unit]
   - `test_version_bump.py` (461 LOC) [if in tests/unit]
2. Fix: Add `@pytest.mark.infrastructure` marker to all infrastructure tests.
   Register marker in `pyproject.toml`.  Document how to exclude them:
   `pytest -m "not infrastructure"`.
3. Do NOT move files — markers are less disruptive than directory moves.
4. Update CI to report infrastructure and business-logic coverage separately.

### Acceptance Criteria

1. All infrastructure test files marked with `@pytest.mark.infrastructure`.
2. Marker registered in `pyproject.toml` (no unknown-marker warnings).
3. `pytest -m "not infrastructure"` excludes all infrastructure tests.
4. Business-logic test-to-code ratio reported separately in CI output.
5. Full gate suite passes (all tests still run; marker is for filtering only).

---

## T61.4 — Add Real DP-SGD Integration Test

**Priority**: P3 — Test depth.

### Context & Constraints

1. All DP training tests in `test_synthesizer_tasks_dp.py` mock the engine,
   session, and DP wrapper.  No test exercises real Opacus DP-SGD training.
2. `tests/integration/test_dp_training_integration.py` exists but uses
   limited mocking — verify its coverage and extend if needed.
3. Fix: Add or extend an integration test that:
   - Creates a real `DPCompatibleCTGAN` instance
   - Trains on a small fixture DataFrame (≤100 rows, 3 columns)
   - Verifies epsilon consumption is non-zero and within bounds
   - Verifies generated output has correct schema
4. Guard with `@pytest.mark.synthesizer` so it only runs when the
   synthesizer optional dependency group is installed.

### Acceptance Criteria

1. Integration test exercises real CTGAN + Opacus DP-SGD training.
2. Test verifies: epsilon > 0, output schema matches input, row count
   matches requested count.
3. Test completes in < 60 seconds on a 4-core machine.
4. Guarded by `@pytest.mark.synthesizer`.
5. Full gate suite passes.

---

## Task Execution Order

```
T61.1 (shallow assertions) ──────────> largest scope, do first
T61.2 (parameterize DP tests) ───────> independent
T61.3 (infrastructure markers) ──────> independent
T61.4 (real DP integration test) ────> independent, requires synthesizer deps
```

---

## Phase 61 Exit Criteria

1. Zero `is not None` sole assertions in any test function.
2. `test_synthesizer_tasks_dp.py` reduced by ≥40% LOC via parameterization.
3. Infrastructure tests marked and filterable.
4. Real DP-SGD integration test passing.
5. All quality gates pass.
6. Review agents pass for all tasks.


---

# Phase 62 — Production Safety Hardening

**Goal**: Fix the issues that WILL cause production incidents — unhandled
database errors, webhook worker starvation, dead supply chain surface,
fragile middleware ordering, and pre-release ORM risk assessment.

**Prerequisite**: Phase 61 merged.

**Source**: Staff-level production readiness audit (2026-03-27), scored
security & data integrity 8/10, hidden technical debt 7/10. Findings:
C1 (unhandled DB commits), C2 (webhook blocking), C3 (phantom dependency),
C4 (SQLModel pre-release), C5 (middleware ordering).

---

## Critical Issues Addressed

| ID | Issue | Source | Impact |
|----|-------|--------|--------|
| C1 | Database commits without exception handling — unhandled 500s | Audit 2026-03-27 | Operator sees raw 500; partial state on connection drop |
| C2 | Webhook retry `time.sleep()` blocks Huey worker for up to 42s | Audit 2026-03-27 | Worker starvation; job processing stalls |
| C3 | `requests` dependency declared but never imported | Audit 2026-03-27 | Unnecessary attack surface; CVE liability |
| C4 | SQLModel `0.0.x` pre-release as ORM foundation | Audit 2026-03-27 | Breaking changes possible at any minor bump |
| C5 | Middleware ordering enforced by comment, not code | Audit 2026-03-27 | Silent security bypass if refactored |

---

## T62.1 — Wrap Database Commits in Exception Handlers

**Priority**: P1 — Production reliability.

### Context & Constraints

1. Multiple router endpoints perform `session.commit()` without try-catch:
   - `bootstrapper/routers/connections.py:110-112` — create connection
   - `bootstrapper/routers/connections.py:194-195` — delete connection
   - `bootstrapper/routers/jobs.py:167-168` — create job
   - `bootstrapper/routers/jobs.py:259` — update job status
   - `bootstrapper/routers/jobs.py:309` — shred operation
   - `bootstrapper/routers/settings.py` — settings update
   - `bootstrapper/routers/webhooks.py:90,118` — webhook registration/deactivation
2. Constraint violations, connection drops, or transaction rollbacks produce
   unhandled 500 errors instead of RFC 7807 Problem Details responses.
3. Fix: Wrap each `session.commit()` in try-catch for
   `sqlalchemy.exc.IntegrityError` (→ 409 Conflict) and
   `sqlalchemy.exc.SQLAlchemyError` (→ 500 with RFC 7807 body).
4. Use the existing `operator_error_response()` helper for consistent
   error formatting.

### Acceptance Criteria

1. All `session.commit()` calls in router modules wrapped in try-catch.
2. `IntegrityError` → 409 Conflict with RFC 7807 body.
3. `SQLAlchemyError` → 500 Internal Server Error with RFC 7807 body.
4. No raw 500 responses from database errors.
5. Attack tests: simulate constraint violation, verify 409 response format.
6. Full gate suite passes.

---

## T62.2 — Circuit Breaker for Webhook Delivery

**Priority**: P1 — Worker availability.

### Context & Constraints

1. `modules/synthesizer/jobs/webhook_delivery.py:234-245`: The retry loop
   uses `time.sleep(_BACKOFF_DELAYS[attempt - 1])` inside the exception
   handler, blocking the Huey worker thread.
2. With 3 attempts, 10s timeout each, and 1s + 4s backoff, a single webhook
   delivery can block a worker for ~42 seconds.
3. Fix: Replace blocking `time.sleep()` with non-blocking backoff.
   Options:
   a. Use Huey's built-in retry mechanism (`@task(retries=3, retry_delay=...)`)
      instead of manual retry loop.
   b. Add a circuit breaker: after N consecutive failures to the same
      registration URL, mark it as DOWN and skip delivery attempts for a
      cooldown period.
4. Add a total time budget (e.g., 30s max per delivery attempt chain).
5. Add a Prometheus counter for circuit breaker trips.

### Acceptance Criteria

1. Webhook delivery does not block a Huey worker for more than 15 seconds
   total (including all retries).
2. Circuit breaker prevents repeated attempts to failing endpoints.
3. Prometheus counter `webhook_circuit_breaker_trips_total` tracks trips.
4. Existing webhook delivery tests pass (behavioral equivalence for
   successful deliveries).
5. Attack test: hanging webhook endpoint triggers circuit breaker.
6. Full gate suite passes.

---

## T62.3 — Remove Phantom `requests` Dependency

**Priority**: P2 — Supply chain hygiene.

### Context & Constraints

1. `pyproject.toml:66`: `requests = ">=2.33.0"` declared as a production
   dependency.
2. Zero imports of `requests` anywhere in `src/`.  Only `httpx` is used for
   HTTP operations.
3. Fix: Remove `requests` from `[tool.poetry.dependencies]`.  Run
   `poetry lock` to regenerate the lockfile.
4. Verify no transitive dependency pulls in `requests` unexpectedly.

### Acceptance Criteria

1. `requests` removed from `pyproject.toml` production dependencies.
2. `poetry lock` succeeds.
3. `grep -r "import requests" src/` returns zero results (already true).
4. Full gate suite passes.

---

## T62.4 — Programmatic Middleware Ordering Assertion

**Priority**: P2 — Security invariant enforcement.

### Context & Constraints

1. `bootstrapper/main.py:99-104`: Middleware ordering (LIFO) is documented
   in a comment.  The order is security-critical:
   - RequestBodyLimitMiddleware (outermost — rejects oversize/deep payloads)
   - CSPMiddleware
   - SealGateMiddleware (423 while vault sealed)
   - LicenseGateMiddleware (402 if unlicensed)
2. If middleware is reordered during refactoring, security gates can be
   bypassed silently.
3. Fix: Add a startup assertion in `create_app()` that inspects
   `app.middleware_stack` (or the internal `app.user_middleware` list) and
   verifies the expected type order.
4. If FastAPI internals make stack inspection fragile, add an integration
   test instead that sends requests verifying the correct ordering behavior
   (e.g., oversized body rejected before auth check).

### Acceptance Criteria

1. Middleware ordering verified programmatically at startup OR by integration
   test.
2. Adding middleware in wrong position causes a clear failure (assertion
   error or test failure).
3. Documentation in `main.py` updated to reference the enforcement mechanism.
4. Full gate suite passes.

---

## T62.5 — SQLModel Pre-Release Risk Assessment

**Priority**: P3 — Supply chain stability.

### Context & Constraints

1. `pyproject.toml:27`: `sqlmodel = ">=0.0.21,<0.1.0"` — the entire ORM
   layer depends on a 0.0.x pre-release library with no stability guarantee.
2. SQLModel 0.0.22 was the last release as of 2025.  The project wraps
   SQLAlchemy + Pydantic — both are stable.
3. This task is research, not implementation.  Deliverable: ADR documenting:
   a. Current SQLModel usage scope (which models, which features).
   b. Risk of 0.0.x breaking changes (changelog review).
   c. Migration path options: (i) stay and pin, (ii) migrate to plain
      SQLAlchemy + Pydantic, (iii) wait for 0.1.0.
   d. Recommendation with rationale.

### Acceptance Criteria

1. ADR created (e.g., `ADR-0059-sqlmodel-stability-assessment.md`).
2. ADR documents usage scope, risk assessment, and recommendation.
3. No code changes in this task.
4. Full gate suite passes (docs-only).

---

## Task Execution Order

```
T62.3 (remove requests) ────────────> trivial, do first
T62.1 (DB commit handlers) ─────────> high priority, parallel with T62.2
T62.2 (circuit breaker) ────────────> high priority, parallel with T62.1
T62.4 (middleware assertion) ────────> after T62.1 (both touch bootstrapper)
T62.5 (SQLModel assessment) ────────> independent research, any time
```

---

## Phase 62 Exit Criteria

1. All `session.commit()` calls wrapped with RFC 7807 error handling.
2. Webhook delivery does not block workers for more than 15 seconds.
3. `requests` removed from production dependencies.
4. Middleware ordering enforced programmatically.
5. SQLModel risk documented in ADR.
6. All quality gates pass.
7. Review agents pass for all tasks.


---

# Phase 63 — Configuration & Compliance Hardening

**Goal**: Consolidate configuration validation, fix compliance gaps, harden
rate limiting, and address remaining security debt from the production
readiness audit.

**Prerequisite**: Phase 62 merged.

**Source**: Staff-level production readiness audit (2026-03-27), scored
data compliance 8/10, hidden technical debt 7/10.  Findings: C6 (rate limit
fallback), C8 (Parquet not encrypted at rest), C9 (split validation),
C10 (env var naming), C12 (bcrypt error leakage).

---

## Critical Issues Addressed

| ID | Issue | Source | Impact |
|----|-------|--------|--------|
| C6 | Rate limiter in-memory fallback = N x limit in multi-pod | Audit 2026-03-27 | Rate limit bypass under Redis failure |
| C8 | Parquet artifacts HMAC-signed but not encrypted at rest | Audit 2026-03-27 | Filesystem compromise exposes synthetic data |
| C9 | Settings validation split across 2 files | Audit 2026-03-27 | Operator confusion; validation gaps |
| C10 | Environment variable naming inconsistency (mixed prefix) | Audit 2026-03-27 | Operator onboarding friction |
| C12 | `bcrypt` error string in 401 response body | Audit 2026-03-27 | Potential future information leakage |

---

## T63.1 — Consolidate Settings Validation

**Priority**: P2 — Maintainability.

### Context & Constraints

1. Validation currently lives in TWO files:
   - `shared/settings.py`: Pydantic field validators and `@model_validator`
   - `bootstrapper/config_validation.py` (481 LOC): Additional startup checks
2. An operator adding a new validated setting must know which file to edit.
   There is no single source of truth for "what gets validated when."
3. Fix: Move all production-required-field checks into Pydantic validators
   inside `settings.py`.  Reduce `config_validation.py` to a thin startup
   call that invokes `settings.validate()` and logs warnings (file existence
   checks, deprecation notices).
4. Preserve the existing behavior: non-production environments skip
   production-only validation.

### Acceptance Criteria

1. All field-level validation in `settings.py` Pydantic validators.
2. `config_validation.py` reduced to startup orchestration only (warnings,
   file existence, deprecation notices).
3. No validation logic duplicated between the two files.
4. All existing config validation tests pass.
5. Full gate suite passes.

---

## T63.2 — Unify Environment Variable Naming

**Priority**: P3 — Operator experience.

### Context & Constraints

1. Mixed prefixing in `settings.py`:
   - Unprefixed: `DATABASE_URL` (line 113), `AUDIT_KEY` (line 121),
     `MASKING_SALT` (line 162), `JWT_SECRET_KEY` (line 289)
   - Prefixed: `CONCLAVE_ENV`, `CONCLAVE_SSL_REQUIRED`,
     `CONCLAVE_TLS_CERT_PATH`
2. Fix: Add `CONCLAVE_` prefixed aliases for all unprefixed vars.  Accept
   both forms with a deprecation warning for the unprefixed form.
3. Update `.env.example` to show `CONCLAVE_` prefixed names as primary.
4. ADR documenting the naming convention and deprecation timeline.

### Acceptance Criteria

1. All env vars accept `CONCLAVE_` prefixed form.
2. Unprefixed form still works with deprecation WARNING logged at startup.
3. `.env.example` uses `CONCLAVE_` prefixed names.
4. ADR documenting the convention.
5. Full gate suite passes.

---

## T63.3 — Rate Limiter Fail-Closed on Redis Failure

**Priority**: P2 — Security.

### Context & Constraints

1. `bootstrapper/dependencies/rate_limit.py`: When Redis is unavailable,
   each pod falls back to an independent in-memory counter.  Effective rate
   limit becomes N_pods x configured_limit.
2. In a 5-pod deployment with 10 req/min auth limit, an attacker gets
   50 req/min during Redis outage.
3. Fix: Change fallback behavior to fail-closed (reject requests) when
   Redis is unavailable, with a configurable grace period.
4. Add setting `CONCLAVE_RATE_LIMIT_FAIL_OPEN` (default: `false`) for
   operators who prefer availability over rate-limit enforcement.
5. Log WARNING on every fallback activation.

### Acceptance Criteria

1. Default behavior: requests rejected (429) when Redis unavailable.
2. `CONCLAVE_RATE_LIMIT_FAIL_OPEN=true` restores current fallback behavior.
3. Grace period: first 5 seconds of Redis unavailability still served from
   in-memory (brief blip tolerance).
4. Prometheus counter `rate_limit_redis_fallback_total` tracks activations.
5. Attack test: Redis down → requests rejected after grace period.
6. Full gate suite passes.

---

## T63.4 — Harden bcrypt Error Message in 401 Response

**Priority**: P4 — Information leakage prevention.

### Context & Constraints

1. `bootstrapper/dependencies/auth.py:274-278`: `str(exc)` from bcrypt
   errors is included in the 401 response body.
2. Current bcrypt versions produce safe messages, but future versions may
   include internal state (hash format, truncation info).
3. Fix: Replace `str(exc)` with a static error message:
   `"Invalid credentials"`.  Log the actual exception at DEBUG level.

### Acceptance Criteria

1. 401 response body contains only `"Invalid credentials"` (no exception
   string).
2. Actual bcrypt exception logged at DEBUG with `exc_info=True`.
3. Existing auth failure tests updated to assert static message.
4. Full gate suite passes.

---

## T63.5 — At-Rest Encryption for Parquet Artifacts

**Priority**: P3 — Data confidentiality.

### Context & Constraints

1. `modules/synthesizer/storage/artifact.py`: Model artifacts are
   HMAC-signed (integrity) but not encrypted (confidentiality).
2. An attacker with filesystem access can read synthetic data in cleartext.
   While synthetic data is not PII, it may contain statistical signatures
   that reveal information about the source distribution.
3. Fix: Encrypt artifact payload with AES-256-GCM before signing.
   Key derived from `ARTIFACT_SIGNING_KEY` via HKDF (separate from HMAC
   key to maintain key separation).
4. Backward compatibility: reading must detect encrypted vs unencrypted
   format and handle both (migration period).
5. ADR documenting the encryption scheme.

### Acceptance Criteria

1. New artifacts encrypted with AES-256-GCM before HMAC signing.
2. Old unencrypted artifacts still loadable (backward compat).
3. Key derived via HKDF from signing key (key separation).
4. ADR documenting the encryption scheme.
5. Attack test: raw file read yields ciphertext, not cleartext.
6. Full gate suite passes.

---

## Task Execution Order

```
T63.4 (bcrypt hardening) ───────────> trivial, do first
T63.1 (consolidate validation) ─────> moderate scope
T63.2 (env var naming) ────────────> depends on T63.1 (settings.py changes)
T63.3 (rate limiter fail-closed) ──> independent
T63.5 (Parquet encryption) ────────> independent, largest scope
```

---

## Phase 63 Exit Criteria

1. Settings validation consolidated — single source of truth.
2. All env vars accept `CONCLAVE_` prefix with backward compat.
3. Rate limiter fails closed by default on Redis unavailability.
4. bcrypt errors never leak to API responses.
5. Parquet artifacts encrypted at rest.
6. All quality gates pass.
7. Review agents pass for all tasks.


---

# Phase 64 — Maintainability Polish

**Goal**: Reduce cognitive load for future maintainers by eliminating
re-export shims, decomposing oversized files, documenting canonical import
paths, and improving RETRO_LOG navigability.

**Prerequisite**: Phase 63 merged.

**Source**: Staff-level production readiness audit (2026-03-27), scored
maintainability 6/10.  Findings: C11 (dual import paths), RETRO_LOG
navigation, rate_limit.py multi-responsibility, import chain depth.

---

## Issues Addressed

| ID | Issue | Source | Impact |
|----|-------|--------|--------|
| C11 | Re-export shims create dual import paths | Audit 2026-03-27 | Developer confusion; bug fixes applied to wrong file |
| — | `rate_limit.py` (475 LOC) mixes rate limiting + Redis fallback + JWT extraction | Audit 2026-03-27 | Multiple responsibilities in single file |
| — | RETRO_LOG has no table-of-contents or domain index | Audit 2026-03-27 | Finding open advisories requires keyword search |
| — | No canonical import path documentation | Audit 2026-03-27 | 10-file import chains; dual paths confuse newcomers |

---

## T64.1 — Eliminate Re-Export Shims

**Priority**: P3 — Maintainability.

### Context & Constraints

1. `modules/synthesizer/storage/models.py` (53 LOC): Exists solely to
   re-export from `artifact.py` and `restricted_unpickler.py`.  Created
   in T58.4 for backward compatibility.
2. `modules/synthesizer/jobs/tasks.py`: Re-exports step classes from
   `job_orchestration.py` so "both old import paths work."
3. Dual import paths mean `grep "class ModelArtifact"` lands on the shim,
   not the real definition.  A developer may fix a bug in the wrong file.
4. Fix: Update all internal callers to use canonical import paths.  Keep
   shim files but add deprecation warnings (`warnings.warn()` at module
   scope).  Set removal deadline for Phase 70.
5. Scan all `from ... import` statements to ensure no internal code uses
   the deprecated paths.

### Acceptance Criteria

1. All internal code uses canonical import paths (not shims).
2. Shim files emit `DeprecationWarning` on import.
3. Deprecation deadline documented in shim module docstrings.
4. `grep -r "from.*storage.models import" src/` returns zero internal hits.
5. Full gate suite passes.

---

## T64.2 — RETRO_LOG Table of Contents

**Priority**: P4 — Documentation navigability.

### Context & Constraints

1. `docs/RETRO_LOG.md` is chronological (most recent first).  Finding
   "open advisories for privacy domain" requires keyword search.
2. Fix: Add a table-of-contents section at the top with:
   - Open advisories by severity (BLOCKER / FINDING / ADVISORY)
   - Open advisories by domain (privacy, synthesis, security, infra)
   - Link to each phase section
3. Keep the chronological body unchanged.

### Acceptance Criteria

1. RETRO_LOG has a TOC section at the top.
2. Open advisories listed by severity and domain.
3. Each phase section reachable by anchor link.
4. Full gate suite passes.

---

## T64.3 — Decompose `rate_limit.py`

**Priority**: P3 — Single responsibility.

### Context & Constraints

1. `bootstrapper/dependencies/rate_limit.py` (475 LOC) contains:
   - Rate limiting middleware dispatch logic
   - Redis-backed counter implementation
   - In-memory fallback counter
   - JWT token extraction for rate-limit identity
   - Tier configuration and endpoint matching
2. Fix: Split into:
   - `rate_limit_middleware.py` — ASGI middleware dispatch
   - `rate_limit_backend.py` — Redis + in-memory counter implementations
   - `rate_limit.py` — configuration, tier definitions, public API
3. Re-export from `rate_limit.py` for backward compatibility (but internal
   code updated to use new paths).

### Acceptance Criteria

1. No file exceeds 200 LOC after split.
2. Single responsibility per file.
3. All existing rate limit tests pass unchanged.
4. Full gate suite passes.

---

## T64.4 — Document Canonical Import Paths

**Priority**: P4 — Developer onboarding.

### Context & Constraints

1. A developer debugging a synthesis job must trace through 10+ files.
   There is no single reference listing "where does each concept live."
2. Fix: Add an "Import Map" section to `docs/DEVELOPER_GUIDE.md` that
   lists every public symbol with its canonical import path, organized
   by domain.
3. Example format:
   ```
   | Symbol | Canonical Import | Domain |
   |--------|-----------------|--------|
   | ModelArtifact | synth_engine.modules.synthesizer.storage.artifact | Synthesizer |
   | DPTrainingWrapper | synth_engine.modules.privacy.dp_engine | Privacy |
   ```

### Acceptance Criteria

1. Import map added to DEVELOPER_GUIDE.md.
2. Every public class, protocol, and factory function listed.
3. No deprecated shim paths listed as canonical.
4. Full gate suite passes.

---

## Task Execution Order

```
T64.2 (RETRO_LOG TOC) ──────────────> trivial, do first
T64.1 (eliminate shims) ────────────> moderate scope
T64.3 (decompose rate_limit.py) ───> moderate scope, parallel with T64.1
T64.4 (import map) ────────────────> after T64.1 (needs canonical paths finalized)
```

---

## Phase 64 Exit Criteria

1. All internal code uses canonical import paths.
2. Re-export shims emit deprecation warnings.
3. `rate_limit.py` decomposed into ≤200 LOC files.
4. RETRO_LOG has navigable TOC with severity/domain index.
5. Import map in DEVELOPER_GUIDE.md covers all public symbols.
6. All quality gates pass.
7. Review agents pass for all tasks.


---

# Phase 66 — Expired Security Advisory Resolution & PII Fix

**Goal**: Resolve 3 expired security advisories (Rule 26 compliance), fix CRITICAL
PII logging vulnerability, fix correctness bug in privacy accountant, and document
single-operator privacy ledger assumption.

**Prerequisite**: Phase 65 merged.

**Source**: Production readiness audit (2026-03-28) + Rule 26 TTL enforcement.
ADV-P62-01 (TTL P64), ADV-P62-02 (TTL P64), ADV-P63-05 (TTL P65) — all expired.

---

## T66.1 — Fix PII Logging in Auth Router (CRITICAL)

**Priority**: P0 — Security / GDPR Article 32.

`bootstrapper/routers/auth.py:142` logs operator username at INFO level:
```python
_logger.info("Issued JWT token for operator=%r", body.username)
```
This propagates to SIEM, log aggregators, and backups.

### Acceptance Criteria

1. `auth.py` no longer logs `body.username` at any level above DEBUG.
2. Token issuance logged with opaque operator identifier only (e.g., truncated hash).
3. Attack test: assert no PII (username) appears in INFO/WARNING/ERROR log output
   during token issuance.
4. All quality gates pass.

---

## T66.2 — Disable OpenAPI Docs in Production Mode (ADV-P62-01)

**Priority**: P0 — Expired SECURITY advisory (raised P62, TTL P64).

`bootstrapper/main.py:244-245` always enables `/docs`, `/redoc`, and the
`/openapi.json` schema endpoint. In production mode, these expose API surface
area to reconnaissance.

### Acceptance Criteria

1. `/docs`, `/redoc`, and `/openapi.json` return 404 when `CONCLAVE_ENV=production`.
2. Endpoints remain available when `CONCLAVE_ENV=development` (default).
3. Setting controlled via `ConclaveSettings` field (no hardcoded check).
4. Attack test: production-mode request to `/docs` returns 404.
5. Feature test: dev-mode request to `/docs` returns 200.
6. All quality gates pass.

---

## T66.3 — Trusted Proxy Validation for X-Forwarded-For (ADV-P62-02)

**Priority**: P0 — Expired SECURITY advisory (raised P62, TTL P64).

`bootstrapper/dependencies/rate_limit.py:105-111` blindly trusts the first
entry in `X-Forwarded-For`. An attacker can spoof this header to bypass
per-IP rate limiting.

### Acceptance Criteria

1. New `ConclaveSettings` field: `trusted_proxy_count` (int, default 0).
2. When `trusted_proxy_count == 0`, `X-Forwarded-For` is ignored entirely;
   client IP falls back to `request.client.host` (zero-trust default).
3. When `trusted_proxy_count == N`, extract the Nth-from-right entry in XFF
   (standard proxy-peeling convention).
4. Attack test: spoofed XFF header with `trusted_proxy_count=0` does NOT
   change the extracted IP.
5. Feature test: correctly configured proxy count extracts the real client IP.
6. All quality gates pass.

---

## T66.4 — Resolve Pygments CVE-2026-4539 (ADV-P63-05)

**Priority**: P0 — Expired SECURITY advisory (raised P63, TTL P65).

Pygments is a transitive dependency (via click, rich, ipython). No upstream
fix available. Must verify production exposure and document mitigation.

### Acceptance Criteria

1. Verify whether pygments is included in the production Docker image
   (check `Dockerfile` dependency installation).
2. If NOT in production: document in ADR that pygments is dev-only and not
   deployed; close advisory as mitigated.
3. If IN production: either pin a non-vulnerable version range, or add a
   compensating control (input sanitization on any pygments entry point),
   and document in ADR.
4. All quality gates pass.

---

## T66.5 — Fix Accountant NoResultFound Propagation (Correctness)

**Priority**: P1 — Correctness bug.

`modules/privacy/accountant.py:174` calls `result.scalar_one()` which raises
`sqlalchemy.exc.NoResultFound` if `ledger_id` does not exist. This raw
SQLAlchemy exception propagates instead of a domain-specific error.

### Acceptance Criteria

1. `scalar_one()` failure wrapped in a new `LedgerNotFoundError` domain
   exception (in `shared/exceptions.py`).
2. Error message includes `ledger_id` for operator debugging.
3. Attack test: `spend_budget()` with nonexistent ledger_id raises
   `LedgerNotFoundError`, not `NoResultFound`.
4. All quality gates pass.

---

## T66.6 — Document Single-Operator Privacy Ledger Assumption (ADV-P63-03)

**Priority**: P2 — ADVISORY.

The privacy ledger has no `owner_id` filter — it assumes a single-operator
model. This is undocumented.

### Acceptance Criteria

1. ADR-0050 amended (or new ADR created) documenting the single-operator
   assumption and its implications for future multi-tenant support.
2. Code comment added at `accountant.py` ledger query explaining the assumption.
3. Advisory ADV-P63-03 closed in RETRO_LOG.
4. All quality gates pass.

---

## Task Execution Order

```
T66.1 (PII fix — CRITICAL)
T66.2 (OpenAPI docs — expired SECURITY)
T66.3 (XFF validation — expired SECURITY)
T66.4 (pygments CVE — expired SECURITY)
T66.5 (accountant fix — correctness)
T66.6 (ledger docs — advisory)
```

---

## Phase 66 Exit Criteria

1. All 3 expired security advisories resolved and closed in RETRO_LOG.
2. PII logging vulnerability eliminated.
3. Accountant correctness bug fixed.
4. Single-operator assumption documented.
5. All quality gates pass.
6. Review agents pass.


---

# Phase 68 — Critical Safety Hardening

**Goal**: Fix the two P0 production-failure risks (thread-unsafe masking, admin
IDOR), enforce audit-before-destructive-ops, harden health checks and auth
error handling, and close the remaining unbounded-input advisory.

**Prerequisite**: Phase 67 merged.

**Source**: Senior Architect & Security Audit (2026-03-28), categories 1/4/6.
Findings C1, C2, C3, C7, C9; open advisories ADV-P67-01, ADV-P67-02.

---

## T68.1 — Thread-Local Faker in Masking Module (CRITICAL — C1)

**Priority**: P0 — Data integrity.

`modules/masking/deterministic.py:58` uses a module-level `_FAKER` singleton.
`seed_instance()` + `mask_fn(_FAKER)` is a race condition under concurrent
threads — one thread's seed is overwritten by another between the seed call
and the Faker call, silently breaking determinism and producing corrupt
masked output.

The risk is documented in lines 38-57 with a mitigation note ("do not call
from multiple threads") but **zero code guards** enforce this.

### Acceptance Criteria

1. `_FAKER` replaced with `threading.local()` storage — each thread gets its
   own Faker instance, constructed lazily on first use.
2. `mask_value()` is safe to call from concurrent threads with no external
   synchronization.
3. Determinism preserved: same `(value, salt)` pair produces the same masked
   output regardless of thread.
4. Attack test: spawn 10 threads, each masking the same `(value, salt)` pair
   1000 times — assert all results identical.
5. Performance: no more than 20% regression vs current single-thread baseline
   (Faker construction is ~7x slower, but happens once per thread).
6. All quality gates pass.

---

## T68.2 — RBAC Guard on Admin Endpoints (CRITICAL — C2)

**Priority**: P0 — Privilege escalation.

`bootstrapper/routers/admin.py:136-145` (`set_legal_hold`) does not check
`job.owner_id == current_operator`. Any authenticated operator can set or
clear legal hold on any job by ID. The docstring (lines 14-21) acknowledges
this as "intentional for single-operator" but provides no code guard for
multi-operator deployments.

### Acceptance Criteria

1. `set_legal_hold()` checks `job.owner_id == current_operator` and returns
   404 if mismatch (same pattern as `jobs.py`, `connections.py`).
2. OR: a `@require_role("admin")` decorator is introduced that future
   multi-operator RBAC can extend, and admin endpoints are decorated with it.
   In single-operator mode the decorator is a no-op.
3. Attack test: operator A creates a job, operator B (different `sub` claim)
   attempts `PUT /admin/jobs/{id}/legal-hold` — assert 404.
4. Existing single-operator behavior unchanged (operator can still manage
   their own jobs).
5. All quality gates pass.

---

## T68.3 — Mandatory Audit Before Destructive Operations (C3)

**Priority**: P1 — Compliance gap.

`bootstrapper/routers/security.py:149-150` proceeds with CRYPTO_SHRED even
when audit write fails (catches `ValueError`, logs warning, continues).
Line 246-247 does the same for key rotation. `admin.py:192-195` swallows
audit failure on legal hold changes.

In contrast, `privacy.py:321` correctly returns 500 on audit failure.

### Acceptance Criteria

1. `security.py` CRYPTO_SHRED endpoint returns 500 if WORM audit emission
   fails — shred does NOT proceed without audit evidence.
2. `security.py` key rotation endpoint returns 500 if WORM audit emission
   fails — Huey task is NOT enqueued without audit evidence.
3. `admin.py` legal hold endpoint returns 500 if audit emission fails —
   database commit is rolled back.
4. Pattern matches `privacy.py:321` (the correct reference implementation).
5. Attack test: mock audit emitter to raise, assert endpoint returns 500 and
   no mutation occurred (no shred, no rotation, no legal hold change).
6. All quality gates pass.

---

## T68.4 — Health Check Strict Mode for Production (C7)

**Priority**: P2 — Availability.

`bootstrapper/routers/health.py:74-76` skips the database check if
`DATABASE_URL` is unset. Line 118 skips MinIO check on `RuntimeError`.
The `/ready` endpoint returns 200 even when expected services are
unconfigured — load balancer routes traffic to a broken instance.

### Acceptance Criteria

1. New setting `conclave_health_strict: bool` (default `True` in production,
   `False` in development).
2. In strict mode, `/ready` returns 503 if ANY configured service (database,
   Redis, MinIO) is unreachable or unconfigured-but-expected.
3. "Expected" is determined by whether the corresponding URL/config is set —
   if `DATABASE_URL` is set, database MUST be reachable.
4. In permissive mode (development), current skip behavior preserved.
5. Feature test: strict mode with unreachable DB returns 503.
6. Feature test: permissive mode with unreachable DB returns 200 with
   `"database": "skipped"`.
7. All quality gates pass.

---

## T68.5 — Narrow Bcrypt Exception Handling (C9)

**Priority**: P2 — Auth reliability.

`bootstrapper/dependencies/auth.py:157` catches bare `Exception` on
`bcrypt.checkpw()` and returns `False`. This means `SystemExit`,
`KeyboardInterrupt`, `MemoryError`, and `ImportError` are all treated
as "wrong password" instead of propagating.

### Acceptance Criteria

1. Exception catch narrowed to `(ValueError, TypeError, AttributeError)` —
   the documented bcrypt failure modes.
2. All other exceptions propagate (system errors should crash, not silently
   return False).
3. Attack test: mock `bcrypt.checkpw` to raise `RuntimeError` — assert it
   propagates (not caught as auth failure).
4. Existing behavior preserved for malformed hash input (`ValueError`).
5. All quality gates pass.

---

## T68.6 — Close Unbounded Input Field Advisory (ADV-P67-01)

**Priority**: P2 — Input validation.

Open advisory ADV-P67-01 identifies unbounded `max_length` on request body
fields: `parquet_path` (jobs schema), `callback_url` (webhooks schema),
`signing_key` (webhooks schema), `justification` (privacy schema),
`table_name` (jobs schema, has pattern but no length).

### Acceptance Criteria

1. `table_name`: `max_length=255` added (PostgreSQL identifier limit).
2. `parquet_path`: `max_length=1024` added (filesystem path limit).
3. `callback_url`: `max_length=2048` added (HTTP URL practical limit).
4. `signing_key`: `max_length=512` added (HMAC key reasonable bound).
5. `justification`: `max_length=2000` added (audit log usability).
6. Attack tests: oversized input for each field returns 422.
7. ADV-P67-01 closed in RETRO_LOG.
8. All quality gates pass.

---

## T68.7 — Enforce rate_limit_fail_open Block in Production (ADV-P67-02)

**Priority**: P2 — Security configuration.

`config_validation.py:184-192` only emits a WARNING when
`conclave_rate_limit_fail_open=True` in production mode. This allows
operators to accidentally disable distributed rate limiting.

### Acceptance Criteria

1. `validate_config()` raises `ConfigurationError` when
   `conclave_rate_limit_fail_open=True` and `CONCLAVE_ENV=production`.
2. Error message includes remediation steps.
3. Development mode behavior unchanged (fail-open allowed).
4. Attack test: production mode with fail-open=True — assert startup fails.
5. ADV-P67-02 closed in RETRO_LOG.
6. All quality gates pass.

---

## Task Execution Order

```
T68.1 (thread-local Faker) ────────> P0, independent
T68.2 (admin RBAC) ────────────────> P0, independent
T68.3 (audit-before-destructive) ──> P1, independent
T68.5 (bcrypt narrowing) ─────────> P2, independent
T68.4 (health strict mode) ───────> P2, touches settings.py
T68.6 (unbounded fields) ─────────> P2, touches schemas
T68.7 (fail-open block) ──────────> P2, touches config_validation.py
```

---

## Phase 68 Exit Criteria

1. Masking is thread-safe with verified determinism under concurrency.
2. Admin endpoints enforce ownership or role check.
3. No destructive operation proceeds without successful audit write.
4. Health check fails closed in production for configured services.
5. Bcrypt errors distinguished from system errors.
6. All request body fields have explicit max_length bounds.
7. Production startup rejects rate_limit_fail_open=True.
8. ADV-P67-01 and ADV-P67-02 closed.
9. All quality gates pass.
10. Review agents pass for all tasks.


---

# Phase 69 — Security Depth & Test Coverage

**Goal**: Close the SSRF TOCTOU gap, prevent PII leakage from the profiler,
fix two SECURITY-tagged advisories (compliance erasure IDOR, parquet_path
sandbox), add concurrent/timeout test coverage, and surface webhook delivery
errors to operators.

**Prerequisite**: Phase 68 merged.

**Source**: Senior Architect & Security Audit (2026-03-28), categories 2/6/7.
Findings C4, C5, C8, C10. Security advisories ADV-P68-01 (TTL P70),
ADV-P68-02 (TTL P70).

---

## T69.1 — DNS Pinning for Webhook SSRF Protection (C4)

**Priority**: P1 — SSRF bypass via DNS rebinding.

`shared/ssrf.py:139` resolves DNS once at webhook registration time via
`socket.getaddrinfo()`. Between registration and delivery, an attacker
controlling DNS can rebind the hostname to a private IP (e.g., 169.254.169.254
for AWS metadata). The delivery HTTP request then hits the internal network.

### Context & Constraints

1. Current SSRF protection is strong at registration: scheme validation,
   hostname presence, IPv4-mapped IPv6 unwrapping, RFC 1918 / loopback /
   link-local blocking.
2. The gap is TOCTOU: validation happens at registration, HTTP request
   happens at delivery (potentially hours later).
3. Fix: pin resolved IP(s) at registration time in the webhook record.
   At delivery time, re-resolve and compare against pinned IPs. If mismatch,
   re-validate the new IP against BLOCKED_NETWORKS before proceeding.
4. Alternative: force delivery to connect to the pinned IP directly
   (httpx `transport` with explicit IP), bypassing DNS entirely at
   delivery time.

### Acceptance Criteria

1. `WebhookRegistration` model stores resolved IP(s) at registration time.
2. At delivery time, DNS is re-resolved. If any resolved IP is in
   BLOCKED_NETWORKS, delivery is rejected.
3. If DNS re-resolution fails, delivery is retried per existing backoff
   policy (not silently skipped).
4. Attack test: register webhook with hostname resolving to public IP,
   then mock DNS to resolve to 169.254.169.254 at delivery time — assert
   delivery rejected with SSRF violation.
5. Attack test: register webhook with hostname resolving to public IP,
   DNS re-resolution returns same public IP — assert delivery proceeds.
6. Prometheus counter for SSRF rejections at delivery time (separate from
   registration rejections).
7. All quality gates pass.

---

## T69.2 — Profiler PII-Aware Mode (C5)

**Priority**: P1 — Data leakage / GDPR.

`modules/profiler/profiler.py:116-141` computes `value_counts()` on all
columns, including PII columns (e.g., `email`, `ssn`, `phone`). The
resulting statistical profile contains raw PII values in the categorical
distribution. If the profile is exported or logged, PII is leaked.

### Context & Constraints

1. The profiler is used to generate statistical metadata for the synthesizer.
   It does NOT need raw values — it needs distribution shapes.
2. Fix: introduce a PII column classification parameter. For PII-tagged
   columns, suppress raw value distributions and report only aggregate
   statistics (cardinality, null rate, min/max length).
3. Column classification source: masking registry knows which columns are
   masked. Any column with a masking rule is PII.
4. If no classification is provided, default to safe mode (suppress all
   categorical value distributions with cardinality > k-anonymity threshold).

### Acceptance Criteria

1. `Profiler.profile()` accepts optional `pii_columns: set[str]` parameter.
2. For PII columns: value_counts omitted from output. Only cardinality,
   null_rate, min_length, max_length reported.
3. When `pii_columns` is None: columns with cardinality < 50 are reported
   normally; columns with cardinality >= 50 are treated as PII (safe default).
4. Attack test: profile a DataFrame with an `email` column — assert no
   email addresses appear in the profile output when column is tagged PII.
5. Feature test: non-PII column (e.g., `status` with 3 values) still
   reports full value_counts.
6. All quality gates pass.

---

## T69.3 — Concurrent Load Tests (C8)

**Priority**: P2 — Test coverage gap.

The test suite has zero tests for concurrent access patterns: no thread
contention tests, no connection pool exhaustion tests, no tests verifying
behavior under 50+ simultaneous requests.

### Context & Constraints

1. Use `concurrent.futures.ThreadPoolExecutor` for thread contention tests.
2. Use `pytest-asyncio` with `asyncio.gather` for async endpoint tests.
3. Focus on the three highest-risk concurrency points:
   - Masking determinism under thread contention (after T68.1 fix)
   - Vault unseal/seal race conditions (existing lock correctness)
   - Rate limiter accuracy under burst traffic
4. Do NOT use locust or external load testing frameworks — keep tests
   in-process for CI compatibility.

### Acceptance Criteria

1. Test: 10 threads masking the same `(value, salt)` pair 100 times each —
   all 1000 results identical (validates T68.1).
2. Test: 10 threads attempting concurrent vault unseal — exactly one
   succeeds, others get `VaultAlreadyUnsealedError`.
3. Test: 50 concurrent requests to a rate-limited endpoint — assert
   exactly `rate_limit` requests succeed, remainder get 429.
4. Test: database connection pool exhaustion — `_POOL_SIZE + _MAX_OVERFLOW`
   concurrent queries succeed, additional queries wait or fail gracefully.
5. All tests run in < 30 seconds (no external infrastructure required).
6. All quality gates pass.

---

## T69.4 — Timeout Simulation Tests (C8)

**Priority**: P2 — Test coverage gap.

No tests verify behavior when infrastructure operations take longer than
expected: slow DB queries, Redis timeouts, webhook delivery hangs.

### Acceptance Criteria

1. Test: mock database query to sleep beyond `_WORKER_POOL_TIMEOUT` (30s) —
   assert `OperationalError` raised, not hung indefinitely. Use short
   timeout override (1s) for test speed.
2. Test: mock Redis to sleep beyond rate limiter grace period — assert
   fail-closed behavior (429 returned, not hung).
3. Test: mock httpx to sleep beyond `webhook_delivery_timeout_seconds` —
   assert timeout exception caught, retry scheduled, delivery not hung.
4. Test: mock vault PBKDF2 to simulate slow derivation — assert unseal
   completes (no premature timeout).
5. All tests use `unittest.mock.patch` with short timeouts (< 2s each).
6. All quality gates pass.

---

## T69.5 — Webhook Delivery Error Surfacing (C10)

**Priority**: P2 — Observability gap.

`bootstrapper/wiring.py:182-197` catches ALL exceptions from webhook
delivery (including bare `Exception`) and logs but never re-raises or
returns error status. Operators have no feedback that webhook delivery
is failing beyond parsing logs or monitoring a Prometheus counter.

### Context & Constraints

1. The broad catch is intentional — webhook failure must never crash the
   job lifecycle. This contract is correct.
2. The gap is surfacing: operators need a queryable mechanism to see
   delivery failures beyond log scraping.
3. Fix: persist delivery status in the `WebhookDelivery` table (already
   exists). Add a `GET /webhooks/{id}/deliveries` endpoint returning
   recent delivery attempts with status, HTTP code, and error message.

### Acceptance Criteria

1. `WebhookDelivery` records include `error_message` field (max 500 chars,
   sanitized via `safe_error_msg()`).
2. `GET /webhooks/{id}/deliveries` returns paginated list of delivery
   attempts for a registration, scoped to current operator (IDOR check).
3. Failed deliveries include sanitized error message and HTTP status code
   (or null if network failure).
4. Feature test: trigger webhook delivery failure, query deliveries endpoint,
   assert failure record present with error details.
5. Attack test: operator A queries deliveries for operator B's webhook —
   assert 404.
6. All quality gates pass.

---

## T69.6 — Fix Compliance Erasure IDOR (ADV-P68-01)

**Priority**: P0 — SECURITY advisory (TTL P70).

`bootstrapper/routers/compliance.py:166-218` (`DELETE /compliance/erasure`)
accepts an arbitrary `subject_id` and cascades deletion of all matching
`Connection` and `SynthesisJob` records. There is no check that
`body.subject_id == current_operator`. Any authenticated operator can erase
all of another operator's data.

### Acceptance Criteria

1. `DELETE /compliance/erasure` enforces `body.subject_id == current_operator`.
   If mismatch, return 403 with RFC 7807 detail explaining self-erasure only.
2. Attack test: operator A calls erasure with `subject_id` set to operator B's
   ID — assert 403.
3. Feature test: operator A calls erasure with their own ID — assert 200 and
   data deleted.
4. Audit event emitted for attempted cross-operator erasure (intrusion
   detection).
5. ADV-P68-01 closed in RETRO_LOG.
6. All quality gates pass.

---

## T69.7 — Sandbox parquet_path to Allowed Directory (ADV-P68-02)

**Priority**: P0 — SECURITY advisory (TTL P70).

`bootstrapper/schemas/jobs.py:96-99` validates `parquet_path` for `.parquet`
suffix and normalization via `Path.resolve()`, but does not restrict the
resolved path to a configured base directory. The synthesizer opens and reads
the file at this path directly. An attacker can point the path at any
world-readable `.parquet` file on the filesystem.

### Acceptance Criteria

1. New setting `conclave_data_dir: str` in `ConclaveSettings` (default
   `"data/"`, resolved to absolute path at construction).
2. `validate_parquet_path` enforces that the resolved path starts with
   `conclave_data_dir` (using `Path.is_relative_to()`).
3. Attack test: `parquet_path` pointing outside `conclave_data_dir` (e.g.,
   `/etc/passwd.parquet` or `../../secrets.parquet`) — assert 422.
4. Attack test: symlink from inside `conclave_data_dir` pointing outside —
   assert 422 after resolve.
5. Feature test: valid path inside `conclave_data_dir` — assert accepted.
6. `CONCLAVE_DATA_DIR` documented in `.env.example`.
7. ADV-P68-02 closed in RETRO_LOG.
8. All quality gates pass.

---

## Task Execution Order

```
T69.6 (compliance erasure IDOR) ──> P0, SECURITY, independent
T69.7 (parquet_path sandbox) ─────> P0, SECURITY, independent
T69.1 (DNS pinning) ──────────────> P1, independent, touches ssrf.py + webhook models
T69.2 (profiler PII mode) ────────> P1, independent, touches profiler/
T69.3 (concurrent load tests) ────> P2, depends on T68.1 (thread-local Faker)
T69.4 (timeout simulation tests) ─> P2, independent
T69.5 (webhook error surfacing) ──> P2, independent, touches wiring.py + webhooks router
```

---

## Phase 69 Exit Criteria

1. Compliance erasure restricted to self-erasure only (ADV-P68-01 closed).
2. parquet_path sandboxed to configured data directory (ADV-P68-02 closed).
3. Webhook SSRF protection validated at both registration and delivery time.
4. Profiler output contains no raw PII values for tagged columns.
5. Concurrent access tests cover masking, vault, rate limiter, DB pool.
6. Timeout behavior tested for DB, Redis, webhook delivery.
7. Webhook delivery failures queryable via REST endpoint.
8. All quality gates pass.
9. Review agents pass for all tasks.


---

# Phase 70 — Structural Debt Reduction

**Goal**: Address the structural technical debt that increases cognitive load
and long-term maintenance cost: composite key support for correctness,
legacy signature format removal, memory-safe vault operations, settings
decomposition, and an operational runbook.

**Prerequisite**: Phase 69 merged.

**Source**: Senior Architect & Security Audit (2026-03-28), categories 4/5/3.
Findings C6, C11, C12. Also completes P64-T64.1 shim removal deadline.

---

## T70.1 — Composite PK/FK Support in Subsetting (C6)

**Priority**: P1 — Data correctness.

`modules/subsetting/traversal.py:228` extracts only the first PK column
for FK joins. `modules/mapping/reflection.py:158-161` deduplicates FK
edges using only the first constrained column. Tables with composite
primary or foreign keys will produce wrong join conditions and incorrect
row selection.

### Context & Constraints

1. Current single-column assumption is undocumented as a limitation.
2. Composite keys are common in junction/association tables (many-to-many).
3. Fix: represent PK/FK as tuples of column names. Join conditions become
   AND-ed equality predicates.
4. SchemaTopology must carry multi-column edge definitions.
5. EgressWriter must handle multi-column primary keys for rollback
   (TRUNCATE CASCADE already handles this correctly).
6. Scope: support 2-4 column composites. Keys wider than 4 columns are
   rejected with a clear error at reflection time.

### Acceptance Criteria

1. `SchemaTopology` edges represent FK relationships as
   `tuple[str, ...]` for both constrained and referred columns.
2. `traversal.py` builds WHERE clauses with AND-ed equality for composite
   FKs (e.g., `WHERE a = :v0 AND b = :v1`).
3. `reflection.py` deduplicates edges using full column tuples, not just
   the first column.
4. Feature test: schema with a junction table (composite FK) — assert
   subsetting correctly follows both columns.
5. Feature test: schema with composite PK — assert all PK columns used
   in join conditions.
6. Edge case test: FK with > 4 columns — assert `ValueError` raised at
   reflection time with clear message.
7. Integration test: real PostgreSQL schema with composite FK — assert
   round-trip subset correctness.
8. All quality gates pass.

---

## T70.2 — Remove Legacy Audit Signature Formats v1/v2 (C11)

**Priority**: P2 — Audit chain integrity.

`shared/security/audit_signatures.py:37-66` (v1) and lines 69-118 (v2)
use pipe-delimited HMAC input. ADV-P53-01 documents that pipe delimiter
injection can forge valid signatures by manipulating field boundaries.
v3 (lines 121-184) uses length-prefixed format that eliminates this.

### Context & Constraints

1. v1 and v2 are kept for backward compatibility — existing audit chains
   may contain v1/v2 entries that must still be verifiable.
2. Fix: remove v1/v2 from the **signing** path (new entries always v3).
   Keep v1/v2 in the **verification** path behind a deprecation flag.
3. Add a migration tool that re-signs v1/v2 entries as v3 (requires
   audit_key access).
4. After migration, the verification path can drop v1/v2 entirely.

### Acceptance Criteria

1. `sign_audit_entry()` always uses v3 format. v1/v2 sign functions are
   removed from the public API (kept as private for migration tool only).
2. `verify_audit_entry()` still accepts v1/v2/v3 but logs a WARNING for
   v1/v2 entries encountered.
3. Migration CLI command: `conclave audit migrate-signatures` re-signs
   all v1/v2 entries in the local audit log as v3.
4. Feature test: new audit entries are always v3.
5. Feature test: v1/v2 entries still verify (backward compat).
6. Feature test: migration tool converts v1/v2 entries to v3.
7. Attack test: pipe-injection attempt on v3 format — assert signature
   mismatch.
8. All quality gates pass.

---

## T70.3 — Memory-Safe Vault Operations (C12)

**Priority**: P3 — Memory forensics defense.

`shared/security/vault.py:197-199` zeroes the KEK via a memoryview
byte-by-byte loop. This is not guaranteed by the Python runtime — JIT
or compiler optimizations could eliminate the loop if the buffer is not
read afterward. The unseal passphrase (`vault.py:112`) is a Python
string (immutable) that lingers in memory until garbage collected.

### Context & Constraints

1. Python's memory model does not guarantee that overwritten bytes are
   not optimized away. `ctypes.memset` is the standard workaround.
2. Passphrase as `str` cannot be zeroed. Accept `bytes` or `bytearray`
   instead, zero after derivation.
3. For defense-in-depth, call `gc.collect()` after zeroing to encourage
   prompt deallocation of the old string object.
4. This is a P3 because exploitation requires physical memory access
   (memory dump, cold boot attack) — relevant for air-gapped high-security
   deployments but not typical threat models.

### Acceptance Criteria

1. KEK zeroing uses `ctypes.memset(ctypes.addressof(...), 0, len(...))`
   instead of memoryview loop.
2. `VaultState.unseal()` accepts `bytes | bytearray` passphrase (not `str`).
   Callers updated to encode before calling.
3. Passphrase `bytearray` is zeroed after PBKDF2 derivation completes.
4. `gc.collect()` called after zeroing to encourage deallocation.
5. Test: after `seal()`, raw KEK bytes are zero (read via ctypes).
6. Test: after `unseal()`, passphrase buffer is zeroed.
7. All quality gates pass.

---

## T70.4 — Settings Decomposition into Sub-Models

**Priority**: P3 — Maintainability.

`shared/settings.py` is 838 lines with 50+ fields in a single flat model.
Finding a specific setting requires scrolling through unrelated domains.
Adding a new setting risks merge conflicts with any other settings change.

### Context & Constraints

1. Decompose into nested Pydantic sub-models: `TLSSettings`,
   `RateLimitSettings`, `WebhookSettings`, `RetentionSettings`,
   `ParquetSettings`, `AnchorSettings`.
2. `ConclaveSettings` becomes a composition of sub-models.
3. Environment variable names unchanged — use Pydantic `env_prefix` or
   `alias` to maintain backward compatibility.
4. All existing settings access patterns (`get_settings().field`) must
   continue to work, possibly via `__getattr__` delegation or flat
   re-export.

### Acceptance Criteria

1. Settings grouped into >=5 sub-models by domain.
2. No file exceeds 200 LOC after decomposition.
3. All existing environment variable names work unchanged.
4. All existing `get_settings().field` access patterns work unchanged.
5. `config_validation.py` updated to reference sub-models.
6. All quality gates pass.

---

## T70.5 — Operational Runbook

**Priority**: P3 — Documentation gap.

No operational documentation exists for: deployment procedures, rollback
steps, incident response, key rotation, vault recovery, or startup
failure troubleshooting.

### Acceptance Criteria

1. `docs/OPERATIONS_RUNBOOK.md` created with sections:
   - **Deployment**: Docker Compose and Kubernetes deployment steps.
   - **Startup Failures**: troubleshooting table for each
     `ConfigurationError` with required environment variables and
     remediation steps.
   - **Vault Operations**: unseal, seal, key rotation, recovery from
     lost passphrase.
   - **Incident Response**: steps for PII exposure, audit chain break,
     compromised signing key, database corruption.
   - **Rollback**: how to revert a bad deployment (Docker Compose and K8s).
   - **Key Rotation**: step-by-step for audit key, signing key, JWT
     secret, masking salt rotation with zero-downtime.
2. Each section includes pre-conditions, steps, verification, and
   rollback-of-the-rollback.
3. All file paths and commands verified against current codebase.
4. All quality gates pass.

---

## T70.6 — Remove Re-Export Shims (P64-T64.1 Deadline)

**Priority**: P3 — Maintainability (deferred from Phase 64).

`modules/synthesizer/storage/models.py` and `modules/synthesizer/jobs/tasks.py`
re-export symbols from their canonical locations. `docs/REQUEST_FLOW.md:355`
documents removal deadline as Phase 70.

### Acceptance Criteria

1. All internal imports updated to use canonical paths.
2. Shim files removed entirely (not just deprecated).
3. `import-linter` contracts updated if needed.
4. `docs/REQUEST_FLOW.md` reference to Phase 70 removal updated to
   "completed".
5. `grep -r "from.*storage.models import" src/` returns zero hits.
6. `grep -r "from.*jobs.tasks import.*set_" src/` returns zero hits
   (factory re-exports removed).
7. All quality gates pass.

---

## T70.7 — Drain Advisory: Unbounded Path Params (ADV-P68-03)

**Priority**: P2 — Input validation.

`connection_id` and `webhook_id` path parameters lack `max_length` constraints.
Partial closure of ADV-P67-01 (5/7 fields addressed in T68.6).

### Acceptance Criteria

1. `connection_id` path param: `max_length=255` on all connection endpoints.
2. `webhook_id` path param: `max_length=255` on all webhook endpoints.
3. Attack test: oversized `connection_id` returns 422.
4. Attack test: oversized `webhook_id` returns 422.
5. ADV-P68-03 closed in RETRO_LOG.
6. All quality gates pass.

---

## T70.8 — Drain Advisory: Audit Ordering Consistency (ADV-P68-04)

**Priority**: P2 — Consistency.

T68.3 established audit-before-mutation in `security.py` and `admin.py`.
`jobs.py:shred_job` and `privacy.py:refresh_budget` still use
audit-after-mutation. Standardize to audit-before-mutation.

### Acceptance Criteria

1. `jobs.py` shred_job: audit write BEFORE artifact deletion.
2. `privacy.py` refresh_budget: audit write BEFORE budget reset.
3. If audit raises, return 500 — no mutation occurs.
4. Test: mock audit to raise on shred_job — assert 500, artifact not deleted.
5. Test: mock audit to raise on refresh_budget — assert 500, budget unchanged.
6. ADV-P68-04 closed in RETRO_LOG.
7. All quality gates pass.

---

## T70.9 — Drain Advisory: Audit Failure Prometheus Counter (ADV-P68-05)

**Priority**: P2 — Observability.

Audit-write-failure paths in `admin.py`, `security.py` log at ERROR but
do not increment a Prometheus counter. Operators monitoring metrics have
no visibility into audit failures.

### Acceptance Criteria

1. New counter `AUDIT_WRITE_FAILURE_TOTAL` (labels: `endpoint`).
2. Incremented in all audit-failure catch blocks: `admin.py` (legal hold),
   `security.py` (shred, rotation), `jobs.py` (shred_job if changed in T70.8),
   `privacy.py` (refresh_budget if changed in T70.8).
3. Test: mock audit to raise, assert counter incremented.
4. ADV-P68-05 closed in RETRO_LOG.
5. All quality gates pass.

---

## Task Execution Order

```
T70.7 (unbounded path params) ────> P2, small, independent (advisory drain)
T70.8 (audit ordering) ──────────> P2, medium, independent (advisory drain)
T70.9 (audit failure counter) ───> P2, small, depends on T70.8
T70.1 (composite PK/FK) ──────────> P1, large, independent
T70.2 (v1/v2 signature removal) ──> P2, medium, independent
T70.3 (memory-safe vault) ────────> P3, small, independent
T70.4 (settings decomposition) ───> P3, medium, independent
T70.5 (operational runbook) ──────> P3, medium, independent (docs only)
T70.6 (shim removal) ─────────────> P3, small, depends on T70.4 if settings
                                    shims exist
```

---

## Phase 70 Exit Criteria

1. All path params bounded (ADV-P68-03 closed).
2. Audit-before-mutation standardized across all destructive endpoints (ADV-P68-04 closed).
3. Audit failure Prometheus counter on all audit-fail paths (ADV-P68-05 closed).
4. Composite PK/FK tables subset correctly with multi-column joins.
5. New audit entries always v3 format; v1/v2 verifiable but deprecated.
6. Vault KEK and passphrase zeroed via OS-level primitives.
7. Settings file decomposed into domain-specific sub-models.
8. Operational runbook covers deployment, rollback, incident response,
   key rotation.
9. All re-export shims removed; canonical imports enforced.
10. All quality gates pass.
11. Review agents pass for all tasks.


---

# Phase 71 — Audit Coverage Completion & Polish

**Goal**: Close the 4 audit-event gaps on destructive endpoints, wire the
audit CLI commands referenced in the runbook, add licensing test coverage,
and batch cosmetic/maintainability items from the P70 retrospective.

**Prerequisite**: Phase 70 merged.

**Source**: Post-P70 retrospective (2026-03-29). Findings F1-F6 (correctness/
security), advisories A1-A6 (cosmetic/maintainability batched per Rule 16).

---

## T71.1 — Add Audit Events to Unaudited Destructive Endpoints (F1-F4)

**Priority**: P1 — Compliance / audit trail completeness.

Four destructive endpoints currently mutate data without emitting WORM audit
events, violating the audit-before-mutation pattern established in T68.3/T70.8:

1. `DELETE /connections/{id}` — `connections.py:229`
2. `PUT /settings/{key}` — `settings.py:114-119`
3. `DELETE /settings/{key}` — `settings.py:210`
4. `DELETE /webhooks/{id}` — `webhooks.py:344`

### Acceptance Criteria

1. All 4 endpoints emit a WORM audit event BEFORE the destructive mutation.
2. If audit write fails, endpoint returns 500 — no mutation occurs.
3. `AUDIT_WRITE_FAILURE_TOTAL` counter incremented on audit failure.
4. Attack test per endpoint: mock audit to raise → assert 500, no mutation.
5. Feature test per endpoint: successful operation → audit event emitted.
6. All quality gates pass.

---

## T71.2 — Wire Audit CLI Commands (F5)

**Priority**: P1 — Operational readiness.

`docs/OPERATIONS_RUNBOOK.md` references `conclave audit migrate-signatures`
(lines 188, 237, 359) and `conclave audit log-event` (lines 186, 367) but
neither CLI command exists. The `migrate_audit_signatures()` library function
exists in `shared/security/audit_migrations.py` but has no CLI entry point.

### Acceptance Criteria

1. `conclave audit` CLI group added to `bootstrapper/cli.py` via Click.
2. `conclave audit migrate-signatures` subcommand wired to
   `migrate_audit_signatures()` with `--input`, `--output`, `--dry-run`,
   `--audit-key` options.
3. `conclave audit log-event` subcommand for manual audit event emission
   with `--type`, `--actor`, `--resource`, `--action`, `--details` options.
4. All runbook command references verified to work.
5. Feature test: invoke CLI commands with `--help` flag succeeds.
6. Feature test: `--dry-run` on migrate-signatures produces preview output.
7. All quality gates pass.

---

## T71.3 — Add Licensing Router Test Coverage (F6)

**Priority**: P2 — Test coverage gap on security boundary.

`bootstrapper/routers/licensing.py` and `bootstrapper/schemas/licensing.py`
have zero dedicated test coverage. Licensing is a security boundary — the
license gate middleware blocks all non-exempt routes when unlicensed.

### Acceptance Criteria

1. `tests/unit/test_licensing_router.py` created with tests covering:
   - `GET /license/challenge` returns hardware_id and QR code
   - `POST /license/activate` with valid token activates license
   - `POST /license/activate` with expired token returns 403
   - `POST /license/activate` with wrong hardware_id returns 403
   - `POST /license/activate` with invalid signature returns 403
2. Schema validation tests for `LicenseActivationRequest`,
   `LicenseChallengeResponse`, `LicenseActivationResponse`.
3. All quality gates pass.

---

## T71.4 — Polish: Consolidate Settings Sub-Models (A1)

**Priority**: P3 — Maintainability.

`shared/settings.py` is 1096 LOC with 6 sub-models defined inline.
T70.4 AC2 ("no file exceeds 200 LOC") was not met.

### Acceptance Criteria

1. Sub-models extracted to `shared/settings_models.py`.
2. `settings.py` imports and re-exports them for backward compatibility.
3. No file exceeds 300 LOC (relaxed from 200 given the central role).
4. All existing access patterns unchanged.
5. All quality gates pass.

---

## T71.5 — Polish: Unify Audit Failure Prometheus Counter (A2)

**Priority**: P3 — Observability.

Four separate `audit_write_failure_total_*` counters exist instead of one
unified counter with a `router` label.

### Acceptance Criteria

1. Single `AUDIT_WRITE_FAILURE_TOTAL` Counter defined in
   `shared/observability.py` with labels `router` and `endpoint`.
2. All 4 router files import and use the shared counter.
3. All existing tests pass with updated counter references.
4. All quality gates pass.

---

## T71.6 — Polish: Rename P68-P70 Test Files (A4)

**Priority**: P4 — Maintainability.

Test files use task IDs (`test_p70_t701_...`) instead of module names,
making navigation difficult.

### Acceptance Criteria

1. P68-P70 test files renamed to module-based names (e.g.,
   `test_p70_t701_composite_pk_fk_attack.py` →
   `test_subsetting_composite_fk_attack.py`).
2. No test logic changed — rename only.
3. All quality gates pass.

---

## Task Execution Order

```
T71.1 (audit events) ─────────> P1, correctness, 4 endpoints
T71.2 (audit CLI) ────────────> P1, operational, CLI wiring
T71.3 (licensing tests) ──────> P2, test coverage
T71.4 (settings split) ───────> P3, polish
T71.5 (counter unification) ──> P3, polish
T71.6 (test rename) ──────────> P4, polish
```

---

## Phase 71 Exit Criteria

1. All destructive endpoints emit WORM audit events before mutation.
2. `conclave audit` CLI group functional with migrate-signatures and log-event.
3. Licensing router has dedicated test coverage.
4. Settings file split (no file >300 LOC).
5. Single unified audit failure Prometheus counter.
6. Test files named by module, not task ID.
7. All quality gates pass.
8. Review agents pass for all tasks.


---

