# ADR-0054: Adopt cosmic-ray as Mutation Testing Tool — Supersedes ADR-0052

**Status**: Accepted (Amended 2026-03-24)
**Date**: 2026-03-24
**Deciders**: PM, Engineering team
**Task**: T53.1 — Mutation Testing: Evaluate cosmic-ray, Adopt or Fallback
**Supersedes**: ADR-0052 (mutmut / Python 3.14 Compatibility Gap)

---

## Amendment — 2026-03-24: Move Mutation Testing to Local PM Gate

**Amended by**: PM (Phase 53 advisory drain)

GitHub Actions shared runners cannot complete 789 mutants within a reasonable
timeout. Empirical CI runs showed consistent timeout failures at the 45-minute
limit. The `mutation-test` CI job has been removed from `.github/workflows/ci.yml`.

**New enforcement model**: cosmic-ray runs locally before every merge. The PM
executes the full three-step gate:

    cosmic-ray init cosmic-ray.toml session.sqlite
    cosmic-ray exec cosmic-ray.toml session.sqlite
    python scripts/check_mutation_score.py session.sqlite

The 60% threshold (ADR-0047), zero-mutant guard, and incomplete-run detection
remain unchanged — they are enforced by `scripts/check_mutation_score.py`.
The `cosmic-ray.toml` config and `scripts/check_mutation_score.py` are
retained for local use.

CONSTITUTION.md Priority 4 enforcement row updated to reflect the local gate.

---

## Context

ADR-0052 documented that `mutmut 3.x` was non-functional on Python 3.14 due to
its in-process trampoline mechanism causing SIGSEGV on every mutant (200/200
crashes with no meaningful mutation score). ADR-0052 accepted the gap and
deferred to upstream resolution, noting that `cosmic-ray` should be evaluated
as an alternative (re-evaluation trigger (c) in ADR-0052).

This ADR documents the evaluation of `cosmic-ray 8.4.4` on Python 3.14 and
the decision to adopt it as the project's mutation testing tool.

### Spike Methodology

1. Installed `cosmic-ray 8.4.4` via Poetry: `poetry add --group dev cosmic-ray`.
2. Created a minimal config targeting `shared/security/audit.py` with the
   `local` distributor (subprocess-per-mutant isolation).
3. Ran `cosmic-ray init` + `cosmic-ray exec` against the spike config.
4. Recorded: mutant count, kill count, surviving mutants, exit codes.

### Spike Results

| Metric | Value |
|--------|-------|
| Module | `src/synth_engine/shared/security/audit.py` |
| Total mutants | 83 |
| Killed | 63 |
| Survived | 20 |
| Timeout | 0 |
| SIGSEGV | **0** |
| Mutation score | **75.9%** |
| Threshold (ADR-0047) | 60% |
| Result | **PASS** |

**Key finding**: All 83 mutants executed cleanly on CPython 3.14 — zero
segmentation faults. The subprocess-per-mutant `local` distributor completely
avoids the trampoline mechanism that caused mutmut 3.x to crash.

### Why cosmic-ray Works on Python 3.14

`cosmic-ray` operates differently from `mutmut 3.x`:

- **mutmut 3.x**: Injects mutations into a running CPython process via a
  bytecode trampoline. CPython 3.14's internal memory layout changes broke this
  approach, causing SIGSEGV before any test could run.
- **cosmic-ray**: Applies mutations to source files on disk, then spawns an
  entirely new Python subprocess to run the test suite against the mutated code.
  Each mutant is an isolated subprocess that starts fresh — no trampoline, no
  shared CPython state, no SIGSEGV.

---

## Decision

**Adopt `cosmic-ray 8.4.4` as the mutation testing tool for this project.**

Specifically:

1. **Install `cosmic-ray` in the dev dependency group.** Replace `mutmut 3.x`
   as the primary mutation testing tool. Both packages remain installed
   temporarily (mutmut configuration still in `pyproject.toml`); the active
   gate uses cosmic-ray.

2. **Configure `cosmic-ray.toml` in the project root** targeting both
   security-critical module trees:
   - `src/synth_engine/shared/security/` (vault, audit, HMAC, ALE, rotation)
   - `src/synth_engine/modules/privacy/` (epsilon/delta accountant, DP engine)
   Trivial `__init__.py` files are excluded via `excluded-modules` glob patterns.

