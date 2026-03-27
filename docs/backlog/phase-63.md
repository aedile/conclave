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
