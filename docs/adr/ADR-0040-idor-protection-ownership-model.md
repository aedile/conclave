# ADR-0040: IDOR Protection — Ownership Model for Resource Endpoints

> **Amendment (Phase 56):** File paths updated to reflect synthesizer sub-package decomposition.

**Status**: Superseded by ADR-0065
**Date**: 2026-03-20
**Task**: T39.2 — Add Authorization & IDOR Protection on All Resource Endpoints
**Deciders**: Engineering team

---

## Context

After introducing JWT authentication in T39.1, the API endpoints for `SynthesisJob` and
`Connection` resources remained vulnerable to **Insecure Direct Object Reference (IDOR)**
attacks: any authenticated operator could read, modify, or delete another operator's
resources by guessing or enumerating their IDs.

This is a horizontal privilege escalation vulnerability (OWASP API3:2023). An attacker
who holds a valid token for operator A can trivially enumerate integer job IDs or UUID
connection IDs to access operator B's data.

---

## Decision

Implement a **per-column ownership model** based on the JWT `sub` claim with the
following design choices:

### 1. `owner_id` column on all resource models

Every resource table that represents operator-owned data carries an `owner_id` column:

- `synthesis_job.owner_id` (`VARCHAR`, NOT NULL, default `""`, indexed)
- `connection.owner_id` (`VARCHAR`, NOT NULL, default `""`, indexed)

The column stores the JWT `sub` claim of the operator who created the resource. This is
a **string discriminator** — it does not reference a users table and does not require a
foreign key constraint. This keeps the schema simple and avoids introducing a user
registry before multi-operator support is required.

### 2. JWT `sub` claim as the resource ownership discriminator

`get_current_operator()` in `bootstrapper/dependencies/auth.py` extracts the `sub` claim
from the verified Bearer token and returns it as a plain string. All resource creation
endpoints assign this string to `owner_id`; all resource read and delete endpoints filter
by it.

The `sub` claim must:

- Be present in the token (enforced by `options={"require": ["sub", "exp", "iat"]}` in
  `verify_token()`).
- Be a non-empty string (enforced by the empty-sub guard in `get_current_operator()`).
  An empty `sub` would collide with the pass-through sentinel and grant unintended access
  to all pre-T39.2 resources.

### 3. 404-not-403 response for ownership mismatches

When an operator requests a resource that exists but is owned by a different operator,
the endpoint returns **HTTP 404 Not Found** rather than HTTP 403 Forbidden.

Rationale: a 403 response confirms that the resource ID exists, leaking information about
other operators' resources and enabling ID enumeration. A 404 response is
indistinguishable from a genuinely missing resource, preventing enumeration attacks.

This is consistent with the security principle of not revealing information about
resources the requester has no right to access (OWASP API3:2023 recommendation).

### 4. Index requirement on `owner_id` columns

All `owner_id` columns carry a database index:

- `ix_synthesis_job_owner_id` on `synthesis_job(owner_id)`
- `ix_connection_owner_id` on `connection(owner_id)`

Rationale: every resource list query and single-item fetch filters by `owner_id`. Without
an index, each authenticated request requires a full table scan, which degrades linearly
with table size in a multi-operator deployment. The index is created explicitly in the
Alembic migration (`008_add_owner_id_columns.py`) and declared with `index=True` in the
SQLModel field definitions.

### 5. Pass-through mode: empty `jwt_secret_key` → `owner_id=""` sentinel

When `JWT_SECRET_KEY` is empty (development / unconfigured mode), `get_current_operator()`
returns `""` instead of raising an authentication error. This sentinel value matches the
`server_default=""` on all pre-T39.2 rows, preserving backward compatibility for
single-operator deployments where JWT is not yet configured.

In pass-through mode:

- All resources with `owner_id=""` are accessible to the sentinel operator.
- No data migration is required when upgrading from a pre-T39.2 single-operator
  deployment.
- The `AuthenticationGateMiddleware` logs a `WARNING` on every non-exempt request to
  remind operators that authentication is not configured.

**Security note**: production deployments MUST set `JWT_SECRET_KEY` to a non-empty value.
An empty key leaves the application in pass-through mode with no per-operator isolation.

---

## Known Debt

### SynthesisJob file placement (ADR-0021)

