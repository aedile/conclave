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

---

## Tasks

### T83.1 — Usage Event Model & Recording

**Files to create**:
- `src/synth_engine/modules/metering/` (new module)
- `src/synth_engine/modules/metering/events.py`
- `src/synth_engine/modules/metering/recorder.py`
- Alembic migration for `usage_events` table

**Acceptance Criteria**:
- [ ] `UsageEvent` model: `id`, `org_id`, `event_type`, `quantity`, `unit`, `metadata` (JSON), `recorded_at`
- [ ] Event types: `rows_synthesized`, `rows_masked`, `training_seconds`, `storage_bytes`, `api_calls`
- [ ] `record_usage()` function — async, non-blocking, fire-and-forget
- [ ] Events are append-only (no UPDATE or DELETE)
- [ ] Bulk recording for batch operations (e.g., 50K rows synthesized = one event, not 50K)

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
- [ ] Admin can query any org's usage; operator/viewer sees only their own org

### T83.3 — Billing Webhook

**Files to create/modify**:
- `src/synth_engine/modules/metering/billing_webhook.py` (new)
- `shared/settings.py` (billing webhook URL config)

**Acceptance Criteria**:
- [ ] Configurable webhook URL for pushing usage events to external billing system
- [ ] Webhook fires at end of billing period (configurable: daily, weekly, monthly)
- [ ] Payload: org_id, period, usage summary, signed with HMAC for integrity
- [ ] Retry with exponential backoff on failure (3 attempts)
- [ ] If no webhook configured, metering still works (just no push)
- [ ] Webhook delivery uses existing circuit breaker infrastructure from P75

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
- [ ] Quota check is atomic with job creation (no TOCTOU race between check and create)
- [ ] Default quotas configurable via settings (applied to orgs without explicit quotas)

---

## Testing & Quality Gates

- Attack tests: Org at quota limit attempts job creation (429 with informative error)
- Attack tests: Org A cannot view Org B's usage
- Integration tests: record events → aggregate → verify totals match
- Performance test: 10,000 usage events recorded in <1s (non-blocking path)
- Metering module must follow existing module boundary rules (no cross-module imports)
