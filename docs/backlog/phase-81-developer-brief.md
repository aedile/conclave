# Phase 81 — Developer Brief: SSO / OIDC Integration

**Branch**: `feat/P81-sso-oidc-integration`
**Spec**: `docs/backlog/phase-81.md` (amended with spec-challenger findings)
**Spec Challenge Results**: `docs/backlog/phase-81-spec-challenge.md`

---

## Known Failure Patterns to Guard Against

Per `docs/RETRO_LOG.md` pre-task learning scan (MANDATORY per protocol):

- **Return-value assertion pattern** — test asserts `is not None` instead of specific values. Guard: all asserts check specific role values, specific Redis key formats, specific error codes.
- **Integration-vs-unit substitution pattern** — Redis-down and mock OIDC provider tests substituted with mocks when real integration tests were required. Guard: integration tests use `pytest-httpserver` or real Redis fixture, not monkeypatched stubs.
- **IoC wiring Rule 8 pattern** — OIDC router added to `auth_oidc.py` but not registered in `router_registry.py`. Guard: wire router in same commit as creation.
- **Intra-module cohesion Rule 7 pattern** — OIDC dependencies placed in `modules/` instead of `bootstrapper/`. Guard: all OIDC router and dependency code lives in `bootstrapper/`.
- **Version-pin hallucination pattern** — `authlib` version speculated without checking PyPI. Guard: confirm actual version before writing `pyproject.toml` change.
- **Aspirational-config pattern** — `.env.example` entries added without corresponding config validation. Guard: every new env var must have a corresponding `settings_models.py` field and a `config_validation.py` check.

---

## PM Architectural Decisions

### Decision 1 — OIDC Library: `authlib`

Use `authlib`. Rationale: pure Python (no C extensions — critical for air-gap bundle integrity),
PKCE S256 native support (`AuthorizationCodeFlow` with `code_challenge_method="S256"`),
actively maintained, no LangChain dependency, minimal CVE surface relative to alternatives.
ADR-0067 is required before implementation begins. Developer must confirm the exact version
to pin in `pyproject.toml` and record it in the ADR.

Alternatives considered and rejected:
- `python-jose` + manual PKCE: requires manual PKCE implementation, higher error surface
- `httpx-oauth`: fewer stars, less PKCE documentation, uncertain air-gap status

---

### Decision 2 — SSRF Validator Exception for Air-Gap IdPs

The existing `validate_url()` in `shared/ssrf.py` blocks RFC-1918 addresses. Air-gap IdPs are
deployed on RFC-1918 addresses. These two facts are irreconcilable with the existing function.

Create `validate_oidc_issuer_url()` in `shared/ssrf.py` with the following behavior:

- Blocks unconditionally (in all environments):
  - `169.254.169.254` (AWS/GCP/Azure IMDS)
  - `100.100.100.200` (Alibaba Cloud IMDS)
  - `metadata.google.internal`
  - Loopback: `127.0.0.1/8` and `::1`
- Allows RFC-1918 ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) — required for air-gap IdPs
- Blocks public IP addresses when `CONCLAVE_ENV=production` (same as existing `validate_url()`)
- Logs a `WARNING`-level security notice when an RFC-1918 issuer is accepted

The existing `validate_url()` is NOT modified. It continues to block RFC-1918 for all other uses.

---

### Decision 3 — Identity Anchor: Email-Only (No `oidc_sub` Column)

Email is the sole identity anchor for OIDC users at Tier 8. No `oidc_sub` column is added to
the users table. Matching is by `email + org_id`.

Accepted limitation: if the IdP changes a user's email address, the next OIDC login creates
a new user account. The old account is orphaned until an admin deletes it. This limitation is
documented in ADR-0067 and the OIDC runbook. Sub-based stable identity matching is deferred
to Tier 9.

---

### Decision 4 — State and PKCE Storage in Redis

State and PKCE verifier are stored in a single Redis key:

```
Key:   conclave:oidc:state:<state_value>
Value: {"code_verifier": "<verifier>", "created_at": "<iso8601>"}
TTL:   600 seconds (default), configurable via OIDC_STATE_TTL_SECONDS
```

The key is deleted atomically on first use (read-and-delete in a single pipeline). Any
subsequent request with the same state value returns 401. The state value itself must be
generated with `secrets.token_urlsafe(32)` and validated as URL-safe base64 before use as a
Redis key suffix — reject any value containing `:` or non-URL-safe characters.

