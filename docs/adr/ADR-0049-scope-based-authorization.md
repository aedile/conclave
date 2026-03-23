# ADR-0049: Scope-Based Authorization Model

**Status**: Accepted
**Date**: 2026-03-23
**Deciders**: Engineering team
**Task**: T47.1 — Scope-based auth for security endpoints; T47.3 — Scope-based auth for settings write endpoints

---

## Context

After T39.1 introduced JWT Bearer token authentication, all authenticated routes
verified only that a valid token was present — any authenticated operator could call
any endpoint.  This coarse model is insufficient for operations with distinct risk
profiles:

- **Cryptographic shred** (`POST /security/shred`) — permanently destroys the vault
  KEK and renders all ALE-encrypted database columns unrecoverable.  This is an
  emergency operation that must be protected beyond simple "is authenticated."
- **Key rotation** (`POST /security/keys/rotate`) — enqueues an expensive background
  re-encryption job.  Uncontrolled access could cause repeated re-encryption storms.
- **Settings writes** (`PUT /settings/{key}`, `DELETE /settings/{key}`) — mutate
  application behavior at runtime.  Read access is safely broad; write access is not.

The Conclave Engine is a **single-operator system** in the MVP — one configured
operator identity exists, provisioned via `OPERATOR_CREDENTIALS_HASH`.  Multi-operator
support (per-operator permission assignment, role registries) is deferred to a future
phase.

---

## Decision

Implement **JWT-embedded scope-based authorization** with the following design:

### 1. Scope representation in the JWT

The `scope` claim is a **list of strings** embedded directly in the token:

```json
{
  "sub": "operator-1",
  "iat": 1700000000,
  "exp": 1700003600,
  "scope": ["read", "write", "security:admin", "settings:write"]
}
```

A bare string scope claim (e.g. `"scope": "security:admin"`) is **unconditionally
rejected** — this eliminates a substring injection vector where
`"security:admin" in "security:admin"` evaluates `True` for string containment
but the claim is not a valid list.

### 2. `require_scope()` dependency factory

Authorization is enforced by a FastAPI dependency factory in
`bootstrapper/dependencies/auth.py`:

```python
@router.post("/security/shred")
async def shred_vault(
    current_operator: Annotated[str, Depends(require_scope("security:admin"))],
) -> JSONResponse: ...
```

`require_scope(scope)` returns a `_check_scope` dependency that:

1. Resolves `get_current_operator` first (authentication gate — ensures a valid JWT
   was presented before any authorization check).
2. Re-reads the JWT claims from the `Authorization` header.
3. Verifies `scope` claim is a `list` (rejects bare strings).
4. Checks exact list membership: `scope_string in raw_scope_list`.
5. Returns the operator `sub` on success; raises HTTP 403 on failure.

### 3. Defined scopes

| Scope | Purpose | Endpoints |
|-------|---------|-----------|
| `read` | Read access to non-sensitive resources | General read endpoints |
| `write` | Write access to non-sensitive resources | Job creation, artifact download |
| `security:admin` | High-privilege cryptographic operations | `POST /security/shred`, `POST /security/keys/rotate` |
| `settings:write` | Runtime settings mutation | `PUT /settings/{key}`, `DELETE /settings/{key}` |

### 4. Default scope issuance (single-operator model)

The `POST /auth/token` endpoint issues all four scopes to any authenticated operator:

```python
_DEFAULT_OPERATOR_SCOPES = ["read", "write", "security:admin", "settings:write"]
```

In the single-operator MVP, one configured identity holds every permission.
Future multi-operator support would require per-operator scope assignment at
registration time (tracked as a post-T47 backlog item).

### 5. Pass-through mode compatibility

When `jwt_secret_key` is empty (development / unconfigured mode), `require_scope()`
bypasses the scope check — consistent with `AuthenticationGateMiddleware` and
`get_current_operator` pass-through behavior.  This preserves the existing
development workflow without credentials.

### 6. Module boundary

The authorization logic lives exclusively in
`bootstrapper/dependencies/auth.py`.  The `shared/` module has zero FastAPI
imports.  This maintains the modular monolith boundary defined in ADR-0001.

---

## Consequences

### Positive

- High-value destructive operations (`/security/shred`, `/security/keys/rotate`)
  require an explicit `security:admin` scope — authentication alone is insufficient.
- Runtime settings mutations require `settings:write` — read-only observability
  remains broadly available to any authenticated operator.
- Scope enforcement is **stateless** — no database lookup required at authorization
  time; the token carries all necessary claims.
- The bare-string injection vector is structurally blocked by the `isinstance(list)`
  guard.
- Pass-through compatibility preserves zero-friction development workflow.

### Negative / Constraints

- **Single-operator model**: the MVP issues all scopes to the one configured
  operator.  There is no mechanism to issue a token with a subset of scopes —
  an operator either has all permissions or none.
- **No scope revocation**: scopes are embedded in the JWT and cannot be revoked
  without invalidating the token (no revocation list in MVP).  The token TTL
  (`jwt_expiry_seconds`, default 1 hour) bounds the window of unauthorized access
  after a scope change.
- **Future multi-operator friction**: adding per-operator scope assignment requires
  either a scope registry in the database or a separate token issuance flow.  The
  `require_scope()` enforcement layer is already correct; only issuance must change.
- **Token re-verification**: `require_scope()` re-decodes the JWT to read scope
  claims, even though `get_current_operator` already verified the signature.  This
  is a minor overhead chosen for correctness over caching (ADR-0039 alignment).

---

## Alternatives Considered

| Option | Rejected Because |
|--------|-----------------|
| Role-based access control (RBAC) with DB roles | Requires operator registry and DB lookups per request — over-engineered for a single-operator MVP. |
| Separate high-privilege tokens (no scopes) | No standard claim representation; more token types to manage and document. |
| Middleware-level scope enforcement (not route-level) | Middleware cannot inspect route-specific required scopes without coupling the middleware to route metadata — violates separation of concerns. |
| `scope` as a space-delimited string (OAuth2 convention) | Invites substring injection bugs; list representation is unambiguous and avoids splitting logic. |

---

## References

- RFC 6750 — OAuth 2.0 Bearer Token Usage
- RFC 7519 — JSON Web Token (JWT) — `scope` claim conventions
- OWASP Authorization Testing Guide — injection attack vectors
- `src/synth_engine/bootstrapper/dependencies/auth.py` — `require_scope()` implementation
- `src/synth_engine/bootstrapper/routers/auth.py` — `_DEFAULT_OPERATOR_SCOPES` issuance
- `src/synth_engine/bootstrapper/routers/security.py` — `security:admin` usage
- `src/synth_engine/bootstrapper/routers/settings.py` — `settings:write` usage
- ADR-0039 — JWT Bearer Token Authentication (authentication layer this ADR extends)
