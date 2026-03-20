# ADR-0039: JWT Bearer Token Authentication

**Status**: Accepted
**Date**: 2026-03-20
**Task**: T39.1 — Add Authentication Middleware (JWT Bearer Token)
**Deciders**: Engineering team

---

## Context

The Conclave Engine API exposed all endpoints without authentication, relying entirely on network-level access controls. This is insufficient for a production air-gapped data pipeline — operators need a cryptographically verified identity mechanism to ensure that only authorized principals can trigger synthesis jobs, read connection credentials, or shred vault artifacts.

The primary threats are:

1. **Unauthorized access** — any process on the host can reach the API without credentials.
2. **Algorithm confusion attacks** — a naive JWT implementation accepting multiple algorithms (or `alg:none`) can be exploited to forge tokens.
3. **Credential brute-forcing** — plain-text password storage or weak comparison allows offline or timing attacks.

---

## Decision

Implement **HS256 JWT Bearer Token authentication** with the following architectural choices:

### 1. Algorithm pinning (security critical)

The JWT algorithm is pinned to `ConclaveSettings.jwt_algorithm` (default `"HS256"`). `pyjwt.decode()` is called with `algorithms=[settings.jwt_algorithm]` — a single-element list. This means:

- `alg:none` tokens are unconditionally rejected (PyJWT raises `InvalidAlgorithmError`).
- RS256/RS384/RS512 tokens presented to an HS256-configured service are rejected.
- The operator cannot be tricked into accepting a downgraded or substituted algorithm by crafting a malicious JWT header.

### 2. HMAC secret key (`jwt_secret_key`)

HMAC-SHA256 symmetric signing was chosen over asymmetric RSA/EC for the MVP. Rationale:

- **Air-gapped simplicity**: no PKI or certificate authority is required.
- **Single-operator model**: in the MVP, one operator identity is registered; asymmetric signing adds key management complexity without a concrete benefit at this stage.
- **Forward compatibility**: the `jwt_algorithm` setting can be changed to `RS256`/`ES256` in a future task; `verify_token()` already reads the algorithm from settings at every call.

The secret key must be a cryptographically random string of ≥ 32 characters. An empty key is only acceptable in development/test environments (the middleware runs in **pass-through mode** when `jwt_secret_key` is empty — see below).

### 3. bcrypt credential hashing

Operator passphrases are stored as bcrypt hashes in `ConclaveSettings.operator_credentials_hash`. Verification uses `bcrypt.checkpw()` which:

- Provides constant-time comparison (prevents timing oracle attacks).
- Uses a per-hash cost factor (work factor embedded in the `$2b$` prefix).
- Rejects the hash immediately if no hash is configured, preventing empty-hash bypass.

The `passlib` library was evaluated and rejected: `passlib.hash.bcrypt` is incompatible with `bcrypt 5.0.0` (missing `__about__` attribute). The `bcrypt` library is used directly.

### 4. Middleware placement — INNERMOST gate

`AuthenticationGateMiddleware` is registered first in `setup_middleware()` (innermost in LIFO evaluation), meaning it fires last on the request path — after `SealGateMiddleware` and `LicenseGateMiddleware` have already passed the request.

This ordering is intentional:

- The vault must be unsealed before authentication can succeed (tokens could be stored in the vault in a future task).
- The license must be valid before the service accepts any authenticated work.
- Authentication is the final gate before the route handler.

### 5. Pass-through mode (development safety valve)

When `jwt_secret_key` is empty (the default setting), `AuthenticationGateMiddleware` allows all requests through with a `WARNING` log. This:

- Prevents regression in the entire existing test suite (14+ test files use `create_app()` without JWT configuration).
- Allows the application to start and be accessed before JWT credentials are provisioned.
- Is explicitly documented as **NOT suitable for production** — the WARNING log reminds operators.

A production health check should verify that `jwt_secret_key` is non-empty.

### 6. Module boundary

The FastAPI/Starlette coupling lives exclusively in `bootstrapper/dependencies/auth.py` and `bootstrapper/routers/auth.py`. The `shared/` module has zero FastAPI imports, maintaining the modular monolith boundary.

---

## Consequences

### Positive

- All non-exempt endpoints are protected by a cryptographically signed token.
- Algorithm confusion and `alg:none` attacks are structurally impossible.
- Credential verification is constant-time.
- The middleware stack is self-documenting (LIFO ordering in `middleware.py` docstring).
- Pass-through mode eliminates test regression risk during the migration period.

### Negative / Trade-offs

- **Single-operator model**: only one operator identity is supported in the MVP. Multi-operator support requires a vault-backed operator registry (deferred backlog item).
- **Symmetric HMAC**: if the `jwt_secret_key` is leaked, all outstanding tokens are compromised and the only remediation is key rotation plus token revocation (no revocation list in MVP).
- **Pass-through mode risk**: a misconfigured production deployment with an empty `jwt_secret_key` is silently open. Future hardening: add a production-mode startup assertion that rejects an empty key when `is_production()` is true.

### Exempt paths

The following paths bypass authentication by definition (pre-auth bootstrapping endpoints):

- `/unseal`, `/health`, `/metrics` — infrastructure / liveness
- `/docs`, `/redoc`, `/openapi.json` — API documentation
- `/license/challenge`, `/license/activate` — license activation flow
- `/security/shred`, `/security/keys/rotate` — vault shred and key rotation
- `/auth/token` — token issuance (must be pre-auth so operators can log in)

---

## Alternatives Considered

| Option | Rejected Because |
|--------|-----------------|
| API key (static header) | No expiry, no revocation granularity, no standard claim structure. |
| mTLS client certificates | Requires PKI infrastructure — incompatible with air-gapped simplicity requirement. |
| OAuth2 Authorization Code | Requires external authorization server — violates air-gapped constraint. |
| asymmetric JWT (RS256) | No PKI benefit in single-operator MVP; symmetric HMAC is simpler and equally secure for this threat model. Can be adopted later by changing `jwt_algorithm`. |

---

## References

- RFC 7519 — JSON Web Token (JWT)
- RFC 6750 — OAuth 2.0 Bearer Token Usage
- RFC 7807 — Problem Details for HTTP APIs
- OWASP JWT Security Cheat Sheet — algorithm pinning requirement
- `src/synth_engine/bootstrapper/dependencies/auth.py`
- `src/synth_engine/bootstrapper/routers/auth.py`
- `src/synth_engine/bootstrapper/middleware.py`
