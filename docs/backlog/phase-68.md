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
