# ADR-0053: demos/ Directory Placement and Quality Gate Scope

**Status:** Accepted
**Date:** 2026-03-23
**Deciders:** Engineering team
**Task:** P52-T52.1 — Benchmark Infrastructure (arch-review follow-up)

---

## Context

The P52-T52.1 benchmark infrastructure task introduced a `demos/` directory at the
repository root containing `conclave_demo.py` — a self-contained synthesis pipeline
wrapper designed for interactive notebook usage and offline walkthroughs.

The architecture reviewer raised a FINDING: the `demos/` directory lacked documented
rationale for its placement outside `src/synth_engine/`, its `sys.path.insert` usage,
its relationship to the existing `scripts/` directory, and its exemption from the
`mypy --strict` and 95% coverage quality gates.

This ADR formally documents the decision.

---

## Decision

### 1. Placement: top-level `demos/` outside `src/synth_engine/`

`demos/` is a **top-level package** outside the production source tree.  It is not
production code and does not belong inside `src/synth_engine/`.  Placing it there
would subject demo scripts to the full suite of production quality gates (`mypy
--strict`, 95% coverage, import-linter boundary contracts) — requirements that are
inappropriate for interactive demo wrappers where developer experience and
notebook-friendliness are the primary goals.

The `demos/` directory follows the same convention used by many open-source Python
projects (e.g., `examples/`, `notebooks/`, `demos/`) that keep exploratory or
illustrative code separate from the importable library.

### 2. `sys.path.insert` pattern

`demos/conclave_demo.py` prepends the `src/` directory to `sys.path` at import time:

```python
_REPO_ROOT = Path(__file__).parent.parent
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
```

This is the **standard pattern for scripts and notebooks** that need to import from
a sibling source tree without requiring a package install.  It is idempotent (guarded
by the `not in sys.path` check) and does not affect the production runtime — the
guard prevents double-insertion if the package is also installed in the environment.

This pattern is appropriate for `demos/` because:
- Demo consumers may run files directly (`python demos/conclave_demo.py`) or from
  a Jupyter notebook without installing the package.
- The `src/` layout convention (used by this project) means the package is not on
  `sys.path` by default unless installed via `pip install -e .` or `poetry install`.

### 3. Optional dependency group

`demos/` depends on the optional `demos` dependency group defined in `pyproject.toml`.
Consumers must install it explicitly:

```bash
poetry install --with demos
```

This ensures that notebook-only dependencies (e.g., `jupyterlab`, visualization
libraries) are never pulled into the production environment.

### 4. Relationship to `scripts/`

| Directory | Purpose | Invocation |
|-----------|---------|------------|
| `scripts/` | CLI tools, automation helpers, one-off maintenance scripts | `python scripts/foo.py` or as shell commands |
| `demos/` | Notebook wrappers and interactive pipeline walkthroughs | `from demos.X import run_X` in a notebook, or direct execution |

`scripts/` tools are operational — they interact with the running system or perform
maintenance tasks.  `demos/` code is illustrative — it runs an isolated, self-contained
pipeline to show what the system does, using fictional data and ephemeral storage.

### 5. Quality gate scope

| Gate | `src/synth_engine/` | `demos/` | Rationale |
|------|--------------------|---------:|-----------|
| `mypy --strict` | Yes | No | Demo scripts use dynamic imports and optional deps that would require significant `# type: ignore` scaffolding; strict mode is inappropriate for illustrative code |
| `pytest --cov-fail-under=95` | Yes | No | Demos are not covered by the production test suite; they are validated manually or via separate benchmark runs |
| `ruff check` | Yes | Yes (CI gap — see below) | Style and correctness linting applies to all Python in the repo |
| `bandit` | Yes | Yes (CI gap — see below) | Security scanning applies to all Python in the repo |
| `pre-commit` | Yes | Yes | All hooks run on all files |

**CI gap**: As of P52, the CI linting and security scan jobs target `src/` and `tests/`
only.  Extending `ruff` and `bandit` coverage to `demos/` is a tracked improvement
(to be addressed in a future CI hardening task).  Until that task is complete, `demos/`
should be scanned manually during review.

---

## Consequences

**Positive:**
- Demo consumers get a frictionless experience — direct file execution and notebook
  `import` both work without a package install.
- Production quality gates (`mypy --strict`, 95% coverage) remain meaningful because
  they are not diluted by demo code that can never satisfy them cleanly.
- The `sys.path` guard is idempotent — no side effects in environments where the
  package is already installed.
- The `demos` optional dependency group prevents notebook dependencies from leaking
  into production containers.

**Negative / Constraints:**
- `demos/` is not covered by the automated test suite.  Regression risk is accepted
  in exchange for notebook usability.
- The CI gap for `ruff` and `bandit` on `demos/` means demo code can silently
  accumulate style violations or low-severity security findings between manual reviews.
  This is tracked as a technical debt item.
- Strict typing is not enforced on `demos/` — type errors in demo code will not block
  CI.  Reviewers must inspect demo type annotations manually.

---

## Alternatives Considered

| Option | Rejected Because |
|--------|-----------------|
| Place demos inside `src/synth_engine/bootstrapper/` or a new `demos/` module | Subjects illustrative code to production quality gates it cannot satisfy cleanly; pollutes the importable package namespace |
| Place demos inside `tests/` | Tests are for automated verification, not interactive walkthroughs; mixing them would confuse the test runner and coverage reporting |
| Require `pip install -e .` before any demo usage | Removes the notebook-friendliness that is the primary motivation for the `sys.path.insert` pattern |
| Install `demos/` as a separate package | Over-engineered for a single demo module; adds packaging overhead with no benefit |

---

## References

- `demos/conclave_demo.py` — the demo wrapper this ADR governs
- `pyproject.toml` — `[tool.poetry.group.demos.dependencies]` optional group
- ADR-0001 — Modular Monolith Topology (defines `src/synth_engine/` as the production boundary)
- P52-T52.1 architecture review finding (arch-reviewer, 2026-03-23)
