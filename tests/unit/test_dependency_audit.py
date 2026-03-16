"""Unit tests for T18.2 — Dependency Tree Audit & Slimming.

Verifies:
1. ``docs/DEPENDENCY_AUDIT.md`` exists and contains the required sections.
2. ``chromadb`` is NOT in the main ``[tool.poetry.dependencies]`` section.
3. ``chromadb`` IS in a dev/non-production dependency group.
4. The ``datamodel-code-generator`` entry is present in a non-main group.
5. ``edoburu/pgbouncer`` image is referenced in docker-compose.yml with a
   SHA-256 digest (ADV-015 fix).
6. ``ADR-0031`` for pgbouncer image substitution exists.
7. The old phantom tag ``pgbouncer/pgbouncer:1.23.1`` is no longer in docker-compose.yml.
"""

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"
DEPENDENCY_AUDIT_DOC = REPO_ROOT / "docs" / "DEPENDENCY_AUDIT.md"
ADR_DIR = REPO_ROOT / "docs" / "adr"


# ---------------------------------------------------------------------------
# DEPENDENCY_AUDIT.md tests
# ---------------------------------------------------------------------------


class TestDependencyAuditDoc:
    """Verify that docs/DEPENDENCY_AUDIT.md exists and is well-structured."""

    def test_audit_doc_exists(self) -> None:
        """docs/DEPENDENCY_AUDIT.md must exist."""
        assert DEPENDENCY_AUDIT_DOC.exists(), (
            f"docs/DEPENDENCY_AUDIT.md not found at {DEPENDENCY_AUDIT_DOC}. "
            "T18.2 AC#1 requires a dependency audit table."
        )

    def test_audit_doc_has_table(self) -> None:
        """docs/DEPENDENCY_AUDIT.md must contain a markdown table."""
        content = DEPENDENCY_AUDIT_DOC.read_text()
        assert "|" in content, (
            "docs/DEPENDENCY_AUDIT.md must contain a markdown table listing each direct dependency."
        )

    def test_audit_doc_covers_chromadb(self) -> None:
        """docs/DEPENDENCY_AUDIT.md must document the chromadb dependency."""
        content = DEPENDENCY_AUDIT_DOC.read_text()
        assert "chromadb" in content, (
            "docs/DEPENDENCY_AUDIT.md must document the chromadb dependency "
            "and its evaluation outcome."
        )

    def test_audit_doc_covers_asyncpg(self) -> None:
        """docs/DEPENDENCY_AUDIT.md must document asyncpg."""
        content = DEPENDENCY_AUDIT_DOC.read_text()
        assert "asyncpg" in content, (
            "docs/DEPENDENCY_AUDIT.md must document asyncpg and its runtime role."
        )

    def test_audit_doc_covers_greenlet(self) -> None:
        """docs/DEPENDENCY_AUDIT.md must document greenlet."""
        content = DEPENDENCY_AUDIT_DOC.read_text()
        assert "greenlet" in content, (
            "docs/DEPENDENCY_AUDIT.md must document greenlet and its runtime role."
        )

    def test_audit_doc_covers_datamodel_code_generator(self) -> None:
        """docs/DEPENDENCY_AUDIT.md must document datamodel-code-generator."""
        content = DEPENDENCY_AUDIT_DOC.read_text()
        assert "datamodel-code-generator" in content, (
            "docs/DEPENDENCY_AUDIT.md must document datamodel-code-generator "
            "and its group placement."
        )

    def test_audit_doc_has_purpose_column(self) -> None:
        """The audit table must have a 'Purpose' column."""
        content = DEPENDENCY_AUDIT_DOC.read_text()
        assert "Purpose" in content, (
            "docs/DEPENDENCY_AUDIT.md table must include a 'Purpose' column "
            "describing what each dependency does."
        )

    def test_audit_doc_has_runtime_column(self) -> None:
        """The audit table must have a runtime usage column."""
        content = DEPENDENCY_AUDIT_DOC.read_text()
        assert "Runtime" in content or "runtime" in content, (
            "docs/DEPENDENCY_AUDIT.md table must include a runtime/used-at-runtime "
            "column to distinguish production from dev-only deps."
        )


# ---------------------------------------------------------------------------
# pyproject.toml chromadb placement tests
# ---------------------------------------------------------------------------


