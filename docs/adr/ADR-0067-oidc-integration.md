# ADR-0067: OIDC Integration for SSO

**Status**: Accepted
**Date**: 2026-04-09
**Phase**: 81 — SSO/OIDC Integration
**Supersedes**: None
**Superseded by**: None

---

## Context

Conclave Engine uses passphrase-based authentication (POST /auth/token with bcrypt-hashed
credentials). This is suitable for single-operator air-gapped deployments but inadequate for
multi-tenant deployments where:

1. Many users require authentication without sharing a single passphrase.
2. Organizations need to integrate with their existing Identity Provider (IdP).
3. Air-gapped deployments require an on-premises IdP accessible over RFC-1918 networks.

The multi-tenant RBAC system (ADR-0066) provides authorization infrastructure, but authentication
remains passphrase-only. SSO/OIDC integration completes the identity stack.

---

## Decision

### Library: `authlib >= 1.3.0, < 2.0.0` (pinned: 1.6.10 at implementation time)

**Rationale**:
- Pure Python implementation — no C extensions required (critical for air-gap bundle integrity;
  eliminates the need for platform-specific wheels in disconnected environments).
- Native PKCE S256 support (`AuthorizationCodeFlow` with `code_challenge_method="S256"`).
- Actively maintained with regular security releases.
- Minimal CVE surface relative to alternatives.
- No LangChain or external service dependencies.

**Alternatives considered and rejected**:
- `python-jose` + manual PKCE: requires manual PKCE implementation, higher error surface.
- `httpx-oauth`: fewer stars, less PKCE documentation, uncertain air-gap status.

### PKCE Requirement: S256 Only

Authorization Code Flow with PKCE S256 is the only supported flow. The plain PKCE method
(`code_challenge_method=plain`) is rejected because it provides no security benefit over
not using PKCE — the verifier is the same as the challenge. The implicit flow
(`response_type=token`) is rejected because it bypasses PKCE entirely.

### State and PKCE Storage in Redis

State and PKCE verifier are stored in a single Redis key:

```
Key:   conclave:oidc:state:<state_value>
Value: {"code_verifier": "<verifier>", "created_at": "<iso8601>"}
TTL:   600 seconds (default), configurable via OIDC_STATE_TTL_SECONDS (max: 3600)
```

The key is deleted atomically on first use (read-and-delete in a single pipeline). Any
subsequent request with the same state value returns 401. The state value is generated with
`secrets.token_urlsafe(32)` and validated as URL-safe base64 before use as a Redis key suffix
— values containing `:` or non-URL-safe characters are rejected.

### Session Architecture

Sessions exist only when OIDC is enabled. Passphrase auth remains stateless JWT with no
Redis session.

Session Redis key format:
```
Key:   conclave:session:<random_token>
Value: {"user_id": "<uuid>", "org_id": "<uuid>", "role": "<role_name>",
        "created_at": "<iso8601>", "last_refreshed_at": "<iso8601>"}
TTL:   28800 seconds (8 hours, default), configurable via SESSION_TTL_SECONDS (min: 60)
```

The key uses a random token (not derived from user_id) to prevent session fixation (AV-5).
Token generated with `secrets.token_urlsafe(32)`.

Concurrent session limit: 3 (default), configurable via `CONCURRENT_SESSION_LIMIT` (min: 1).
Eviction policy: when the limit is exceeded on a new login, the session with the earliest
`created_at` is deleted. The new session is then written.

Endpoints `/auth/refresh` and `/auth/revoke` return 404 when OIDC is not configured. This
prevents these endpoints from advertising their existence when sessions are not in use.

### JWKS Caching: Fetch at Boot, No Runtime Refresh

JWKS are fetched once at application startup and cached in memory for the process lifetime.
If the IdP rotates signing keys, an application restart is required.

**Accepted operational limitation**: This simplifies the implementation significantly while
remaining operationally viable in air-gapped environments where key rotation is a planned
maintenance event. The restart procedure is documented in `docs/runbooks/oidc-idp-unavailable.md`.

The fetch uses HTTPS when `CONCLAVE_ENV=production`. HTTP is permitted only in development mode
(for local test IdPs). A CRITICAL-level security log message is emitted if HTTP is used in
production mode.

### Air-Gap SSRF Exception: RFC-1918 Allowed for OIDC Issuers

The existing `validate_callback_url()` in `shared/ssrf.py` blocks all RFC-1918 addresses.
Air-gap IdPs are deployed on RFC-1918 addresses. These facts are irreconcilable.

A new function `validate_oidc_issuer_url()` is added to `shared/ssrf.py` with different rules:

**Always blocked** (in all environments):
- `169.254.169.254` (AWS/GCP/Azure IMDS)
- `100.100.100.200` (Alibaba Cloud IMDS)
- `metadata.google.internal`
- Loopback: `127.0.0.0/8` and `::1`

