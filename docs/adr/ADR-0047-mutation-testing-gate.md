# ADR-0047: Mutation Testing Gate

**Status:** Accepted
**Date:** 2026-03-22
**Deciders:** PM, Architecture Reviewer
**Task:** Framework amendment — architecture review

---

## Context

The 95% coverage gate (Constitution Priority 4) can be satisfied by shallow tests that
don't catch real defects. Architecture review found ~30% of tests would pass even if the
implementation were broken, primarily due to truthiness-only assertions and over-mocking.
Coverage measures lines hit, not defects caught.

Examples of shallow test patterns:
- `assert result is not None` — passes for any non-None return value
- `assert isinstance(result, dict)` — passes for any dict, regardless of content
- `assert "field" in result` — passes if the field exists with any value

These patterns inflate coverage metrics without providing meaningful regression protection.
A mutation testing tool like `mutmut` can detect these gaps by modifying production code
and verifying that tests fail — if they don't, the test suite has a blind spot.

---

## Decision

**Amended 2026-03-24**: CI tool updated from mutmut to cosmic-ray 8.4.4; see ADR-0054 for rationale. Threshold (60% -> 70% by Phase 55) is unchanged.

Add mutation testing (mutmut) as a CI gate for security-critical modules. Start with
`shared/security/` and `modules/privacy/`. Initial threshold: 60% mutation score,
targeting 70% by Phase 55. Also add assertion quality requirements to the Constitution.

Specifically:
1. **Assertion quality** (Constitution Priority 4): Tests MUST contain at least one
   specific value assertion per test function. Truthiness, type, or existence checks
   alone are insufficient.
2. **Mutation score** (Constitution Priority 4): `mutmut` MUST achieve the configured
   threshold on security-critical modules. CI command:
   `mutmut run --paths-to-mutate=src/synth_engine/shared/security/ src/synth_engine/modules/privacy/`
3. **Phase-boundary-auditor** gains an Assertion Specificity sweep to catch shallow
   assertions in changed test files.

---

## Consequences

**Positive:**
- Forces tests to actually catch defects, not just hit lines.
- Security-critical modules get the strongest testing guarantee.
- Phased rollout (60% -> 70%) avoids blocking all development while the test suite
  is brought up to standard.
- Assertion quality requirement is enforceable by the phase-boundary-auditor without
  any new tooling.

**Negative / Constraints:**
- Slower CI: mutation testing is computationally expensive. Running only on critical
  paths (shared/security/, modules/privacy/) limits the impact.
- Initial 60% threshold may require test improvements before the gate can be enabled.
- mutmut is a new dependency that must be added to the dev dependency group.

---

## Alternatives Considered

1. **Coverage-only with higher threshold** (e.g., 98%). Rejected: higher coverage
   does not address assertion quality; shallow tests can achieve 100% coverage.
2. **Manual test review** — rely on QA reviewer to catch shallow tests. Rejected:
   inconsistent; the phase-boundary-auditor's automated sweep is more reliable.
3. **Full-codebase mutation testing**. Rejected: too expensive for CI; security-critical
   modules are the highest-value targets. Can expand scope later if CI budget allows.
4. **Property-based testing only** (Hypothesis). Rejected: complementary but different
   purpose; property tests find edge cases, mutation tests verify assertion strength.

---

## References

- CONSTITUTION.md Section 1 (Priority 4 — Comprehensive Testing)
- CONSTITUTION.md Section 4 (Enforcement Inventory)
- `.claude/agents/phase-boundary-auditor.md` (Assertion Specificity sweep)
