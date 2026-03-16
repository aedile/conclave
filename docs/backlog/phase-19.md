# Phase 19 — Production Hardening & Integration Integrity

**Goal**: Fix critical correctness and security findings from the Phase 18 roast,
close the E2E validation gap, and add missing production safeguards. No new features.

**Prerequisite**: Phase 18 must be complete (all tasks merged, retrospective signed off).

**Source**: Post-Phase 18 roast by Sr Principal Engineer, QA Engineer, Architect, and PM.

---

## T19.1 — Middleware & Engine Singleton Fixes

**Priority**: P0 — Correctness (Constitution Priority 1).

### Context & Constraints

1. `RFC7807Middleware` in `bootstrapper/middleware.py` uses Starlette's `BaseHTTPMiddleware`
   which buffers the entire response body before returning. This breaks SSE streaming
   (ServerEventGenerator yields chunks) and can cause memory issues on large responses.
   Fix: convert to pure ASGI middleware or use `@app.middleware("http")` pattern.

2. `shared/db.py` `get_engine()` creates a new SQLAlchemy engine on every call. In a
   request-heavy environment, this means a new connection pool per call — connection
   exhaustion risk. Fix: cache engine as module-level singleton with lazy initialization.

3. `EgressWriter.write()` in `modules/subsetting/egress.py` may not wrap all rows in a
   single transaction per batch. The Saga pattern documentation implies atomicity, but
   verify the actual transaction boundary. If rows are committed individually, a mid-batch
   failure leaves partial data without Saga rollback.

### Acceptance Criteria

1. `RFC7807Middleware` converted from `BaseHTTPMiddleware` to pure ASGI middleware.
   SSE streaming verified to work without response buffering.
2. `get_engine()` returns a cached singleton. Multiple calls return the same engine instance.
3. `EgressWriter.write()` transaction boundaries verified and documented.
   If not already atomic per batch, wrapped in explicit transaction.
4. Unit tests for all three fixes.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/ -v --no-cov` (SSE streaming test)
- All review agents spawned (conditional: QA + DevOps + Architecture).

---

## T19.2 — Security Hardening: Proxy Trust & Config Validation

**Priority**: P0 — Security (Constitution Priority 0).

### Context & Constraints

1. The FastAPI app accepts `X-Forwarded-For` headers without validating the request
   source is a trusted reverse proxy. An attacker can spoof their IP address by
   setting this header directly. Fix: add trusted proxy validation middleware or
   document that the app MUST be behind a trusted reverse proxy in production.

2. `MASKING_SALT` is not enforced in production config validation
   (`bootstrapper/config_validation.py`). When `ENV=production`, the system falls
   back to a hardcoded development salt — deterministic masking in production uses
   a known value, making it reversible. Fix: add `MASKING_SALT` to
   `_PRODUCTION_REQUIRED` tuple.

3. `pgbouncer/userlist.txt.example` was created in T18.2 but the actual auth type
   is `md5` (ADV-016). PostgreSQL 14+ deprecates md5 in favor of `scram-sha-256`.

### Acceptance Criteria

1. `X-Forwarded-For` handling documented in OPERATOR_MANUAL.md with clear warning
   about trusted proxy requirement. If middleware-level validation is feasible,
   implement it with configurable trusted proxy CIDR list.
2. `MASKING_SALT` added to `_PRODUCTION_REQUIRED` in config_validation.py.
   Production startup without `MASKING_SALT` causes immediate exit.
3. ADV-016 resolved: `PGBOUNCER_AUTH_TYPE` changed to `scram-sha-256` in
   docker-compose.yml. `userlist.txt.example` updated with SCRAM hash format.
4. Unit tests for config validation changes.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run bandit -c pyproject.toml -r src/`
- All review agents spawned (conditional: QA + DevOps + Architecture).

---

## T19.3 — Integration Test CI Gate & Property-Based Testing

**Priority**: P1 — Test integrity (Constitution Priority 1).

### Context & Constraints

1. Integration tests (`tests/integration/`) skip silently if PostgreSQL is not
   available. In CI, if the PostgreSQL service fails to start, the entire integration
   test suite passes with 0 tests run — a false green. Fix: add a CI step that
   verifies integration test count is >0 before declaring pass.

2. The project has zero property-based tests. For invariant-critical code paths
   (deterministic masking: same input → same output, FK traversal: parent before
   child, epsilon accounting: monotonically increasing), property-based tests
   with `hypothesis` would catch edge cases that example-based tests miss.

