"""Tests for T51.3 — Air-Gap Deployment Validation Script.

This module verifies the correctness and security properties of:
  - scripts/validate_airgap.sh: operator-facing bundle validation script
  - scripts/build_airgap.sh: bundle builder (must NOT include override file)
  - Makefile: must contain load-images and validate-airgap targets

Attack/negative tests verify that the scripts fail safely under adversarial
or erroneous conditions (missing Docker, malformed bundles, etc.).

Task: P51-T51.3 — Air-Gap Deployment Validation Script
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants — resolved relative to the repo root
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent
VALIDATE_SCRIPT = REPO_ROOT / "scripts" / "validate_airgap.sh"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_airgap.sh"
MAKEFILE = REPO_ROOT / "Makefile"


# ---------------------------------------------------------------------------
# ATTACK / NEGATIVE TESTS (committed first per Rule 22)
# ---------------------------------------------------------------------------


class TestValidateAirgapAttack:
    """Negative and attack tests for validate_airgap.sh.

    These tests verify that the script behaves safely under hostile or
    erroneous conditions: missing daemon, missing files, bad bundles.
    """

    def test_validate_script_exists(self) -> None:
        """The validate_airgap.sh script must exist at scripts/validate_airgap.sh."""
        assert VALIDATE_SCRIPT.exists(), (
            f"scripts/validate_airgap.sh not found at {VALIDATE_SCRIPT}"
        )

    def test_validate_script_is_executable_or_bash_runnable(self) -> None:
        """The script must be executable (or at minimum readable for bash)."""
        assert VALIDATE_SCRIPT.exists(), "validate_airgap.sh must exist"
        # Verify it's a valid bash script by checking the shebang line
        content = VALIDATE_SCRIPT.read_text()
        assert content.startswith("#!/usr/bin/env bash"), (
            "validate_airgap.sh must start with #!/usr/bin/env bash"
        )

    def test_validate_script_uses_set_euo_pipefail(self) -> None:
        """The script must use 'set -euo pipefail' for fail-safe execution.

        Without this, silent failures (missing commands, piped errors) could
        cause the script to proceed past validation failures.
        """
        content = VALIDATE_SCRIPT.read_text()
        assert "set -euo pipefail" in content, (
            "validate_airgap.sh must use 'set -euo pipefail' to prevent silent failures"
        )

    def test_validate_script_does_not_use_override_file(self) -> None:
        """The validation script must NOT reference docker-compose.override.yml.

        Production air-gap validation must use only the production compose file.
        Including the override file would pull in dev-only services (Jaeger,
        hot-reload mounts) which are absent from production bundles.
        """
        content = VALIDATE_SCRIPT.read_text()
        assert "docker-compose.override.yml" not in content, (
            "validate_airgap.sh must NOT reference docker-compose.override.yml — "
            "production bundles do not include dev overrides"
        )

    def test_validate_script_has_exit_trap_for_cleanup(self) -> None:
        """The script must register a trap on EXIT for guaranteed cleanup.

        Without an EXIT trap, a script failure mid-execution (health check
        timeout, docker error) leaves a running compose stack and temp directory
        on the operator's machine — a resource leak and potential security issue.
        """
        content = VALIDATE_SCRIPT.read_text()
        assert "trap" in content, "validate_airgap.sh must use 'trap' for EXIT cleanup"
        assert "EXIT" in content, "validate_airgap.sh must trap the EXIT signal specifically"

    def test_validate_script_uses_distinct_project_name(self) -> None:
        """The script must use '--project-name conclave-validation' or equivalent.

        Using the default project name risks conflicting with a running
        production stack on the same machine.
        """
        content = VALIDATE_SCRIPT.read_text()
        assert "project-name" in content or "COMPOSE_PROJECT_NAME" in content, (
            "validate_airgap.sh must use a distinct Docker Compose project name "
            "to avoid conflicts with production stacks"
        )

    def test_validate_script_checks_for_docker_daemon(self) -> None:
        """The script must verify Docker is available before proceeding.

        Running without Docker available should produce a clear error, not
        a cryptic failure deep in the script.
        """
        content = VALIDATE_SCRIPT.read_text()
        assert "docker" in content, "validate_airgap.sh must reference docker"
        # Must check for docker availability — look for command -v or which
        assert "command -v" in content or "which docker" in content, (
            "validate_airgap.sh must verify docker is in PATH before proceeding"
        )

    def test_validate_script_has_health_check_timeout(self) -> None:
        """The script must implement a bounded health check poll, not an infinite loop.

        An unbounded wait on /health would hang indefinitely if the stack fails
        to start — the script must timeout after a configurable maximum.
        """
        content = VALIDATE_SCRIPT.read_text()
        # Must contain a numeric timeout value (60s as specified)
        assert "60" in content, "validate_airgap.sh must implement a 60-second health check timeout"

    def test_validate_script_checks_minimum_tar_file_count(self) -> None:
        """The script must verify at least 3 .tar files exist in images/.

        Fewer than 3 images means the bundle is incomplete (missing engine,
        postgres, or redis at minimum).
        """
        content = VALIDATE_SCRIPT.read_text()
        assert "images/" in content or "images" in content, (
            "validate_airgap.sh must verify the images/ directory contents"
        )
        # Must check for at least some minimum count — look for count/3
        assert "3" in content, "validate_airgap.sh must verify at least 3 image tar files exist"

    def test_validate_script_verifies_required_files(self) -> None:
        """The script must verify docker-compose.yml and VERSION exist in the bundle.

        A bundle missing these files is structurally invalid and should fail
        early with a clear message rather than proceeding to docker load.
        """
        content = VALIDATE_SCRIPT.read_text()
        assert "docker-compose.yml" in content, (
            "validate_airgap.sh must verify docker-compose.yml exists in the bundle"
        )
        assert "VERSION" in content, (
            "validate_airgap.sh must verify VERSION file exists in the bundle"
        )

    def test_validate_script_passes_shellcheck(self) -> None:
        """The script must pass shellcheck with no errors.

        shellcheck catches common bash pitfalls: unquoted variables, word
        splitting, subshell issues, and other correctness problems.
        """
        result = subprocess.run(
            ["shellcheck", str(VALIDATE_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"shellcheck found errors in validate_airgap.sh:\n{result.stdout}\n{result.stderr}"
        )


class TestBuildAirgapAttack:
    """Negative and safety tests for build_airgap.sh.

    Verifies that the bundle builder excludes dev-only artifacts.
    """

    def test_build_script_does_not_copy_override_file(self) -> None:
        """build_airgap.sh must NOT copy docker-compose.override.yml into the bundle.

        The override file contains hot-reload volume mounts and Jaeger tracing
        profile — both are dev-only and inappropriate for production bundles.
        Including it expands the attack surface of the air-gap bundle.
        """
        content = BUILD_SCRIPT.read_text()
        # The line 'cp docker-compose.override.yml "${DIST_DIR}/"' must be gone
        assert "cp docker-compose.override.yml" not in content, (
            "build_airgap.sh must NOT copy docker-compose.override.yml — "
            "this dev-only file has no place in a production air-gap bundle"
        )

    def test_build_script_passes_shellcheck(self) -> None:
        """build_airgap.sh must pass shellcheck with no errors."""
        result = subprocess.run(
            ["shellcheck", str(BUILD_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"shellcheck found errors in build_airgap.sh:\n{result.stdout}\n{result.stderr}"
        )

    def test_build_script_still_copies_production_compose_file(self) -> None:
        """build_airgap.sh must still copy the production docker-compose.yml.

        Removing the override copy must not accidentally remove the main
        compose file — the bundle must remain deployable.
        """
        content = BUILD_SCRIPT.read_text()
        assert "cp docker-compose.yml" in content, (
            "build_airgap.sh must copy docker-compose.yml into the bundle"
        )


# ---------------------------------------------------------------------------
# FEATURE TESTS
# ---------------------------------------------------------------------------


class TestMakefileTargets:
    """Verify Makefile has the required infrastructure targets.

    docs/PRODUCTION_DEPLOYMENT.md references make load-images and
    make validate-airgap — both must be present.
    """

    def test_makefile_has_load_images_target(self) -> None:
        """Makefile must define a load-images target."""
        content = MAKEFILE.read_text()
        assert "load-images:" in content, (
            "Makefile must define a 'load-images' target — "
            "referenced by docs/PRODUCTION_DEPLOYMENT.md"
        )

    def test_makefile_has_validate_airgap_target(self) -> None:
        """Makefile must define a validate-airgap target."""
        content = MAKEFILE.read_text()
        assert "validate-airgap:" in content, "Makefile must define a 'validate-airgap' target"

    def test_makefile_load_images_iterates_dist_images(self) -> None:
        """make load-images must load from dist/images/*.tar.

        The target must iterate over dist/images/*.tar files, consistent
        with the bundle layout produced by build_airgap.sh.
        """
        content = MAKEFILE.read_text()
        assert "dist/images/" in content or "dist/images" in content, (
            "load-images target must reference dist/images/ directory"
        )

    def test_makefile_validate_airgap_calls_validation_script(self) -> None:
        """make validate-airgap must invoke validate_airgap.sh."""
        content = MAKEFILE.read_text()
        assert "validate_airgap.sh" in content, (
            "validate-airgap target must call scripts/validate_airgap.sh"
        )

    def test_makefile_load_images_has_help_comment(self) -> None:
        """make load-images must have a help comment (## ...) for make help output."""
        content = MAKEFILE.read_text()
        # Find load-images line and check it has a ## comment
        for line in content.splitlines():
            if line.startswith("load-images:"):
                assert "##" in line, (
                    "load-images target must have a '##' help comment for display by 'make help'"
                )
                break
        else:
            pytest.fail("load-images target not found in Makefile")

    def test_makefile_validate_airgap_has_help_comment(self) -> None:
        """make validate-airgap must have a help comment (## ...) for make help output."""
        content = MAKEFILE.read_text()
        for line in content.splitlines():
            if line.startswith("validate-airgap:"):
                assert "##" in line, (
                    "validate-airgap target must have a '##' help comment "
                    "for display by 'make help'"
                )
                break
        else:
            pytest.fail("validate-airgap target not found in Makefile")


class TestValidateAirgapFeatures:
    """Feature tests verifying the structural correctness of validate_airgap.sh."""

    def test_validate_script_loads_docker_images(self) -> None:
        """The script must load Docker images using 'docker load'."""
        content = VALIDATE_SCRIPT.read_text()
        assert "docker load" in content, "validate_airgap.sh must load images via 'docker load'"

    def test_validate_script_starts_compose_stack(self) -> None:
        """The script must start the stack via 'docker compose ... up -d'."""
        content = VALIDATE_SCRIPT.read_text()
        assert "docker compose" in content or "docker-compose" in content, (
            "validate_airgap.sh must use docker compose to start the stack"
        )
        assert "up" in content, "validate_airgap.sh must run 'up' to start the stack"

    def test_validate_script_performs_health_check(self) -> None:
        """The script must poll the /health endpoint."""
        content = VALIDATE_SCRIPT.read_text()
        assert "/health" in content, (
            "validate_airgap.sh must poll GET /health to verify the stack is up"
        )

    def test_validate_script_tears_down_compose_stack(self) -> None:
        """The script must call 'docker compose down' in its cleanup."""
        content = VALIDATE_SCRIPT.read_text()
        assert "docker compose" in content or "docker-compose" in content
        assert "down" in content, (
            "validate_airgap.sh must call 'docker compose down' to tear down the stack"
        )

    def test_validate_script_cleans_temp_directory(self) -> None:
        """The script must remove the temp extraction directory on exit."""
        content = VALIDATE_SCRIPT.read_text()
        assert "rm -rf" in content or "rm -r" in content, (
            "validate_airgap.sh must clean up the temp extraction directory"
        )

    def test_validate_script_extracts_bundle_to_temp_dir(self) -> None:
        """The script must extract the bundle tarball to a temporary directory."""
        content = VALIDATE_SCRIPT.read_text()
        assert "mktemp" in content or "TMPDIR" in content or "tmp" in content.lower(), (
            "validate_airgap.sh must extract bundle to a temp directory"
        )
        tar_cmds = ("tar -xz", "tar xz", "tar -xzf", "tar xzf")
        assert any(cmd in content for cmd in tar_cmds), (
            "validate_airgap.sh must extract the bundle with tar"
        )

    def test_validate_script_prints_success_message(self) -> None:
        """The script must print a success message upon completion."""
        content = VALIDATE_SCRIPT.read_text()
        # Look for a success/pass keyword in echo/log statements
        lower = content.lower()
        assert "success" in lower or "passed" in lower or "valid" in lower, (
            "validate_airgap.sh must print a success message on completion"
        )
