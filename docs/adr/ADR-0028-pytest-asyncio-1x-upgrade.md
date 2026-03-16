# ADR-0028 — pytest-asyncio 1.x Upgrade and Warning Filter Architecture

**Date:** 2026-03-16
**Status:** Accepted
**Deciders:** Architecture Reviewer + PM
**Task:** P10-T10.1
**Resolves:** Architecture review FINDING from P10-T10.1 — document pytest-asyncio major version upgrade decision

---

## Context

### Python 3.14 deprecations

Python 3.14.1 deprecated `asyncio.get_event_loop_policy()` and
`asyncio.set_event_loop_policy()` as part of the broader asyncio event-loop
management cleanup.  pytest-asyncio 0.26.0 called both APIs during test
collection (specifically in its event-loop scope management code), causing every
one of the 809 tests to error when the test suite is run with `-W error`:

```
DeprecationWarning: There is no current event loop
  ... asyncio.get_event_loop_policy().get_event_loop()
```

The project's CI and quality gate both pass `-W error` — a zero-tolerance stance
on unaddressed deprecations.  With pytest-asyncio 0.26.0 on Python 3.14.1, this
meant the full test suite was broken.

### Warning filter precedence (the `-W error` override problem)

pytest's `-W error` command-line flag is appended to the warning filter chain by
`_pytest.warnings.apply_warning_filters` AFTER pyproject.toml `filterwarnings`
entries are loaded.  Because `warnings.filterwarnings()` prepends new filters,
`-W error` ends up at position 0 — the highest priority — and overrides every
`"ignore"` entry declared in pyproject.toml.

This means pyproject.toml-level `filterwarnings` suppressions have no effect when
`-W error` is active.  The only way to suppress a specific warning while still
treating all other warnings as errors is to add "ignore" filters from within the
per-test warning context (i.e., from a pytest fixture), where they are prepended
after `-W error` is already at position 0.

---

## Decision

### 1. Upgrade pytest-asyncio to 1.3.0

pytest-asyncio 1.x rewrote its event-loop management to avoid calling the
deprecated `asyncio.get_event_loop_policy()` and `asyncio.set_event_loop_policy()`
APIs.  The upgrade is a major version bump (0.26.0 → 1.3.0) but requires no
changes to test code: the project already uses `asyncio_mode = "auto"` in
`pyproject.toml`, which is the recommended setting for both 0.x and 1.x.

Confirmed installed version (from `poetry.lock`): **1.3.0**.

### 2. Autouse fixture for warning filter management

An autouse fixture `_suppress_third_party_deprecation_warnings` in
`tests/conftest.py` wraps all per-test filter mutations in a
`warnings.catch_warnings()` context manager.  This provides two guarantees:

1. **Correct precedence**: Filters added inside the `with` block are prepended
   after `-W error` is at position 0, placing the "ignore" entries above it in
   the chain.
2. **Guaranteed rollback**: `catch_warnings()` snapshots the filter list on entry
   and restores it on exit.  This is safe at any fixture scope — including
   `"session"` — unlike bare `filterwarnings()` calls which mutate global state
   without cleanup.

`gc.collect()` is called after `yield` but still inside the `with` block, so
`ResourceWarning` suppression remains active during GC.  This prevents
`PytestUnraisableExceptionWarning` from firing on short-lived SQLAlchemy
in-memory engines that are finalised during teardown.

### 3. Third-party warnings suppressed by the fixture

| Warning | Source | Upstream fix ETA |
|---------|--------|-----------------|
| `module 'sre_parse' is deprecated` | `rdt` / `sdv` import chain | None known |
| `module 'sre_constants' is deprecated` | `rdt` / `sdv` import chain | None known |
| `module 'sre_compile' is deprecated` | `rdt` / `sdv` import chain | None known |
| `'asyncio.iscoroutinefunction' is deprecated` | `chromadb` telemetry module | Open issue upstream |
| `ResourceWarning` (unclosed socket/engine) | SQLAlchemy in-memory test engines | Intentional; engines are short-lived |

### 4. Exit condition

The fixture can be simplified — or removed entirely — once the upstream packages
fix their PEP 594 deprecation warnings:

- `rdt` / `sdv`: use of deprecated `sre_*` stdlib modules
- `chromadb`: replace `asyncio.iscoroutinefunction` with `inspect.iscoroutinefunction`

Until then, the fixture is the correct and only workable suppression mechanism
when `-W error` is active.

---

## Consequences

**Positive:**
- All 809 tests pass with `-W error` on Python 3.14.1.
- The `catch_warnings()` wrapper makes filter mutations scope-safe: changing the
  fixture scope to `"session"` will not leak mutations into the global warning
  state.
- The fixture is self-documenting: the docstring explains the `-W error` precedence
  problem and the reason for each suppressed warning.

**Negative / Constraints:**
- pytest-asyncio 1.x is a major version bump.  Any project that pins to
  `pytest-asyncio ^0.26` and imports from `pytest_asyncio.plugin` internals will
  break.  This project does not use internal APIs, so no breakage occurs.
- The suppression fixture must be maintained as third-party packages are upgraded.
  Each row in the table above should be audited during dependency upgrades and
  removed when no longer needed.
- If a new third-party package introduces a new PEP 594 deprecation warning, the
  CI will fail with `-W error` until a suppression is added here.  This is
  intentional — unaddressed deprecations are visible and actionable.

---

## References

- P10-T10.1: pytest-asyncio deprecation fix
- ADR-0001: Modular monolith topology
- `tests/conftest.py`: `_suppress_third_party_deprecation_warnings` fixture
- Python PEP 594: Deprecated stdlib modules
- pytest-asyncio changelog: https://pytest-asyncio.readthedocs.io/en/latest/reference/changelog.html
