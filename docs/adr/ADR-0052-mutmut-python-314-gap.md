# ADR-0052: mutmut / Python 3.14 Compatibility Gap — Accept with Manual Hardening

**Status**: Superseded by ADR-0054
**Date**: 2026-03-23
**Superseded**: 2026-03-24 by ADR-0054 (cosmic-ray adoption — cosmic-ray 8.4.4 confirmed functional on CPython 3.14)
**Deciders**: PM, Engineering team
**Task**: T50 documentation — closes ADV-T49-01, ADV-P49-03

---

## Context

ADR-0047 established mutation testing via `mutmut` as a CI gate for
security-critical modules (`shared/security/`, `modules/privacy/`), with
an initial threshold of 60% mutation score targeting 70% by Phase 55.

During T49.5, `mutmut 3.x` was configured and executed against the target
modules, generating 200 mutants.  None survived — but not because the tests
caught them.  Every mutant process exited with **SIGSEGV (signal -11)** rather
than normal test failure (exit code 1).

### Root Cause

`mutmut 3.x` uses a trampoline mechanism to inject mutations into a running
CPython process at runtime.  On **Python 3.14**, CPython's internal memory
layout and bytecode evaluation loop changed in ways that are incompatible with
this trampoline.  The injected mutation causes CPython to crash with a
segmentation fault before the test suite can evaluate the mutant.

The result is systematically misleading: all 200 mutants report status
`"segfault"`.  `mutmut` reports 0 surviving mutants and 0 killed mutants —
neither number is meaningful.  The mutation score cannot be computed.

This renders `mutmut 3.x` **non-functional on Python 3.14**.  The issue is
upstream in `mutmut`; no workaround within this project can resolve it.

### Constraints

- **Python 3.14 is our production runtime.**  Downgrading is not an option
  without a separate ADR (with a full security and compatibility audit).
- **Constitution Priority 4** requires mutation testing on security-critical
  modules.  ADR-0047 is the binding decision; this ADR records the temporary
  gap.
- **No viable alternative** mutation tool (e.g., `cosmic-ray`) has been
  evaluated for Python 3.14 compatibility.  Substituting tools without an ADR
  violates PM Rule 6.

---

## Decision

**Accept the gap until upstream `mutmut` releases a Python 3.14-compatible
version.**

Specifically:

1. **Do not remove `mutmut` from the project.**  The `pyproject.toml`
   configuration block (`[tool.mutmut]`) remains in place, ready to activate
   the moment upstream support lands.

2. **Do not wire `mutmut` as a blocking CI gate.**  Running `mutmut` in CI
   while it SEGFAULTs on every mutant would produce false-green signals and
   consume CI budget without value.  The `mutmut` step is omitted from
   `.github/workflows/ci.yml` until the upstream issue is resolved.

3. **Mitigate with manually-written mutation hardening tests.**  T49.5
   produced `tests/unit/shared/security/test_mutation_hardening_t49_5.py`
   (19 tests) covering the behavioral patterns that mutation testing would
   otherwise verify:
   - Vault HMAC key derivation produces distinct values for distinct inputs
   - HMAC verification rejects wrong keys, wrong data, wrong signatures
   - Audit chain anchoring rejects tampered hashes
   - Privacy accountant boundary — epsilon/delta deductions are irreversible
   These tests provide partial, manually-maintained coverage for the
   highest-priority security mutation patterns.

4. **Tag Constitution Priority 4 as `[ADVISORY — no programmatic gate]`
   for mutation score.**  The assertion quality requirement from ADR-0047
   remains fully enforced (specific value assertions mandated, enforced by
   phase-boundary-auditor sweep).  Only the programmatic mutation score gate
   is deferred.

5. **Track upstream progress.**  The mutmut issue tracker should be monitored
   for a Python 3.14-compatible release.  Re-evaluation is mandatory when any
   of the conditions in the Consequences section are met.

---