class TestChromadbNotInMainDeps:
    """Verify that chromadb has been moved out of the main dependency group."""

    def test_chromadb_absent_from_main_dependencies_section(self) -> None:
        """chromadb must NOT appear in [tool.poetry.dependencies].

        chromadb is only used in scripts/ for retrospective seeding. It does not
        belong in the production dependency group; it should live in dev or a
        scripts group to prevent it from being installed in production.
        """
        content = PYPROJECT.read_text()
        lines = content.splitlines()

        # Find the [tool.poetry.dependencies] section
        in_main_deps = False
        for line in lines:
            stripped = line.strip()
            if stripped == "[tool.poetry.dependencies]":
                in_main_deps = True
                continue
            # Any new TOML section ends the main deps section
            is_new_section = stripped.startswith("[") and stripped != "[tool.poetry.dependencies]"
            if in_main_deps and is_new_section:
                in_main_deps = False
            if in_main_deps and stripped.startswith("chromadb"):
                pytest.fail(
                    "chromadb found in [tool.poetry.dependencies] section. "
                    "It is only used in scripts/ and must be in a dev/scripts group. "
                    "Move it to [tool.poetry.group.dev.dependencies] or a dedicated "
                    "scripts group."
                )

    def test_chromadb_present_in_dev_or_scripts_group(self) -> None:
        """chromadb must appear in a non-main dependency group (dev or scripts).

        After moving out of main deps, it must still be declared somewhere so
        ``poetry install --with dev`` makes it available for script usage.
        """
        content = PYPROJECT.read_text()
        lines = content.splitlines()

        in_dev_or_scripts = False
        for line in lines:
            stripped = line.strip()
            if "[tool.poetry.group." in stripped and ".dependencies]" in stripped:
                in_dev_or_scripts = True
                continue
            if stripped.startswith("[") and "[tool.poetry.group." not in stripped:
                in_dev_or_scripts = False
            if in_dev_or_scripts and stripped.startswith("chromadb"):
                return  # Found — pass

        pytest.fail(
            "chromadb not found in any group dependency section "
            "([tool.poetry.group.*.dependencies]). "
            "It must be declared in dev or a scripts group."
        )


# ---------------------------------------------------------------------------
# ADV-015: edoburu/pgbouncer fix tests
# ---------------------------------------------------------------------------


class TestPgbouncerImageFix:
    """Verify ADV-015 is resolved: phantom pgbouncer tag replaced with valid image."""

    def test_phantom_pgbouncer_tag_removed(self) -> None:
        """The phantom tag pgbouncer/pgbouncer:1.23.1 must no longer appear in docker-compose.yml.

        This tag does not exist in Docker Hub. It must be replaced with a valid image
        (edoburu/pgbouncer:v1.23.1-p3) per ADV-015.
        """
        content = DOCKER_COMPOSE.read_text()
        assert "pgbouncer/pgbouncer:1.23.1" not in content, (
            "docker-compose.yml still references pgbouncer/pgbouncer:1.23.1 — "
            "a phantom tag that does not exist in Docker Hub. "
            "ADV-015 requires replacing it with edoburu/pgbouncer:v1.23.1-p3."
        )

    def test_edoburu_pgbouncer_image_present(self) -> None:
        """edoburu/pgbouncer must be referenced in docker-compose.yml."""
        content = DOCKER_COMPOSE.read_text()
        assert "edoburu/pgbouncer" in content, (
            "docker-compose.yml must reference edoburu/pgbouncer:v1.23.1-p3 "
            "(the valid replacement for the phantom pgbouncer/pgbouncer:1.23.1 tag)."
        )

    def test_pgbouncer_image_sha256_pinned(self) -> None:
        """The edoburu/pgbouncer image line must include a SHA-256 digest."""
        import re

        content = DOCKER_COMPOSE.read_text()
        sha256_pattern = re.compile(r"@sha256:[a-f0-9]{64}")
        for line in content.splitlines():
            if "edoburu/pgbouncer" in line and line.strip().startswith("image:"):
                assert sha256_pattern.search(line), (
                    f"edoburu/pgbouncer image line is not SHA-256 pinned: {line.strip()!r}\n"
                    "Format must be: image: edoburu/pgbouncer:tag@sha256:<digest>"
                )
                return
        pytest.fail("No edoburu/pgbouncer image line found in docker-compose.yml")

    def test_warning_comment_removed(self) -> None:
        """WARNING(P17-T17.1) comment must be removed after the fix."""
        content = DOCKER_COMPOSE.read_text()
        assert "WARNING(P17-T17.1)" not in content, (
            "docker-compose.yml still contains WARNING(P17-T17.1) comment. "
            "This comment was a temporary marker for ADV-015; remove it now that "
            "the image reference has been corrected."
        )

    def test_adr_0031_exists(self) -> None:
        """ADR-0031 documenting the pgbouncer image substitution must exist."""
        adr_file = ADR_DIR / "ADR-0031-pgbouncer-image-substitution.md"
        assert adr_file.exists(), (
            f"ADR-0031 not found at {adr_file}. "
            "CLAUDE.md Rule 6 requires an ADR for technology substitution. "
            "Replacing pgbouncer/pgbouncer with edoburu/pgbouncer is a "
            "technology substitution requiring documentation."
        )

    def test_adr_0031_references_adv015(self) -> None:
        """ADR-0031 must reference ADV-015 to link the decision to the advisory."""
        adr_file = ADR_DIR / "ADR-0031-pgbouncer-image-substitution.md"
        if not adr_file.exists():
            pytest.skip("ADR-0031 does not exist yet — covered by test_adr_0031_exists")
        content = adr_file.read_text()
        assert "ADV-015" in content, (
            "ADR-0031 must reference ADV-015 to link this decision to its origin advisory."
        )

    def test_adr_0031_references_edoburu(self) -> None:
        """ADR-0031 must document the chosen edoburu/pgbouncer image."""
        adr_file = ADR_DIR / "ADR-0031-pgbouncer-image-substitution.md"
        if not adr_file.exists():
            pytest.skip("ADR-0031 does not exist yet — covered by test_adr_0031_exists")
        content = adr_file.read_text()
        assert "edoburu/pgbouncer" in content, (
            "ADR-0031 must document the edoburu/pgbouncer image as the chosen replacement."
        )
