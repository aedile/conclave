"""Tests verifying README link integrity for T52.6.

All markdown links in demos/README.md and top-level README.md must
point to files that exist in the repository. Tests parse markdown link
syntax and resolve paths relative to the project root.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.infrastructure]

PROJECT_ROOT = Path(__file__).parent.parent.parent
DEMOS_README = PROJECT_ROOT / "demos" / "README.md"
MAIN_README = PROJECT_ROOT / "README.md"

# Three notebooks required per T52.6 AC
REQUIRED_NOTEBOOKS = [
    "demos/quickstart.ipynb",
    "demos/epsilon_curves.ipynb",
    "demos/training_data.ipynb",
]


def _extract_markdown_links(text: str) -> list[str]:
    """Return all link targets from markdown [text](target) syntax.

    Args:
        text: Raw markdown content.

    Returns:
        List of link target strings (may include anchors and external URLs).
    """
    return re.findall(r"\[(?:[^\]]*)\]\(([^)]+)\)", text)


def _is_internal_file_link(link: str) -> bool:
    """Return True if link is an internal relative-path file reference.

    Filters out HTTP(S) URLs, anchor-only links, and mailto links.

    Args:
        link: Raw link target string from markdown.

    Returns:
        True when the link is a relative path to a local file.
    """
    if link.startswith(("http://", "https://", "mailto:", "#")):
        return False
    # Strip any trailing anchor fragment, e.g. file.md#section
    path_part = link.split("#")[0]
    return bool(path_part)


def _resolve_link(link: str, readme_path: Path) -> Path:
    """Resolve a relative markdown link against the given README's directory.

    Args:
        link: Relative file path from a markdown link (anchor stripped).
        readme_path: Absolute path to the README file containing the link.

    Returns:
        Resolved absolute Path.
    """
    path_part = link.split("#")[0]
    return (readme_path.parent / path_part).resolve()


def _extract_section(content: str, heading_pattern: str) -> str:
    """Extract section text from a heading match to the next heading of any level.

    Args:
        content: Full README text.
        heading_pattern: Regex pattern to locate the section heading.

    Returns:
        Section text from the matched heading to the next heading, or to EOF.
    """
    section_match = re.search(heading_pattern, content, re.IGNORECASE)
    if section_match is None:
        return ""
    section_start = section_match.start()
    # Search for the next heading after an offset to avoid re-matching the current one
    next_heading = re.search(r"^#{1,4}\s+", content[section_start + 10 :], re.MULTILINE)
    section_end = section_start + 10 + next_heading.start() if next_heading else len(content)
    return content[section_start:section_end]


# ---------------------------------------------------------------------------
# Attack tests -- dead-link and missing-section checks
# ---------------------------------------------------------------------------


class TestDemosReadmeLinks:
    """Verify every internal link in demos/README.md resolves to an existing file."""

    def test_demos_readme_exists(self) -> None:
        """demos/README.md must exist on disk."""
        assert DEMOS_README.is_file(), f"Expected {DEMOS_README} to exist"

    def test_demos_readme_links_resolve_to_existing_files(self) -> None:
        """Every relative link in demos/README.md must point to an existing file."""
        content = DEMOS_README.read_text(encoding="utf-8")
        links = _extract_markdown_links(content)
        internal_links = [lnk for lnk in links if _is_internal_file_link(lnk)]

        assert internal_links, (
            "demos/README.md must contain at least one internal markdown link "
            "([text](path) syntax). Add a link to a related document."
        )

        broken: list[str] = []
        for link in internal_links:
            resolved = _resolve_link(link, DEMOS_README)
            if not resolved.exists():
                broken.append(f"{link!r} -> {resolved}")

        assert not broken, f"Found {len(broken)} broken link(s) in demos/README.md:\n" + "\n".join(
            f"  {b}" for b in broken
        )

    def test_demos_readme_contains_quickstart_entry(self) -> None:
        """demos/README.md must document quickstart.ipynb in a dedicated section.

        Requires a ### section heading for quickstart.ipynb, the notebook filename
        in the section body, and runtime or audience information in the section.
        """
        content = DEMOS_README.read_text(encoding="utf-8")

        section_text = _extract_section(content, r"###\s+`?quickstart\.ipynb`?")
        assert section_text, (
            "demos/README.md must contain a ### section heading for quickstart.ipynb"
        )

        assert "quickstart.ipynb" in section_text, (
            "demos/README.md quickstart.ipynb section must reference the notebook filename"
        )

        has_audience_or_runtime = bool(
            re.search(
                r"\b(audience|runtime|\d+[-\u2013]\d+\s*(min|minute|hour)|\d+\s*(min|minute|hour))\b",
                section_text,
                re.IGNORECASE,
            )
        )
        assert has_audience_or_runtime, (
            "demos/README.md quickstart.ipynb section must include audience or runtime "
            f"information. Section text:\n{section_text[:300]}"
        )

    def test_demos_readme_contains_epsilon_curves_entry(self) -> None:
        """demos/README.md must document epsilon_curves.ipynb."""
        content = DEMOS_README.read_text(encoding="utf-8")
        assert "epsilon_curves.ipynb" in content, (
            "demos/README.md must include an entry for epsilon_curves.ipynb"
        )

    def test_demos_readme_contains_training_data_entry(self) -> None:
        """demos/README.md must document training_data.ipynb in a dedicated section.

        Requires a ### section heading for training_data.ipynb, the notebook filename
        in the section body, and runtime or audience information in the section.
        """
        content = DEMOS_README.read_text(encoding="utf-8")

        section_text = _extract_section(content, r"###\s+`?training_data\.ipynb`?")
        assert section_text, (
            "demos/README.md must contain a ### section heading for training_data.ipynb"
        )

        assert "training_data.ipynb" in section_text, (
            "demos/README.md training_data.ipynb section must reference the notebook filename"
        )

        has_audience_or_runtime = bool(
            re.search(
                r"\b(audience|runtime|\d+[-\u2013]\d+\s*(min|minute|hour)|\d+\s*(min|minute|hour))\b",
                section_text,
                re.IGNORECASE,
            )
        )
        assert has_audience_or_runtime, (
            "demos/README.md training_data.ipynb section must include audience or runtime "
            f"information. Section text:\n{section_text[:300]}"
        )

    def test_demos_readme_contains_generate_figures_entry(self) -> None:
        """demos/README.md must document generate_figures.py."""
        content = DEMOS_README.read_text(encoding="utf-8")
        assert "generate_figures.py" in content, (
            "demos/README.md must include an entry for generate_figures.py"
        )

    def test_demos_readme_epsilon_curves_has_runtime(self) -> None:
        """demos/README.md must state expected runtime for epsilon_curves.ipynb.

        The runtime must appear in the epsilon_curves section heading area,
        not just anywhere the filename is mentioned (e.g., directory listing).
        """
        content = DEMOS_README.read_text(encoding="utf-8")
        # Locate the section heading specifically for epsilon_curves notebook
        # (not just any mention of the filename in a directory tree)
        section_match = re.search(r"###\s+`?epsilon_curves\.ipynb`?", content, re.IGNORECASE)
        assert section_match is not None, (
            "demos/README.md must contain a ### section heading for epsilon_curves.ipynb"
        )
        # Extract text from the section heading to the next heading
        section_start = section_match.start()
        next_heading = re.search(r"^#{1,4}\s+", content[section_start + 10 :], re.MULTILINE)
        section_end = section_start + 10 + next_heading.start() if next_heading else len(content)
        section_text = content[section_start:section_end]

        has_runtime = bool(
            re.search(
                r"\d+[-]\d+\s*(min|hour|h\b)|runtime|\d+\s*(min|hour)",
                section_text,
                re.IGNORECASE,
            )
        )
        assert has_runtime, (
            "demos/README.md epsilon_curves.ipynb section must include expected runtime "
            f"information (e.g., '45-90 minutes'). Section text:\n{section_text[:300]}"
        )


class TestMainReadmeDemosSection:
    """Verify top-level README.md has an accurate Demos & Benchmarks section."""

    def test_main_readme_exists(self) -> None:
        """README.md must exist at project root."""
        assert MAIN_README.is_file(), f"Expected {MAIN_README} to exist"

    def test_main_readme_demos_section_exists(self) -> None:
        """README.md must contain a 'Demos' section heading."""
        content = MAIN_README.read_text(encoding="utf-8")
        # Match any heading level containing "Demos" (case-insensitive)
        has_demos = bool(re.search(r"^#{1,4}\s+.*[Dd]emos", content, re.MULTILINE))
        assert has_demos, "README.md must contain a section heading with 'Demos'"

    def test_main_readme_links_to_all_three_notebooks(self) -> None:
        """README.md must link to all three required notebooks."""
        content = MAIN_README.read_text(encoding="utf-8")
        missing = [nb for nb in REQUIRED_NOTEBOOKS if nb not in content]
        assert not missing, f"README.md is missing references to these notebooks: {missing}"

    def test_main_readme_links_to_demos_readme(self) -> None:
        """README.md must link to demos/README.md for full setup instructions."""
        content = MAIN_README.read_text(encoding="utf-8")
        assert "demos/README.md" in content, (
            "README.md must link to demos/README.md for full setup details"
        )

    def test_main_readme_svg_references_exist(self) -> None:
        """Every SVG path referenced in README.md must point to an existing file.

        Fails if README.md contains no SVG references at all, since the Demos
        section is required to include pre-rendered figures per T52.6 AC.
        """
        content = MAIN_README.read_text(encoding="utf-8")
        # Capture SVG references in markdown image syntax: ![alt](path.svg) or "path.svg"
        all_svg = re.findall(r'["(]([^"()\s]+\.svg)[")]', content)

        assert all_svg, (
            "README.md should reference at least one SVG figure. "
            "The Demos section must include pre-rendered figure links per T52.6 AC."
        )

        broken: list[str] = []
        for svg_ref in all_svg:
            resolved = (PROJECT_ROOT / svg_ref).resolve()
            if not resolved.exists():
                broken.append(f"{svg_ref!r} -> {resolved}")

        assert not broken, (
            f"Found {len(broken)} broken SVG reference(s) in README.md:\n"
            + "\n".join(f"  {b}" for b in broken)
        )

    def test_main_readme_methodology_note_present(self) -> None:
        """README.md Demos section must include the Opacus RDP methodology note."""
        content = MAIN_README.read_text(encoding="utf-8")
        assert "Opacus" in content, "README.md must include a methodology note referencing 'Opacus'"
        assert "RDP" in content, (
            "README.md must include a methodology note referencing 'RDP accountant'"
        )

    def test_main_readme_demos_links_resolve_to_existing_files(self) -> None:
        """Every relative link in README.md must point to an existing file."""
        content = MAIN_README.read_text(encoding="utf-8")
        links = _extract_markdown_links(content)
        internal_links = [lnk for lnk in links if _is_internal_file_link(lnk)]

        assert internal_links, "README.md must contain at least one internal link"

        broken: list[str] = []
        for link in internal_links:
            resolved = _resolve_link(link, MAIN_README)
            if not resolved.exists():
                broken.append(f"{link!r} -> {resolved}")

        assert not broken, f"Found {len(broken)} broken link(s) in README.md:\n" + "\n".join(
            f"  {b}" for b in broken
        )

    def test_main_readme_how_this_was_built_metrics_present(self) -> None:
        """README.md 'How This Was Built' section must contain numeric metrics."""
        content = MAIN_README.read_text(encoding="utf-8")
        assert "How This Was Built" in content, (
            "README.md must contain a 'How This Was Built' section"
        )
        # Verify metric rows exist -- check for table with at least Commits row
        assert "Commits" in content, (
            "README.md 'How This Was Built' section must include a Commits metric"
        )
        assert "Pull requests merged" in content, (
            "README.md 'How This Was Built' section must include 'Pull requests merged'"
        )
