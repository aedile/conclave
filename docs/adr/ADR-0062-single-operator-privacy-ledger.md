# ADR-0062: Single-Operator Privacy Ledger Assumption

**Status**: Accepted
**Date**: 2026-03-28
**Task**: T66.6 — Document Single-Operator Privacy Ledger Assumption
**Advisory**: ADV-P63-03 — Privacy ledger assumes single-operator model without documentation

## Context

The privacy accountant (`src/synth_engine/modules/privacy/accountant.py`) manages
differential privacy budgets via `PrivacyLedger` rows in the database. The current
implementation assumes a single operator model:

- There is exactly one configured operator (enforced by `ConclaveSettings`).
- All `spend_budget()` and `reset_budget()` calls reference a specific `ledger_id`.
- The `ledger_id` is a database primary key that must be created (e.g., via migration
  or setup endpoint) before budget operations are possible.
- The accountant does NOT perform multi-tenant isolation; it is the caller's
  responsibility to pass the correct `ledger_id`.

This assumption was implicit in the code: the accountant directly queries
`PrivacyLedger` by primary key without any ownership check. While this is correct
for the current single-operator deployment model, the lack of documentation created
ambiguity about whether multi-tenant operation was intended or supported.

## Decision

Document and formally accept the single-operator privacy ledger assumption:

1. The privacy accountant is explicitly designed for single-operator deployments.
   This is aligned with the broader system architecture: `ConclaveSettings` supports
   exactly one `OPERATOR_CREDENTIALS_HASH` — multi-tenancy is not in scope for this
   project.

2. The `ledger_id` parameter in `spend_budget()` and `reset_budget()` is a
   data-model concept (supporting multiple named budgets per operator, e.g., one
   per project or data source), NOT a multi-tenant isolation concept.

3. If multi-tenant operation is ever required, it must be implemented at a higher
   level (e.g., a separate `PrivacyLedger` per tenant with ownership enforcement
   in the API layer), not by modifying the accountant internals.

4. All public APIs that accept `ledger_id` document this assumption in their
   docstrings with a reference to this ADR.

5. The `LedgerNotFoundError` (introduced in T66.5) provides typed error handling
   when a `ledger_id` does not exist, returning HTTP 404 without leaking internal
   IDs. This is consistent with the single-operator model: the operator is expected
   to know their own ledger IDs.

## Architecture Constraint

The `PrivacyLedger` table is owned by the `privacy` module. Cross-module database
queries are forbidden (Architectural Requirement §4). Other modules that need
privacy budget information must call the accountant's Python interface, not query
the `PrivacyLedger` table directly.

## Alternatives Considered

### Option A: Implement multi-tenant isolation in the accountant

Rejected because: Multi-tenancy is out of scope for the current product. Adding
tenant isolation to the accountant would increase complexity without a corresponding
business requirement. If ever needed, it should be a deliberate architectural decision
with its own ADR.

### Option B: Add an ownership check in the accountant

Rejected because: The accountant currently has no concept of "current user" — it
operates on database sessions passed by the caller. Injecting caller identity into
the accountant would couple it to the auth system, violating the separation of
concerns between the `privacy` module and `bootstrapper`.

### Option C: Document the assumption (this ADR)

Selected because: The assumption is correct for the current deployment model. Clear
documentation eliminates ambiguity and provides a clear protocol for future
multi-tenant requirements.

## Consequences

- **Positive**: The architecture is explicit and consistent with the broader
  single-operator deployment model of the system.
- **Positive**: ADV-P63-03 is closed: the assumption is now documented and
  consciously accepted.
- **Negative**: If multi-tenant operation is ever required, this assumption must
  be revisited and the accountant API extended. This ADR serves as the starting
  point for that future work.
- **Tracking**: Re-evaluate if a multi-operator requirement is added to the backlog.

## References

- `src/synth_engine/modules/privacy/accountant.py` — spend_budget, reset_budget
- `src/synth_engine/shared/exceptions.py` — LedgerNotFoundError
- ADR-0037 — Domain exception hierarchy
- `docs/archive/ARCHITECTURAL_REQUIREMENTS.md` — §4 Cross-module query prohibition