`SynthesisJob` lives in `modules/synthesizer/jobs/job_models.py` rather than `bootstrapper/`
because it was introduced before the resource ownership model was designed. It is a domain
model that is also used as an API resource, which places it in a grey zone between
`modules/synthesizer/` and `bootstrapper/`.

Per ADR-0021, moving `SynthesisJob` to `bootstrapper/` is deferred due to import boundary
complexity. This is pre-existing placement debt and is not introduced by T39.2. A future
refactor task should evaluate extracting a separate `SynthesisJobResource` schema in
`bootstrapper/schemas/jobs.py` and keeping the domain model in `modules/synthesizer/`.

### `owner_id` visible in API responses

`owner_id` is currently included in API response bodies (`ConnectionResponse`,
`SynthesisJobResponse`). In a single-operator deployment this is harmless — the operator
sees their own sub claim reflected back.

In a multi-operator deployment, `owner_id` leaks the `sub` claim of the creating operator
to any consumer of the response. Before multi-tenant rollout, evaluate excluding
`owner_id` from response schemas or replacing it with a boolean `is_mine` flag. This
is tracked as an advisory — no action required for the current single-operator MVP.

---

## Consequences

### Positive

- Horizontal privilege escalation (IDOR) is structurally prevented on all resource
  endpoints.
- The 404-not-403 pattern prevents ID enumeration.
- Indexed `owner_id` columns ensure O(log n) rather than O(n) per-operator queries.
- Pass-through mode preserves backward compatibility for existing single-operator
  deployments.
- The ownership model is simple enough to audit: one column per table, one guard per
  endpoint.

### Negative / Trade-offs

- **Empty-sub collision risk**: the `""` sentinel doubles as the pass-through identity.
  If an operator somehow obtains a token with `sub=""`, they could access pre-T39.2
  legacy rows. The empty-sub guard in `get_current_operator()` prevents this when JWT is
  configured.
- **No multi-operator isolation in pass-through mode**: when `JWT_SECRET_KEY` is empty,
  all operators see all resources. This is acceptable for the single-operator MVP but must
  be resolved before any multi-operator or multi-tenant deployment.
- **`owner_id` in responses**: see Known Debt above.

---

## Alternatives Considered

| Option | Rejected Because |
|--------|-----------------|
| Row-level security (database-level) | Requires PostgreSQL RLS configuration; not portable to SQLite (used in tests); adds ops complexity without benefit for single-operator MVP. |
| Separate tenants table with FK constraint | Over-engineered for single-operator MVP; requires user registry before multi-operator support is needed. |
| 403 Forbidden for ownership mismatch | Confirms resource existence, enabling enumeration attacks. 404 is the OWASP-recommended approach. |
| No index on `owner_id` | Every authenticated list query would be a full table scan. Unacceptable at scale. |

---

## References

- OWASP API Security Top 10 — API3:2023 Broken Object Property Level Authorization
- OWASP API Security Top 10 — API1:2023 Broken Object Level Authorization
- ADR-0039 — JWT Bearer Token Authentication (establishes the `sub` claim source)
- ADR-0021 — SynthesisJob placement debt (pre-existing)
- `src/synth_engine/bootstrapper/dependencies/auth.py` — `get_current_operator()`
- `src/synth_engine/bootstrapper/schemas/connections.py` — `Connection.owner_id`
- `src/synth_engine/modules/synthesizer/jobs/job_models.py` — `SynthesisJob.owner_id`
- `alembic/versions/008_add_owner_id_columns.py` — migration with explicit index DDL

---

### Amendment (Advisory Drain, 2026-03-21) — owner_id visibility in API responses

The `owner_id` field is included in API response bodies (`ConnectionResponse`,
`JobResponse`). This was evaluated for exclusion.

**Decision: Retain `owner_id` in responses.**

1. `owner_id` is the JWT `sub` claim, which the operator already knows
   (it is their own identity).
2. Filtering excludes other operators' records entirely — an operator
   never sees another operator's `owner_id`.
3. Including `owner_id` aids debugging and audit log correlation.
4. If multi-tenant isolation requires hiding operator identity from
   shared views, a `ReadSchema` without `owner_id` can be introduced
   at that time.

No code changes required.
