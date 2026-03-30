"""Gate 2 — Assertion density meta-test (P73).

Scans ALL test files using AST parsing and enforces three rules:

1. No test function may have ONLY weak assertions as its sole assertions.
   Weak assertions: ``assert x is None``, ``assert x is not None``,
   ``assert isinstance(x, T)``, ``assert x is True``, ``assert x is False``,
   bare ``assert x`` (single name).

2. The average assertion density across the suite must be ≥ 1.5 assertions
   per test function.

3. The weak assertion ratio must be ≤ 30% of total assertions across the suite.

CRITICAL DESIGN NOTES:
- Guard assertions are NOT violations.  ``assert x is not None`` followed by
  ``assert x.field == expected`` in the SAME function is fine — the function
  has at least one specific-value assertion.  Only functions where ALL
  assertions are weak are flagged for Rule 1.
- Tests with ZERO assertions are treated as weak-only (0 total == 0 specific).
- This file itself is excluded from its own scan (would cause recursion issues).
- Attack test files (``*_attack.py``) are scanned by the same rules.

Constitution Priority 4: Comprehensive Testing.
Task: P73 — Test Quality Rehabilitation.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Minimum average assertions per test function (Rule 2).
_MIN_DENSITY: float = 1.5

#: Maximum allowed ratio of weak assertions to total assertions (Rule 3).
_MAX_WEAK_RATIO: float = 0.30

#: Path to the tests directory.
_TESTS_ROOT: Path = Path(__file__).parent.parent

#: This file — excluded from self-scan.
_THIS_FILE: Path = Path(__file__).resolve()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_weak_assertion(assert_test: ast.expr) -> bool:
    """Return True if the assertion test expression is a weak/shallow check.

    Weak patterns (sole assertion is insufficient per Constitution Priority 4):
    - ``assert x is None``      (Is comparison to None)
    - ``assert x is not None``  (IsNot comparison to None)
    - ``assert x is True``      (Is comparison to True)
    - ``assert x is False``     (Is comparison to False)
    - ``assert isinstance(x, T)`` (isinstance call)
    - ``assert x``              (bare Name — truthiness only)

    Args:
        assert_test: The ``.test`` attribute of an ``ast.Assert`` node.

    Returns:
        True when the expression matches one of the weak patterns.
    """
    # isinstance(x, T)
    if isinstance(assert_test, ast.Call):
        if isinstance(assert_test.func, ast.Name) and assert_test.func.id == "isinstance":
            return True

    # Bare truthiness: assert varname
    if isinstance(assert_test, ast.Name):
        return True

    # Is / IsNot comparisons
    if isinstance(assert_test, ast.Compare):
        for op in assert_test.ops:
            if isinstance(op, ast.Is | ast.IsNot):
                for comparator in assert_test.comparators:
                    if isinstance(comparator, ast.Constant) and comparator.value in (
                        None,
                        True,
                        False,
                    ):
                        return True

    return False


def _has_pytest_raises(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function uses ``pytest.raises(...)`` as a context manager.

    A ``with pytest.raises(SomeException):`` block asserts a specific exception
    type — it is a behavioral assertion equivalent to asserting a return value.
    Functions that ONLY use ``pytest.raises`` (no other assertions) should not
    be flagged as violations.

    Args:
        func: AST function definition node.

    Returns:
        True when at least one ``pytest.raises(...)`` with-statement exists.
    """
    for node in ast.walk(func):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            ctx = item.context_expr
            if not isinstance(ctx, ast.Call):
                continue
            func_node = ctx.func
            # pytest.raises(...)
            if isinstance(func_node, ast.Attribute):
                if (
                    isinstance(func_node.value, ast.Name)
                    and func_node.value.id == "pytest"
                    and func_node.attr == "raises"
                ):
                    # Must have at least one positional arg (the exception type)
                    if ctx.args:
                        return True
            # raises(...) — direct import
            if isinstance(func_node, ast.Name) and func_node.id == "raises":
                if ctx.args:
                    return True
    return False


def _analyse_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int]:
    """Count total and weak assertions in a test function.

    Counts both ``ast.Assert`` nodes and ``pytest.raises(...)`` context
    managers as assertions.  A ``pytest.raises(SomeException)`` block asserts
    a specific exception type — it is a strong behavioral assertion.

    Args:
        func: AST function definition node.

    Returns:
        Tuple of (total_assertions, weak_assertions).  A function that uses
        only ``pytest.raises`` has total >= 1 and weak == 0.
    """
    total = 0
    weak = 0
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            total += 1
            if _is_weak_assertion(node.test):
                weak += 1

    # Count pytest.raises(...) blocks as strong (non-weak) assertions.
    if _has_pytest_raises(func):
        total += 1  # contributes one strong assertion

    return total, weak


