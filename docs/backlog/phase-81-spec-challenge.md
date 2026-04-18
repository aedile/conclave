# Phase 81 — Spec Challenge Results: SSO / OIDC Integration

**Challenger run date**: 2026-04-09
**Spec reviewed**: `docs/backlog/phase-81.md`
**Branch**: `feat/P81-sso-oidc-integration`

---

## Summary

The spec-challenger reviewed the Phase 81 spec against the RBAC permission matrix (ADR-0066),
the existing auth infrastructure (Phase 79 multi-tenancy, Phase 80 RBAC), the SSRF validator
in `shared/ssrf.py`, the Redis session namespace convention, and the air-gap operational
constraints documented in `CONSTITUTION.md`.

**16 missing acceptance criteria** were identified. PM decisions for each are recorded inline.
**45 negative tests** are required. **9 attack vectors** were surfaced. **6 configuration risks**
were identified.

---

## Missing Acceptance Criteria

### AC-1 — OIDC endpoints not added to AUTH_EXEMPT_PATHS

The spec does not call out that `/auth/oidc/authorize` and `/auth/oidc/callback` must be added
to `AUTH_EXEMPT_PATHS` (or equivalent exempt-paths mechanism). Without this, unauthenticated
users cannot initiate the OIDC flow — the middleware will reject them with 401 before the
OIDC router sees the request.

**PM decision**: Add both endpoints to `AUTH_EXEMPT_PATHS` in
`bootstrapper/dependencies/_exempt_paths.py`. The developer MUST add this to the implementation
checklist and write an attack test confirming an unauthenticated request to `/auth/oidc/authorize`
is forwarded (not rejected by auth middleware).

---

### AC-2 — No `last_login_at` field on the User model

T81.2 AC says "Subsequent logins update `last_login_at` timestamp" but the Phase 79 User model
has no such column. There is no migration specified.

**PM decision**: Add migration `011_add_last_login_at.py`. Column: nullable `datetime`, no
default. Developer creates `alembic/versions/011_add_last_login_at.py` and adds the field to
`shared/models/user.py`.

---

### AC-3 — No `oidc_sub` field and identity anchor ambiguity

The spec says "Email from OIDC `sub`/`email` claim used as user identifier" but does not
specify whether to store the IdP's `sub` claim. Email addresses can change at the IdP level.
Storing `sub` provides a stable anchor; omitting it makes email renames cause duplicate accounts.

**PM decision**: Email-only identity anchor for Tier 8. No `oidc_sub` column. If the IdP
changes the user's email, a new account is created. This is an accepted limitation, documented
in ADR-0067 and the OIDC runbook. Sub-based matching is deferred to Tier 9.

---

### AC-4 — State TTL unspecified

The spec says state must be stored in Redis with a TTL but does not specify what the TTL is.
An excessively long TTL widens the CSRF replay window; too short breaks slow users on
high-latency networks.

**PM decision**: Default TTL 600 seconds (10 minutes). Configurable via
`OIDC_STATE_TTL_SECONDS`. The developer must document the default in `.env.example`.

---

### AC-5 — PKCE `code_verifier` storage unspecified

The spec requires PKCE S256 but does not specify where `code_verifier` is stored between
the authorize request and the callback. Candidates: Redis alongside state, or in the state
value itself.

**PM decision**: Store `code_verifier` in the same Redis key as the state, as a JSON object:
`{"code_verifier": "...", "created_at": "<iso8601>"}`. Key: `conclave:oidc:state:<state_value>`.
Deleted atomically on first use. No separate key — one atomic read-and-delete protects both
the CSRF state and the PKCE verifier from replay.

---

### AC-6 — `POST /auth/refresh` auth model undefined

The spec lists a refresh endpoint but does not specify what credential is required to call it.
Options: a separate refresh token, the current JWT, or a session token.

**PM decision**: `POST /auth/refresh` requires a valid JWT via `get_current_user()`. Any role
is sufficient. Returns a new JWT with a fresh `exp`. Old JWT remains valid until natural expiry.
No token rotation. If OIDC is not configured, endpoint returns 404.

---

### AC-7 — `POST /auth/revoke` request body undefined

The spec mentions this endpoint but does not define the request body schema.

**PM decision**: Request body is `{"user_id": "<uuid>"}`. Deletes all Redis session keys for
the target user in the admin's org. Self-revocation is allowed without admin permission.
If OIDC is not configured, endpoint returns 404.

---

