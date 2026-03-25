"""Negative/attack tests and feature tests for T55.5 — Dead Dependency Cleanup.

Verifies that passlib and chromadb are fully absent from the project,
that all related files and references have been removed, and that
main.py no longer uses ``Any`` escape hatches for the storage backend.

Attack tests (Section A) assert that known-dead dependencies and their
supporting files are completely absent — the system MUST reject any
future re-introduction of these dependencies.

Feature tests (Section B) assert positive structural properties that
are expected to hold after the cleanup.

TDD Phase: RED → GREEN → REFACTOR
Constitution Priority: 1 (Quality Gates), 4 (Comprehensive Testing)
Task: T55.5 — Eliminate Dead Dependencies and Type Safety Holes
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repository root resolution
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKER_COMPOSE_OVERRIDE = REPO_ROOT / "docker-compose.override.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
CONFTEST_ROOT = REPO_ROOT / "tests" / "conftest.py"
SETUP_AGILE_ENV = REPO_ROOT / "scripts" / "setup_agile_env.sh"
MAIN_PY = REPO_ROOT / "src" / "synth_engine" / "bootstrapper" / "main.py"
WIRING_PY = REPO_ROOT / "src" / "synth_engine" / "bootstrapper" / "wiring.py"
DEPENDENCY_AUDIT_DOC = REPO_ROOT / "docs" / "DEPENDENCY_AUDIT.md"
ADR_0007 = REPO_ROOT / "docs" / "adr" / "ADR-0007-jwt-library-selection.md"

# ---------------------------------------------------------------------------
# Section A — Attack Tests
# (Assert that dead dependencies are fully absent)
# These tests FAIL on the original codebase and PASS after cleanup.
# ---------------------------------------------------------------------------


class TestPasslibAbsent:
    """passlib must be removed from all project surfaces."""

    def test_passlib_absent_from_main_dependencies(self) -> None:
        """passlib must NOT appear in [tool.poetry.dependencies].

        passlib is superseded by the direct ``cryptography`` pin and has zero
        import sites in ``src/``.  Retaining it adds unnecessary transitive
        exposure and contradicts the DEPENDENCY_AUDIT finding.
        """
        content = PYPROJECT.read_text()
        lines = content.splitlines()

        in_main_deps = False
        for line in lines:
            stripped = line.strip()
            if stripped == "[tool.poetry.dependencies]":
                in_main_deps = True
                continue
            if (
                in_main_deps
                and stripped.startswith("[")
                and stripped != "[tool.poetry.dependencies]"
            ):
                in_main_deps = False
            if in_main_deps and stripped.startswith("passlib"):
                pytest.fail(
                    "passlib found in [tool.poetry.dependencies]. "
                    "It has no src/ import sites and must be removed entirely "
                    "(superseded by the direct cryptography pin, T55.5)."
                )

    def test_passlib_absent_from_any_pyproject_section(self) -> None:
        """passlib must not appear in ANY pyproject.toml section at all.

        Once removed it should be completely gone — not relocated to dev group.
        """
        content = PYPROJECT.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("passlib"):
                pytest.fail(
                    f"passlib still referenced in pyproject.toml line: {stripped!r}. "
                    "T55.5 requires full removal from all dependency groups."
                )

    def test_passlib_absent_from_pre_commit_config(self) -> None:
        """passlib must NOT appear in .pre-commit-config.yaml additional_dependencies.

        The mirrors-mypy hook listed ``passlib[bcrypt]>=1.7.4`` which is no
        longer needed once passlib is removed from production dependencies.
        """
        content = PRE_COMMIT_CONFIG.read_text()
        for line in content.splitlines():
            if "passlib" in line:
                pytest.fail(
                    f"passlib still referenced in .pre-commit-config.yaml: {line.strip()!r}. "
                    "T55.5 requires removing passlib from the mirrors-mypy "
                    "additional_dependencies list."
                )


class TestChromadbFullyAbsent:
    """chromadb must be removed from every project surface."""

    def test_chromadb_fully_absent_from_pyproject(self) -> None:
        """chromadb must NOT appear in ANY pyproject.toml section.

        chromadb was in the dev group for retrospective seeding scripts which
        are now deleted.  With the scripts gone, the dep has no purpose.
        """
        content = PYPROJECT.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("chromadb"):
                pytest.fail(
                    f"chromadb still in pyproject.toml: {stripped!r}. "
                    "T55.5 requires full removal — the seeding scripts are deleted."
                )

    def test_seed_chroma_scripts_deleted(self) -> None:
        """All three chroma seeding/init scripts must be deleted.

        These scripts had no callers beyond setup_agile_env.sh (which is also
        being cleaned up) and the ChromaDB MCP integration that has been sunsetted.
        """
        deleted_scripts = [
            REPO_ROOT / "scripts" / "seed_chroma.py",
            REPO_ROOT / "scripts" / "seed_chroma_retro.py",
            REPO_ROOT / "scripts" / "init_chroma.py",
        ]
        for script_path in deleted_scripts:
            assert not script_path.exists(), (
                f"Chroma script still exists: {script_path.name}. "
                "T55.5 requires deleting all three chroma seeding scripts."
            )

    def test_chroma_test_files_deleted(self) -> None:
        """Both chroma test files must be deleted along with their tested scripts.

        Tests for deleted code become orphaned dead tests.  Removing them keeps
        the test suite honest and maintains the 95%+ coverage threshold.
        """
        deleted_test_files = [
            REPO_ROOT / "tests" / "unit" / "test_seed_chroma.py",
            REPO_ROOT / "tests" / "unit" / "test_init_chroma.py",
        ]
        for test_file in deleted_test_files:
            assert not test_file.exists(), (
                f"Orphaned chroma test file still exists: {test_file.name}. "
                "T55.5 requires deleting test files for deleted scripts."
            )

    def test_setup_agile_env_does_not_call_init_chroma(self) -> None:
        """setup_agile_env.sh must not reference init_chroma.py.

        The ChromaDB MCP integration has been sunsetted.  setup_agile_env.sh
        must either be updated to remove the ChromaDB section or deleted entirely
        if it has no remaining useful purpose.
        """
        if not SETUP_AGILE_ENV.exists():
            # Script deleted entirely — requirement satisfied
            return
        content = SETUP_AGILE_ENV.read_text()
        assert "init_chroma" not in content, (
            "setup_agile_env.sh still references init_chroma.py. "
            "The ChromaDB MCP integration is sunsetted; remove this call (T55.5)."
        )


# ---------------------------------------------------------------------------
# Section B — Feature Tests
# (Assert positive structural properties after cleanup)
# ---------------------------------------------------------------------------


class TestDockerComposeNoChromaVolume:
    """docker-compose files must not define or mount chroma_data volumes."""

    def test_docker_compose_no_chroma_data_volume(self) -> None:
        """docker-compose.yml must not define a chroma_data volume."""
        content = DOCKER_COMPOSE.read_text()
        assert "chroma_data" not in content, (
            "docker-compose.yml still references chroma_data volume. "
            "Remove the volume definition and all mount points (T55.5)."
        )

    def test_docker_compose_override_no_chroma_data_volume(self) -> None:
        """docker-compose.override.yml must not define or mount chroma_data."""
        if not DOCKER_COMPOSE_OVERRIDE.exists():
            pytest.skip("docker-compose.override.yml does not exist")
        content = DOCKER_COMPOSE_OVERRIDE.read_text()
        assert "chroma_data" not in content, (
            "docker-compose.override.yml still references chroma_data. "
            "Remove the volume definition and all mount points (T55.5)."
        )


class TestEnvExampleNoChromaSection:
    """The .env.example template must not contain ChromaDB configuration."""

    def test_env_example_no_chromadb_section(self) -> None:
        """.env.example must not contain CHROMA tokens or a ChromaDB section.

        CHROMA_DATA_PATH and any other CHROMA-prefixed variables belong to the
        sunsetted ChromaDB integration and must be removed.
        """
        content = ENV_EXAMPLE.read_text()
        chroma_tokens = [line for line in content.splitlines() if "CHROMA" in line.upper()]
        assert not chroma_tokens, (
            f".env.example still contains ChromaDB references: {chroma_tokens!r}. "
            "Remove the ChromaDB section entirely (T55.5)."
        )


class TestConftestNoChromadbFilter:
    """The root conftest.py must not suppress chromadb deprecation warnings.

    Once chromadb is removed from dev dependencies it will not be installed,
    so the filter is dead code that could mask other warnings.
    """

    def test_conftest_no_chromadb_filter(self) -> None:
        """tests/conftest.py must not contain a chromadb asyncio filter."""
        content = CONFTEST_ROOT.read_text()
        chromadb_lines = [line for line in content.splitlines() if "chromadb" in line.lower()]
        assert not chromadb_lines, (
            f"tests/conftest.py still contains chromadb filter(s): {chromadb_lines!r}. "
            "Remove the asyncio.iscoroutinefunction suppression attributed to chromadb (T55.5)."
        )


class TestNoChromadbMutmutIgnores:
    """pyproject.toml mutmut config must not list chromadb test ignores.

    The test files for chroma scripts are deleted so their mutmut --ignore
    entries are now dead configuration that would silently pass for missing
    files.
    """

    def test_no_chromadb_mutmut_ignores(self) -> None:
        """[tool.mutmut] pytest_add_cli_args must not include chroma test ignores."""
        content = PYPROJECT.read_text()
        chroma_ignores = [
            line
            for line in content.splitlines()
            if "test_seed_chroma" in line or "test_init_chroma" in line
        ]
        assert not chroma_ignores, (
            f"pyproject.toml mutmut config still references deleted chroma test files: "
            f"{chroma_ignores!r}. Remove these --ignore entries (T55.5)."
        )


class TestMainPyNoAnyEscapeHatches:
    """main.py must not use ``Any`` annotations for the storage backend.

    The ``backend_cls: Any`` and ``-> Any`` return type on
    ``_build_webhook_delivery_fn`` are type safety holes that defeat mypy's
    ability to catch API contract violations on the storage backend and the
    webhook delivery closure.
    """

    def _get_main_py_ast(self) -> ast.Module:
        """Parse main.py into an AST for structural analysis.

        Returns:
            The parsed AST module for main.py.
        """
        source = MAIN_PY.read_text()
        return ast.parse(source)

    def test_main_py_backend_cls_not_annotated_any(self) -> None:
        """main.py must not annotate backend_cls with ``Any``.

        The ``backend_cls: Any = MinioStorageBackend`` assignment bypasses
        mypy type checking on the MinioStorageBackend constructor call.
        The annotation must be removed or replaced with a proper type.
        """
        source = MAIN_PY.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign):
                continue
            # Check if the target is "backend_cls"
            if not (isinstance(node.target, ast.Name) and node.target.id == "backend_cls"):
                continue
            # Check if the annotation is "Any"
            annotation = node.annotation
            if isinstance(annotation, ast.Name) and annotation.id == "Any":
                pytest.fail(
                    "main.py still has `backend_cls: Any = MinioStorageBackend`. "
                    "Replace with a type-safe pattern — either remove the annotation "
                    "or use a proper type alias (T55.5)."
                )

    def test_wiring_py_build_webhook_fn_not_returns_any(self) -> None:
        """_build_webhook_delivery_fn in wiring.py must not have ``-> Any`` return type.

        T56.2: _build_webhook_delivery_fn moved from main.py to wiring.py.
        The return type must be ``Callable[[int, str], None]`` or equivalent —
        not ``Any`` which defeats mypy's ability to verify call sites.
        """
        # T56.2: _build_webhook_delivery_fn moved to wiring.py
        source = WIRING_PY.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name != "_build_webhook_delivery_fn":
                continue
            ret = node.returns
            if ret is None:
                continue
            if isinstance(ret, ast.Name) and ret.id == "Any":
                pytest.fail(
                    "_build_webhook_delivery_fn still has `-> Any` return type. "
                    "Replace with `Callable[[int, str], None]` (T55.5)."
                )


class TestPoetryLockConsistent:
    """poetry.lock must be consistent with pyproject.toml after dep removal."""

    def test_poetry_lock_consistent(self) -> None:
        """``poetry check`` must exit 0 — lockfile must be consistent with pyproject.toml.

        After removing passlib and chromadb, the lockfile must be regenerated
        so it reflects the current pyproject.toml state.  A stale lockfile
        indicates the regeneration step was skipped.
        """
        result = subprocess.run(
            ["poetry", "check"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"poetry check failed (exit {result.returncode}). "
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}\n"
            "Run `poetry lock` to regenerate the lockfile after removing "
            "passlib and chromadb (T55.5)."
        )


class TestDependencyAuditPasslibRemovalDocumented:
    """docs/DEPENDENCY_AUDIT.md must reflect that passlib has been removed."""

    def test_dependency_audit_passlib_removal_documented(self) -> None:
        """DEPENDENCY_AUDIT.md must document passlib removal with a DONE status.

        The audit row for passlib must indicate the removal is complete, not
        that it is a future task.
        """
        content = DEPENDENCY_AUDIT_DOC.read_text()
        # The doc must mention passlib removal and mark it as DONE
        assert "passlib" in content, (
            "DEPENDENCY_AUDIT.md must document the passlib removal decision. "
            "Add a row in the Findings Summary table (T55.5)."
        )
        # Find the passlib line and verify it indicates DONE status
        for line in content.splitlines():
            if "passlib" in line.lower() and "|" in line:
                assert "DONE" in line or "removed" in line.lower(), (
                    f"passlib row in DEPENDENCY_AUDIT.md does not show removal: {line!r}. "
                    "Update the status to DONE or 'removed' (T55.5)."
                )
                return
        pytest.fail(
            "No passlib table row found in DEPENDENCY_AUDIT.md. "
            "Add a Findings Summary entry documenting the removal (T55.5)."
        )


class TestAdr0007NoPasslibReference:
    """ADR-0007 must not reference passlib after it has been removed.

    The original ADR-0007 mentioned ``passlib[bcrypt]`` as the source of the
    ``cryptography`` package.  With passlib removed, this reference is stale
    and misleading.
    """

    def test_adr_0007_no_passlib_reference(self) -> None:
        """ADR-0007 must not contain 'via passlib[bcrypt]' or similar phrases."""
        content = ADR_0007.read_text()
        passlib_lines = [line for line in content.splitlines() if "passlib" in line]
        assert not passlib_lines, (
            f"ADR-0007 still references passlib: {passlib_lines!r}. "
            "Amend to remove the stale 'via passlib[bcrypt]' phrase (T55.5)."
        )