def _collect_test_functions(
    path: Path,
) -> list[tuple[str, int, int, int]]:
    """Parse a test file and return info about each test function.

    Args:
        path: Absolute path to a test file.

    Returns:
        List of (function_name, lineno, total_assertions, weak_assertions)
        tuples for every function whose name starts with ``test_``.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    results: list[tuple[str, int, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test_"
        ):
            total, weak = _analyse_function(node)
            results.append((node.name, node.lineno, total, weak))
    return results


def _gather_all_test_data() -> tuple[
    list[tuple[Path, str, int, int, int]],  # all functions
    list[tuple[Path, str, int, int, int]],  # shallow-only violations
]:
    """Scan all test files and return collected function data.

    Returns:
        Tuple of:
        - ``all_functions``: every (path, name, lineno, total, weak) entry.
        - ``violations``: functions where total == 0 or total == weak (all weak).
    """
    all_functions: list[tuple[Path, str, int, int, int]] = []
    violations: list[tuple[Path, str, int, int, int]] = []

    for test_file in sorted(_TESTS_ROOT.rglob("test_*.py")):
        if test_file.resolve() == _THIS_FILE:
            continue
        relative = test_file.relative_to(_TESTS_ROOT)
        for name, lineno, total, weak in _collect_test_functions(test_file):
            entry = (relative, name, lineno, total, weak)
            all_functions.append(entry)
            if total == 0 or total == weak:
                violations.append(entry)

    return all_functions, violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_test_function_has_only_weak_assertions() -> None:
    """Rule 1: No test function may have ONLY weak assertions.

    A function that only uses ``assert x is not None``, ``assert isinstance(...)``,
    ``assert x is True/False``, or bare ``assert x`` as its SOLE assertions is
    insufficient per Constitution Priority 4.

    Guard assertions (``assert x is not None`` BEFORE ``assert x.value == 42``)
    are NOT violations — the function must have at least one specific-value
    assertion to pass.
    """
    _, violations = _gather_all_test_data()

    if not violations:
        return

    # Format a human-readable failure report
    lines = [
        f"[Gate 2 — Assertion Density (P73)] {len(violations)} test function(s) have "
        f"ONLY weak assertions (no specific-value check).\n",
        "Violations (file :: function :: line):\n",
    ]
    for path, name, lineno, total, weak in violations[:40]:
        lines.append(f"  {path}::{name} (line {lineno}, {total} assert(s), {weak} weak)")

    if len(violations) > 40:
        lines.append(f"  ... and {len(violations) - 40} more.")

    lines.append(
        textwrap.dedent(
            """
            Fix: add at least one specific-value assertion to each flagged function.
            Example: replace `assert result is not None` with
            `assert result is not None` (guard) + `assert result.status == "active"`.
            """
        )
    )

    pytest.fail("\n".join(lines))


def test_average_assertion_density_meets_minimum() -> None:
    """Rule 2: Average assertion density must be >= 1.5 assertions per test function.

    Density = total assertions / total test functions, across the entire suite.
    A density below 1.5 indicates systematic underspecification of behavior.
    """
    all_functions, _ = _gather_all_test_data()

    if not all_functions:
        pytest.fail("No test functions found — suite appears empty.")

    total_asserts = sum(entry[3] for entry in all_functions)
    total_funcs = len(all_functions)
    density = total_asserts / total_funcs

    assert density >= _MIN_DENSITY, (
        f"[Gate 2 — Assertion Density (P73)] Average assertion density is {density:.2f} "
        f"(requirement: >= {_MIN_DENSITY}).\n"
        f"Total assertions: {total_asserts}, total test functions: {total_funcs}.\n"
        f"Add specific-value assertions to underspecified tests."
    )


def test_weak_assertion_ratio_within_limit() -> None:
    """Rule 3: Weak assertions must be <= 30% of all assertions suite-wide.

    Weak assertions include ``is None``, ``is not None``, ``isinstance``,
    ``is True``, ``is False``, and bare truthiness checks.  When used as guard
    assertions before specific-value checks they are fine; a high ratio suite-
    wide indicates systematic shallow testing.
    """
    all_functions, _ = _gather_all_test_data()

    total_asserts = sum(entry[3] for entry in all_functions)
    total_weak = sum(entry[4] for entry in all_functions)

    if total_asserts == 0:
        pytest.fail("No assertions found across the entire test suite.")

    ratio = total_weak / total_asserts

    assert ratio <= _MAX_WEAK_RATIO, (
        f"[Gate 2 — Assertion Density (P73)] Weak assertion ratio is {ratio:.1%} "
        f"(requirement: <= {_MAX_WEAK_RATIO:.0%}).\n"
        f"Weak assertions: {total_weak}, total assertions: {total_asserts}.\n"
        f"Replace or supplement weak assertions with specific-value checks."
    )
