# ADR-0046 — Priority Sequencing Constraint

**Status:** Accepted
**Date:** 2026-03-22
**Deciders:** PM, Architecture Reviewer
**Task:** P49-T49.1

---

## Context

An architecture review of the Conclave Engine's development history identified a systemic
sequencing defect: authentication (Priority 0 — Security) was not fully implemented until
Phase 39, while code quality work (Priority 5) was being delivered in earlier phases. The
existing Constitution defined priority ordering for conflict resolution ("Priority is Law")
but had no mechanism to enforce priority ordering during phase planning. This allowed lower-
priority work to be scheduled and delivered while higher-priority requirements remained
unimplemented.

The root cause is that the Constitution's priority system was designed for runtime conflict
resolution, not for planning-time sequencing. Nothing prevented a PM from approving a phase
plan that targeted Priority 5 work while Priority 0 requirements had no enforcement gate.

---

## Decision

Add a Priority Sequencing Constraint (Priority 2.5) to CONSTITUTION.md Section 1a. Before
approving a phase plan, the PM MUST verify that all Constitutional requirements with a lower
priority number are either:

1. Fully implemented with passing enforcement gates, or
2. Explicitly deferred with an ADR documenting the deferral rationale.

A phase targeting Priority N work MUST NOT be approved while Priority 0 through N-1
requirements remain unimplemented without an ADR.

Enforcement is provided by:
- The `spec-challenger` agent gains an 8th challenge area ("Priority Compliance") that
  verifies all lower-priority requirements are implemented or deferred with ADRs.
- The PM phase-plan checklist must include a priority compliance verification step.

---

## Consequences

**Positive:**
- Prevents the sequencing defect that allowed auth to ship at Phase 39 while lower-priority
  work was delivered earlier.
- Forces explicit documentation (via ADR) when higher-priority work is intentionally deferred,
  creating an auditable record of the decision.
- The spec-challenger enforcement means the check happens automatically before every phase.

**Negative / Constraints:**
- Phases that would have been approved quickly now require a priority compliance check, adding
  a small overhead to phase planning.
- Legitimate cases where higher-priority work must be deferred (e.g., waiting for external
  dependencies) now require an ADR, which is additional documentation overhead. This is
  intentional — the overhead is the enforcement mechanism.

---

## Alternatives Considered

1. **Retroactive audit only**: Run a one-time audit of priority compliance without adding a
   permanent constraint. Rejected because this would not prevent recurrence.
2. **CI-enforced priority gate**: Build an automated tool that checks git history for priority
   compliance. Rejected as over-engineering — the spec-challenger + PM checklist approach is
   sufficient and lower-cost.

---

## References

- CONSTITUTION.md Section 1a (Priority 2.5) — the amendment this ADR documents
- `.claude/agents/spec-challenger.md` — challenge area 8 (Priority Compliance)
- Phase 39 auth implementation — the incident that motivated this constraint
