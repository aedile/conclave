"""Attack and feature tests for notebook infrastructure (T52.3).

This module contains negative/attack tests written BEFORE the implementation,
following Rule 22 (Attack-First TDD). These tests verify that:
  - The generate_figures.py script exists and is importable
  - The epsilon_curves.ipynb notebook exists
  - The notebook has no hardcoded absolute paths (security/portability gate)
  - The notebook has no cell outputs committed (nbstripout compliance)
  - The figures directory contains generated SVG files

Feature tests verify content quality:
  - The notebook has a Methodology section
  - The notebook has a Limitations section
  - Running generate_figures.py produces expected SVG outputs
  - Generated SVGs have text groups (axis labels check — matplotlib SVG format)

Task: P52-T52.3 — Epsilon Curve Notebook
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Repository root and artifact paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_DEMOS_DIR = _REPO_ROOT / "demos"
_NOTEBOOK_PATH = _DEMOS_DIR / "epsilon_curves.ipynb"
_SCRIPT_PATH = _DEMOS_DIR / "generate_figures.py"
_FIGURES_DIR = _DEMOS_DIR / "figures"
_RESULTS_DIR = _DEMOS_DIR / "results"

# ---------------------------------------------------------------------------
# Expected SVG figure filenames
# ---------------------------------------------------------------------------

_EXPECTED_SVG_FILENAMES = [
    "epsilon_vs_noise_multiplier.svg",
    "epsilon_vs_statistical_fidelity.svg",
    "epsilon_vs_schema_complexity.svg",
    "correlation_preservation.svg",
    "fk_integrity.svg",
]


# ===========================================================================
# ATTACK TESTS — Negative / security cases (Rule 22)
# ===========================================================================


class TestGenerateFiguresScriptExists:
    """The generate_figures.py script must exist and be a valid Python file."""

    def test_generate_figures_script_exists(self) -> None:
        """generate_figures.py must exist in demos/.

        The script is the single source of truth for figure generation (T52.3).
        Its absence means the figures directory cannot be regenerated from
        committed benchmark results.
        """
        assert _SCRIPT_PATH.exists(), (
            f"generate_figures.py not found at {_SCRIPT_PATH}. "
            "This script is required as the single source of truth for SVG generation."
        )
        assert _SCRIPT_PATH.suffix == ".py", (
            f"Expected a .py file at {_SCRIPT_PATH}, got suffix {_SCRIPT_PATH.suffix!r}"
        )
        assert _SCRIPT_PATH.stat().st_size > 0, f"generate_figures.py at {_SCRIPT_PATH} is empty."

    def test_generate_figures_script_has_valid_python_syntax(self) -> None:
        """generate_figures.py must parse as valid Python.

        An unparseable script cannot be imported or executed, making it
        impossible to regenerate figures from committed results.
        """
        assert _SCRIPT_PATH.exists(), f"Script not found: {_SCRIPT_PATH}"
        source = _SCRIPT_PATH.read_text(encoding="utf-8")
        # compile() raises SyntaxError if the file has syntax errors
        compiled = compile(source, str(_SCRIPT_PATH), "exec")
        assert isinstance(compiled, types.CodeType), f"Expected CodeType, got {type(compiled)}"


class TestEpsilonCurvesNotebookExists:
    """The epsilon_curves.ipynb notebook must exist and be valid nbformat JSON."""

    def test_epsilon_curves_notebook_exists(self) -> None:
        """epsilon_curves.ipynb must exist in demos/.

        The notebook is the primary deliverable of T52.3.
        """
        assert _NOTEBOOK_PATH.exists(), (
            f"epsilon_curves.ipynb not found at {_NOTEBOOK_PATH}. "
            "This is the primary deliverable of T52.3."
        )

    def test_epsilon_curves_notebook_is_valid_nbformat_json(self) -> None:
        """epsilon_curves.ipynb must be valid JSON with nbformat structure.

        An invalid notebook cannot be opened in Jupyter or rendered in GitHub.
        """
        assert _NOTEBOOK_PATH.exists(), f"Notebook not found: {_NOTEBOOK_PATH}"
        raw = _NOTEBOOK_PATH.read_text(encoding="utf-8")
        nb = json.loads(raw)
        assert "nbformat" in nb, "Notebook JSON must have 'nbformat' key"
        assert "cells" in nb, "Notebook JSON must have 'cells' key"
        assert isinstance(nb["cells"], list), "Notebook 'cells' must be a list"
        assert int(nb["nbformat"]) >= 4, f"Notebook nbformat must be >= 4, got {nb['nbformat']!r}"


class TestNotebookHasNoHardcodedPaths:
    """The notebook must not contain absolute paths.

    Absolute paths break the notebook when moved across machines or
    environments, and may expose internal directory structure.
    """

    def test_notebook_has_no_hardcoded_paths(self) -> None:
        """Notebook source cells must not contain absolute paths.

        Detects common absolute path patterns:
          - Unix-style: /Users/..., /home/..., /root/..., /opt/...
          - Windows-style: C:\\..., D:\\...
          - Tilde-expanded: ~/ (should use Path(__file__) or relative paths)
        """
        assert _NOTEBOOK_PATH.exists(), f"Notebook not found: {_NOTEBOOK_PATH}"
        raw = _NOTEBOOK_PATH.read_text(encoding="utf-8")
        nb = json.loads(raw)

        absolute_path_pattern = re.compile(
            r"(?:/Users/|/home/|/root/|/opt/|~/"
            r"|[A-Za-z]:\\\\)"
        )

        violations: list[str] = []
        for cell_idx, cell in enumerate(nb["cells"]):
            source_lines = cell.get("source", [])
            if isinstance(source_lines, list):
                source = "".join(source_lines)
            else:
                source = str(source_lines)

            matches = absolute_path_pattern.findall(source)
            if matches:
                violations.append(
                    f"Cell {cell_idx} ({cell.get('cell_type', 'unknown')}): "
                    f"found absolute path pattern(s): {matches}"
                )

        assert violations == [], "Notebook contains hardcoded absolute paths:\n" + "\n".join(
            violations
        )


class TestNotebookCellsHaveNoExecuteResultOutputs:
    """Notebook cells must not have committed execution outputs.

    nbstripout strips outputs before commit. If outputs are present in
    the committed notebook, the pre-commit hook was bypassed.
    """

    def test_notebook_cells_have_no_execute_result_outputs(self) -> None:
        """No cell in the committed notebook may have execution outputs.

        nbstripout (pre-commit hook) must have run before this file was
        committed.  Any outputs indicate a hook bypass — Constitution violation.
        """
        assert _NOTEBOOK_PATH.exists(), f"Notebook not found: {_NOTEBOOK_PATH}"
        raw = _NOTEBOOK_PATH.read_text(encoding="utf-8")
        nb = json.loads(raw)

        cells_with_outputs: list[str] = []
        for cell_idx, cell in enumerate(nb["cells"]):
            outputs = cell.get("outputs", [])
            execution_count = cell.get("execution_count")
            if outputs:
                cells_with_outputs.append(
                    f"Cell {cell_idx} ({cell.get('cell_type', 'unknown')}): "
                    f"{len(outputs)} output(s) found"
                )
            if execution_count is not None:
                cells_with_outputs.append(
                    f"Cell {cell_idx}: execution_count={execution_count!r} "
                    "(should be null/None — suggests outputs not stripped)"
                )

        assert cells_with_outputs == [], (
            "Notebook has committed cell outputs (nbstripout must run pre-commit):\n"
            + "\n".join(cells_with_outputs)
        )


class TestFiguresDirectoryContainsSVGs:
    """The figures directory must contain the expected pre-rendered SVG files.

    SVGs are committed alongside the notebook so readers can see the charts
    without running the notebook.
    """

    def test_figures_directory_exists(self) -> None:
        """demos/figures/ directory must exist.

        The directory holds pre-rendered SVG charts committed alongside
        the notebook for immediate viewing without notebook execution.
        """
        assert _FIGURES_DIR.exists(), (
            f"demos/figures/ not found at {_FIGURES_DIR}. "
            "Run generate_figures.py to create the directory and SVGs."
        )
        assert _FIGURES_DIR.is_dir(), f"{_FIGURES_DIR} exists but is not a directory."

    def test_figures_directory_contains_svgs(self) -> None:
        """demos/figures/ must contain at least one committed SVG file.

        Pre-rendered SVGs allow the charts to be viewed on GitHub and in
        documentation without Jupyter execution.
        """
        assert _FIGURES_DIR.exists(), f"demos/figures/ not found: {_FIGURES_DIR}"
        svg_files = list(_FIGURES_DIR.glob("*.svg"))
        assert len(svg_files) >= 1, (
            f"demos/figures/ contains no SVG files. "
            f"Run generate_figures.py to generate them. "
            f"Files present: {[f.name for f in _FIGURES_DIR.iterdir()]}"
        )

    def test_figures_directory_contains_all_expected_svgs(self) -> None:
        """All expected SVG filenames must be present in demos/figures/.

        Each named figure corresponds to a notebook section.  Missing files
        indicate generate_figures.py did not complete successfully.
        """
        assert _FIGURES_DIR.exists(), f"demos/figures/ not found: {_FIGURES_DIR}"
        present = {f.name for f in _FIGURES_DIR.glob("*.svg")}
        missing = set(_EXPECTED_SVG_FILENAMES) - present
        assert missing == set(), (
            f"Missing expected SVG files in demos/figures/: {sorted(missing)}\n"
            f"Present: {sorted(present)}"
        )


# ===========================================================================
# FEATURE TESTS — Content quality checks
# ===========================================================================


class TestNotebookHasMethodologySection:
    """The notebook must contain a Methodology heading in a markdown cell."""

    def test_notebook_has_methodology_section(self) -> None:
        """A markdown cell with '## Methodology' or '# Methodology' must exist.

        The Methodology section documents hardware, software versions, seed,
        DP accountant, parameter grid, and limitations of the reduced grid.
        Without it, the benchmark results cannot be reproduced or interpreted.
        """
        assert _NOTEBOOK_PATH.exists(), f"Notebook not found: {_NOTEBOOK_PATH}"
        raw = _NOTEBOOK_PATH.read_text(encoding="utf-8")
        nb = json.loads(raw)

        found_methodology = False
        for cell in nb["cells"]:
            if cell.get("cell_type") != "markdown":
                continue
            source_lines = cell.get("source", [])
            source = "".join(source_lines) if isinstance(source_lines, list) else str(source_lines)
            if re.search(r"^#{1,3}\s+Methodology", source, re.MULTILINE | re.IGNORECASE):
                found_methodology = True
                break

        assert found_methodology, (
            "Notebook must contain a markdown cell with a 'Methodology' heading "
            "(e.g., '## Methodology'). This section documents reproducibility context."
        )


class TestNotebookHasLimitationsSection:
    """The notebook must contain a Limitations heading in a markdown cell."""

    def test_notebook_has_limitations_section(self) -> None:
        """A markdown cell with a 'Limitations' heading must exist.

        The Limitations section must honestly document what these benchmark
        numbers mean and do not mean — per the task requirement for honest
        disclosure of the reduced grid, CTGAN constraints, etc.
        """
        assert _NOTEBOOK_PATH.exists(), f"Notebook not found: {_NOTEBOOK_PATH}"
        raw = _NOTEBOOK_PATH.read_text(encoding="utf-8")
        nb = json.loads(raw)

        found_limitations = False
        for cell in nb["cells"]:
            if cell.get("cell_type") != "markdown":
                continue
            source_lines = cell.get("source", [])
            source = "".join(source_lines) if isinstance(source_lines, list) else str(source_lines)
            if re.search(r"^#{1,3}\s+.*Limitations", source, re.MULTILINE | re.IGNORECASE):
                found_limitations = True
                break

        assert found_limitations, (
            "Notebook must contain a markdown cell with a 'Limitations' heading. "
            "This section honestly describes the reduced grid and what the numbers mean."
        )


class TestGenerateFiguresProducesExpectedOutputs:
    """Running generate_figures.py must produce SVG files in demos/figures/."""

    def test_generate_figures_produces_expected_outputs(self, tmp_path: Path) -> None:
        """generate_figures.py must create all expected SVGs when run standalone.

        Uses a temporary output directory so the test can verify creation
        without depending on pre-existing state in demos/figures/.

        The script must accept an --output-dir argument for testability.
        """
        assert _SCRIPT_PATH.exists(), f"Script not found: {_SCRIPT_PATH}"

        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_PATH),
                "--output-dir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=60,
        )
        assert result.returncode == 0, (
            f"generate_figures.py exited with code {result.returncode}.\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )

        generated_svgs = list(tmp_path.glob("*.svg"))
        assert len(generated_svgs) >= len(_EXPECTED_SVG_FILENAMES), (
            f"Expected at least {len(_EXPECTED_SVG_FILENAMES)} SVGs, "
            f"got {len(generated_svgs)}: {[f.name for f in generated_svgs]}"
        )

        generated_names = {f.name for f in generated_svgs}
        missing = set(_EXPECTED_SVG_FILENAMES) - generated_names
        assert missing == set(), (
            f"Missing SVG outputs from generate_figures.py: {sorted(missing)}\n"
            f"Generated: {sorted(generated_names)}"
        )


class TestAllFiguresHaveAxisLabels:
    """SVG figures must contain text group elements (axis labels).

    Matplotlib 3.x renders text as ``<g id="text_N">`` groups rather than
    ``<text>`` elements.  This test checks for those groups as a proxy for
    verifying that axis labels and titles were written into the SVG.
    """

    def test_all_figures_have_axis_labels(self) -> None:
        """Each committed SVG must contain at least 5 matplotlib text groups.

        Matplotlib SVGs store axis tick labels, axis titles, and chart titles
        as ``<g id="text_N">`` elements.  A meaningful chart (with x-axis
        label, y-axis label, title, and at least two tick labels) will have
        at least 5 such groups.

        The threshold of 5 groups is a conservative lower bound that guards
        against blank-canvas SVGs while allowing simple charts with few ticks.
        """
        assert _FIGURES_DIR.exists(), f"demos/figures/ not found: {_FIGURES_DIR}"
        svg_files = list(_FIGURES_DIR.glob("*.svg"))
        assert len(svg_files) >= 1, "No SVG files found in demos/figures/"

        violations: list[str] = []
        for svg_file in sorted(svg_files):
            content = svg_file.read_text(encoding="utf-8")
            # Matplotlib SVG text groups: <g id="text_1">, <g id="text_2">, etc.
            text_group_matches = re.findall(r'<g id="text_\d+"', content)
            threshold = 5
            if len(text_group_matches) < threshold:
                violations.append(
                    f"{svg_file.name}: found {len(text_group_matches)} matplotlib text "
                    f"group(s) (id='text_N'), expected at least {threshold} "
                    "(title + axis labels + tick labels)"
                )

        assert violations == [], "SVG figures appear to be missing axis labels:\n" + "\n".join(
            violations
        )