### AC-8 — Concurrent session limit eviction policy undefined

The spec says "default: 3, configurable" but does not specify what happens when the limit is
exceeded. Options: reject the new login, evict the oldest session, or evict the least-recently-used.

**PM decision**: Evict oldest session (by `created_at`). Write a test confirming that the
fourth concurrent login deletes the first session and issues a new one, leaving exactly 3 active.

---

### AC-9 — Default role for OIDC-provisioned users conflicts with RBAC matrix

The spec says "first OIDC login auto-creates a user with `viewer` role." The RBAC permission
matrix (ADR-0066) defines the roles as `admin`, `operator`, `auditor`. There is no `viewer`
role in the current matrix. The Phase 79 User model has `role: Role` typed to that enum.

**PM decision**: The spec's `viewer` maps to the existing `operator` role for provisioning
purposes only — the lowest-privilege named role available. This must be explicitly re-confirmed
with the role enum. If an actual `viewer` role was intended, ADR-0066 must be amended first.
Developer must verify the role enum before writing provisioning code and flag any mismatch.
Note: ADR-0066 amendment for `sessions:revoke` (AC-15 below) must happen in the same commit.

---

### AC-10 — No rate limiting on OIDC endpoints

The spec adds two new unauthenticated endpoints (`/auth/oidc/authorize` and
`/auth/oidc/callback`) but does not specify rate limiting. These endpoints call the IdP and
perform DB upserts — they are high-value DoS targets.

**PM decision**: Apply the same rate limit as `/auth/token`: 10 requests per minute per IP.
Developer must add this to the router decorator and write a test that verifies the 429 response
on the eleventh request.

---

### AC-11 — No audit events defined for OIDC operations

The spec does not list any audit events for OIDC flows. Per the existing audit infrastructure
(Phase 69+), all security-relevant actions must emit audit events before mutations (T68.3
pattern).

**PM decision**: Emit the following audit events:
- `OIDC_LOGIN_SUCCESS` — after successful callback and JWT issuance
- `OIDC_LOGIN_FAILURE` — on any callback error (state mismatch, token exchange failure, etc.)
- `USER_AUTO_PROVISIONED` — when a new user is created on first OIDC login
- `SESSION_CREATED` — when a new Redis session is written
- `SESSION_REFRESHED` — when `/auth/refresh` issues a new JWT
- `SESSION_REVOKED` — when `/auth/revoke` deletes sessions

All emitted before the associated mutation.

---

### AC-12 — Default org for auto-provisioned users undefined

The spec says "first OIDC login auto-creates a user in the default org" but does not define
how "default org" is resolved. In multi-tenant mode (Phase 79), orgs are explicit; there is
no built-in default.

**PM decision**: Introduce `OIDC_DEFAULT_ORG_ID` config setting. Required when
`CONCLAVE_MULTI_TENANT_ENABLED=true` AND OIDC is enabled. In single-tenant mode, use
`DEFAULT_ORG_UUID`. Config validation must raise a clear startup error if OIDC is enabled
in multi-tenant mode and `OIDC_DEFAULT_ORG_ID` is not set.

---

### AC-13 — SSRF validator blocks RFC-1918 but air-gap IdPs ARE on private IPs

The spec says "`OIDC_ISSUER_URL` validated through `shared/ssrf.py`" and separately says
"Air-gap constraint: the OIDC/SAML provider MUST be inside the security perimeter." These
two requirements are directly contradictory: the existing `shared/ssrf.py` blocks RFC-1918
addresses, but air-gap IdPs are deployed on RFC-1918 addresses.

**PM decision**: Create `validate_oidc_issuer_url()` in `shared/ssrf.py`. This function
ALLOWS RFC-1918 addresses (required for air-gap IdPs) but explicitly blocks cloud metadata
service endpoints:
- `169.254.169.254` (AWS/GCP/Azure IMDS)
- `100.100.100.200` (Alibaba Cloud IMDS)
- `metadata.google.internal`
- Loopback (`127.0.0.1/8`, `::1`) — an IdP on loopback is a test misconfiguration, not a
  production air-gap scenario

The existing `validate_url()` (which blocks RFC-1918) is preserved for all other uses.
Developer must write tests for both the allowed RFC-1918 case and the blocked metadata cases.

---

### AC-14 — No error response schema for OIDC browser-facing failures

OIDC callback failures (state mismatch, token exchange error, user in wrong org) will reach
the browser during the SSO flow. The spec does not define the error response format. Inconsistent
error shapes make frontend integration brittle and can leak information.

