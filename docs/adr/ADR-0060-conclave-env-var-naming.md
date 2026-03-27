# ADR-0060: CONCLAVE_ Prefixed Environment Variable Naming Convention

**Status**: Accepted
**Date**: 2026-03-27
**Deciders**: PM, Software Developer
**Tasks**: T63.2 — Unify Environment Variable Naming

---

## Context

The Conclave Engine reads a large number of environment variables at startup.
Historically, many of these variables had bare names (`DATABASE_URL`, `AUDIT_KEY`,
`MASKING_SALT`, `JWT_SECRET_KEY`) without a common namespace prefix.

This creates two problems:

1. **Namespace pollution**: Generic names like `DATABASE_URL` conflict with other
   applications that may run in the same environment (e.g., another service with
   its own `DATABASE_URL`).

2. **Discoverability**: Operators cannot easily identify which env vars belong to
   the Conclave Engine by grepping for a common prefix.

The `CONCLAVE_ENV`, `CONCLAVE_SSL_REQUIRED`, `CONCLAVE_TLS_CERT_PATH`, and
`CONCLAVE_RATE_LIMIT_*` variables already use the `CONCLAVE_` prefix (introduced
in T57.6 and T39.3). This ADR extends that convention to the remaining
security-critical variables.

---

## Decision

Add `CONCLAVE_` prefixed **aliases** for the four security-critical environment
variables that currently use bare names:

| Old name (still supported) | New preferred name        |
|----------------------------|---------------------------|
| `DATABASE_URL`             | `CONCLAVE_DATABASE_URL`   |
| `AUDIT_KEY`                | `CONCLAVE_AUDIT_KEY`      |
| `MASKING_SALT`             | `CONCLAVE_MASKING_SALT`   |
| `JWT_SECRET_KEY`           | `CONCLAVE_JWT_SECRET_KEY` |

### Implementation

Pydantic v2 `AliasChoices` is used on each field:

```python
database_url: str = Field(
    default="",
    validation_alias=AliasChoices("CONCLAVE_DATABASE_URL", "DATABASE_URL"),
    ...
)
```

The `CONCLAVE_` variant is listed first, so it takes precedence when both are
set. If only the old name is set, it continues to work unchanged.

### Backward Compatibility

The old names (`DATABASE_URL`, `AUDIT_KEY`, `MASKING_SALT`, `JWT_SECRET_KEY`)
remain fully functional. No existing deployment is broken by this change.
The migration from old to new names is optional and can happen at the operator's
own pace.

### Warning suppression

`ConclaveSettings._warn_unrecognized_conclave_env_vars` is updated to recognise
the new `CONCLAVE_` aliases as known fields. Setting `CONCLAVE_DATABASE_URL`
does not trigger the "unrecognized CONCLAVE_ variable" warning.

---

## Consequences

### Positive

- Operators can namespace all Conclave Engine env vars under `CONCLAVE_` for
  easier management and discoverability.
- Reduces risk of env var name collision in shared environments.
- Aligns with the convention already established for `CONCLAVE_ENV`,
  `CONCLAVE_SSL_REQUIRED`, `CONCLAVE_RATE_LIMIT_*`, etc.

### Negative

- Two names per field increase documentation surface. Mitigated by noting
  both in `.env.example` and the field's `description`.
- Operators must understand the precedence rule (CONCLAVE_ wins when both set).
  This is documented in `.env.example` and the field's `description`.

### Neutral

- Full migration (deprecating the old names) is NOT part of this ADR.
  A future ADR can decide if/when the old names should emit a deprecation
  warning, based on adoption metrics.
- Four fields get aliases in this phase. Other fields (e.g., `REDIS_URL`,
  `HUEY_BACKEND`) may be added in a future task if there is operator demand.

---

## Alternatives Considered

### A: Rename (breaking change)

Replace the old names entirely with `CONCLAVE_` variants. Rejected: this would
break all existing deployments that have not yet migrated.

### B: Deprecation warning for old names

Log a WARNING when the old name is used instead of the new one. Rejected for
this phase: operators have not been notified of the new convention and would
see unexplained warnings. Deferred to a future ADR.

### C: env_prefix in SettingsConfigDict

Use `env_prefix="CONCLAVE_"` globally in the model config. Rejected: this
would break ALL existing deployments simultaneously (breaking change for all
fields, not just the four targeted here).
