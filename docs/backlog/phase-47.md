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
