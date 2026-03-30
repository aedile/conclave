"""Unit tests for CI infrastructure compliance (P8-T8.4).

Verifies that:
  1. All synthesizer integration test files carry the ``pytest.mark.synthesizer``
     marker -- either via ``pytestmark`` module-level assignment (idiomatic) or as
     a ``@pytest.mark.synthesizer`` decorator on each test function or class.
  2. The ``synthesizer`` marker is registered in ``pyproject.toml``.

No external services are required.  These are pure file-inspection tests.

Task: P8-T8.4 -- CI Infrastructure (ADV-052, ADV-062, ADV-065, ADV-066, ADV-069)
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths to inspect
# ---------------------------------------------------------------------------
INTEGRATION_DIR = Path(__file__).parent.parent / "integration"
PYPROJECT = Path(__file__).parent.parent.parent / "pyproject.toml"


def _is_synthesizer_mark_node(node: ast.expr) -> bool:
    """Return True if an AST expression node represents ``pytest.mark.synthesizer``.

    Matches the Attribute chain ``pytest.mark.synthesizer``.

    Args:
        node: An AST expression node to inspect.

    Returns:
        True when the node is the ``pytest.mark.synthesizer`` attribute chain.
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "synthesizer"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _has_synthesizer_marker(filepath: Path) -> bool:
    """Return True if a test file carries the ``pytest.mark.synthesizer`` marker.

    Two idiomatic patterns are accepted:

    1. Module-level ``pytestmark`` assignment::

           pytestmark = pytest.mark.synthesizer
           # or
           pytestmark = [pytest.mark.integration, pytest.mark.synthesizer]

    2. Per-function/class decorator::

           @pytest.mark.synthesizer
           class TestFoo: ...

    Args:
        filepath: Absolute path to the Python test file.

    Returns:
        True if the marker is present via either pattern.
    """
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))

    for node in ast.iter_child_nodes(tree):
        # Pattern 1: pytestmark = pytest.mark.synthesizer (or list containing it)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    if _is_synthesizer_mark_node(node.value):
                        return True
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if _is_synthesizer_mark_node(elt):
                                return True

        # Pattern 2: @pytest.mark.synthesizer decorator on function or class
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for decorator in node.decorator_list:
                if _is_synthesizer_mark_node(decorator):
                    return True

    return False


class TestSynthesizerMarkerPresent:
    """Verify pytest.mark.synthesizer exists on all synthesizer test files."""

    def test_test_synthesizer_integration_has_marker(self) -> None:
        """test_synthesizer_integration.py must carry pytest.mark.synthesizer."""
        filepath = INTEGRATION_DIR / "test_synthesizer_integration.py"
        assert _has_synthesizer_marker(filepath), (
            f"{filepath.name} does not carry pytest.mark.synthesizer. "
            "Add pytestmark = pytest.mark.synthesizer so CI can route with "
            "`pytest -m synthesizer` instead of explicit file lists."
        )

    def test_test_dp_training_integration_has_marker(self) -> None:
        """test_dp_training_integration.py must carry pytest.mark.synthesizer."""
        filepath = INTEGRATION_DIR / "test_dp_training_integration.py"
        assert _has_synthesizer_marker(filepath), (
            f"{filepath.name} does not carry pytest.mark.synthesizer. "
            "Add pytestmark = pytest.mark.synthesizer so CI can route with "
            "`pytest -m synthesizer` instead of explicit file lists."
        )

    def test_test_dp_wiring_integration_has_marker(self) -> None:
        """test_dp_wiring_integration.py must carry pytest.mark.synthesizer."""
        filepath = INTEGRATION_DIR / "test_dp_wiring_integration.py"
        assert _has_synthesizer_marker(filepath), (
            f"{filepath.name} does not carry pytest.mark.synthesizer. "
            "Add pytestmark = pytest.mark.synthesizer so CI can route with "
            "`pytest -m synthesizer` instead of explicit file lists."
        )

    def test_test_e2e_dp_synthesis_has_marker(self) -> None:
        """test_e2e_dp_synthesis.py must carry pytest.mark.synthesizer."""
        filepath = INTEGRATION_DIR / "test_e2e_dp_synthesis.py"
        assert _has_synthesizer_marker(filepath), (
            f"{filepath.name} does not carry pytest.mark.synthesizer. "
            "Add pytestmark = pytest.mark.synthesizer so CI can route with "
            "`pytest -m synthesizer` instead of explicit file lists."
        )

    def test_synthesizer_marker_registered_in_pyproject(self) -> None:
        """The ``synthesizer`` marker must be registered in pyproject.toml markers list."""
        content = PYPROJECT.read_text(encoding="utf-8")
        assert '"synthesizer:' in content or "'synthesizer:" in content, (
            "The 'synthesizer' pytest marker is not registered in pyproject.toml. "
            "Add it to the [tool.pytest.ini_options] markers list."
        )