## Consequences

### Positive

- **No false signals**: omitting a broken gate is safer than keeping one that
  reports 0 survivors when the truth is "unknown".
- **Partial coverage preserved**: the 19 manual hardening tests catch the most
  critical mutation patterns (HMAC key confusion, tampered audit chains, budget
  deduction irreversibility) without relying on the trampoline mechanism.
- **Zero disruption to CI**: other gates (ruff, mypy, bandit, vulture, pytest
  at 95% coverage) continue to pass and block bad merges.
- **Clean re-activation path**: `pyproject.toml` config is ready; when mutmut
  supports Python 3.14, the CI wiring is a one-line addition.

### Negative / Constraints

- **Mutation score not programmatically enforced.**  Constitution Priority 4's
  mutation gate is advisory-only until upstream resolves the incompatibility.
  This is a known, documented gap — not a silent one.
- **Manual hardening tests require discipline to maintain.**  Unlike automated
  mutation testing, the hardening tests must be updated by hand when
  security-critical code changes.  The phase-boundary-auditor MUST include a
  check: "were security-critical modules modified? If yes, were hardening tests
  updated?"
- **Mutation score unknown.**  We do not know the true mutation score of
  `shared/security/` and `modules/privacy/`.  The 60% threshold from ADR-0047
  cannot be verified.

### Re-evaluation Triggers

This ADR MUST be revisited when any of the following occur:

- (a) `mutmut` releases a version with documented Python 3.14 support.
- (b) A proposal is made to downgrade Python below 3.14 (requires a separate
  ADR per PM Rule 6).
- (c) An alternative mutation tool (`cosmic-ray`, `mutpy`, or successor)
  is evaluated and found compatible with Python 3.14.
- (d) The Phase 55 ADR-0047 threshold review is due, regardless of upstream
  status — to document the continued gap if still unresolved.

---

## Alternatives Considered

### 1. Downgrade Python to 3.12 or 3.13

`mutmut 3.x` is known to work on Python 3.12.  **Rejected**: Python 3.14 is
our production runtime.  Downgrading affects the entire stack, requires a
security compatibility audit, and changes the target environment for all
existing tests.  This decision belongs in a standalone ADR with full impact
analysis, not as a side effect of a mutation testing gap.

### 2. Switch to cosmic-ray

`cosmic-ray` is an alternative mutation testing tool that takes a different
approach (AST-level mutation rather than bytecode trampoline).  **Deferred**:
compatibility with Python 3.14 has not been verified.  PM Rule 6 requires an
ADR for any tool substitution.  A separate evaluation task should be created
if the mutmut gap remains unresolved past Phase 55.

### 3. Keep mutmut in CI, ignore segfault status

Run `mutmut` in CI but treat SIGSEGV exits as "killed" (not "survived"),
producing an artificially inflated mutation score.  **Rejected**: this is a
false metric.  Reporting a score computed from crash exits violates the intent
of Constitution Priority 4.  A broken gate that passes is more dangerous than
no gate.

### 4. Raise mutation score threshold to compensate

Increase the manual assertion quality bar to 100% specific-value assertions
across all test files.  **Rejected**: assertion quality is already enforced by
the phase-boundary-auditor.  Adding a higher bar does not substitute for
mutation testing — it addresses a different dimension of test quality.

---

## References

- ADR-0047: Mutation Testing Gate (the binding decision this ADR amends)
- `pyproject.toml` `[tool.mutmut]` configuration block
- `tests/unit/shared/security/test_mutation_hardening_t49_5.py` (19 tests)
- RETRO_LOG Phase 49 entry (T49.5 — Mutation Testing Baseline)
- ADV-T49-01: mutmut 3.x + CPython 3.14 segfault — RESOLVED by this ADR
- ADV-P49-03: mutmut CI gate not wired — RESOLVED by this ADR
- CONSTITUTION.md Priority 4 (Comprehensive Testing)
