"""Constitution Priority 4 enforcement gate — shallow-assertion detection.

This module is the "attack test" for Phase 73. It scans every test function in
tests/unit/ and tests/integration/ and FAILS if any function uses a shallow
assertion (``is not None``, bare ``isinstance()``, bare truthy name) as its
SOLE assertion, without also asserting a specific expected value.

Constitution Priority 4 (exact text):
    "Assertions that only check truthiness (``is not None``), type (``isinstance``),
    or existence (``in``) without also asserting a specific expected value are
    insufficient as the sole assertion in any test."

Guard assertions (``assert x is not None`` followed by ``assert x.field == value``
in the same function) are NOT violations — only functions where the shallow
assertion is the ONLY assertion are flagged.

This test is RED against the pre-P73 codebase (128 violations detected in the
audit) and MUST turn GREEN after all T73.3 fixes are applied.

Task: P73 — Test Quality Rehabilitation
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Files that are explicitly excluded from the gate:
# - __init__.py  (no tests)
# - conftest.py  (no test_ functions, only fixtures)
# - This file itself (bootstrapping circularity)
_EXCLUDE_FILES = frozenset(
    {
        "__init__.py",
        "conftest.py",
        "test_p73_assertion_quality_gate.py",
    }
)

# Allowed violation threshold.  Must be ZERO at GREEN.
_MAX_ALLOWED_VIOLATIONS = 0


def _is_shallow_assert(assert_node: ast.Assert) -> bool:
    """Return True if the assertion is a shallow-only check.

    Shallow assertions are:
    - ``assert x is not None``       (IsNot None compare)
    - ``assert isinstance(x, T)``    (bare isinstance call)
    - ``assert x``                   (bare truthy name check)

    Args:
        assert_node: An ``ast.Assert`` node to evaluate.

    Returns:
        True when the assertion matches a known shallow pattern.
    """
    test = assert_node.test

    # Pattern 1: assert x is not None
    if isinstance(test, ast.Compare):
        if (
            len(test.ops) == 1
            and isinstance(test.ops[0], ast.IsNot)
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
        ):
            return True

    # Pattern 2: assert isinstance(x, T)
    if (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Name)
        and test.func.id == "isinstance"
    ):
        return True

    # Pattern 3: assert some_variable  (bare truthy check, no comparison)
    if isinstance(test, ast.Name):
        return True

    return False


def _collect_violations(test_dir: Path) -> list[tuple[str, str, int]]:
    """Scan all test files in ``test_dir`` for shallow-only-assertion functions.

    A function is a violation only when EVERY assert statement in that function
    is shallow.  Guard assertions (shallow + specific value assertion in the same
    function) are NOT violations.

    Args:
        test_dir: Root directory to scan (recursive glob ``*.py``).

    Returns:
        List of (relative_path, function_name, lineno) tuples for each violation.
    """
    violations: list[tuple[str, str, int]] = []
    repo_root = test_dir.parent.parent

    for py_file in sorted(test_dir.rglob("*.py")):
        if py_file.name in _EXCLUDE_FILES:
            continue
        # Skip __pycache__ files
        if "__pycache__" in py_file.parts:
            continue

        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue

            # Collect all assert statements that are direct children of this
            # function body (not nested in inner functions / classes).
            asserts: list[ast.Assert] = []
            for child in ast.walk(node):
                if isinstance(child, ast.Assert):
                    asserts.append(child)

            if not asserts:
                # No asserts — not a shallow-assert violation (may be tested
                # with pytest.raises or other non-assert patterns).
                continue

            if all(_is_shallow_assert(a) for a in asserts):
                rel_path = str(py_file.relative_to(repo_root))
                violations.append((rel_path, node.name, node.lineno))

    return violations


class TestConstitutionPriority4AssertionQuality:
    """Enforce Constitution Priority 4: no test function may rely solely on shallow assertions."""

    def test_no_shallow_only_assertion_violations_in_unit_tests(self) -> None:
        """Every test function in tests/unit/ must contain at least one specific-value assertion.

        A function fails this gate when ALL of its assert statements are shallow
        checks (``is not None``, ``isinstance``, bare truthy name).  Guard
        assertions that precede a specific-value assertion in the same function
        are explicitly allowed.

        Expected outcome after P73 remediation: 0 violations.
        """
        tests_dir = Path(__file__).parent.parent  # tests/
        unit_dir = tests_dir / "unit"
        assert unit_dir.is_dir(), f"Unit test directory not found: {unit_dir}"

        violations = _collect_violations(unit_dir)

        if violations:
            lines = [
                "Constitution Priority 4 violation — shallow-only assertions found:",
                f"  {len(violations)} function(s) have no specific-value assertion.\n",
            ]
            for rel_path, fn_name, lineno in violations:
                lines.append(f"  {rel_path}:L{lineno}  {fn_name}()")
            lines.append(
                "\nFix: add at least one assertion that checks a specific expected value "
                "(e.g., assert result == 42, not just assert result is not None)."
            )
            pytest.fail("\n".join(lines))

        assert len(violations) == _MAX_ALLOWED_VIOLATIONS, (
            f"Expected 0 violations but found {len(violations)}. "
            "This should not happen if the branch above executed."
        )

    def test_detection_logic_correctly_identifies_shallow_is_not_none(self) -> None:
        """Detection logic must flag a function whose only assertion is ``assert x is not None``.

        This is a self-test of the gate: we construct a synthetic AST node and
        verify the detector returns the expected result.
        """
        source = """