class TestSynthesizerMarkerNegative:
    """Verify _has_synthesizer_marker returns False when the marker is absent.

    Guards against false-positives in the marker detection logic -- a file with
    no pytest.mark.synthesizer annotation must NOT be reported as carrying the
    marker.
    """

    def test_returns_false_for_file_without_marker(self, tmp_path: Path) -> None:
        """_has_synthesizer_marker must return False for a file with no marker."""
        snippet = textwrap.dedent(
            """\
            import pytest

            pytestmark = pytest.mark.integration  # only integration, NOT synthesizer

            def test_something() -> None:
                assert True
            """
        )
        target = tmp_path / "test_no_synthesizer_marker.py"
        target.write_text(snippet, encoding="utf-8")

        result = _has_synthesizer_marker(target)
        assert result is False, (
            "_has_synthesizer_marker returned True for a file that only carries "
            "pytest.mark.integration, not pytest.mark.synthesizer. "
            "The marker detection logic has a false-positive bug."
        )
        assert not result

    def test_returns_false_for_empty_file(self, tmp_path: Path) -> None:
        """_has_synthesizer_marker must return False for a file with no markers at all."""
        target = tmp_path / "test_empty.py"
        target.write_text("def test_placeholder() -> None:\n    pass\n", encoding="utf-8")

        result = _has_synthesizer_marker(target)
        assert result is False, (
            "_has_synthesizer_marker returned True for a file with no markers. "
            "The marker detection logic has a false-positive bug."
        )
        assert not result


# ---------------------------------------------------------------------------
# Additional paths for CI configuration inspection
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent.parent
ALEMBIC_VERSIONS = REPO_ROOT / "alembic" / "versions"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CI_LOCAL_SH = REPO_ROOT / "scripts" / "ci-local.sh"


class TestADV052AlembicMigration:
    """ADV-052: Alembic migration for connection and setting tables must exist.

    The connection and setting tables were added in P5-T5.1 without a
    corresponding Alembic migration.  Migration 002 fills this gap.
    """

    def test_migration_002_exists(self) -> None:
        """Migration 002 (for connection/setting tables) must be present."""
        version_files = list(ALEMBIC_VERSIONS.glob("*.py"))
        names = [f.name for f in version_files if not f.name.startswith("__")]
        assert any("002" in name for name in names), (
            "No alembic version file with '002' in its name was found. "
            "ADV-052 requires a dedicated migration for connection/setting tables."
        )

    def test_migration_002_creates_connection_table(self) -> None:
        """Migration 002 must create the 'connection' table via op.create_table."""
        for f in ALEMBIC_VERSIONS.glob("*.py"):
            if "002" in f.name:
                content = f.read_text()
                assert "op.create_table" in content, (
                    f"{f.name}: expected op.create_table calls but found none."
                )
                assert '"connection"' in content or "'connection'" in content, (
                    f"{f.name}: expected create_table for 'connection' table."
                )
                return
        raise AssertionError("No migration 002 file found to inspect.")

    def test_migration_002_creates_setting_table(self) -> None:
        """Migration 002 must create the 'setting' table via op.create_table."""
        for f in ALEMBIC_VERSIONS.glob("*.py"):
            if "002" in f.name:
                content = f.read_text()
                assert '"setting"' in content or "'setting'" in content, (
                    f"{f.name}: expected create_table for 'setting' table."
                )
                return
        raise AssertionError("No migration 002 file found to inspect.")

    def test_migration_002_has_down_revision_001(self) -> None:
        """Migration 002 must chain from revision 001 (down_revision = '001')."""
        for f in ALEMBIC_VERSIONS.glob("*.py"):
            if "002" in f.name:
                content = f.read_text()
                assert "down_revision" in content, f"{f.name}: must declare down_revision."
                assert '"001"' in content or "'001'" in content, (
                    f"{f.name}: down_revision must point to '001'."
                )
                return
        raise AssertionError("No migration 002 file found to inspect.")


