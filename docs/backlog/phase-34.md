# Phase 34 — Exception Hierarchy Unification & Operator Error Coverage

**Goal**: Eliminate the fractured exception hierarchy that will produce generic 500s in
production. Unify all domain exceptions under `SynthEngineError`, complete the
`OPERATOR_ERROR_MAP` for all 11 exception types, and add missing RFC 7807 response
mappings so operators receive actionable error messages instead of opaque stack traces.

**Prerequisite**: Phase 33 merged. Zero open advisories.

**ADR**: ADR-0037 — Exception Hierarchy Consolidation (new).

**Source**: Production Readiness Audit, 2026-03-18 — Critical Issue C1.

---

## T34.1 — Unify Vault Exceptions Under SynthEngineError

**Priority**: P0 — Production blocker. Vault errors currently inherit `ValueError`,
bypassing the domain exception middleware entirely.

### Context & Constraints

1. Three vault exceptions inherit `ValueError` instead of `SynthEngineError`:
   - `VaultEmptyPassphraseError(ValueError)` — `shared/security/vault.py:46`
   - `VaultAlreadyUnsealedError(ValueError)` — `shared/security/vault.py:54`
   - `VaultConfigError(ValueError)` — `shared/security/vault.py:62`

2. `LicenseError(Exception)` in `shared/security/licensing.py:116` also breaks the
   hierarchy — inherits bare `Exception`.

3. Changing base classes may break existing `except ValueError:` catch sites. All catch
   sites must be audited and updated. Grep for `except ValueError` and `except Exception`
   in `bootstrapper/` and `modules/` to find affected handlers.

4. To preserve backwards compatibility for callers who catch `ValueError`, consider
   making `SynthEngineError` a subclass of `ValueError` — **NO**. This pollutes the
   domain hierarchy. Instead, fix all catch sites explicitly.

5. ADR-0037 must document the decision and list all affected catch sites.

### Acceptance Criteria

1. `VaultEmptyPassphraseError`, `VaultAlreadyUnsealedError`, `VaultConfigError` inherit
   `SynthEngineError` (not `ValueError`).
2. `LicenseError` inherits `SynthEngineError` (not bare `Exception`).
3. All `except ValueError:` catch sites in `bootstrapper/lifecycle.py` and elsewhere that
   previously caught vault exceptions are updated to catch the specific domain exception.
4. `shared/exceptions.py` exports all domain exceptions in `__all__`.
5. ADR-0037 created documenting the hierarchy change, affected catch sites, and rationale.
6. All existing tests pass — no regressions from base class change.
7. Full gate suite passes (ruff, mypy, bandit, vulture, pytest unit+integration, pre-commit).

### Testing & Quality Gates

- Existing vault tests must pass without modification (they catch by specific type).
- Add 1 new test: verify `isinstance(VaultEmptyPassphraseError(...), SynthEngineError)`.
- Add 1 new test: verify `isinstance(LicenseError(...), SynthEngineError)`.
- QA + Architecture reviewers spawned.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/security/vault.py` (3 exception classes)
- Modify: `src/synth_engine/shared/security/licensing.py` (LicenseError)
- Modify: `src/synth_engine/shared/exceptions.py` (add to `__all__` if needed)
- Modify: `src/synth_engine/bootstrapper/lifecycle.py` (catch site updates)
- Audit: all files matching `except ValueError` and `except Exception` in src/
- Create: `docs/adr/ADR-0037-exception-hierarchy-consolidation.md`

---

## T34.2 — Consolidate Module-Local Exceptions Into Shared Hierarchy

**Priority**: P0 — Production blocker. `CollisionError` and `CycleDetectionError` produce
unhandled 500s with no operator-friendly message.

### Context & Constraints

1. Two module-local exceptions inherit bare `Exception`:
   - `CollisionError(Exception)` — `modules/masking/registry.py:31`
   - `CycleDetectionError(Exception)` — `modules/mapping/graph.py:23`

2. These are domain-level errors that operators may encounter (collision during masking,
   cycle in FK graph). They must be catchable at the middleware layer via `SynthEngineError`.

3. Moving them to `shared/exceptions.py` would create an import from `shared/` back into
   the module definition, which is fine — `shared/` is the designated cross-cutting layer.
   However, if the exception names are module-specific, they may stay in their module files
   but inherit from `SynthEngineError` (imported from `shared/exceptions.py`).

4. Preferred approach: keep exception classes in their module files, change base class to
   `SynthEngineError`. This avoids moving files and respects module ownership.

### Acceptance Criteria

1. `CollisionError` inherits `SynthEngineError`.
2. `CycleDetectionError` inherits `SynthEngineError`.
3. Both are importable from their original module locations (no relocation).
4. Full gate suite passes.

### Testing & Quality Gates

- Add 1 test each: verify `isinstance(CollisionError(...), SynthEngineError)`.
- Existing masking and mapping tests pass unchanged.
- QA reviewer spawned.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/masking/registry.py` (CollisionError base class)
- Modify: `src/synth_engine/modules/mapping/graph.py` (CycleDetectionError base class)

---

## T34.3 — Complete OPERATOR_ERROR_MAP for All Domain Exceptions

**Priority**: P0 — Production blocker. 6 of 11 domain exceptions have no RFC 7807 mapping,
producing generic 500 responses with no actionable operator guidance.

### Context & Constraints

1. `bootstrapper/errors.py:150-195` defines `OPERATOR_ERROR_MAP` with mappings for only
   5 exception types: `BudgetExhaustionError`, `OOMGuardrailError`, `VaultSealedError`,
   `VaultEmptyPassphraseError`, `VaultConfigError`.

2. Missing mappings (after T34.1 and T34.2 are complete):
   - `PrivilegeEscalationError` — should map to 403 Forbidden
   - `ArtifactTamperingError` — should map to 422 Unprocessable Entity
   - `VaultAlreadyUnsealedError` — should map to 409 Conflict
   - `LicenseError` — should map to 402 Payment Required or 403 Forbidden
   - `CollisionError` — should map to 409 Conflict
   - `CycleDetectionError` — should map to 422 Unprocessable Entity

3. Each mapping must include: HTTP status code, RFC 7807 `type` URI, human-readable
   `title`, and `detail` template. Follow the pattern of existing entries.

4. Security-sensitive exceptions (`PrivilegeEscalationError`, `ArtifactTamperingError`)
   must NOT leak internal details in the response. Use `safe_error_msg()` for detail text.

### Acceptance Criteria

1. `OPERATOR_ERROR_MAP` contains entries for all 11 `SynthEngineError` subclasses.
2. Each entry specifies: status code, type URI, title, detail template.
3. `PrivilegeEscalationError` and `ArtifactTamperingError` responses use sanitized messages.
4. New test: for each of the 6 newly-mapped exceptions, raise it through the RFC 7807
   middleware and assert the response status code and `type` field.
5. Full gate suite passes.

### Testing & Quality Gates

- 6 new unit tests (one per newly-mapped exception).
- Integration test: confirm middleware catches all 11 exception types without fallback to generic 500.
- QA + DevOps reviewers spawned.

### Files to Create/Modify

- Modify: `src/synth_engine/bootstrapper/errors.py` (expand OPERATOR_ERROR_MAP)
- Modify or create: `tests/unit/test_bootstrapper_errors.py` (6 new test cases)

---
