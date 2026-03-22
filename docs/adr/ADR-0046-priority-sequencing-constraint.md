# ADR-0046: Priority Sequencing Constraint

**Status:** Accepted
**Date:** 2026-03-22
**Deciders:** PM, Architecture Reviewer
**Task:** Framework amendment — architecture review

---

## Context

Architecture review (2026-03-22) found that Security (Priority 0) features like JWT auth
shipped at Phase 39 of 48, while lower-priority work was done earlier. The Constitution
enforced quality gates on whatever was built, but did not enforce the ORDER of what gets
built. This is a framework-level defect that transfers to all future projects using the
Constitution.

For example, a project could complete all Priority 5 (Code Quality) and Priority 4 (Testing)
work while leaving Priority 0 (Security) unimplemented until the final phases. The
Constitution would consider each individual phase compliant, despite the systemic risk of
deferring the highest-priority work.

---

## Decision

Add Priority 2.5 (Priority Sequencing) to the Constitution. The PM and spec-challenger must
verify priority compliance before approving phase plans.

Specifically:
- Before approving a phase plan, the PM MUST verify that all Constitutional requirements
  with a lower priority number are either (a) fully implemented with passing enforcement
  gates, or (b) explicitly deferred with an ADR documenting the deferral rationale and
  timeline.
- A phase targeting Priority N work MUST NOT be approved while any Priority 0 through N-1
  requirement remains unimplemented without a deferral ADR.

Enforcement is via the spec-challenger's new Priority Compliance sweep (challenge area #8)
and the PM's phase-plan checklist.

---

## Consequences

**Positive:**
- Phases cannot proceed on Priority N work while Priority 0-(N-1) requirements are
  unimplemented, ensuring the most critical work is addressed first.
- Deferral ADRs provide an escape hatch for justified exceptions, maintaining flexibility
  while requiring explicit documentation of the rationale.
- Framework-level fix that applies to all future projects using the Constitution.

**Negative / Constraints:**
- Slightly increases phase planning overhead (PM must verify priority compliance).
- May require deferral ADRs for legitimate cases where lower-priority work enables
  higher-priority work (e.g., building the API framework before adding auth).
- Retroactive application to the current project is not required; this is a go-forward
  constraint.

---

## Alternatives Considered

1. **Strict linear ordering** — require all Priority 0 work before any Priority 1 work,
   etc. Rejected: too rigid; some lower-priority infrastructure enables higher-priority
   features.
2. **Advisory-only** — log priority gaps without blocking. Rejected: advisory-only
   constraints have poor compliance history (see Constitution Section 4 on enforcement).
3. **Percentage thresholds** — require 80% of Priority N-1 before starting Priority N.
   Rejected: arbitrary threshold; the deferral ADR escape hatch is more precise.

---

## References

- CONSTITUTION.md Section 1 (Priority 2.5)
- CONSTITUTION.md Section 4 (Enforcement Inventory)
- `.claude/agents/spec-challenger.md` (challenge area #8)