3. Concurrent privacy budget contention is untested. Two simultaneous synthesis
   jobs contending for the same table's epsilon budget should either both complete
   within budget or exactly one fail with `BudgetExhaustionError`.

### Acceptance Criteria

1. CI integration test step verifies `collected > 0` before passing.
   If 0 tests collected, the CI step fails.
2. `hypothesis` added to dev dependencies.
3. At least 5 property-based tests added:
   - Deterministic masking roundtrip (same input, same seed → same output)
   - FK traversal ordering (parent always before child)
   - Epsilon accounting monotonicity
   - Subsetting preserves FK integrity
   - Profile comparison symmetry
4. Concurrent budget contention test added to integration tests.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/ -v --no-cov`
- New property-based tests pass with default hypothesis settings.
- All review agents spawned (conditional: QA + DevOps).

---

## T19.4 — Live E2E Pipeline Validation

**Priority**: P0 — System validation (never done).

### Context & Constraints

1. T18.3 created the seeding script, sample data, and documentation, but the actual
   live pipeline was never executed through Docker Compose. The system has never
   been proven to work end-to-end in a container environment.

2. `docs/E2E_VALIDATION.md` contains TODO markers for live validation evidence.

3. This task requires a running Docker Compose stack. The developer agent should:
   - Start `docker-compose up -d`
   - Wait for all services to be healthy
   - Seed the source database
   - Run `conclave-subset` CLI
   - Verify target database has masked data
   - Capture terminal output as evidence
   - Update `docs/E2E_VALIDATION.md` with results

### Acceptance Criteria

1. `docker-compose up -d` starts all services (postgres, redis, minio, pgbouncer).
2. `scripts/seed_sample_data.py --dsn <source_dsn>` seeds the source database.
3. `conclave-subset` CLI completes without error, producing masked output in target DB.
4. `docs/E2E_VALIDATION.md` TODO markers replaced with actual terminal output.
5. Any runtime issues documented as findings for future fixes.

### Testing & Quality Gates

- All docker-compose services healthy.
- conclave-subset CLI exits 0.
- Target database contains masked data (spot-check 3 tables).
- All review agents spawned (conditional: QA + DevOps).

---

## T19.5 — Process Sunset & Rule Consolidation

**Priority**: P2 — Process hygiene.

### Context & Constraints

1. CLAUDE.md Rules 2-11 are tagged `[sunset: Phase 22]`. Phase 19 is the appropriate
   time to evaluate early since several rules have not prevented recurrences:
   - Rule 2 (cross-task integration matrix) — never triggered since Phase 3
   - Rule 3 (integration tests separate gate) — now well-established
   - Rule 6 (technology substitution ADR) — successfully applied in T18.2
   - Rule 8 (operational wiring) — last relevant in Phase 4

2. The 4-agent reviewer pattern costs ~100K tokens per task. For docs-only or
   single-file changes, conditional spawning (T17.4) helps but the pattern is
   still heavy. Evaluate whether 2 reviewers (QA + DevOps) are sufficient for
   non-structural changes.

3. Phase ceremony (backlog file, README update, BACKLOG.md index, retrospective)
   on small phases is disproportionate overhead per Rule 17 findings.

### Acceptance Criteria

1. Rules 2, 3, 7, 8 evaluated against git history for recurrence prevention.
   Rules that have not prevented a failure in 10+ phases retired with justification.
2. CLAUDE.md reduced to ≤400 lines.
3. Phase ceremony simplified for small phases (≤3 tasks).
4. Reviewer spawning cost documented with actual token counts.

### Testing & Quality Gates

- CLAUDE.md ≤400 lines after consolidation.
- `pre-commit run --all-files` passes.
- All review agents spawned (conditional: QA + DevOps — this is a docs/process task).

---

## Phase 19 Exit Criteria

- RFC7807Middleware converted to pure ASGI middleware.
- DB engine singleton cached.
- EgressWriter transaction boundaries verified.
- X-Forwarded-For proxy trust documented/enforced.
- MASKING_SALT enforced in production config validation.
- ADV-016 resolved (pgbouncer scram-sha-256).
- CI integration test gate enforces >0 collected.
- hypothesis property-based tests added (≥5).
- Concurrent budget contention tested.
- Live E2E pipeline executed through Docker Compose.
- E2E_VALIDATION.md TODO markers replaced with evidence.
- CLAUDE.md ≤400 lines with rule sunset evaluation.
- All quality gates passing.
- Phase 19 end-of-phase retrospective completed.
