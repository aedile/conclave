# Phase 81 — SSO / OIDC Integration

**Tier**: 8 (Enterprise Scale)
**Goal**: Enable enterprise identity providers for authentication alongside the existing
JWT passphrase flow.

**Dependencies**: Phase 80 (RBAC — roles must exist for SSO-provisioned users)

---

## Prerequisites

### T81.0 — OIDC Library ADR (Rule 6)

Select the OIDC Python library before implementation begins. Candidates: `authlib`,
`python-jose` + manual flow, `httpx-oauth`. ADR must document:
- Library selection with rationale
- Air-gap bundle impact (C dependencies? Pure Python?)
- CVE surface assessment
- Whether the library handles PKCE S256 natively or requires manual implementation

### T81.0b — Mock OIDC Provider Test Infrastructure

Building an in-process mock OIDC provider that serves `.well-known/openid-configuration`,
handles PKCE S256 `code_verifier`, issues JWKS, and issues ID tokens is a substantial test
infrastructure task. This must be acknowledged as a prerequisite before T81.1 tests can
be written. Consider: `pytest-httpserver` or a custom fixture.

---

## Context & Constraints

- Current auth: `POST /auth/token` with passphrase → JWT. Simple, works for single-operator.
- Enterprise customers use Okta, Azure AD, Keycloak, or internal OIDC/SAML providers.
- Air-gap constraint: the OIDC/SAML provider MUST be inside the security perimeter.
  No external IdP calls. The configuration must support internal IdP URLs.
- **Boot-time HTTP call**: OIDC discovery requires `GET /.well-known/openid-configuration`
  from the application server to the IdP at startup. This is a network call in an air-gapped
  environment. If the IdP is unreachable, the application will not start (fail-closed).
  This is the primary operational consequence of enabling OIDC and must be documented
  prominently in the Operator Manual, not just in an AC line.
- Passphrase auth must remain available as a fallback (air-gap scenarios where IdP is down).
- User provisioning: first OIDC login auto-creates a user in the default org with `viewer`
  role. Admin must explicitly upgrade to `operator` or `admin`.
- **Email uniqueness**: Email is globally unique across the system. A user belongs to exactly
  one org. If a user needs access to multiple orgs, the admin creates separate user accounts
  with different emails. This is simpler than multi-org membership and avoids the JWT
  role-per-org complexity.
- **Redis session failure mode**: Sessions stored in Redis. If Redis is unavailable:
  fail-closed (no authentication possible via sessions; passphrase auth still works as
  fallback since it issues stateless JWTs). This must be explicitly tested.
- Session storage uses Redis key namespace `conclave:session:` to avoid collisions with
  Huey (`huey:`) and circuit breaker (`conclave:cb:`) keys.

---

## Tasks

### T81.1 — OIDC Provider Integration

**Files to create/modify**:
- `bootstrapper/routers/auth_oidc.py` (new)
- `bootstrapper/dependencies/oidc.py` (new)
- `shared/settings.py` (new OIDC config fields)

**Acceptance Criteria**:
- [ ] `GET /auth/oidc/authorize` — redirect to IdP with PKCE (S256)
- [ ] `GET /auth/oidc/callback` — token exchange, create/update local user, issue JWT
- [ ] OIDC discovery via `.well-known/openid-configuration` (internal URL)
- [ ] PKCE (S256) required — no implicit flow
- [ ] OAuth2 `state` parameter: authorize request generates a cryptographically random
      `state` stored in Redis with TTL; callback rejects requests where `state` does not
      match the stored value (CSRF protection)
- [ ] `OIDC_ISSUER_URL` validated through `shared/ssrf.py` at startup: external
      (non-RFC-1918, non-loopback) URLs rejected in `CONCLAVE_ENV=production` mode
- [ ] IdP reachability check at boot: full `.well-known/openid-configuration` fetch
      (not just TCP connect) — fail-closed if discovery document is invalid or unreachable
- [ ] `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` via Docker secrets in production.
      If `CONCLAVE_ENV=production` and `OIDC_CLIENT_SECRET` is supplied as an environment
      variable rather than via `/run/secrets/`, log a CRITICAL security warning at startup.
- [ ] `.env.example` updated with `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`

### T81.2 — User Provisioning on First Login

**Files to modify**:
- `bootstrapper/routers/auth_oidc.py`
- User model from Phase 79

**Acceptance Criteria**:
- [ ] First OIDC login creates user with `viewer` role in default org
- [ ] Subsequent logins update `last_login_at` timestamp
- [ ] Admin can pre-provision users with specific roles before first OIDC login
- [ ] Email from OIDC `sub`/`email` claim used as user identifier
- [ ] Duplicate email on OIDC login: if the email already exists in the system, the login
      maps to the existing user account (same org). If the email exists in a different org,
      the login is rejected with 403 and a clear error message. Test required.
- [ ] Add runbook: `docs/runbooks/oidc-idp-unavailable.md` — steps for recovering when
      IdP is unreachable (fallback to passphrase auth, manual user provisioning)

### T81.3 — Session Management

**Files to create/modify**:
- `bootstrapper/dependencies/sessions.py` (new)
- `shared/settings.py` (session config fields)

**Acceptance Criteria**:
- [ ] Token refresh endpoint: `POST /auth/refresh` (extends session without re-auth)
- [ ] Configurable session TTL (default: 8 hours)
- [ ] Concurrent session limit per user (default: 3, configurable)
- [ ] Session revocation: `POST /auth/revoke` (admin can revoke any user's sessions in
      their org; non-admin attempting to revoke another user's session returns 403)
- [ ] All active sessions stored in Redis with TTL, key namespace `conclave:session:`
- [ ] Redis-down behavior: session validation fails closed (401); passphrase JWT auth
      continues to work as stateless fallback. Integration test required.
- [ ] `.env.example` updated with session config variables

### T81.4 — Air-Gap IdP Configuration

**Files to modify**:
- `shared/settings.py`
- `bootstrapper/config_validation.py`

**Acceptance Criteria**:
- [ ] `OIDC_ISSUER_URL` setting — validated through `shared/ssrf.py` (reuse existing module)
- [ ] `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` via Docker secrets (not env vars)
- [ ] Config validation: if OIDC is enabled, IdP must be reachable at boot (fail-closed)
- [ ] If OIDC is not configured, passphrase auth remains the only option (backward compat)
- [ ] No external network calls from the OIDC flow — all validation local or to internal IdP

---

## Testing & Quality Gates

- Integration test: mock OIDC provider (in-process via T81.0b), full authorize → callback → JWT flow
- Attack tests: CSRF on callback (missing/wrong `state` parameter → rejected)
- Attack tests: token replay (same authorization code used twice → rejected)
- Attack tests: PKCE downgrade attempt (no `code_verifier` → rejected)
- Attack tests: non-admin user attempts `POST /auth/revoke` for another user → 403
- Attack tests: duplicate email cross-org OIDC login → 403 with clear error
- Air-gap test: OIDC configured with unreachable external URL → boot fails
- Air-gap test: OIDC issuer URL is external IP → rejected by SSRF validator
- Redis-down test: Redis unavailable → session auth fails closed (401), passphrase works
- Backward compatibility: all existing passphrase auth tests still pass
- All existing integration tests must pass with the new auth dependency
