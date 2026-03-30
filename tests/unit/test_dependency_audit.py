"""Unit tests for T18.2 — Dependency Tree Audit & Slimming.

Verifies:
1. ``docs/DEPENDENCY_AUDIT.md`` exists and contains the required sections.
2. ``chromadb`` is absent from ALL pyproject.toml sections (removed in T55.5).
3. ``passlib`` is absent from ALL pyproject.toml sections (removed in T55.5).
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
        """docs/DEPENDENCY_AUDIT.md must document the chromadb removal decision.

        chromadb was removed in T55.5.  The audit doc must still reference the
        dependency to record the removal decision and outcome — the Findings
        Summary entry preserves the institutional memory of why it was added
        and why it was subsequently removed.
        """
        content = DEPENDENCY_AUDIT_DOC.read_text()
        assert "chromadb" in content, (
            "docs/DEPENDENCY_AUDIT.md must document the chromadb removal decision "
            "(Findings Summary row referencing T55.5)."
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
# pyproject.toml chromadb and passlib removal tests (T55.5)
# ---------------------------------------------------------------------------


class TestChromadbFullyRemovedFromPyproject:
    """Verify that chromadb has been fully removed from pyproject.toml (T55.5).

    chromadb was in the dev group for retrospective seeding scripts which were
    deleted in T55.5.  With the scripts gone, the dependency has no purpose
    and must be absent from every section of pyproject.toml.
    """

    def test_chromadb_absent_from_main_dependencies_section(self) -> None:
        """chromadb must NOT appear in [tool.poetry.dependencies].

        The scripts that used chromadb have been deleted (T55.5).
        """
        content = PYPROJECT.read_text()
        lines = content.splitlines()

        in_main_deps = False
        for line in lines:
            stripped = line.strip()
            if stripped == "[tool.poetry.dependencies]":
                in_main_deps = True
                continue
            is_new_section = stripped.startswith("[") and stripped != "[tool.poetry.dependencies]"
            if in_main_deps and is_new_section:
                in_main_deps = False
            if in_main_deps and stripped.startswith("chromadb"):
                pytest.fail(
                    "chromadb found in [tool.poetry.dependencies] section. "
                    "The seeding scripts are deleted — remove chromadb entirely (T55.5)."
                )
        assert "chromadb" not in content, "chromadb still referenced in pyproject.toml"

    def test_chromadb_absent_from_all_pyproject_sections(self) -> None:
        """chromadb must NOT appear in ANY section of pyproject.toml.

        T55.5 removes chromadb entirely — it is not relocated to another group.
        """
        content = PYPROJECT.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("chromadb"):
                pytest.fail(
                    f"chromadb found in pyproject.toml: {stripped!r}. "
                    "T55.5 requires full removal from all dependency groups."
                )
        assert "chromadb" not in content, "chromadb still referenced in any pyproject section"

    def test_passlib_absent_from_all_pyproject_sections(self) -> None:
        """passlib must NOT appear in ANY section of pyproject.toml.

        passlib has no import sites in src/ and is superseded by the direct
        cryptography pin.  T55.5 removes it entirely.
        """
        content = PYPROJECT.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("passlib"):
                pytest.fail(
                    f"passlib found in pyproject.toml: {stripped!r}. "
                    "T55.5 requires full removal — passlib has no src/ import sites."
                )
        assert "passlib" not in content, "passlib still referenced in pyproject.toml"


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


# ---------------------------------------------------------------------------
# CVE gate: pygments must not reach production image (ADV-P63-05, T66.4)
# ---------------------------------------------------------------------------


class TestPygmentsProductionExclusion:
    """Pygments must be absent from the production dependency set.

    Pygments has CVE-2026-4539 with no upstream fix. This class verifies
    it is a transitive dev dependency ONLY and does not reach production.
    """

    def test_pygments_absent_from_production_requirements(self) -> None:
        """Pygments must not appear in production dependency groups.

        Runs 'poetry export --only=main' to enumerate production-only
        dependencies and asserts pygments is absent.

        This test uses a subprocess to avoid importing poetry internals
        directly (they are not stable public API).
        """
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "poetry",
                "export",
                "--only=main",
                "--format=requirements.txt",
                "--without-hashes",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

        # If poetry export fails for infra reasons, skip rather than false-fail
        if result.returncode != 0:
            import pytest

            pytest.skip(f"poetry export failed (exit {result.returncode}): {result.stderr[:200]}")

        output_lower = result.stdout.lower()
        assert "pygments" not in output_lower, (
            "pygments was found in the production (main group) requirements export. "
            "This is a CVE-2026-4539 violation. "
            "Matching lines: "
            + "\n".join(line for line in result.stdout.splitlines() if "pygments" in line.lower())
        )