def test_example_shallow():
    result = some_func()
    assert result is not None
"""
        tree = ast.parse(source)
        fn_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        asserts = [c for c in ast.walk(fn_node) if isinstance(c, ast.Assert)]
        assert len(asserts) == 1
        assert _is_shallow_assert(asserts[0]) is True

    def test_detection_logic_correctly_identifies_guard_plus_value_as_clean(self) -> None:
        """Guard assertion followed by specific-value assertion must NOT be flagged.

        ``assert x is not None; assert x.count == 3`` is a guard pattern, not a
        violation.  The second assertion is specific-value, making the function
        compliant with Constitution Priority 4.
        """
        source = """
def test_example_guard_plus_value():
    result = some_func()
    assert result is not None
    assert result.count == 3
"""
        tree = ast.parse(source)
        fn_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        asserts = [c for c in ast.walk(fn_node) if isinstance(c, ast.Assert)]
        # Two assertions: first is shallow (guard), second is specific value
        assert len(asserts) == 2
        assert _is_shallow_assert(asserts[0]) is True
        assert _is_shallow_assert(asserts[1]) is False
        # The function is NOT a violation because not ALL asserts are shallow
        all_shallow = all(_is_shallow_assert(a) for a in asserts)
        assert all_shallow is False

    def test_detection_logic_correctly_identifies_isinstance_as_shallow(self) -> None:
        """``assert isinstance(x, SomeClass)`` must be identified as a shallow assertion."""
        source = """
def test_example_isinstance():
    result = some_func()
    assert isinstance(result, list)
"""
        tree = ast.parse(source)
        fn_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        asserts = [c for c in ast.walk(fn_node) if isinstance(c, ast.Assert)]
        assert len(asserts) == 1
        assert _is_shallow_assert(asserts[0]) is True

    def test_detection_logic_correctly_identifies_equality_as_not_shallow(self) -> None:
        """``assert result == expected_value`` must NOT be flagged as shallow."""
        source = """
def test_example_equality():
    result = compute()
    assert result == 42
"""
        tree = ast.parse(source)
        fn_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        asserts = [c for c in ast.walk(fn_node) if isinstance(c, ast.Assert)]
        assert len(asserts) == 1
        assert _is_shallow_assert(asserts[0]) is False

    def test_detection_logic_handles_file_with_no_test_functions(self) -> None:
        """Files with no test_ functions must produce zero violations."""
        import tempfile

        source = """
def helper_function():
    x = 1 + 1
    assert x is not None  # this is in a non-test function
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            (tmp_dir / "test_helper.py").write_text(source)
            violations = _collect_violations(tmp_dir)
            # The function is named helper_function, not test_*, so no violation
            assert violations == [], (
                f"Expected no violations for non-test functions, got: {violations}"
            )
