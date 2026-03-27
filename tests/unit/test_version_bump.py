"""Tests for version bump automation — negative/attack cases and feature cases.

Covers:
- bump_version.sh rejects malformed and empty version strings (attack tests)
- bump_version.sh fails cleanly when a target file is missing (attack tests)
- All 4 version locations are updated after a bump (feature tests)
- main.py reads __version__ from synth_engine.__init__ dynamically (feature tests)
- PEP 440 format is used in pyproject.toml (no hyphens) (feature tests)
- Idempotency: running twice with the same version is a no-op (feature tests)
- Tag hint in the "Next steps" output is correct for stable and RC versions (ADV-P51-02)

NOTE: bootstrapper/main.py is NOT a bump target. It reads the version
dynamically from synth_engine.__version__ (defined in __init__.py). The 4
bump targets are: pyproject.toml, __init__.py, licensing.py, openapi.json.

CONSTITUTION Priority 3: TDD RED/GREEN phases
Task: P51-T51.1 — Semantic Versioning & Version Bump Automation
ADV-P51-02 — bump_version.sh tag hint fix for stable releases
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.infrastructure]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
BUMP_SCRIPT = REPO_ROOT / "scripts" / "bump_version.sh"

# PEP 440 pattern: X.Y.Z or X.Y.Z(a|b|rc)N
_PEP440_RE = re.compile(r"^\d+\.\d+\.\d+((a|b|rc)\d+)?$")


def _run_bump(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run bump_version.sh with given args, capturing stdout+stderr.

    Args:
        args: Command-line arguments to pass to bump_version.sh.
        env: Optional environment variable overrides merged into os.environ.
        cwd: Working directory for the subprocess (defaults to REPO_ROOT).

    Returns:
        CompletedProcess with returncode, stdout, and stderr attributes.
    """
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [str(BUMP_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=merged_env,
        cwd=str(cwd or REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# ATTACK RED — Negative / rejection tests
# ---------------------------------------------------------------------------


class TestBumpVersionRejectsInvalidInput:
    """bump_version.sh must reject malformed or empty version strings."""

    def test_rejects_empty_string(self) -> None:
        """Empty argument must be rejected with non-zero exit code."""
        result = _run_bump([""])
        assert result.returncode != 0, "Expected non-zero exit for empty version string"
        assert result.stderr or result.stdout, "Expected error output for empty version string"

    def test_rejects_no_arguments(self) -> None:
        """No arguments must be rejected with non-zero exit code."""
        result = _run_bump([])
        assert result.returncode != 0, "Expected non-zero exit when no arguments provided"

    def test_rejects_semver_with_hyphen(self) -> None:
        """SemVer format with hyphen (1.0.0-rc.1) must be rejected — PEP 440 only."""
        result = _run_bump(["1.0.0-rc.1"])
        assert result.returncode != 0, "Expected non-zero exit for SemVer hyphen format"

    def test_rejects_version_with_v_prefix(self) -> None:
        """Version string starting with 'v' (v1.0.0) must be rejected."""
        result = _run_bump(["v1.0.0"])
        assert result.returncode != 0, "Expected non-zero exit for v-prefixed version"

    def test_rejects_invalid_prerelease_separator(self) -> None:
        """Version like 1.0.0.dev1 (invalid PEP 440 format) must be rejected."""
        result = _run_bump(["1.0.0.dev1"])
        assert result.returncode != 0, "Expected non-zero exit for .dev suffix"

    def test_rejects_non_numeric_components(self) -> None:
        """Version like 1.x.0 must be rejected."""
        result = _run_bump(["1.x.0"])
        assert result.returncode != 0, "Expected non-zero exit for non-numeric version components"

    def test_rejects_too_many_components(self) -> None:
        """Version like 1.0.0.0 must be rejected."""
        result = _run_bump(["1.0.0.0"])
        assert result.returncode != 0, "Expected non-zero exit for 4-component version"

    def test_rejects_partial_version(self) -> None:
        """Version like 1.0 (missing patch component) must be rejected."""
        result = _run_bump(["1.0"])
        assert result.returncode != 0, "Expected non-zero exit for partial version (1.0)"


class TestBumpVersionHandlesMissingFiles:
    """bump_version.sh must fail without partial updates when a file is missing."""

    def test_fails_atomically_when_file_missing(self, tmp_path: Path) -> None:
        """If any target file is absent, the script must exit non-zero.

        This guards against partial updates where some files get bumped
        but others do not because they cannot be found.
        """
        # Create a minimal structure that is intentionally MISSING openapi.json
        (tmp_path / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "conclave-engine"\nversion = "0.1.0"\n'
        )
        src_pkg = tmp_path / "src" / "synth_engine"
        src_pkg.mkdir(parents=True)
        (src_pkg / "__init__.py").write_text('__version__ = "0.1.0"\n')
        # openapi.json intentionally NOT created — script should fail

        result = _run_bump(
            ["1.0.0rc1"],
            env={"BUMP_ROOT": str(tmp_path)},
        )
        # Script must fail — not silently succeed with partial updates
        assert result.returncode != 0, (
            "Expected non-zero exit when target files are missing; "
            f"got stdout={result.stdout!r}, stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# FEATURE RED — Positive feature tests
# ---------------------------------------------------------------------------


class TestVersionConsistency:
    """All 4 version locations must be consistent after a bump."""

    def test_all_four_locations_have_same_version_after_bump(self, tmp_path: Path) -> None:
        """bump_version.sh must update all 4 version locations atomically.

        Creates a minimal replica of the repo directory structure in tmp_path,
        runs the bump script with BUMP_ROOT override, then asserts all 4
        locations contain the new version.

        NOTE: bootstrapper/main.py is NOT in the bump target list — it reads
        version dynamically from synth_engine.__version__ (__init__.py).
        """
        _build_fake_repo(tmp_path, current_version="0.1.0")

        result = _run_bump(
            ["1.0.0rc1"],
            env={"BUMP_ROOT": str(tmp_path)},
        )
        assert result.returncode == 0, (
            f"bump_version.sh failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )

        # 1. pyproject.toml
        pyproject_text = (tmp_path / "pyproject.toml").read_text()
        assert 'version = "1.0.0rc1"' in pyproject_text, (
            f"pyproject.toml not updated: {pyproject_text!r}"
        )

        # 2. src/synth_engine/__init__.py
        init_text = (tmp_path / "src" / "synth_engine" / "__init__.py").read_text()
        assert '__version__ = "1.0.0rc1"' in init_text, f"__init__.py not updated: {init_text!r}"

        # 3. shared/security/licensing.py
        licensing_text = (
            tmp_path / "src" / "synth_engine" / "shared" / "security" / "licensing.py"
        ).read_text()
        assert '_APP_VERSION: str = "1.0.0rc1"' in licensing_text, (
            f"licensing.py not updated: {licensing_text!r}"
        )

        # 4. docs/api/openapi.json
        openapi_text = (tmp_path / "docs" / "api" / "openapi.json").read_text()
        openapi = json.loads(openapi_text)
        assert openapi["info"]["version"] == "1.0.0rc1", (
            f"openapi.json not updated: {openapi_text!r}"
        )

    def test_main_py_is_not_a_bump_target(self, tmp_path: Path) -> None:
        """bump_version.sh must NOT attempt to modify bootstrapper/main.py.

        main.py reads version dynamically from synth_engine.__version__
        (defined in __init__.py). Attempting to bump a hardcoded version
        string in main.py would silently fail since no such string exists
        in the current codebase.
        """
        _build_fake_repo(tmp_path, current_version="0.1.0")

        # Record main.py content before bump
        main_py = tmp_path / "src" / "synth_engine" / "bootstrapper" / "main.py"
        content_before = main_py.read_text()

        result = _run_bump(
            ["1.0.0rc1"],
            env={"BUMP_ROOT": str(tmp_path)},
        )
        assert result.returncode == 0, (
            f"bump_version.sh failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )

        # main.py content must be unchanged — it is not a bump target
        content_after = main_py.read_text()
        assert content_before == content_after, (
            "bump_version.sh modified bootstrapper/main.py — it must not do so. "
            "main.py reads version dynamically from synth_engine.__version__."
        )

    def test_bump_script_output_mentions_init_py_not_main_py(self, tmp_path: Path) -> None:
        """Bump script summary output must list __init__.py but not main.py.

        The summary output communicates to the operator which files were
        updated. main.py is intentionally absent from the list.
        """
        _build_fake_repo(tmp_path, current_version="0.1.0")
        result = _run_bump(["1.0.0rc1"], env={"BUMP_ROOT": str(tmp_path)})
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "__init__.py" in combined, "Expected __init__.py in bump script output"
        assert "main.py" not in combined or "NOT" in combined or "dynamically" in combined, (
            "bump script output must not imply main.py is a bump target"
        )

    def test_pep440_format_no_hyphens_in_pyproject(self, tmp_path: Path) -> None:
        """Version in pyproject.toml must use PEP 440 format — no hyphens allowed."""
        _build_fake_repo(tmp_path, current_version="0.1.0")
        result = _run_bump(["1.0.0rc1"], env={"BUMP_ROOT": str(tmp_path)})
        assert result.returncode == 0

        pyproject_text = (tmp_path / "pyproject.toml").read_text()
        match = re.search(r'version\s*=\s*"([^"]+)"', pyproject_text)
        assert match is not None, "No version field found in pyproject.toml"
        version_in_file = match.group(1)

        assert "-" not in version_in_file, (
            f"Hyphen found in pyproject.toml version: {version_in_file!r}"
        )
        assert _PEP440_RE.match(version_in_file), (
            f"Version {version_in_file!r} does not match PEP 440 pattern"
        )

    def test_idempotent_same_version_twice(self, tmp_path: Path) -> None:
        """Running bump_version.sh twice with the same version must be a no-op."""
        _build_fake_repo(tmp_path, current_version="0.1.0")

        result1 = _run_bump(["1.0.0rc1"], env={"BUMP_ROOT": str(tmp_path)})
        assert result1.returncode == 0, f"First run failed: {result1.stderr}"

        init_text_after_first = (tmp_path / "src" / "synth_engine" / "__init__.py").read_text()

        result2 = _run_bump(["1.0.0rc1"], env={"BUMP_ROOT": str(tmp_path)})
        assert result2.returncode == 0, f"Second run failed: {result2.stderr}"

        init_text_after_second = (tmp_path / "src" / "synth_engine" / "__init__.py").read_text()
        assert init_text_after_first == init_text_after_second, (
            "Second bump run changed file content — not idempotent"
        )

    def test_valid_alpha_prerelease_accepted(self, tmp_path: Path) -> None:
        """PEP 440 alpha release (1.0.0a1) must be accepted by the script."""
        _build_fake_repo(tmp_path, current_version="0.1.0")
        result = _run_bump(["1.0.0a1"], env={"BUMP_ROOT": str(tmp_path)})
        assert result.returncode == 0, (
            f"bump_version.sh rejected valid alpha version: {result.stderr}"
        )

    def test_valid_beta_prerelease_accepted(self, tmp_path: Path) -> None:
        """PEP 440 beta release (1.0.0b2) must be accepted by the script."""
        _build_fake_repo(tmp_path, current_version="0.1.0")
        result = _run_bump(["1.0.0b2"], env={"BUMP_ROOT": str(tmp_path)})
        assert result.returncode == 0, (
            f"bump_version.sh rejected valid beta version: {result.stderr}"
        )

    def test_valid_stable_release_accepted(self, tmp_path: Path) -> None:
        """Stable release version (1.0.0) must be accepted by the script."""
        _build_fake_repo(tmp_path, current_version="0.1.0")
        result = _run_bump(["1.0.0"], env={"BUMP_ROOT": str(tmp_path)})
        assert result.returncode == 0, (
            f"bump_version.sh rejected valid stable version: {result.stderr}"
        )


class TestMainPyVersionWiring:
    """main.py must read __version__ from synth_engine, not hardcode it."""

    def test_create_app_version_matches_package_version(self) -> None:
        """FastAPI app version must equal synth_engine.__version__.

        After the refactor, create_app() must dynamically read __version__
        rather than hardcoding any literal string.
        """
        import synth_engine
        from synth_engine.bootstrapper.main import create_app

        app = create_app()
        assert app.version == synth_engine.__version__, (
            f"FastAPI app version {app.version!r} does not match "
            f"synth_engine.__version__ {synth_engine.__version__!r}"
        )

    def test_create_app_version_is_pep440(self) -> None:
        """FastAPI app version must be a valid PEP 440 string."""
        from synth_engine.bootstrapper.main import create_app

        app = create_app()
        assert _PEP440_RE.match(app.version), (
            f"FastAPI app version {app.version!r} is not a valid PEP 440 string"
        )

    def test_package_version_is_pep440(self) -> None:
        """synth_engine.__version__ must be a valid PEP 440 string."""
        import synth_engine

        assert _PEP440_RE.match(synth_engine.__version__), (
            f"synth_engine.__version__ {synth_engine.__version__!r} is not PEP 440"
        )


class TestVersionBumpScript:
    """Integration-style tests for bump_version.sh using tmp_path replicas."""

    def test_script_is_executable(self) -> None:
        """bump_version.sh must be marked executable in the repo."""
        assert BUMP_SCRIPT.exists(), f"bump_version.sh not found at {BUMP_SCRIPT}"
        assert os.access(str(BUMP_SCRIPT), os.X_OK), (
            f"bump_version.sh is not executable: {BUMP_SCRIPT}"
        )

    def test_script_produces_summary_output(self, tmp_path: Path) -> None:
        """Successful bump must print a summary of what was changed."""
        _build_fake_repo(tmp_path, current_version="0.1.0")
        result = _run_bump(["1.0.0rc1"], env={"BUMP_ROOT": str(tmp_path)})
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "1.0.0rc1" in combined, (
            "Expected new version in script output but got: "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )


class TestBumpVersionTagHint:
    """Tag hint in bump_version.sh 'Next steps' output must be correct.

    ADV-P51-02: The shell expansion ``v${NEW_VERSION%rc*}-rc.${NEW_VERSION##*rc}``
    produces ``v1.0.0-rc.1.0.0`` for stable versions like ``1.0.0``. The hint
    must be conditional: stable versions get ``git tag v1.0.0``, RC versions
    get ``git tag v1.0.0-rc.1``.
    """

    def test_tag_hint_stable_version(self, tmp_path: Path) -> None:
        """Stable version bump hint must read ``git tag v1.0.0``, not a mangled RC form.

        ADV-P51-02: The old expansion ``v${NEW_VERSION%rc*}-rc.${NEW_VERSION##*rc}``
        expands to ``v1.0.0-rc.1.0.0`` for ``1.0.0`` (wrong). The fixed
        conditional must emit ``git tag v1.0.0`` for stable releases.
        """
        _build_fake_repo(tmp_path, current_version="0.1.0")
        result = _run_bump(["1.0.0"], env={"BUMP_ROOT": str(tmp_path)})
        assert result.returncode == 0, f"bump_version.sh failed for stable version: {result.stderr}"
        combined = result.stdout + result.stderr
        assert "git tag v1.0.0" in combined, (
            f"Expected 'git tag v1.0.0' in output for stable version bump, got:\n{combined}"
        )
        # Ensure the malformed RC form is absent
        assert "git tag v1.0.0-rc." not in combined, (
            f"Stable version bump output contains erroneous RC tag hint:\n{combined}"
        )

    def test_tag_hint_rc_version(self, tmp_path: Path) -> None:
        """RC version bump hint must read ``git tag v1.0.0-rc.1``.

        ADV-P51-02: The conditional branch for RC versions must correctly
        transform ``1.0.0rc1`` (PEP 440) into the semver pre-release form
        ``v1.0.0-rc.1`` for the tag hint.
        """
        _build_fake_repo(tmp_path, current_version="0.1.0")
        result = _run_bump(["1.0.0rc1"], env={"BUMP_ROOT": str(tmp_path)})
        assert result.returncode == 0, f"bump_version.sh failed for RC version: {result.stderr}"
        combined = result.stdout + result.stderr
        assert "git tag v1.0.0-rc.1" in combined, (
            f"Expected 'git tag v1.0.0-rc.1' in output for RC version bump, got:\n{combined}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_fake_repo(root: Path, *, current_version: str) -> None:
    """Create a minimal repo replica in root with the given current_version.

    Creates the 4 version-bearing files in the correct relative paths so
    bump_version.sh can find and update them via BUMP_ROOT override.

    bootstrapper/main.py is included for structural completeness (the real
    repo has it), but it contains a dynamic import of __version__ rather than
    a hardcoded version string — matching the real file. The bump script does
    NOT modify main.py; it reads version from __init__.py at runtime.

    Args:
        root: Temp directory to build the fake repo in.
        current_version: The version string to embed in all bump-target files.
    """
    # 1. pyproject.toml
    (root / "pyproject.toml").write_text(
        f'[tool.poetry]\nname = "conclave-engine"\nversion = "{current_version}"\n'
    )

    # 2. src/synth_engine/__init__.py
    src_pkg = root / "src" / "synth_engine"
    src_pkg.mkdir(parents=True)
    (src_pkg / "__init__.py").write_text(
        f'"""Conclave Engine."""\n\n__version__ = "{current_version}"\n'
    )

    # 3. shared/security/licensing.py
    sec_dir = src_pkg / "shared" / "security"
    sec_dir.mkdir(parents=True)
    (sec_dir / "licensing.py").write_text(
        f'"""Licensing module."""\n\n_APP_VERSION: str = "{current_version}"\n'
    )

    # 4. bootstrapper/main.py — uses dynamic import, NOT a bump target.
    #    Included here to mirror the real repo structure. The bump script
    #    must NOT modify this file.
    boot_dir = src_pkg / "bootstrapper"
    boot_dir.mkdir(parents=True)
    (boot_dir / "main.py").write_text(
        "import synth_engine\n\n"
        "def create_app():\n"
        "    app = FastAPI(\n"
        "        version=synth_engine.__version__,\n"
        "    )\n"
        "    return app\n"
    )

    # 5. docs/api/openapi.json
    docs_api = root / "docs" / "api"
    docs_api.mkdir(parents=True)
    openapi_data = {"info": {"version": current_version}, "paths": {}}
    (docs_api / "openapi.json").write_text(json.dumps(openapi_data, indent=2))