---

### Decision 5 — Session Architecture

Sessions exist only when OIDC is enabled. Passphrase auth remains stateless JWT with no
Redis session.

Session Redis key format:

```
Key:   conclave:session:<random_token>
Value: {
  "user_id": "<uuid>",
  "org_id":  "<uuid>",
  "role":    "<role_name>",
  "created_at":       "<iso8601>",
  "last_refreshed_at": "<iso8601>"
}
TTL:   28800 seconds (8 hours, default), configurable via SESSION_TTL_SECONDS
```

The key MUST use a random token (not derived from user_id) to prevent session fixation
(Attack Vector AV-5). Token generated with `secrets.token_urlsafe(32)`.

Concurrent session limit: 3 (default), configurable via `CONCURRENT_SESSION_LIMIT`.
Eviction policy: when the limit is exceeded on a new login, the session with the earliest
`created_at` is deleted. The new session is then written.

Endpoints `/auth/refresh` and `/auth/revoke` return 404 when OIDC is not configured. This
prevents these endpoints from advertising their existence when sessions are not in use.

Config validation constraints (fail at startup):
- `SESSION_TTL_SECONDS >= 60`
- `CONCURRENT_SESSION_LIMIT >= 1`
- If OIDC enabled and `REDIS_URL` not set: raise `ConfigurationError`

---

### Decision 6 — `POST /auth/refresh` Auth Model

Requires a valid JWT via `get_current_user()`. Any role is sufficient. Returns a new JWT with
a fresh `exp`. The old JWT remains valid until natural expiry (no token rotation — no blocklist
needed). If OIDC is not configured, returns 404. No RBAC permission check beyond valid JWT.
Documented in ADR-0067 to prevent accidental future restriction.

---

### Decision 7 — `POST /auth/revoke` Semantics

Request body: `{"user_id": "<uuid>"}`.

Behavior:
- Admin calling with any `user_id` in their org: deletes ALL Redis session keys for that user
- Non-admin calling with their own `user_id`: deletes their own sessions (self-revocation)
- Non-admin calling with another user's `user_id`: returns 403
- Any role calling with a `user_id` in a different org: returns 404
- If OIDC is not configured: returns 404

Requires `sessions:revoke` permission for cross-user revocation (admin only). Self-revocation
bypasses the permission check.

---

### Decision 8 — RBAC Addition: `sessions:revoke`

Add `sessions:revoke` to `PERMISSION_MATRIX` in `bootstrapper/dependencies/permissions.py`
with `frozenset({Role.admin})`. Amend ADR-0066 in the same commit as the permissions code change.
This is a delivery requirement under Rule 8.

---

### Decision 9 — Default Org for Auto-Provisioned Users

Introduce `OIDC_DEFAULT_ORG_ID` setting in `shared/settings_models.py`.

Resolution logic:
1. If `CONCLAVE_MULTI_TENANT_ENABLED=false`: use `DEFAULT_ORG_UUID` (existing setting)
2. If `CONCLAVE_MULTI_TENANT_ENABLED=true` AND `OIDC_DEFAULT_ORG_ID` is set: use it
3. If `CONCLAVE_MULTI_TENANT_ENABLED=true` AND `OIDC_DEFAULT_ORG_ID` is NOT set: raise
   `ConfigurationError` at startup — not at first login

---

### Decision 10 — IdP Role Claims: Ignored

The OIDC callback MUST NOT read any `role`, `groups`, `permissions`, or equivalent claim from
the IdP's ID token to set or modify the local user's role (Attack Vector AV-4). Role is ALWAYS
resolved from the local DB `users.role` column. Auto-provisioned users get the default role
regardless of what the IdP token claims. This policy is documented in ADR-0067 and enforced
by a test: `test_idp_role_claim_does_not_escalate_privileges`.

---

### Decision 11 — Post-Callback Response: JSON, Not Redirect

The OIDC callback endpoint (`GET /auth/oidc/callback`) returns:

```json
{"access_token": "<jwt>", "token_type": "bearer", "expires_in": <seconds>}
```

No browser redirect. No `Location` header. The frontend SPA owns the OIDC flow and reads
this JSON response to store the token. This eliminates the open redirect attack surface
(Attack Vector AV-3). Test must assert absence of `Location` header.

