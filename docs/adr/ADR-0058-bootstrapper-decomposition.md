# ADR-0058 — Bootstrapper Decomposition (Phase 60)

**Date**: 2026-03-26
**Status**: Accepted
**Task**: T60.1 – T60.5

## Context

The bootstrapper package had grown to 9,137 LOC across 47 files with four
multi-responsibility files:

| File | LOC Before | Problem |
|------|-----------|---------|
| `dependencies/auth.py` | 534 | Mixed JWT logic + Starlette middleware class |
| `lifecycle.py` | 217 | Lifespan hook + inline route handlers + schema definitions |
| `main.py` | 212 | App wiring + factory function (`build_ephemeral_storage_client`) |
| `factories.py` | 377 | IoC wiring + full domain transaction logic (`PrivacyLedger` ORM) |

Domain accounting code in `factories.py` was the highest-priority violation:
the bootstrapper is responsible for IoC wiring, not for implementing the
pessimistic-locking budget deduction protocol. `PrivacyLedger` ORM access in
the bootstrapper layer breaches the module boundary principle.

## Decision

Decompose all four files with zero behavioral changes:

1. **T60.1** — Extract `AuthenticationGateMiddleware` (and its private
   `_build_401_response`) to `dependencies/auth_middleware.py`.  Re-export
   from `auth.py` unconditionally for backward compatibility.  Circular
   import resolved via deferred imports inside `dispatch()`.

2. **T60.2** — Move `GET /health` liveness route to `routers/health.py`.
   Keep `POST /unseal` in `lifecycle.py` (tightly coupled to lifespan state).
   `_register_routes` remains in `lifecycle.py`.

3. **T60.3** — Move `build_ephemeral_storage_client` to `factories.py`
   (canonical home for all DI factories).  Re-export from `main.py` to
   preserve existing patch targets in tests.

4. **T60.4** — Extract budget transaction domain logic to
   `modules/privacy/sync_budget.py`.  `factories.py` `_sync_wrapper` now
   delegates via a single call.  All SQLAlchemy imports deferred inside
   `sync_spend_budget()`.  Module boundary enforced: `sync_budget.py` must
   not import from `bootstrapper`.

5. **T60.5** — Move `UnsealRequest` Pydantic model to
   `bootstrapper/schemas/vault.py`.  Re-export chain: `schemas/vault.py` →
   `lifecycle.py` → `main.py` for backward compatibility.

## Consequences

**Positive**:
- `auth.py`: 534 → 343 LOC (−36%)
- `lifecycle.py`: 217 → 116 LOC (−47%)
- `factories.py`: 377 → 323 LOC (−14%), and no longer contains ORM access
- `sync_budget.py`: new 128 LOC file in the correct module
- Import-linter contracts continue to pass: `modules/privacy` does not import
  from `bootstrapper`

**Neutral**:
- All re-exports preserve backward compatibility; no callers require changes
- Zero behavioral changes; all 3,076 unit tests and 233 integration tests pass

**Negative**:
- Deferred imports in `auth_middleware.dispatch()` are slightly unusual; the
  comment documents why (circular import resolution)