class TestADV066ZeroWarningPolicy:
    """ADV-066: Zero-warning policy must be active in all CI environments.

    ``-W error`` CLI flags ARE processed by Python after ini-file ``filterwarnings``
    entries.  Because ``warnings.filterwarnings()`` prepends to the filter chain,
    ``-W error`` ends up at the TOP and overrides every ``"ignore"`` entry in
    ``pyproject.toml``, causing spurious failures from third-party
    DeprecationWarnings in SDV/opacus/torch.

    The correct fix — implemented in ``tests/conftest.py`` — is the autouse
    fixture ``_suppress_third_party_deprecation_warnings``.  It adds ``"ignore"``
    filters inside a ``warnings.catch_warnings()`` context per test, so they are
    prepended AFTER ``-W error`` is already in the chain.  This restores correct
    precedence without removing the zero-warning enforcement.
    """

    def test_pyproject_filterwarnings_has_error_baseline(self) -> None:
        """pyproject.toml filterwarnings must have 'error' as the first entry."""
        import tomllib

        with open(PYPROJECT, "rb") as fh:
            config = tomllib.load(fh)
        fw: list[str] = (
            config.get("tool", {})
            .get("pytest", {})
            .get("ini_options", {})
            .get("filterwarnings", [])
        )
        assert fw, "filterwarnings must not be empty in pyproject.toml."
        assert fw[0] == "error", (
            f"First filterwarnings entry must be 'error' (zero-warning baseline). Got: '{fw[0]}'."
        )

    def test_ci_yml_references_adv066(self) -> None:
        """ci.yml must document ADV-066 compliance via a comment."""
        content = CI_YML.read_text()
        assert "ADV-066" in content, (
            "ci.yml must include an ADV-066 reference documenting the zero-warning "
            "policy and why filterwarnings in pyproject.toml satisfies it."
        )

    def test_ci_local_sh_references_adv066(self) -> None:
        """ci-local.sh must document ADV-066 compliance via a comment."""
        content = CI_LOCAL_SH.read_text()
        assert "ADV-066" in content, (
            "scripts/ci-local.sh must include an ADV-066 reference documenting "
            "the zero-warning policy per the advisory drain requirement."
        )


class TestADV065ZapCleanup:
    """ADV-065: ZAP CI job must clean up zap_test.db after completion."""

    def test_zap_job_has_cleanup_step(self) -> None:
        """ci.yml ZAP baseline job must delete zap_test.db after the scan."""
        import re

        content = CI_YML.read_text()
        assert "zap_test.db" in content, "ci.yml ZAP job must reference zap_test.db."
        assert re.search(r"rm\s+-f\s+[^\n]*zap_test\.db", content) or re.search(
            r"rm\s+[^\n]*zap_test\.db", content
        ), (
            "ci.yml ZAP job must have a cleanup step that removes zap_test.db. "
            "ADV-065 requires cleanup to prevent leftover test artifacts."
        )


class TestADV062FrontendArtifact:
    """ADV-062: Frontend build artifact must be shared between frontend and e2e jobs."""

    def test_frontend_job_uploads_build_artifact(self) -> None:
        """ci.yml frontend job must upload an artifact named 'frontend-dist'."""
        content = CI_YML.read_text()
        assert "name: frontend-dist" in content, (
            "ci.yml frontend job must upload artifact named 'frontend-dist'. "
            "ADV-062: Share the built dist/ between frontend and e2e jobs."
        )

    def test_e2e_job_downloads_build_artifact(self) -> None:
        """ci.yml e2e job must download the frontend build artifact."""
        content = CI_YML.read_text()
        assert "download-artifact" in content, (
            "ci.yml e2e job must use actions/download-artifact. "
            "ADV-062: no double-build — reuse the frontend artifact."
        )

    def test_e2e_job_does_not_run_npm_build(self) -> None:
        """ci.yml e2e job must NOT run 'npm run build' (avoids double-build)."""
        import re

        content = CI_YML.read_text()
        # Extract only the e2e: job section to avoid false positives from frontend job
        jobs_section = content.split("\n  e2e:", 1)
        assert len(jobs_section) == 2, "Could not find 'e2e:' job in ci.yml."
        e2e_section = jobs_section[1]
        # Trim at next top-level job boundary
        next_job = re.search(r"\n  [a-z][\w-]*:", e2e_section)
        if next_job:
            e2e_section = e2e_section[: next_job.start()]
        assert "npm run build" not in e2e_section, (
            "ci.yml e2e job must NOT run 'npm run build' — "
            "it should download the pre-built artifact (ADV-062)."
        )