**PM decision**: All OIDC error paths return RFC 7807 Problem Details JSON
(`Content-Type: application/problem+json`). Cross-org email collision returns the generic
message "Authentication failed" — NOT "email exists in another org" — to prevent account
existence oracle attacks.

---

### AC-15 — No `sessions:revoke` permission in the RBAC matrix

The spec defines `POST /auth/revoke` (admin-only) but the ADR-0066 permission matrix has no
`sessions:revoke` permission. Adding an endpoint without a corresponding permission entry
violates the RBAC enforcement contract.

**PM decision**: Add `sessions:revoke` to `PERMISSION_MATRIX` with
`frozenset({Role.admin})`. Amend ADR-0066 in the same commit as the implementation. The
developer must update both the code and the ADR.

---

### AC-16 — No permission defined for `POST /auth/refresh`

`POST /auth/refresh` is listed as returning a new JWT for any authenticated user, but there
is no corresponding permission entry in ADR-0066 for this operation.

**PM decision**: `POST /auth/refresh` is a no-permission endpoint — it requires only
`get_current_user()` (i.e., a valid JWT). No RBAC check. This is consistent with the existing
`GET /auth/me` endpoint. Document this explicitly in ADR-0067 to prevent a future developer
from accidentally adding permission enforcement that locks out `viewer`-equivalent users.

---

## Required Negative Tests (45 total)

The following negative tests are MANDATORY. Each must be a distinct test function. Tests are
grouped by domain. All must be written before feature tests (ATTACK RED phase per Rule 22).

### OIDC State / CSRF Protection (6 tests)

1. `test_callback_missing_state_returns_422` — callback with no `state` query param
2. `test_callback_wrong_state_returns_401` — callback with state not in Redis
3. `test_callback_expired_state_returns_401` — callback after TTL has elapsed
4. `test_callback_state_replay_returns_401` — same callback URL submitted twice (state deleted on first use)
5. `test_callback_state_from_different_session_returns_401` — state belonging to a different concurrent user
6. `test_authorize_missing_code_challenge_returns_422` — authorize request without PKCE code_challenge

### PKCE Enforcement (4 tests)

7. `test_callback_missing_code_verifier_returns_422` — callback with no code_verifier
8. `test_callback_wrong_code_verifier_returns_401` — callback with verifier that does not match challenge
9. `test_pkce_plain_method_rejected` — code_challenge_method=plain (must require S256)
10. `test_implicit_flow_rejected` — response_type=token (implicit) is not supported

### User Provisioning (5 tests)

11. `test_cross_org_email_returns_401_generic_message` — existing user in org B tries OIDC in org A; returns "Authentication failed"
12. `test_cross_org_email_does_not_leak_org_existence` — response body for cross-org collision identical to general auth failure
13. `test_oidc_provisioned_user_has_correct_default_role` — auto-provisioned user has the role defined by the PM decision (AC-9)
14. `test_oidc_login_missing_email_claim_returns_401` — IdP token has no `email` claim
15. `test_oidc_login_empty_email_claim_returns_401` — IdP token has `email: ""`

### Session Management (8 tests)

16. `test_refresh_without_jwt_returns_401` — unauthenticated request to /auth/refresh
17. `test_refresh_with_expired_jwt_returns_401` — expired JWT on /auth/refresh
18. `test_revoke_non_admin_own_sessions_allowed` — non-admin revoking own sessions returns 200
19. `test_revoke_non_admin_other_user_returns_403` — non-admin attempting to revoke another user
20. `test_revoke_admin_cross_org_returns_404` — admin attempting to revoke user in a different org
21. `test_revoke_missing_user_id_returns_422` — /auth/revoke with no body
22. `test_revoke_invalid_user_id_uuid_returns_422` — user_id is not a valid UUID
23. `test_concurrent_session_limit_evicts_oldest` — fourth login evicts first session; exactly 3 remain

### Redis Failure Modes (4 tests)

24. `test_session_auth_fails_closed_when_redis_down` — Redis unavailable; session validation returns 401
25. `test_passphrase_auth_still_works_when_redis_down` — passphrase JWT auth succeeds when Redis is down
26. `test_oidc_authorize_fails_closed_when_redis_down` — cannot write state to Redis; returns 503
27. `test_oidc_callback_fails_closed_when_redis_down` — cannot read state from Redis; returns 503

