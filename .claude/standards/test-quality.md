# Test Quality Standards (P78 Meta-Development)

Shared reference for both the software-developer agent (prevention) and qa-reviewer agent
(detection). Single source of truth — both agents reference this file.

These standards exist because recurring audits found the same test quality problems being
regenerated phase after phase. They are enforceable rules with programmatic gates.

---

## Rule A — Parametrize or Perish

When 3+ test functions share the same structure (same setup pattern, same assertion pattern,
different inputs/expected values), you MUST use `@pytest.mark.parametrize`. Writing copy-paste
test functions with varied inputs is a process violation. Before writing a third similar test
function, stop and refactor the first two into a parametrized test.

## Rule B — No Tautological Assertions

The following assertion patterns are FORBIDDEN. Gate 3 (conftest plugin) will fail CI if present:

- `assert str(result) == "None"` — testing that None is None
- `assert func.__name__ == "func_name"` — testing that imports work
- `assert SomeClass.__name__ == "SomeClass"` — testing that a class has its own name
- Consecutive redundant assertions: `assert x is True` followed by `assert x`, or
  `assert result is False` followed by `assert not result`

## Rule C — No Coverage Gaming

Every assertion must test a behavioral property of the code under test. Assertions that exist
solely to generate coverage hits are forbidden:

- Testing that a void function returns None (unless None-vs-exception is the behavior)
- Testing that a module can be imported (unless import-time side effects are the behavior)
- Testing that a class/function has the name it was imported under
- Asserting the same property twice with different syntax

## Rule D — Fixture Reuse

Before defining a local fixture, check `tests/conftest.py` and `tests/unit/conftest.py`.
If an equivalent fixture exists (e.g., `_clear_settings_cache`), use it — do NOT duplicate
it locally. If you need a helper in 2+ test files, put it in the appropriate conftest.py,
not copy-pasted into each file.

## Rule E — Test File Size Limit

No test file may exceed 800 lines. Gate 6 (conftest plugin) will fail CI if any test file
exceeds this limit without a `# gate-exempt: <reason>` comment on line 1. If you approach
800 lines, you MUST increase parametrize usage or split by concern.

## Rule F — Helpers Over Duplication

If you define a helper function (e.g., `_make_persons_df`, `_create_test_job`), search the
file and other test files for duplicates. If the same helper exists elsewhere, extract it to
conftest or a `tests/helpers/` module. Never define the same helper function twice.

## Rule G — Integration Test Isolation

Integration tests must reset state between tests via fixture scope. No cross-test database
state leakage. Specifically:

- Use `pytest-postgresql` fixtures for real PostgreSQL tests (not persistent local databases)
- Each test function or class must start with a clean database state (transaction rollback
  or table truncation)
- Tests must not depend on execution order — running tests in isolation or in any order
  must produce the same result
- Redis state used in integration tests must be namespaced and cleaned up per-test

This rule exists because Tier 8 phases (multi-tenancy, RBAC, metering, multi-database)
all require integration tests that share PostgreSQL and Redis infrastructure. Without
isolation guarantees, the test suite becomes order-dependent.

---

## QA Reviewer Detection Checklist

When reviewing test files in the diff, check for:

| Anti-Pattern | Classification | What to Look For |
|-------------|---------------|-----------------|
| Tautological assertions | FINDING | `assert str(...) == "None"`, `__name__` checks, consecutive redundant assertions |
| Copy-paste tests | FINDING | 3+ functions with identical structure, different inputs |
| Fixture duplication | FINDING | New fixture that already exists in conftest.py |
| Test file bloat | FINDING | Any file > 800 lines without `# gate-exempt:` |
