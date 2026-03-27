# ADR-0059: SQLModel Pre-Release Stability Assessment

**Status**: Accepted
**Date**: 2026-03-27
**Deciders**: PM, Senior Developer
**Task**: T62.5 — SQLModel Risk Assessment ADR

---

## Context

The Conclave Engine uses SQLModel `>=0.0.21,<0.1.0` as the ORM layer that
bridges SQLAlchemy (database persistence) and Pydantic (data validation).

SQLModel has been on the `0.0.x` version series since its initial release.
As of 2026-03-27, the latest version is `0.0.21`.  The `0.0.x` pre-release
versioning signals that the maintainer (Sebastián Ramírez / tiangolo) has not
yet committed to a stable public API.

---

## Current Usage Scope

SQLModel is used pervasively across the codebase:

| Usage Pattern | Files | Notes |
|---|---|---|
| `SQLModel` base class (ORM table models) | `bootstrapper/schemas/connections.py`, `bootstrapper/schemas/settings.py`, `bootstrapper/schemas/webhooks.py`, `modules/synthesizer/jobs/job_models.py`, `modules/privacy/ledger.py` | Core ORM table definitions |
| `Session` for database I/O | 15+ files across `bootstrapper/`, `modules/synthesizer/`, `modules/privacy/` | All DB access goes through SQLModel Session |
| `SQLModel.metadata.create_all()` | Test files (in-memory SQLite) | Schema creation in tests |
| `select()` query builder | 8+ files | All structured queries |
| `SQLModelConfig` (internal compat API) | `shared/db.py` | Accesses `sqlmodel._compat` — a PRIVATE module |
| `Field` for column definitions | Schema files | Column constraints, primary keys, foreign keys |

**Critical risk**: `shared/db.py` imports `from sqlmodel._compat import SQLModelConfig`.
This is a private internal module that is not part of any stable API contract
and may be removed or renamed between `0.0.x` patch versions without notice.

---

## Risk Assessment

### Risk 1: Pre-release API instability (MEDIUM)

**Problem**: Version `0.0.x` carries no stability guarantee.  The maintainer
can change any public API in a patch release without a deprecation notice.

**Observed stability**: In practice, SQLModel's API has been highly stable
since `0.0.14`.  The `Field`, `Session`, `SQLModel`, and `select` APIs used
in this codebase have not changed across the `0.0.14`–`0.0.21` range.  The
pin `>=0.0.21,<0.1.0` locks out `0.1.0` when/if it releases and may require
manual intervention to upgrade.

**Mitigation**: Current pin is constrained to `<0.1.0`.  Any `0.0.x` release
within that range must remain backward-compatible with our usage.

### Risk 2: Private module import (MEDIUM)

**Problem**: `shared/db.py` imports `from sqlmodel._compat import SQLModelConfig`.
This private module name could change in any patch release.

**Mitigation**: This import is isolated to one file.  A failure would be
immediately visible at import time (not silently at runtime).  The import
should be replaced with a direct Pydantic `model_config` approach when
SQLModel `0.1.0` releases.

### Risk 3: SQLModel + SQLAlchemy 2.x compatibility (LOW)

**Problem**: SQLModel `0.0.21` has known compatibility with SQLAlchemy 2.x
(verified through the `0.0.18`+ releases).  However, SQLAlchemy 3.x (not yet
released as of this writing) would require SQLModel to update.

**Mitigation**: `sqlalchemy` is an indirect dependency (via `sqlmodel`).  Our
`pyproject.toml` pins are on SQLModel, not SQLAlchemy directly.  A future
SQLAlchemy major release would require a deliberate upgrade path.

### Risk 4: No upstream `py.typed` marker (LOW-MEDIUM)

**Problem**: SQLModel does not ship a `py.typed` marker.  mypy operates on
inferred types only.  This reduces type safety for SQLModel-specific patterns.

**Mitigation**: `mypy` overrides in `pyproject.toml` already handle this
via `ignore_missing_imports` on `sqlmodel` (transitively).

---

## Options Considered

### Option A: Stay and pin (current approach — RECOMMENDED)

Keep `sqlmodel>=0.0.21,<0.1.0` with strict upper bound.

**Pros**:
- Zero migration cost.
- SQLModel API has been stable in practice since 0.0.14.
- The pin prevents surprise upgrades.
- Full feature parity: Session + Pydantic v2 integration is well-tested.

**Cons**:
- No formal stability guarantee.
- Private `_compat` import is a latent fragility.
- Lock-out at `0.1.0` — will need manual intervention to upgrade.

**Action items**:
1. Fix `shared/db.py` to remove the `sqlmodel._compat` import (log as advisory).
2. Monitor the SQLModel `0.1.0` release for breaking changes.
3. Review pin when `0.1.0` is released.

### Option B: Migrate to SQLAlchemy + Pydantic directly

Replace SQLModel with `SQLAlchemy` ORM models and `Pydantic` validators.

**Pros**:
- SQLAlchemy 2.x is a stable, production-grade library with a formal API contract.
- Pydantic 2.x is production-stable.
- Removes the `0.0.x` risk entirely.

**Cons**:
- Significant migration effort: 27+ import sites, 5+ table model files.
- SQLModel's `SQLModel` base class elegantly unifies ORM table definition with
  Pydantic schema validation.  Replacing it requires explicit ORM/schema separation.
- Risk of introducing bugs during migration (no equivalent feature set in one package).
- Migration would need a dedicated phase (estimated Phase 66–67).

**Verdict**: This is the correct long-term direction but is premature while SQLModel
`0.0.x` remains stable in practice.  Revisit at Phase 70 or when SQLModel `0.1.0`
breaks our pin.

### Option C: Wait for SQLModel 0.1.0

Take no action until `0.1.0` is released with a stable API contract.

**Pros**: Zero effort now.

**Cons**: SQLModel's release timeline is uncertain (has been `0.0.x` since 2021).
The private `_compat` import is still a fragility that should be fixed regardless.

---

## Decision

**Recommendation: Option A — Stay and pin.**

Rationale:
1. SQLModel `0.0.21` has demonstrated practical API stability across 7 minor
   releases.  The risk is theoretical, not observed.
2. Migration to SQLAlchemy + Pydantic is a correctness-neutral, purely
   architectural refactor with non-trivial scope.  It does not improve security,
   performance, or functionality.
3. The only concrete action item is eliminating the `sqlmodel._compat` private
   import in `shared/db.py`, which is logged as an advisory below.

---

## Consequences

### Advisories logged (non-blocking)

- **ADV-P62-02**: `shared/db.py` imports `from sqlmodel._compat import SQLModelConfig`
  — a private module.  Replace with `model_config = ConfigDict(...)` from Pydantic
  directly when time permits.  Risk: breakage on SQLModel patch updates that rename
  internal modules.

### Monitoring plan

- Watch the SQLModel GitHub releases for `0.1.0` release notes.
- When `0.1.0` is released, evaluate the changelog for breaking changes before
  updating the pin in `pyproject.toml`.
- If SQLModel `0.0.x` breaks our usage (import error or behavioural change) before
  `0.1.0`, escalate to Option B (SQLAlchemy + Pydantic migration).

### Not a BLOCKER advisory

The recommendation is Option A (stay and pin), so no BLOCKER advisory is raised.
The ADV-P62-02 advisory covers the private import fragility at normal cadence.