### SSRF / Air-Gap (6 tests)

28. `test_metadata_endpoint_aws_blocked` — issuer URL 169.254.169.254 rejected by validate_oidc_issuer_url
29. `test_metadata_endpoint_alibaba_blocked` — issuer URL 100.100.100.200 rejected
30. `test_metadata_endpoint_gcp_hostname_blocked` — metadata.google.internal rejected
31. `test_loopback_issuer_blocked` — issuer URL 127.0.0.1 rejected
32. `test_rfc1918_issuer_allowed_in_air_gap` — issuer URL 10.x.x.x accepted by validate_oidc_issuer_url
33. `test_external_public_ip_rejected_in_production_mode` — public IP in CONCLAVE_ENV=production rejected

### Rate Limiting (3 tests)

34. `test_authorize_endpoint_rate_limit_429_on_eleventh_request` — 11th request within 1 min returns 429
35. `test_callback_endpoint_rate_limit_429_on_eleventh_request` — 11th callback request returns 429
36. `test_rate_limit_resets_after_window` — requests succeed again after the window expires

### Auth Middleware Exempt Path (2 tests)

37. `test_authorize_reachable_without_jwt` — unauthenticated GET /auth/oidc/authorize is not rejected by auth middleware
38. `test_callback_reachable_without_jwt` — unauthenticated GET /auth/oidc/callback is not rejected by auth middleware

### IdP Availability (3 tests)

39. `test_boot_fails_if_oidc_enabled_and_idp_unreachable` — startup raises on unreachable IdP
40. `test_boot_fails_if_oidc_enabled_and_discovery_doc_invalid` — startup raises on malformed discovery document
41. `test_boot_succeeds_if_oidc_not_configured` — no OIDC env vars; startup proceeds normally

### Token Replay / Authorization Code (2 tests)

42. `test_authorization_code_replay_returns_401` — same authorization code used twice
43. `test_authorization_code_from_different_client_returns_401` — code issued to a different client_id

### Input Validation (4 tests)

44. `test_callback_oversized_id_token_returns_413` — ID token body exceeding 64KB rejected
45. `test_revoke_user_id_sql_injection_returns_422` — user_id containing SQL injection chars fails UUID validation

---

## Attack Vectors

### AV-1 — SSRF via RFC-1918 OIDC issuer URL (non-air-gap deployments)

An operator could configure `OIDC_ISSUER_URL=http://10.0.0.1/admin` to make the application
server fetch internal services at boot and at every discovery refresh. The existing
`shared/ssrf.py` `validate_url()` function would block this — but AC-13 requires an exception
for air-gap deployments. The exception must be narrowly scoped. Any implementation that
fully disables SSRF checks for OIDC URLs creates an SSRF primitive.

**Required mitigation**: `validate_oidc_issuer_url()` blocks cloud metadata endpoints
explicitly; logs a security warning when an RFC-1918 issuer is accepted.

---

### AV-2 — State parameter prediction

If `state` is generated with a non-cryptographically-random source (e.g., `random.random()`),
an attacker can predict valid state values and forge CSRF callbacks.

**Required mitigation**: State MUST be generated with `secrets.token_urlsafe(32)`. Test must
assert the generator is `secrets.token_urlsafe`, not `random`.

---

### AV-3 — Open redirect via crafted `redirect_uri`

If the callback endpoint follows a redirect to a client-supplied URL after successful auth,
an attacker can supply `redirect_uri=https://evil.com` to exfiltrate the authorization code
or JWT.

**Required mitigation**: The PM decision (AC-11, post-callback response) eliminates browser
redirects entirely. The callback endpoint returns JSON, not a redirect. No `redirect_uri`
parameter is followed by the server. Test must confirm no `Location` header is present in
a successful callback response.

---

### AV-4 — JWT role escalation via IdP claims

If the OIDC callback trusts a `role` or `groups` claim from the IdP's ID token to set the
local user's role, an attacker who controls or compromises the IdP can escalate privileges.

**Required mitigation**: PM decision (AC-10 / PM decision 10 in the developer brief) explicitly
bans reading role claims from the IdP. Role is ALWAYS resolved from the local DB User record.
Test must verify that an ID token containing `role: admin` does NOT upgrade a `viewer`-role user.

---

### AV-5 — Session fixation via predictable Redis keys

If session keys are derived from the user ID (e.g., `conclave:session:<user_id>`), an attacker
who knows a target user's ID can pre-create a session key and wait for the victim to log in,
inheriting their session.

