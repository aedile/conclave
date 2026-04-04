# Phase 83 — Usage Metering & Quota Enforcement

**Tier**: 8 (Enterprise Scale)
**Goal**: Track per-tenant resource consumption and enforce configurable quotas.

**Dependencies**: Phase 79 (multi-tenancy — org_id must exist for metering)

---

## Context & Constraints

- No usage tracking exists today. There's no way to answer "how many rows did Org A
  synthesize this month?" or "how much storage is Org B consuming?"
- Metering is foundational for billing (whether internal chargeback or external SaaS).
- Quotas prevent one tenant from consuming all resources in a shared deployment.
- Usage events should be append-only (like the audit log) for billing dispute resolution.
- Metering must not add significant latency to the hot path (job creation, synthesis).
- **Import-linter**: Adding `modules/metering/` requires updating all three import-linter
  contracts in `pyproject.toml`: (1) independence contract, (2) modules-cannot-import-
  bootstrapper contract, (3) shared-cannot-import-modules contract.
- **Circuit breaker boundary**: The billing webhook (T83.3) needs the circuit breaker
  from P75, which lives in `bootstrapper/dependencies/`. Modules cannot import from
  bootstrapper (import-linter). Solution: inject the circuit breaker as a callback via
  IoC wiring in `bootstrapper/wiring.py` (same pattern as `_build_webhook_delivery_fn`),
  or move the circuit breaker to `shared/` if it's now used by 2+ consumers. ADR required
  if moving to shared.
- **TOCTOU on quota enforcement**: Quota check and job creation must be atomic. Use
  `SELECT ... FOR UPDATE` on the quota record (same pattern as privacy accountant's
  `spend_budget()`). Do not use a two-step check-then-create pattern.
- **Dropped usage events**: `record_usage()` is fire-and-forget. Dropped events must
  increment `conclave_usage_event_dropped_total` Prometheus counter for observability.

---

## Tasks

### T83.1 — Usage Event Model & Recording

**Files to create**:
- `src/synth_engine/modules/metering/__init__.py` (new module)
- `src/synth_engine/modules/metering/events.py`
- `src/synth_engine/modules/metering/recorder.py`
- Alembic migration for `usage_events` table
- Update `pyproject.toml` import-linter contracts (all three)

**Acceptance Criteria**:
- [ ] `UsageEvent` model: `id`, `org_id`, `event_type`, `quantity`, `unit`, `metadata` (JSON), `recorded_at`
- [ ] Event types: `rows_synthesized`, `rows_masked`, `training_seconds`, `storage_bytes`, `api_calls`
- [ ] `record_usage()` function — async, non-blocking, fire-and-forget
- [ ] Dropped events increment `conclave_usage_event_dropped_total` Prometheus counter
- [ ] Events are append-only (no UPDATE or DELETE). Integration test required: verify the
      application code never issues UPDATE/DELETE against `usage_events` table.
- [ ] Bulk recording for batch operations (e.g., 50K rows synthesized = one event, not 50K)
- [ ] Import-linter contracts updated in `pyproject.toml` for `modules/metering/`
- [ ] `.env.example` updated with metering config variables

### T83.2 — Usage Aggregation & Reporting

**Files to create**:
- `src/synth_engine/modules/metering/aggregation.py`
- `bootstrapper/routers/usage.py` (new)
- `bootstrapper/schemas/usage.py` (new)

**Acceptance Criteria**:
- [ ] `GET /api/v1/usage` — current period usage summary for requesting org
- [ ] `GET /api/v1/usage/history?start=&end=` — historical usage with date range
- [ ] Response includes: total rows synthesized, total training time, storage consumed, API call count
- [ ] Aggregation by day, week, or month (query parameter)
- [ ] Export as CSV or JSON
- [ ] All roles see only their own org's usage (admin included — admin is per-org,
      not a system superadmin). No cross-org usage queries exist at this tier.

### T83.3 — Billing Webhook

**Files to create/modify**:
- `src/synth_engine/modules/metering/billing_webhook.py` (new)
- `shared/settings.py` (billing webhook URL config)
- `bootstrapper/wiring.py` (IoC injection of circuit breaker callback)

**Acceptance Criteria**:
- [ ] Configurable webhook URL for pushing usage events to external billing system
- [ ] **Billing webhook URL validated through `shared/ssrf.py`** at configuration time
      AND before each delivery attempt. Private IPs, loopback, and link-local rejected.
- [ ] Webhook fires at end of billing period (configurable: daily, weekly, monthly)
- [ ] Payload: org_id, period, usage summary, signed with HMAC for integrity
- [ ] HMAC signing key: `BILLING_WEBHOOK_SECRET` env var (Docker secrets in production).
      Startup validation: if webhook URL is configured, signing key must be non-empty
      (fail-closed). `.env.example` updated with `BILLING_WEBHOOK_SECRET`.
- [ ] Retry with exponential backoff on failure (3 attempts)
- [ ] If no webhook configured, metering still works (just no push)
- [ ] Circuit breaker injected via IoC callback from `bootstrapper/wiring.py` (modules
      cannot import from bootstrapper — use same pattern as webhook delivery)
- [ ] Malformed response handling: webhook endpoint returns 200 with non-JSON body →
      logged as warning, not treated as success
- [ ] `.env.example` updated with `BILLING_WEBHOOK_URL`

### T83.4 — Quota Enforcement

**Files to create/modify**:
- `src/synth_engine/modules/metering/quotas.py` (new)
- `bootstrapper/routers/jobs.py` (quota check before job creation)
- `bootstrapper/routers/admin.py` (quota configuration per org)

**Acceptance Criteria**:
- [ ] Per-org quotas: `max_rows_per_month`, `max_concurrent_jobs`, `max_storage_bytes`
- [ ] `POST /api/v1/admin/orgs/{org_id}/quotas` — set quotas (admin only)
- [ ] Job creation checks quota before accepting (fail-fast, not fail-mid-training)
- [ ] Quota exceeded → 429 with clear error message and current/limit values
- [ ] Quota check is atomic with job creation via `SELECT ... FOR UPDATE` on quota record
      (same pattern as privacy accountant `spend_budget()`). No TOCTOU race.
- [ ] Concurrent quota exhaustion test: two simultaneous requests from same org at quota
      boundary — only one succeeds. Integration test required (not satisfiable by mocks).
- [ ] Default quotas configurable via settings (applied to orgs without explicit quotas)

---

## Testing & Quality Gates

- Attack tests: Org at quota limit attempts job creation (429 with informative error)
- Attack tests: Org A cannot view Org B's usage
- Integration tests: record events → aggregate → verify totals match
- Integration test: append-only invariant — verify no UPDATE/DELETE on usage_events
- Integration test: concurrent quota exhaustion (two simultaneous requests at limit)
- Performance test: 10,000 usage events recorded in <1s (non-blocking path).
  Use `pytest-benchmark` or a standalone script; document approach in test docstring.
- Metering module must follow existing module boundary rules (no cross-module imports)
- Integration tests must reset state between tests (no cross-test leakage)
