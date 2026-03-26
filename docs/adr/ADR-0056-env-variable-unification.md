# ADR-0056 — ENV= Alias Deprecation and CONCLAVE_ENV= Unification

**Status**: Accepted
**Date**: 2026-03-26
**Task**: T57.6 — Unify Environment Configuration
**Author**: Software-developer subagent (P57)

---

## Context

The system historically accepted two environment variables to select the deployment
mode: `ENV=` (legacy) and `CONCLAVE_ENV=` (canonical). Both influenced `is_production()`
behaviour through the `ConclaveSettings` model. The `env` field acted as a fallback
when `conclave_env` was absent, creating an implicit merging rule that was not
consistently documented or enforced.

Two problems arose:

1. **Silent misconfiguration**: When both `ENV=` and `CONCLAVE_ENV=` were set with
   conflicting values (e.g. `ENV=development`, `CONCLAVE_ENV=production`), the
   resolution depended on which field was non-empty in the Pydantic model — there
   was no logged warning and operators had no visibility into which value won.

2. **Audit surface ambiguity**: The presence of two authoritative sources for
   "are we in production?" made security reviews harder. T57.3 (construction-time
   validation) needed a single, unambiguous `conclave_env` to fire correctly.

---

## Decision

`CONCLAVE_ENV=` is the single source of truth for deployment mode.

1. `ENV=` becomes a **deprecated alias**. When present, a `WARNING` is emitted at
   `ConclaveSettings` construction time (not at usage time) so operators are notified
   once per process startup.

2. When both `ENV=` and `CONCLAVE_ENV=` are set and conflict, `CONCLAVE_ENV=` wins.
   A second `WARNING` is emitted identifying the conflict.

3. `is_production()` reads only `self.conclave_env.lower() == "production"`. The
   legacy `env` field no longer influences this method.

4. `config_validation._is_production()` delegates to `get_settings().is_production()`,
   which reads `conclave_env` exclusively.

Implementation: `@model_validator(mode="after")` in `ConclaveSettings` handles the
warning emission and conflict resolution at construction time. This ensures that every
`get_settings()` call in production returns a settings object whose `conclave_env` is
authoritative and whose deprecation state has been logged.

---

## Consequences

**Positive**:
- Single authoritative field for production-mode detection.
- Operators who mistakenly set both variables are warned immediately at startup.
- T57.3 construction-time validation (database_url / audit_key checks) has a
  reliable single field to inspect.
- Security audit surface is reduced: reviewers can trace one variable.

**Negative / Mitigations**:
- Operators using `ENV=production` only (without `CONCLAVE_ENV=`) will see a
  deprecation WARNING. This is intentional. Their deployment continues to work
  because the model_validator copies the effective mode — the warning is
  informational, not fatal.
- `ENV=` is NOT removed in P57 to avoid breaking existing deployments. A future
  phase may remove the field once all known deployments have migrated (sunset: P65).

---

## Alternatives Considered

**Remove `env` field entirely (P57)**: Rejected. Breaking change; existing deployments
may use `ENV=development` in `.env` files or container manifests. Deprecation-first
is safer.

**Keep both fields as equals**: Rejected. The ambiguity is the root cause of the
problem. "Last writer wins" or "alphabetical order" rules are invisible to operators.

**Merge at startup via `validate_config()`**: Rejected. That function runs after
`get_settings()` and cannot mutate the settings object. The `@model_validator` runs
at construction time, ensuring the object is always internally consistent.
