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
