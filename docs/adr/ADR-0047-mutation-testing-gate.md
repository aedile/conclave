# ADR-0047 — Mutation Testing Gate

**Status:** Accepted
**Date:** 2026-03-22
**Deciders:** PM, Architecture Reviewer
**Task:** P49-T49.3

---

## Context

The Conclave Engine's Constitution mandates 95%+ test coverage (Priority 4). While this
gate is effective at ensuring code is exercised by tests, line coverage alone does not
guarantee that tests detect real defects. A test suite can achieve 95% coverage with
assertions that only check truthiness (`assert result is not None`) or type
(`assert isinstance(result, dict)`) — passing coverage gates while catching none of
the actual bugs mutation testing would reveal.

Architecture review confirmed this risk: the 95% coverage metric can be satisfied by
shallow tests that do not catch real defects. This is a coverage-gaming vulnerability
in the enforcement framework.

---

## Decision

Add mutation testing (mutmut) as a CI gate for security-critical modules, starting with:
- `src/synth_engine/shared/security/` — cryptographic operations, vault, audit trail
- `src/synth_engine/modules/privacy/` — epsilon/delta budget accounting

**Thresholds:**
- Initial (Phase 49): 60% mutation score
- Target (Phase 55): 70% mutation score

The mutation testing gate is documented in CONSTITUTION.md Priority 4 as a governance
requirement. Tooling configuration (pyproject.toml, CI pipeline) is deferred to Phase 50.

---

## Consequences

**Positive:**
- Catches tests that achieve high line coverage but fail to detect real defects
- Forces test assertions to be specific enough to catch mutations (killed mutants)
- Starting with security-critical modules focuses the expensive CI time on highest-impact code
- Gradual threshold ramp (60% -> 70%) allows the team to build mutation testing expertise

**Negative / Constraints:**
- Mutation testing is computationally expensive — CI times will increase for the targeted
  modules. Mitigated by limiting scope to `shared/security/` and `modules/privacy/` initially.
- Developers must understand mutmut output to fix surviving mutants. Learning curve expected.
- False positives: some surviving mutants are equivalent mutants (semantically identical to
  the original). The team will need to build a mutant-equivalence allowlist over time.

---

## Alternatives Considered

1. **Coverage-only with assertion linting**: Use a custom pytest plugin to check assertion
   quality. Rejected — assertion linting catches shallow assertions but does not prove tests
   detect defects. Mutation testing is the gold standard.
2. **Full-codebase mutation testing**: Run mutmut on all `src/`. Rejected due to CI cost.
   Scoping to security-critical modules balances cost and value.
3. **Property-based testing only**: Hypothesis already used in some tests. Rejected as
   insufficient alone — property tests complement but do not replace mutation testing.

---

## References

- CONSTITUTION.md Priority 4 (Comprehensive Testing) — the amended directive
- ADR-0046 — Priority Sequencing Constraint (related Phase 49 amendment)
- `src/synth_engine/shared/security/` — initial mutation testing scope
- `src/synth_engine/modules/privacy/` — initial mutation testing scope
- mutmut documentation: https://mutmut.readthedocs.io/