**Allowed** (for air-gap IdPs):
- RFC-1918 ranges: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- A WARNING-level security notice is logged when an RFC-1918 issuer is accepted.

**Blocked in production mode** (`CONCLAVE_ENV=production`):
- Public IP addresses (same restriction as `validate_callback_url()`)

The existing `validate_callback_url()` is NOT modified.

### Identity Anchor: Email Only (No `oidc_sub` Column)

Email is the sole identity anchor for OIDC users at Tier 8. No `oidc_sub` column is added.
Matching is by `email + org_id`.

**Accepted limitation**: If the IdP changes a user's email address, the next OIDC login
creates a new user account. The old account is orphaned until an admin deletes it.
Sub-based stable identity matching is deferred to Tier 9.

### IdP Role Claims: Ignored (DB-Authoritative)

The OIDC callback must NOT read any `role`, `groups`, `permissions`, or equivalent claim from
the IdP's ID token to set or modify the local user's role. Role is ALWAYS resolved from the
local DB `users.role` column. Auto-provisioned users get `operator` role (lowest privilege)
regardless of what the IdP token claims.

**Security rationale**: If IdP role claims were respected, a compromised IdP or a misconfigured
claim mapping could elevate any user to admin. The DB is the authoritative source of truth for
authorization, independent of authentication.

### Post-Callback Response: JSON, Not Redirect

The OIDC callback endpoint (`GET /auth/oidc/callback`) returns:
```json
{"access_token": "<jwt>", "token_type": "bearer", "expires_in": <seconds>}
```

No browser redirect. No `Location` header. The frontend SPA owns the OIDC flow and reads
this JSON response to store the token. This eliminates the open redirect attack surface.

### Default Role for Auto-Provisioned Users

Auto-provisioned users receive `Role.operator` (the lowest-privilege role in the RBAC enum).
This prevents privilege escalation via OIDC provisioning. Admins must explicitly elevate users
who require higher permissions.

### Error Responses: RFC 7807 Problem Details

All OIDC error paths return `Content-Type: application/problem+json` with RFC 7807 shape.
Cross-org email collision detail is always `"Authentication failed"` — the generic message
prevents oracle attacks that would reveal email existence in another org.

### Token Exchange Response Size Limit: 64KB

The HTTP client used for the IdP token exchange is configured with a 64KB response size limit.
This prevents memory exhaustion from a rogue or misconfigured IdP.

---

## Rate Limiting

Both `GET /auth/oidc/authorize` and `GET /auth/oidc/callback` are rate-limited at
10 requests per minute per IP — the same limit as `POST /auth/token`.

---

## Audit Events

| Event Name | Trigger |
|---|---|
| `OIDC_LOGIN_SUCCESS` | JWT issued after successful callback |
| `OIDC_LOGIN_FAILURE` | Any error in the callback flow |
| `USER_AUTO_PROVISIONED` | New user created on first OIDC login |
| `SESSION_CREATED` | New Redis session written |
| `SESSION_REFRESHED` | `/auth/refresh` issues new JWT |
| `SESSION_REVOKED` | `/auth/revoke` deletes sessions |

All events are emitted BEFORE the associated mutation (T68.3 pattern). If the audit write
fails, the endpoint returns 500 and no mutation occurs.

---

## Accepted Limitations (Tier 8)

1. **Email identity anchor**: Email change at IdP creates orphaned old account.
2. **JWKS refresh**: Key rotation requires application restart.
3. **Concurrent session limit**: Enforced per-user, not per-device; device tracking is Tier 9.
4. **Single default org for auto-provisioning**: Multi-IdP multi-org routing is Tier 9.
5. **No OIDC sub-based identity**: Deferred to Tier 9.

---

## Consequences

### Positive
- Multi-tenant deployments can use existing corporate IdPs (Keycloak, Okta, Azure AD).
- Air-gapped deployments can use on-premises IdPs on RFC-1918 networks.
- PKCE S256 protects against authorization code interception.
- Session management provides explicit revocation capability (absent with stateless JWT).
- DB-authoritative roles prevent IdP compromise from escalating privileges.

### Negative
- Redis becomes a required dependency when OIDC is enabled.
- Application restart required for JWKS key rotation.
- Email address changes at IdP create orphaned accounts.

---

## Implementation Notes

- `authlib` version confirmed on PyPI: 1.6.10 (current stable at implementation time).
- `pytest-httpserver` added to dev dependencies for integration tests with mock OIDC provider.
- Migration 011 adds `last_login_at: datetime | None` to the `users` table.
- `sessions:revoke` added to PERMISSION_MATRIX in `bootstrapper/dependencies/permissions.py`
  (admin only, per ADR-0066 amendment).
- OIDC router registered in `router_registry.py` in the same commit it is created (Rule 8).
