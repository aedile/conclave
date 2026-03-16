# ADR-0032 — Mypy Strategy for Optional Synthesizer Dependencies

**Date:** 2026-03-16
**Status:** Accepted
**Deciders:** PM + Architecture Reviewer
**Task:** P20-T20.4 — Architecture Tightening (AC3)
**Resolves:** T20.4 finding — `ignore_missing_imports = true` for sdv/opacus/ctgan
prevents mypy from catching API breakage even when the synthesizer group is installed.

---

## Context

The synthesizer module (`src/synth_engine/modules/synthesizer/`) depends on three
packages — `sdv`, `opacus`, and `ctgan` — that live in an optional Poetry dependency
group (`[tool.poetry.group.synthesizer.dependencies]`) in `pyproject.toml`.

These packages are not installed by default. They are only available when the
synthesizer group is explicitly installed:

```bash
poetry install --with dev,synthesizer
```

The `pyproject.toml` mypy configuration includes per-module overrides:

```toml
[[tool.mypy.overrides]]
module = ["sdv.*", "ctgan.*", "opacus.*", "huey", "huey.*"]
ignore_missing_imports = true
```

This override was added to prevent mypy from failing when the synthesizer group
is not installed (e.g., in CI standard jobs that only install the default group).
It also applies to the `pre-commit mirrors-mypy` hook, which always runs in an
isolated environment that does not have the synthesizer group.

The Phase 20 roast identified a correctness risk: `ignore_missing_imports = true`
suppresses ALL import-level errors for these modules — including type errors that
would be caught if the packages were installed. If sdv changes its API (e.g.,
renames `CTGANSynthesizer` to `CTGANModel` in a minor release), mypy will not
detect the breakage even when running against a synthesizer-group installation.

---

## Decision

**Keep `ignore_missing_imports = true`** for `sdv.*`, `ctgan.*`, `opacus.*`.

The alternative — removing `ignore_missing_imports` and running a separate mypy
pass against the synthesizer group — was evaluated and rejected for the following
reasons:

### Why not remove `ignore_missing_imports`?

**None of these packages ship `py.typed`** (a PEP 561 marker that declares the
package provides inline types). Without `py.typed`:

- `mypy --strict` cannot use the packages' runtime types for inference.
- Even with the packages installed, mypy would fall back to treating them as
  `Any` unless explicit stub packages (`sdv-stubs`, `ctgan-stubs`, `opacus-stubs`)
  exist.
- As of 2026-03-16, no stub packages exist for sdv, ctgan, or opacus on PyPI.

**The practical outcome** is that removing `ignore_missing_imports` would switch
from "mypy skips import errors" to "mypy infers `Any` for all sdv/ctgan/opacus
symbols". This does not improve type safety — it changes a known suppression into
a silent `Any` propagation, which is objectively worse: the code appears to type-
check but the types are unverified.

### Why not add a separate mypy pass for the synthesizer group?

A separate mypy pass (e.g., a CI job that installs `--with synthesizer` and runs
`poetry run mypy src/synth_engine/modules/synthesizer/`) would exercise the
synthesizer code path. However, without stub packages or `py.typed` markers in the
upstream libraries, this pass would still infer `Any` for all sdv/ctgan/opacus calls.
The additional CI job cost is not justified by the type safety improvement.

### The correct long-term mitigation

The correct mitigation for API breakage detection is **integration tests that
exercise the real library APIs**, not mypy inference. Task T20.2 (Integration Test
Expansion) addresses this by adding a real SDV/CTGAN training integration test
(`@pytest.mark.slow`) that fails at runtime if the sdv API changes in an
incompatible way. A runtime test failure is a more reliable signal than a mypy
annotation failure for libraries without `py.typed`.

---

## Consequences

**Positive:**
- No change to the `pyproject.toml` mypy overrides — zero risk of introducing
  new mypy failures in the pre-commit hook or CI standard jobs.
- The documented decision closes the T20.4 AC3 requirement and prevents future
  reviewers from re-raising this as an undocumented deviation.
- The `ignore_missing_imports` override comment in `pyproject.toml` is updated
  to reference this ADR for traceability.

**Negative / Constraints:**
- API breakage in sdv/ctgan/opacus is NOT caught at type-check time. Mitigation:
  the T20.2 real SDV/CTGAN integration test catches it at test-run time.
- This decision is contingent on upstream packages remaining without `py.typed`.
  If sdv or opacus adds `py.typed` in a future release, this ADR should be
  re-evaluated. The `ignore_missing_imports` override should be removed at that
  point to enable genuine type checking.
- `huey` is included in the same override block because it also lacks `py.typed`.
  The same rationale applies: no stub package exists, and removing the override
  would produce `Any` inference, not real type coverage.

---

## References

- `pyproject.toml` `[[tool.mypy.overrides]]` section — current suppression config
- ADR-0017 — Synthesizer DP Library Selection (sdv/ctgan/opacus chosen)
- ADR-0017a — Opacus Secure Mode Decision
- Task T20.2 — Integration Test Expansion (real SDV training test)
- PEP 561 — Distributing and Packaging Type Information
- https://mypy.readthedocs.io/en/stable/running_mypy.html#missing-imports