---

### Decision 12 — JWKS Caching: Fetch at Boot, No Runtime Refresh

JWKS are fetched once at application startup and cached in memory for the process lifetime.
If the IdP rotates signing keys, an application restart is required. This is an accepted
operational limitation documented in ADR-0067 and the OIDC runbook.

The fetch MUST use HTTPS when `CONCLAVE_ENV=production`. HTTP is permitted only in
`CONCLAVE_ENV=development` (for local test IdPs). A CRITICAL-level security log message is
emitted if HTTP is used in production mode (Attack Vector AV-8 mitigation).

---

### Decision 13 — Error Responses: RFC 7807 Problem Details

All OIDC error paths return `Content-Type: application/problem+json` with RFC 7807 shape:

```json
{
  "type": "about:blank",
  "title": "<short description>",
  "status": <http_status_code>,
  "detail": "<human-readable detail>"
}
```

Cross-org email collision detail MUST be `"Authentication failed"` — not a message that
reveals the email exists in another org (oracle prevention, Attack Vector AV-6).

---

### Decision 14 — Audit Events

The following audit events must be emitted using the existing audit infrastructure. All events
are emitted BEFORE the associated mutation (T68.3 pattern):

| Event Name | Trigger |
|---|---|
| `OIDC_LOGIN_SUCCESS` | JWT issued after successful callback |
| `OIDC_LOGIN_FAILURE` | Any error in the callback flow |
| `USER_AUTO_PROVISIONED` | New user created on first OIDC login |
| `SESSION_CREATED` | New Redis session written |
| `SESSION_REFRESHED` | `/auth/refresh` issues new JWT |
| `SESSION_REVOKED` | `/auth/revoke` deletes sessions |

---

### Decision 15 — Migration 011: Add `last_login_at`

Create `alembic/versions/011_add_last_login_at.py`. Add column `last_login_at: datetime | None`
(nullable, no default) to the `users` table. Add the corresponding field to
`shared/models/user.py` with type `datetime | None = None`.

---

### Decision 16 — Auth-Exempt Paths

Add `/auth/oidc/authorize` and `/auth/oidc/callback` to `AUTH_EXEMPT_PATHS` in
`bootstrapper/dependencies/_exempt_paths.py`. Without this, unauthenticated users cannot
initiate the OIDC flow.

---

### Decision 17 — Rate Limiting on OIDC Endpoints

Apply the same rate limit as `/auth/token` (10 requests per minute per IP) to both
`GET /auth/oidc/authorize` and `GET /auth/oidc/callback`. Implement using the existing
rate-limiter decorator pattern. Test must verify 429 response on the eleventh request.

---

### Decision 18 — Token Exchange Response Size Limit