**Required mitigation**: Session key MUST be `conclave:session:<random_token>` where
`random_token` is generated with `secrets.token_urlsafe(32)`. The key must NOT embed the user
ID. Test must assert key format.

---

### AV-6 — Account takeover via cross-org email matching

If the OIDC callback matches users by email across all orgs (not just the current org), an
operator of org B who controls the IdP could authenticate as a user of org A by issuing
an ID token with that user's email.

**Required mitigation**: Email match MUST be scoped to `email + org_id`. The developer must
verify the DB query includes both predicates. Test: `test_cross_org_email_returns_401_generic_message`.

---

### AV-7 — Redis key namespace pollution via crafted state value

If the state value is used as a raw Redis key suffix without sanitization, an attacker can
supply a state like `conclave:session:victim_token` to read or overwrite a session key.

**Required mitigation**: State value must be validated as URL-safe base64 before use as a
key suffix. Reject any state containing `:` or other Redis key separator characters.
Test: `test_callback_state_with_colon_returns_422`.

---

### AV-8 — IdP impersonation via JWKS cache poisoning

The spec says JWKS are fetched once at boot and cached in memory. If the fetch occurs over
HTTP (not HTTPS), a network-path attacker can inject a forged JWKS document, allowing them
to issue arbitrary valid ID tokens.

**Required mitigation**: JWKS fetch MUST use HTTPS when `CONCLAVE_ENV=production`. In
development mode (`CONCLAVE_ENV=development`), HTTP is permitted for local test IdPs.
Log a CRITICAL warning if HTTP is used in production mode. Test must assert the HTTPS check.

---

### AV-9 — DoS via large ID token body

The OIDC token exchange endpoint makes an HTTP request to the IdP's token endpoint and reads
the response. An attacker-controlled IdP (or a misconfigured legitimate IdP) could return
a very large response body, causing excessive memory allocation.

**Required mitigation**: The HTTP client used for the token exchange MUST enforce a 64KB
response size limit. Test: `test_callback_oversized_id_token_returns_413`.

---

## Configuration Risks

### CR-1 — `OIDC_CLIENT_SECRET` in environment variable (production mode)

Docker secrets (`/run/secrets/`) are the required delivery mechanism in production. If
`OIDC_CLIENT_SECRET` is supplied as a plain environment variable in production, it will appear
in process listings, Docker inspect output, and crash dumps. The spec AC already calls this
out; the developer must implement the startup warning.

---

### CR-2 — `OIDC_DEFAULT_ORG_ID` missing in multi-tenant mode

If `CONCLAVE_MULTI_TENANT_ENABLED=true` and OIDC is enabled but `OIDC_DEFAULT_ORG_ID` is not
set, auto-provisioning will fail at runtime with an ambiguous error. Config validation must
catch this at startup, not at first login.

---

### CR-3 — `OIDC_STATE_TTL_SECONDS` set too high

An operator who misunderstands the setting could set `OIDC_STATE_TTL_SECONDS=86400`. This
widens the CSRF replay window to 24 hours. The config validator should enforce a maximum
value (suggested: 3600 seconds / 1 hour) and log a warning if the value exceeds 300 seconds.

---

### CR-4 — `SESSION_TTL_SECONDS` set to zero or negative

A misconfigured `SESSION_TTL_SECONDS=0` would result in sessions expiring immediately (or
never — depending on Redis behavior for TTL=0). Config validation must enforce
`SESSION_TTL_SECONDS >= 60`.

---

### CR-5 — OIDC enabled without Redis

Sessions require Redis. If `OIDC_ENABLED=true` and no Redis URL is configured, the
application will fail at runtime when the first OIDC login attempts to write a session.
Config validation must raise a startup error if OIDC is enabled but `REDIS_URL` is not set.

---

### CR-6 — `CONCURRENT_SESSION_LIMIT` set to zero

A `CONCURRENT_SESSION_LIMIT=0` would mean no sessions are ever allowed. Config validation
must enforce `CONCURRENT_SESSION_LIMIT >= 1`.

---

## Spec-Challenger Verdict

The Phase 81 spec is **conditionally ready** for developer implementation, pending incorporation
of the 16 missing ACs (all resolved by PM decisions above) into the developer brief. The 45
negative tests and 9 attack vectors are mandatory inputs to the ATTACK RED phase. The developer
brief MUST reference this document and list all 45 negative tests in its "Negative Test
Requirements" section.
