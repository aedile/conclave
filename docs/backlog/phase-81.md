# Phase 81 — SSO / OIDC Integration

**Tier**: 8 (Enterprise Scale)
**Goal**: Enable enterprise identity providers for authentication alongside the existing
JWT passphrase flow.

**Dependencies**: Phase 80 (RBAC — roles must exist for SSO-provisioned users)

---

## Context & Constraints

- Current auth: `POST /auth/token` with passphrase → JWT. Simple, works for single-operator.
- Enterprise customers use Okta, Azure AD, Keycloak, or internal OIDC/SAML providers.
- Air-gap constraint: the OIDC/SAML provider MUST be inside the security perimeter.
  No external IdP calls. The configuration must support internal IdP URLs.
- Passphrase auth must remain available as a fallback (air-gap scenarios where IdP is down).
- User provisioning: first OIDC login auto-creates a user in the default org with `viewer`
  role. Admin must explicitly upgrade to `operator` or `admin`.
- ADR required: OIDC vs SAML — recommend OIDC-first with SAML as a future phase.

---

## Tasks

### T81.1 — OIDC Provider Integration

**Files to create/modify**:
- `bootstrapper/routers/auth_oidc.py` (new)
- `bootstrapper/dependencies/oidc.py` (new)
- `shared/settings.py` (new OIDC config fields)
- ADR for OIDC selection

**Acceptance Criteria**:
- [ ] `GET /auth/oidc/authorize` — redirect to IdP with PKCE
- [ ] `GET /auth/oidc/callback` — token exchange, create/update local user, issue JWT
- [ ] OIDC discovery via `.well-known/openid-configuration` (internal URL)
- [ ] PKCE (S256) required — no implicit flow
- [ ] IdP URL must be configurable and validated (no external URLs in production mode)
- [ ] ADR documenting OIDC-first decision with SAML deferral

### T81.2 — User Provisioning on First Login

**Files to modify**:
- `bootstrapper/routers/auth_oidc.py`
- User model from Phase 79

**Acceptance Criteria**:
- [ ] First OIDC login creates user with `viewer` role in default org
- [ ] Subsequent logins update `last_login_at` timestamp
- [ ] Admin can pre-provision users with specific roles before first OIDC login
- [ ] Email from OIDC `sub`/`email` claim used as user identifier
- [ ] Duplicate email across orgs handled gracefully (user belongs to one org)

### T81.3 — Session Management

**Files to create/modify**:
- `bootstrapper/dependencies/sessions.py` (new)
- `shared/settings.py` (session config fields)

**Acceptance Criteria**:
- [ ] Token refresh endpoint: `POST /auth/refresh` (extends session without re-auth)
- [ ] Configurable session TTL (default: 8 hours)
- [ ] Concurrent session limit per user (default: 3, configurable)
- [ ] Session revocation: `POST /auth/revoke` (admin can revoke any user's sessions)
- [ ] All active sessions stored in Redis with TTL

### T81.4 — Air-Gap IdP Configuration

**Files to modify**:
- `shared/settings.py`
- `bootstrapper/config_validation.py`

**Acceptance Criteria**:
- [ ] `OIDC_ISSUER_URL` setting — must be internal network URL in production mode
- [ ] `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` via Docker secrets (not env vars)
- [ ] Config validation: if OIDC is enabled, IdP must be reachable at boot (fail-closed)
- [ ] If OIDC is not configured, passphrase auth remains the only option (backward compat)
- [ ] No external network calls from the OIDC flow — all validation local or to internal IdP

---

## Testing & Quality Gates

- Integration test: mock OIDC provider (in-process), full authorize → callback → JWT flow
- Attack tests: CSRF on callback, token replay, PKCE downgrade attempt
- Air-gap test: OIDC configured with unreachable external URL → boot fails
- Backward compatibility: all existing passphrase auth tests still pass