The HTTP client used for the IdP token exchange (authlib's internal call to the token endpoint)
must be configured with a 64KB response size limit. This prevents memory exhaustion from
a rogue or misconfigured IdP (Attack Vector AV-9).

---

## Attack Surface Analysis

| Attack Surface Area | Details |
|---|---|
| New endpoints added | `GET /auth/oidc/authorize` (exempt, rate-limited 10/min/IP); `GET /auth/oidc/callback` (exempt, rate-limited); `POST /auth/refresh` (requires valid JWT); `POST /auth/revoke` (requires `sessions:revoke` permission for cross-user, valid JWT for self) |
| New user inputs accepted | `state` (URL-safe base64, max 64 chars, no `:` chars); `code` (authorization code, max 256 chars); `code_verifier` (PKCE, 43-128 chars); `user_id` (UUID format) in revoke body; OIDC config URLs at startup |
| New data written to storage | Redis: `conclave:oidc:state:<state>` (TTL 600s, deleted on use); Redis: `conclave:session:<token>` (TTL 8h); DB: `users.last_login_at` (nullable datetime) |
| New external calls made | IdP `.well-known/openid-configuration` at boot (timeout: 10s, fail-closed); IdP JWKS endpoint at boot (timeout: 10s, fail-closed); IdP token exchange endpoint per-request (timeout: 10s, 64KB limit) |
| Failure modes | IdP unreachable at boot: application fails to start (fail-closed). IdP unreachable during callback: 503. Redis down: OIDC auth fails 503; session validation fails 401; passphrase JWT auth unaffected. DB down: provisioning fails 503. |
| What does an attacker see? | Auth failures return 401 with RFC 7807 body. Cross-org collisions return generic "Authentication failed" (no oracle). State mismatch: 401. Rate limit: 429. No stack traces in any response. |

---

## Task Execution Order

### T81.0 — OIDC ADR (ADR-0067) — FIRST

**Files to create**:
- `docs/adr/ADR-0067-oidc-integration.md`

**Content must document**:
- `authlib` selection rationale and version
- PKCE S256 requirement and implementation approach
- State + code_verifier storage in Redis (single key, one-time use)
- Session architecture (Redis-backed, random key, no user_id in key)
- JWKS caching policy (boot-time only, restart-to-rotate)
- Air-gap SSRF exception: RFC-1918 allowed, cloud metadata blocked
- IdP role claim policy: ignored, role always from local DB
- Email-only identity anchor: accepted limitation of sub-based matching deferral
- Accepted limitations enumerated

**Commit**: `docs: add ADR-0067 OIDC integration`

---

### T81.1 — OIDC Provider Integration + SSRF Exception

**Files to create**:
- `src/synth_engine/bootstrapper/routers/auth_oidc.py`
- `src/synth_engine/bootstrapper/dependencies/oidc.py`

**Files to modify**:
- `src/synth_engine/shared/ssrf.py` — add `validate_oidc_issuer_url()`
- `src/synth_engine/shared/settings_models.py` — OIDC config fields: `OIDC_ENABLED`, `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_STATE_TTL_SECONDS` (default 600, max 3600)
- `src/synth_engine/bootstrapper/dependencies/_exempt_paths.py` — add `/auth/oidc/authorize`, `/auth/oidc/callback`
- `src/synth_engine/bootstrapper/router_registry.py` — register `auth_oidc` router (Rule 8: wire in same commit)
- `.env.example` — add OIDC settings with comments

**Key implementation notes**:
- `GET /auth/oidc/authorize`: generate state with `secrets.token_urlsafe(32)`, generate PKCE verifier and challenge, write to Redis, return redirect URL as JSON (not HTTP redirect)
- `GET /auth/oidc/callback`: validate state (atomic read-delete), validate PKCE, exchange code for tokens, extract email claim, do NOT read role from token
- Boot-time: fetch `.well-known/openid-configuration` and JWKS; fail-closed on any error
- Rate limiting: 10 req/min/IP on both endpoints

---

### T81.2 — User Provisioning + Migration 011

**Files to create**:
- `alembic/versions/011_add_last_login_at.py`

**Files to modify**:
- `src/synth_engine/shared/models/user.py` — add `last_login_at: datetime | None = None`
- `src/synth_engine/bootstrapper/routers/auth_oidc.py` — provisioning logic in callback handler

**Key implementation notes**:
- Provisioning query: `SELECT * FROM users WHERE email = :email AND org_id = :org_id`
- If no row: create with default role, emit `USER_AUTO_PROVISIONED` before insert
- If row exists in a different org: return 401 with generic "Authentication failed" (not 403 — prevents oracle)
- If row exists in the same org: update `last_login_at`, emit `OIDC_LOGIN_SUCCESS`
- Default role for new users: `operator` (lowest-privilege role in the current RBAC enum — see AC-9 note; developer must verify against `Role` enum before coding)
- Default org resolution: per Decision 9

---

### T81.3 — Session Management

**Files to create**:
- `src/synth_engine/bootstrapper/dependencies/sessions.py`

**Files to modify**:
- `src/synth_engine/bootstrapper/routers/auth_oidc.py` — add `/auth/refresh` and `/auth/revoke` endpoints
- `src/synth_engine/bootstrapper/dependencies/permissions.py` — add `sessions:revoke` with `frozenset({Role.admin})`
- `docs/adr/ADR-0066-rbac-permission-model.md` — amend permission table (Rule 8: same commit as code change)
- `src/synth_engine/shared/settings_models.py` — add `SESSION_TTL_SECONDS` (default 28800), `CONCURRENT_SESSION_LIMIT` (default 3)
- `.env.example` — add session config variables

**Key implementation notes**:
- Session key: `conclave:session:<secrets.token_urlsafe(32)>`
- On new login: scan for existing user sessions, evict oldest if count >= limit, write new session
- `/auth/refresh`: read current JWT user, update `last_refreshed_at` in session value, issue new JWT
- `/auth/revoke`: admin path scans `conclave:session:*` keys, loads each, deletes matching user_id+org_id; self-path deletes caller's sessions only
- Redis-down behavior: catch `RedisError`, return 503 for write paths, 401 for read/validate paths

---

### T81.4 — Air-Gap Configuration + Runbook

**Files to create**:
- `docs/runbooks/oidc-idp-unavailable.md`

**Files to modify**:
- `src/synth_engine/bootstrapper/config_validation.py` — OIDC config validation (OIDC_DEFAULT_ORG_ID requirement, REDIS_URL requirement, STATE_TTL max, SESSION_TTL min, CONCURRENT_SESSION_LIMIT min)
- `.env.example` — confirm all OIDC and session settings are documented

**Runbook content must cover**:
- Symptoms of IdP unavailability (startup failure, 503 on OIDC endpoints)
- How to enable passphrase auth fallback
- How to manually provision users when IdP is down
- How to restart after IdP recovery
- Key rotation: steps required (application restart)

---

## Negative Test Requirements (from spec-challenger)

All 45 tests below are MANDATORY. Write them in the ATTACK RED phase, BEFORE any feature
tests. Commit separately as `test: add negative/attack tests for SSO/OIDC`.

### OIDC State / CSRF Protection (6 tests)

1. `test_callback_missing_state_returns_422` — callback with no `state` query param
2. `test_callback_wrong_state_returns_401` — callback with state not in Redis
3. `test_callback_expired_state_returns_401` — callback after state TTL has elapsed
4. `test_callback_state_replay_returns_401` — same callback URL submitted twice; second attempt rejected after state deleted on first use
5. `test_callback_state_from_different_session_returns_401` — state belonging to a different concurrent user's Redis key
6. `test_authorize_missing_code_challenge_returns_422` — authorize request without PKCE `code_challenge` param

### PKCE Enforcement (4 tests)

7. `test_callback_missing_code_verifier_returns_422` — callback with no `code_verifier`
8. `test_callback_wrong_code_verifier_returns_401` — `code_verifier` does not match stored `code_challenge`
9. `test_pkce_plain_method_rejected` — `code_challenge_method=plain` must be rejected; only S256 accepted
10. `test_implicit_flow_rejected` — `response_type=token` (implicit flow) not supported; returns 422

### User Provisioning (5 tests)

11. `test_cross_org_email_returns_401_generic_message` — existing user in org B authenticates via OIDC in org A; returns 401 "Authentication failed"
12. `test_cross_org_email_does_not_leak_org_existence` — response body for cross-org collision is byte-for-byte identical to a generic auth failure; no "org" or "email" in message
13. `test_oidc_provisioned_user_has_correct_default_role` — auto-provisioned user has the role from Decision 9 (developer confirms against Role enum)
14. `test_oidc_login_missing_email_claim_returns_401` — IdP ID token has no `email` claim
15. `test_oidc_login_empty_email_claim_returns_401` — IdP ID token has `email: ""`

### Session Management (8 tests)

16. `test_refresh_without_jwt_returns_401` — unauthenticated POST /auth/refresh
17. `test_refresh_with_expired_jwt_returns_401` — expired JWT on POST /auth/refresh
18. `test_revoke_non_admin_own_sessions_allowed` — non-admin revoking own sessions returns 200
19. `test_revoke_non_admin_other_user_returns_403` — non-admin POSTing another user's UUID returns 403
20. `test_revoke_admin_cross_org_returns_404` — admin POSTing a user_id from a different org returns 404
21. `test_revoke_missing_user_id_returns_422` — POST /auth/revoke with empty body
22. `test_revoke_invalid_user_id_uuid_returns_422` — `user_id` is not a valid UUID
23. `test_concurrent_session_limit_evicts_oldest` — fourth login creates session, first session is deleted, exactly 3 remain

### Redis Failure Modes (4 tests)

24. `test_session_auth_fails_closed_when_redis_down` — Redis `ConnectionError`; session validation returns 401
25. `test_passphrase_auth_still_works_when_redis_down` — passphrase JWT auth succeeds when Redis raises `ConnectionError`
26. `test_oidc_authorize_fails_closed_when_redis_down` — Redis `ConnectionError` on state write returns 503
27. `test_oidc_callback_fails_closed_when_redis_down` — Redis `ConnectionError` on state read returns 503

### SSRF / Air-Gap (6 tests)

28. `test_metadata_endpoint_aws_blocked` — `169.254.169.254` rejected by `validate_oidc_issuer_url`
29. `test_metadata_endpoint_alibaba_blocked` — `100.100.100.200` rejected
30. `test_metadata_endpoint_gcp_hostname_blocked` — `metadata.google.internal` rejected
31. `test_loopback_issuer_blocked` — `127.0.0.1` rejected
32. `test_rfc1918_issuer_allowed_in_air_gap` — `http://10.0.0.1/` accepted by `validate_oidc_issuer_url`
33. `test_external_public_ip_rejected_in_production_mode` — public IP issuer in `CONCLAVE_ENV=production` rejected

### Rate Limiting (3 tests)

34. `test_authorize_endpoint_rate_limit_429_on_eleventh_request` — 11th request within the window returns 429
35. `test_callback_endpoint_rate_limit_429_on_eleventh_request` — 11th callback within the window returns 429
36. `test_rate_limit_resets_after_window` — requests succeed again after the rate-limit window expires

### Auth Middleware Exempt Paths (2 tests)

37. `test_authorize_reachable_without_jwt` — unauthenticated GET /auth/oidc/authorize is NOT rejected by auth middleware (returns something other than 401)
38. `test_callback_reachable_without_jwt` — unauthenticated GET /auth/oidc/callback is NOT rejected by auth middleware

### IdP Availability (3 tests)

39. `test_boot_fails_if_oidc_enabled_and_idp_unreachable` — startup raises `ConfigurationError` or equivalent when discovery endpoint returns connection refused
40. `test_boot_fails_if_oidc_enabled_and_discovery_doc_invalid` — startup raises when discovery document is not valid JSON or missing required fields
41. `test_boot_succeeds_if_oidc_not_configured` — absence of `OIDC_ENABLED=true` means startup proceeds without any IdP check

### Token Replay / Authorization Code (2 tests)

42. `test_authorization_code_replay_returns_401` — same authorization code submitted twice returns 401 on second use
43. `test_authorization_code_from_different_client_returns_401` — code issued to `client_id=A` is rejected when presented to `client_id=B`

### Input Validation (4 tests)

44. `test_callback_oversized_id_token_returns_413` — response body from IdP token endpoint exceeding 64KB causes 413 or safe truncation with 401
45. `test_revoke_user_id_sql_injection_returns_422` — `user_id` containing `'; DROP TABLE users; --` fails UUID validation with 422

---

## Commit Plan (expected 10-12 commits)

| # | Type | Description | Phase |
|---|------|-------------|-------|
| 1 | `test:` | `add negative/attack tests for SSO/OIDC` | ATTACK RED |
| 2 | `test:` | `add failing tests for SSO/OIDC feature` | RED |
| 3 | `docs:` | `add ADR-0067 OIDC integration` | T81.0 |
| 4 | `feat:` | `implement OIDC provider integration and SSRF exception` | GREEN T81.1 |
| 5 | `feat:` | `implement user provisioning and migration 011` | GREEN T81.2 |
| 6 | `feat:` | `implement session management` | GREEN T81.3 |
| 7 | `feat:` | `implement air-gap IdP configuration and runbook` | GREEN T81.4 |
| 8 | `refactor:` | `clean up SSO/OIDC implementation` | REFACTOR |
| 9 | `review:` | `address reviewer findings for SSO/OIDC` | REVIEW |
| 10 | `docs:` | `update documentation for SSO/OIDC` | DOCS |

---

## Quality Gates

All gates per `CLAUDE.md`. Two-gate policy (Rule 18) applies.

**Gate 1** (post-GREEN, after commit 7): full unit suite + integration suite must pass.
**Gate 2** (pre-merge): full unit suite + integration suite must pass.

Between gates: light gate only (changed-file tests + dependents + all static analysis).

Static analysis (ruff, mypy, bandit, vulture, pre-commit) runs at every checkpoint.

Coverage requirement: 95% minimum on `src/synth_engine/`.

Integration test infrastructure requirement: mock OIDC provider using `pytest-httpserver`
(per T81.0b). A unit test with a monkeypatched HTTP call does NOT satisfy the integration
test AC for the authorize → callback → JWT full flow.
