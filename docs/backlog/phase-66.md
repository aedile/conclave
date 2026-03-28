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
