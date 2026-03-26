# ADR-0057: API Versioning Strategy — Prefix-Based /api/v1/ for Business Routes

**Status:** Accepted
**Date:** 2026-03-26
**Deciders:** PM, Architecture Reviewer, Red-team Reviewer
**Task:** P59-T59.1 — API Versioning

---

## Context

As the Conclave Engine approaches v1.0 production release, the API requires a
stable, versioned contract for external clients and the frontend. Without
versioning, any structural change to a route path is a breaking change for all
callers. The system has two categories of endpoints:

1. **Business-logic routes** — synthesis jobs, connections, settings, webhooks,
   privacy budget, admin, compliance. These are the primary API surface that
   clients and the frontend consume.

2. **Infrastructure routes** — health probes, vault unseal, auth token issuance,
   license activation, cryptographic security operations. These are bootstrapping
   and operations endpoints used by middleware, orchestrators, and the unseal UI.

The infrastructure routes are referenced in middleware exempt-path sets
(`COMMON_INFRA_EXEMPT_PATHS`, `SEAL_EXEMPT_PATHS`, `AUTH_EXEMPT_PATHS`). Moving
them under a version prefix would require updating every middleware path-matching
expression — a high-risk, low-value change for endpoints that are intentionally
stable and unversioned.

---

## Decision

All **business-logic routes** are registered under a parent `APIRouter(prefix="/api/v1")`
in `bootstrapper/router_registry.py`. Each child router retains its existing prefix
(e.g. `/jobs`), so the combined route becomes `/api/v1/jobs`, `/api/v1/connections`, etc.

**Infrastructure routes** remain at the root path. This keeps middleware exempt-path
matching simple and backward-compatible:

| Route type | Path prefix | Example |
|------------|-------------|---------|
| Business-logic | `/api/v1/` | `GET /api/v1/jobs` |
| Auth bootstrapping | `/auth/` | `POST /auth/token` |
| Vault operations | `/unseal`, `/security/` | `POST /unseal`, `POST /security/shred` |
| Licensing | `/license/` | `POST /license/activate` |
| Health/readiness | `/health`, `/ready` | `GET /health` |

**Security invariant:** No `/api/v1/` path may appear in any exempt-paths set
(`COMMON_INFRA_EXEMPT_PATHS`, `SEAL_EXEMPT_PATHS`, `AUTH_EXEMPT_PATHS`).
This is enforced programmatically by
`tests/integration/test_api_versioning_attack.py::test_versioned_routes_not_in_exempt_paths`.

**Frontend client alignment:** The frontend `client.ts` and `useSSE.ts` use
`/api/v1/` prefixed paths for all business-logic calls. The Vite dev proxy
forwards `/api/*` to the backend **without any path rewrite** — the full
`/api/v1/jobs` path passes through intact to FastAPI.

**Middleware path-matching:** Middleware that performs path matching
(`AuthenticationGateMiddleware`, `SealGateMiddleware`, `LicenseGateMiddleware`)
operates on the full request path including the `/api/v1/` prefix. The exempt-path
sets only contain infrastructure paths (no `/api/v1/` entries), so all
business-logic routes are correctly subject to auth and seal gates.

---

## Consequences

**Positive:**

- Clear separation between stable infrastructure paths and versioned business API.
- Client contracts are versioned — future `/api/v2/` routes can coexist without
  breaking `/api/v1/` consumers.
- Middleware exempt-path logic remains unchanged and simple.
- The security invariant (no versioned path in exempt sets) is programmatically
  enforced by an integration test — not a convention.
- The OpenAPI spec at `/docs` correctly shows `/api/v1/` paths for all business
  routes, giving operators an accurate contract to code against.

**Negative / Constraints:**

- Clients that hard-coded unversioned paths (e.g. `/jobs`) must be updated.
  The frontend `client.ts` was updated as part of T59.1.
- SSE connections in `useSSE.ts` must also use `/api/v1/jobs/{id}/stream`.
- The Vite proxy rewrite (`path.replace(/^\/api/, "")`) was intentionally
  removed — it would strip the `/api` prefix and turn `/api/v1/jobs` into
  `/v1/jobs`, which is not a registered route. The proxy now passes paths
  through unchanged.

---

## Alternatives Considered

**Header-based versioning (`Accept: application/vnd.conclave.v1+json`):**
Rejected. Header-based versioning is invisible in browser URLs and harder to
route in nginx/reverse proxies. It also cannot be enforced by route registration
alone — FastAPI would require middleware to dispatch based on headers.

**URL segment versioning at the app level (`app = FastAPI(root_path="/api/v1")`):**
Rejected. `root_path` in FastAPI is intended for ASGI-level path prefixes (e.g.
behind a reverse proxy strip). Using it for versioning would affect the OpenAPI
spec base path and break infrastructure routes that deliberately live at root.

**Per-router versioning (each router owns `/api/v1/<resource>` path):**
Rejected. Duplicating `/api/v1` into every router creates copy-paste maintenance
burden and makes it impossible to distinguish the version prefix from the resource
path by inspection. The parent `APIRouter` approach is DRY and mechanical.

---

## References

- `src/synth_engine/bootstrapper/router_registry.py` — `_include_routers()` implementation
- `tests/integration/test_api_versioning_attack.py` — programmatic enforcement tests
- `frontend/src/api/client.ts` — frontend client updated to `/api/v1/` paths
- `frontend/src/hooks/useSSE.ts` — SSE hook updated to `/api/v1/jobs/{id}/stream`
- `frontend/vite.config.ts` — Vite proxy without path rewrite
- ADR-0009 — Authentication exempt paths (COMMON_INFRA_EXEMPT_PATHS authoritative source)