3. **Run `cosmic-ray` as a local PM gate** before every merge (see Amendment
   above). The CI mutation-test job was removed due to GitHub Actions budget
   constraints. The PM runs init/exec/check_mutation_score.py locally.

4. **Enforce the 60% threshold** (ADR-0047) via `scripts/check_mutation_score.py`,
   which also provides:
   - Zero-mutant guard: fails loudly if no mutants were generated.
   - Incomplete run detection: fails if pending mutants remain after `exec`.

5. **Update CONSTITUTION.md** Priority 4 enforcement row: replace `mutmut run`
   command with `cosmic-ray init + exec + check_mutation_score.py`, noting
   local execution.

6. **Update ADR-0052 status** to `Superseded by ADR-0054`.

---

## Consequences

### Positive

- **Mutation score is now programmatically enforced.** Constitution Priority 4's
  mutation gate is no longer advisory-only. The PM local gate verifies score
  before merge.
- **No SIGSEGV on Python 3.14.** The subprocess-per-mutant approach is
  intrinsically safe on any CPython version that supports standard subprocess
  spawning.
- **75.9% mutation score on audit.py** confirmed from the spike — already above
  the Phase 55 target of 70%.
- **Clean execution semantics.** Each mutant runs in an isolated subprocess.
  Crashes in mutated code do not affect subsequent mutants.

### Negative / Constraints

- **Slower than mutmut (when mutmut works).** Spawning a new subprocess per
  mutant adds overhead vs. mutmut's in-process injection. With 789 mutants and
  a 10-second timeout, worst-case wall time is ~131 minutes. In practice, the
  test suite is fast and each mutant completes in < 5 seconds.
- **CI no longer runs mutation testing.** The gate relies on PM discipline
  rather than automated CI enforcement. This is a trade-off accepted due to
  GitHub Actions budget constraints. The PM must not skip this gate before
  merge.
- **`mutmut` config remains in `pyproject.toml`.** The `[tool.mutmut]` block is
  preserved in case upstream `mutmut` adds Python 3.14 support. It does not
  affect CI (no `mutmut` CI step) but adds minor config noise.

---

## Alternatives Considered

### 1. Dual-interpreter (tox/nox) — Python 3.13 for mutmut

Run mutmut under a separate Python 3.13 installation while keeping production
on 3.14. **Rejected**: introduces a parallel interpreter dependency, complicates
CI provisioning, and does not test the actual production runtime. If a security-
critical function has 3.14-specific behavior, mutations under 3.13 may not
catch it.

### 2. Keep ADR-0052 "accept the gap" position

Continue without a programmatic mutation gate. **Rejected**: Constitution
Priority 4 is now satisfiable with cosmic-ray. Maintaining an advisory-only
gap when a working tool exists is inconsistent with the Programmatic
Enforcement Principle (Section 4).

### 3. Wait for mutmut Python 3.14 support

Monitor upstream mutmut and re-evaluate when Python 3.14 support lands.
**Rejected**: cosmic-ray is available now, works correctly, and produces
meaningful scores. Waiting further delays the Constitution Priority 4 gate
with no benefit.

### 4. Keep mutation-test in CI with continue-on-error

Retain the CI job but with `continue-on-error: true` indefinitely. **Rejected**:
this gives false confidence that the gate is enforced. A non-blocking CI job
that routinely times out provides no mutation quality signal. A local PM gate
with real enforcement is preferable to a CI job that never completes.

---

## References

- ADR-0047: Mutation Testing Gate (binding scope and threshold decision)
- ADR-0052: mutmut / Python 3.14 Compatibility Gap (superseded by this ADR)
- `cosmic-ray.toml`: project root config file (T53.1)
- `scripts/check_mutation_score.py`: threshold enforcement script (T53.1)
- `.github/workflows/ci.yml`: mutation-test job removed (Amendment 2026-03-24)
- CONSTITUTION.md Priority 4: Comprehensive Testing (enforcement table updated T53.1, amended 2026-03-24)
- `tests/unit/test_mutation_testing_infrastructure.py`: infrastructure tests (T53.1)
- PM Rule 6: Technology substitution requires ADR (this ADR satisfies that rule)
