"""Tests for the quickstart notebook (T52.4).

Security-first attack tests verify that the notebook:
  - Contains no hardcoded credentials
  - Contains no unsafe pickle.load() calls
  - Uses environment variables for all credentials
  - Commits with stripped outputs (nbstripout compliance)

Feature tests verify that the notebook has the correct structure
for data architects following the connect → synthesize → compare workflow.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_NOTEBOOK_PATH = _REPO_ROOT / "demos" / "quickstart.ipynb"
_DEMOS_README_PATH = _REPO_ROOT / "demos" / "README.md"


@pytest.fixture(scope="module")
def notebook_json() -> dict:  # type: ignore[type-arg]
    """Load and parse the quickstart notebook JSON.

    Returns:
        Parsed notebook as a dictionary.
    """
    assert _NOTEBOOK_PATH.exists(), (
        f"Notebook not found at {_NOTEBOOK_PATH}. Create demos/quickstart.ipynb."
    )
    raw = _NOTEBOOK_PATH.read_text(encoding="utf-8")
    return json.loads(raw)  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def all_source_lines(notebook_json: dict) -> list[str]:  # type: ignore[type-arg]
    """Extract all source lines from every cell in the notebook.

    Args:
        notebook_json: Parsed notebook dictionary.

    Returns:
        Flat list of all source lines across all cells.
    """
    lines: list[str] = []
    for cell in notebook_json.get("cells", []):
        source = cell.get("source", [])
        if isinstance(source, list):
            lines.extend(source)
        elif isinstance(source, str):
            lines.extend(source.splitlines(keepends=True))
    return lines


@pytest.fixture(scope="module")
def code_source_lines(notebook_json: dict) -> list[str]:  # type: ignore[type-arg]
    """Extract source lines from code cells only.

    Args:
        notebook_json: Parsed notebook dictionary.

    Returns:
        Flat list of source lines from code cells only.
    """
    lines: list[str] = []
    for cell in notebook_json.get("cells", []):
        if cell.get("cell_type") == "code":
            source = cell.get("source", [])
            if isinstance(source, list):
                lines.extend(source)
            elif isinstance(source, str):
                lines.extend(source.splitlines(keepends=True))
    return lines


# ===========================================================================
# ATTACK / NEGATIVE TESTS
# ===========================================================================


def test_quickstart_notebook_exists() -> None:
    """Verify the notebook file exists at the expected path.

    The notebook must exist at demos/quickstart.ipynb for users to find it
    alongside the other demo assets.
    """
    assert _NOTEBOOK_PATH.exists(), (
        f"Notebook not found at {_NOTEBOOK_PATH}. "
        "The file demos/quickstart.ipynb must be created."
    )
    assert _NOTEBOOK_PATH.suffix == ".ipynb", (
        f"Expected a .ipynb file, got: {_NOTEBOOK_PATH.suffix}"
    )


def test_quickstart_no_hardcoded_credentials(
    all_source_lines: list[str],
) -> None:
    """Scan notebook for hardcoded credential patterns — must find NONE.

    Security: hardcoded passwords, signing keys, or DSN passwords in a
    notebook committed to source control constitute a secret exposure.

    Args:
        all_source_lines: All source lines from the notebook.
    """
    # Patterns that indicate hardcoded secrets
    dangerous_patterns = [
        r'password\s*=\s*["\'][^"\']{3,}',  # password="secret"
        r'signing_key\s*=\s*b["\']',  # signing_key=b"..."
        r'postgresql://[^:]+:[^@]{3,}@',  # DSN with password
        r'ARTIFACT_SIGNING_KEY\s*=\s*["\']',  # env var assignment with literal
        r'secret\s*=\s*["\'][^"\']{3,}',  # secret="value"
    ]
    full_text = "".join(all_source_lines)
    for pattern in dangerous_patterns:
        matches = re.findall(pattern, full_text, re.IGNORECASE)
        # Filter out comments and environment variable reads
        non_comment_matches = [
            m for m in matches
            if not any(m.strip().startswith(c) for c in ("#", "//"))
            and "os.environ" not in m
            and "os.getenv" not in m
            and "getenv" not in m
        ]
        assert not non_comment_matches, (
            f"Hardcoded credential pattern '{pattern}' found in notebook: "
            f"{non_comment_matches!r}. Use os.environ / os.getenv instead."
        )


def test_quickstart_no_pickle_load(code_source_lines: list[str]) -> None:
    """Scan notebook code cells for pickle.load calls — must find NONE.

    Security: pickle.load without verification is a deserialization attack
    vector. All model artifact loading MUST use ModelArtifact.load() with
    a signing_key, which verifies the HMAC before deserializing.

    Args:
        code_source_lines: Source lines from code cells only.
    """
    full_code = "".join(code_source_lines)
    # Match pickle.load( but not ModelArtifact.load(
    pickle_pattern = r'\bpickle\.load\s*\('
    matches = re.findall(pickle_pattern, full_code)
    assert not matches, (
        f"Found {len(matches)} pickle.load() call(s) in notebook code cells. "
        "Use ModelArtifact.load(path, signing_key=...) instead."
    )


def test_quickstart_no_cell_outputs(notebook_json: dict) -> None:  # type: ignore[type-arg]
    """Verify all cells have empty outputs (nbstripout compliance).

    Notebooks committed with cell outputs can contain PII or sensitive data
    in rendered output. nbstripout must be run before every commit.

    Args:
        notebook_json: Parsed notebook dictionary.
    """
    for idx, cell in enumerate(notebook_json.get("cells", [])):
        cell_type = cell.get("cell_type", "")
        if cell_type == "code":
            outputs = cell.get("outputs", [])
            assert outputs == [], (
                f"Cell {idx} (code) has non-empty outputs: {outputs!r}. "
                "Run nbstripout before committing: "
                "poetry run nbstripout demos/quickstart.ipynb"
            )
            execution_count = cell.get("execution_count")
            assert execution_count is None, (
                f"Cell {idx} has execution_count={execution_count!r}, "
                "expected None. Run nbstripout before committing."
            )


def test_quickstart_uses_env_vars_for_credentials(
    code_source_lines: list[str],
) -> None:
    """Verify code cells use os.environ / os.getenv for all credentials.

    The signing key and database connection string must come from environment
    variables, never from hardcoded literals.

    Args:
        code_source_lines: Source lines from code cells only.
    """
    full_code = "".join(code_source_lines)
    # Must import os or use one of the env-access patterns
    uses_env = (
        re.search(r'\bos\.environ\b', full_code) is not None
        or re.search(r'\bos\.getenv\b', full_code) is not None
    )
    assert uses_env, (
        "No os.environ or os.getenv usage found in notebook code cells. "
        "Credentials (ARTIFACT_SIGNING_KEY, DATABASE_URL) must be read "
        "from environment variables."
    )
    # ARTIFACT_SIGNING_KEY must specifically be fetched from the environment
    signing_key_env = (
        re.search(r'ARTIFACT_SIGNING_KEY', full_code) is not None
    )
    assert signing_key_env, (
        "ARTIFACT_SIGNING_KEY is not referenced in notebook code cells. "
        "The signing key must be read from os.environ['ARTIFACT_SIGNING_KEY']."
    )


# ===========================================================================
# FEATURE TESTS
# ===========================================================================


def test_quickstart_has_three_main_sections(
    notebook_json: dict,  # type: ignore[type-arg]
) -> None:
    """Verify the notebook contains Connect, Synthesize, and Compare sections.

    These three headings define the connect → synthesize → compare workflow
    that data architects follow in the quickstart.

    Args:
        notebook_json: Parsed notebook dictionary.
    """
    markdown_text = ""
    for cell in notebook_json.get("cells", []):
        if cell.get("cell_type") == "markdown":
            source = cell.get("source", [])
            if isinstance(source, list):
                markdown_text += "".join(source)
            else:
                markdown_text += source

    required_sections = ["Connect", "Synthesize", "Compare"]
    for section in required_sections:
        assert section in markdown_text, (
            f"Required section '{section}' not found in notebook markdown cells. "
            f"The notebook must contain the {section} heading."
        )


def test_quickstart_has_setup_instructions(
    notebook_json: dict,  # type: ignore[type-arg]
) -> None:
    """Verify the notebook has a setup markdown cell with prerequisites.

    Data architects need setup instructions covering Docker Compose,
    Poetry installation, and the ARTIFACT_SIGNING_KEY environment variable.

    Args:
        notebook_json: Parsed notebook dictionary.
    """
    markdown_text = ""
    for cell in notebook_json.get("cells", []):
        if cell.get("cell_type") == "markdown":
            source = cell.get("source", [])
            if isinstance(source, list):
                markdown_text += "".join(source)
            else:
                markdown_text += source

    required_terms = [
        "poetry install",
        "docker",
        "ARTIFACT_SIGNING_KEY",
    ]
    for term in required_terms:
        assert term.lower() in markdown_text.lower(), (
            f"Setup prerequisite '{term}' not mentioned in notebook markdown. "
            "The setup section must cover Docker Compose, Poetry, and signing key setup."
        )


def test_demos_readme_exists() -> None:
    """Verify the demos/README.md file exists.

    A README is required to orient new users to the demos directory contents,
    prerequisites, and expected runtimes.
    """
    assert _DEMOS_README_PATH.exists(), (
        f"demos/README.md not found at {_DEMOS_README_PATH}. "
        "Create a README describing the demo notebooks."
    )
    content = _DEMOS_README_PATH.read_text(encoding="utf-8")
    assert len(content) > 200, (  # noqa: PLR2004 — minimum meaningful content
        f"demos/README.md is too short ({len(content)} bytes). "
        "The README must contain setup instructions and notebook descriptions."
    )


def test_demos_readme_links_resolve() -> None:
    """Verify all markdown links in demos/README.md point to existing files.

    Broken links in the README mislead users and indicate stale documentation.
    """
    assert _DEMOS_README_PATH.exists(), (
        "demos/README.md does not exist — cannot check links."
    )
    content = _DEMOS_README_PATH.read_text(encoding="utf-8")
    # Match relative markdown links: [text](path) — skip http/https/# anchors
    link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
    demos_dir = _DEMOS_README_PATH.parent

    broken_links: list[str] = []
    for _label, target in link_pattern.findall(content):
        # Skip absolute URLs and anchor-only links
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        # Strip any fragment from the path
        path_part = target.split("#")[0]
        if not path_part:
            continue
        resolved = (demos_dir / path_part).resolve()
        if not resolved.exists():
            broken_links.append(target)

    assert not broken_links, (
        f"The following links in demos/README.md do not resolve to existing files: "
        f"{broken_links!r}"
    )
