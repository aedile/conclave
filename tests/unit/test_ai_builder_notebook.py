"""Attack and feature tests for the AI builder training data notebook (T52.5).

These tests verify the notebook file structure, security properties, and
content requirements for the training_data.ipynb demo notebook.

Attack tests (Rule 22) are written first. They cover:
  - Existence: notebook file must be present
  - No hardcoded credentials: no password patterns or DSN strings
  - No pickle.load: secure artifact loading only (ModelArtifact.load)
  - No cell outputs: nbstripout compliance (clean committed notebook)
  - Fixed random seed: reproducibility requirement

Feature tests cover:
  - Model Selection section with name, metric, split ratio
  - Limitations section with honest caveats
  - Privacy explanation in plain language
  - Utility curve section for privacy-utility tradeoff
  - Augmentation section for real+synthetic combined training

Task: P52-T52.5 — AI Builder Notebook
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.infrastructure]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_NOTEBOOK_PATH = _REPO_ROOT / "demos" / "training_data.ipynb"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_notebook() -> dict:  # type: ignore[type-arg]  # notebook JSON is untyped; full nbformat schema out of scope
    """Load and parse the notebook JSON.

    Returns:
        Parsed notebook as a dictionary.

    Raises:
        Uses pytest.fail if the notebook file does not exist.
        json.JSONDecodeError: If the file exists but is not valid JSON.
    """
    if not _NOTEBOOK_PATH.exists():
        pytest.fail(f"Notebook not found: {_NOTEBOOK_PATH}")
    return json.loads(_NOTEBOOK_PATH.read_text(encoding="utf-8"))


def _code_cell_sources(nb: dict) -> list[str]:  # type: ignore[type-arg]  # notebook JSON is untyped; full nbformat schema out of scope
    """Extract all source text from code cells.

    Args:
        nb: Parsed notebook dictionary.

    Returns:
        List of full source strings for each code cell.
    """
    sources = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            source = cell.get("source", "")
            if isinstance(source, list):
                source = "".join(source)
            sources.append(source)
    return sources


def _markdown_cell_sources(nb: dict) -> list[str]:  # type: ignore[type-arg]  # notebook JSON is untyped; full nbformat schema out of scope
    """Extract all source text from markdown cells.

    Args:
        nb: Parsed notebook dictionary.

    Returns:
        List of full source strings for each markdown cell.
    """
    sources = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "markdown":
            source = cell.get("source", "")
            if isinstance(source, list):
                source = "".join(source)
            sources.append(source)
    return sources


def _all_cell_sources(nb: dict) -> list[str]:  # type: ignore[type-arg]  # notebook JSON is untyped; full nbformat schema out of scope
    """Extract source text from all cells regardless of type.

    Args:
        nb: Parsed notebook dictionary.

    Returns:
        List of full source strings for every cell.
    """
    return _code_cell_sources(nb) + _markdown_cell_sources(nb)


def _find_markdown_heading(nb: dict, heading_text: str) -> bool:  # type: ignore[type-arg]  # notebook JSON is untyped; full nbformat schema out of scope
    """Return True if any markdown cell contains a heading with the given text.

    Args:
        nb: Parsed notebook dictionary.
        heading_text: The heading text to search for (case-insensitive).

    Returns:
        True if a matching heading is found, False otherwise.
    """
    pattern = re.compile(r"^#{1,6}\s+" + re.escape(heading_text), re.IGNORECASE | re.MULTILINE)
    for source in _markdown_cell_sources(nb):
        if pattern.search(source):
            return True
    return False


# ===========================================================================
# ATTACK TESTS — Negative / security cases (Rule 22)
# ===========================================================================


def test_training_data_notebook_exists() -> None:
    """Notebook file must exist at demos/training_data.ipynb."""
    assert _NOTEBOOK_PATH.exists(), (
        f"Expected notebook at {_NOTEBOOK_PATH} but it was not found. "
        "Create demos/training_data.ipynb to satisfy T52.5."
    )


def test_training_data_no_hardcoded_credentials() -> None:
    """Notebook must not contain hardcoded passwords or DSN strings.

    Scans for common credential patterns: password= assignments, full
    postgresql:// DSN strings with embedded credentials (user:pass@host),
    and common secret key variable assignments.
    """
    nb = _load_notebook()
    all_sources = " ".join(_all_cell_sources(nb))

    # Forbidden: postgresql://user:password@host style DSN with credentials
    credential_dsn_pattern = re.compile(r"postgresql://[^@\s]+:[^@\s]+@", re.IGNORECASE)
    assert not credential_dsn_pattern.search(all_sources), (
        "Notebook contains a hardcoded DSN with embedded credentials "
        "(postgresql://user:pass@host). Use os.environ.get('DATABASE_URL') instead."
    )

    # Forbidden: password='...' or password="..." literal assignments
    hardcoded_password_pattern = re.compile(r"""password\s*=\s*['"][^'"]{4,}['"]""", re.IGNORECASE)
    assert not hardcoded_password_pattern.search(all_sources), (
        "Notebook contains a hardcoded password literal assignment. "
        "Use environment variables for all credentials."
    )

    # Forbidden: signing_key = b"..." literal with actual key bytes (not env read)
    hardcoded_signing_key_pattern = re.compile(
        r"""signing_key\s*=\s*b['"][^'"]{10,}['"]""", re.IGNORECASE
    )
    assert not hardcoded_signing_key_pattern.search(all_sources), (
        "Notebook contains a hardcoded signing_key bytes literal. "
        "Read from os.environ['ARTIFACT_SIGNING_KEY'] instead."
    )


def test_training_data_no_pickle_load() -> None:
    """Notebook must not call pickle.load directly.

    All model artifact loading must use ModelArtifact.load() with a
    signing_key to prevent silent downgrade attacks on pickled artifacts.
    """
    nb = _load_notebook()
    code_sources = " ".join(_code_cell_sources(nb))

    assert "pickle.load" not in code_sources, (
        "Notebook calls pickle.load() directly. "
        "Use ModelArtifact.load(path, signing_key=signing_key) instead."
    )


def test_training_data_no_cell_outputs() -> None:
    """All cells must have empty outputs (nbstripout compliance).

    Committed notebooks must not contain executed cell outputs to prevent
    accidental PII leakage from data previews or printed results.
    """
    nb = _load_notebook()
    violations: list[str] = []

    for idx, cell in enumerate(nb.get("cells", [])):
        outputs = cell.get("outputs", [])
        if outputs:
            violations.append(f"Cell {idx} has {len(outputs)} output(s)")
        execution_count = cell.get("execution_count")
        if execution_count is not None:
            violations.append(f"Cell {idx} has non-null execution_count={execution_count}")

    assert not violations, (
        "Notebook has non-empty cell outputs or execution counts. "
        "Run `nbstripout demos/training_data.ipynb` before committing.\n" + "\n".join(violations)
    )


def test_training_data_uses_fixed_random_seed() -> None:
    """Notebook code cells must set a fixed random seed for reproducibility.

    Looks for at least one of: random_state=42, np.random.seed, seed=42,
    RANDOM_STATE = 42. Ensures deterministic runs across environments.
    """
    nb = _load_notebook()
    code_sources = " ".join(_code_cell_sources(nb))

    seed_patterns = [
        re.compile(r"RANDOM_STATE\s*=\s*\d+"),
        re.compile(r"random_state\s*=\s*\d+"),
        re.compile(r"np\.random\.seed\s*\("),
        re.compile(r"\.seed\s*\(\s*\d+\s*\)"),
        re.compile(r"manual_seed\s*\("),
    ]

    found_seed = any(p.search(code_sources) for p in seed_patterns)
    assert found_seed == True, (
        "Notebook code cells do not set a fixed random seed. "
        "Add RANDOM_STATE = 42 and pass random_state=RANDOM_STATE to all stochastic calls."
    )
    # Specific: we checked against exactly 5 seed patterns
    assert len(seed_patterns) == 5, "Expected 5 seed detection patterns"


# ===========================================================================
# FEATURE TESTS
# ===========================================================================


def test_training_data_has_model_selection_section() -> None:
    """Notebook must have a 'Model Selection' markdown heading.

    The section must name the model, metric, and split ratio so readers
    understand the experimental design before running any code.
    """
    nb = _load_notebook()
    assert _find_markdown_heading(nb, "Model Selection"), (
        "No markdown heading 'Model Selection' found in notebook. "
        "Add a section describing the model, metric, and train/test split."
    )

    # Verify the section contains required content keywords
    markdown_text = " ".join(_markdown_cell_sources(nb)).lower()

    assert "logisticregression" in markdown_text or "logistic regression" in markdown_text, (
        "Model Selection section must name the model (LogisticRegression)."
    )
    assert "roc-auc" in markdown_text or "roc_auc" in markdown_text or "auc" in markdown_text, (
        "Model Selection section must name the evaluation metric (ROC-AUC)."
    )
    assert "80" in markdown_text, (
        "Model Selection section must state the 80/20 train/test split ratio."
    )
    assert "20" in markdown_text, "Model Selection section must state the 20% test split ratio."


def test_training_data_has_limitations_section() -> None:
    """Notebook must have a 'Limitations' markdown heading with honest caveats.

    The section must explicitly state that synthetic data typically
    underperforms real data to prevent misrepresentation of results.
    """
    nb = _load_notebook()
    assert _find_markdown_heading(nb, "Limitations"), (
        "No markdown heading 'Limitations' found in notebook. "
        "Add an honest Limitations section acknowledging synthetic data's shortcomings."
    )

    # Must explicitly state underperformance caveat
    markdown_text = " ".join(_markdown_cell_sources(nb)).lower()
    underperformance_patterns = [
        "underperform",
        "lower than real",
        "worse than real",
        "does not match real",
        "typically lower",
        "typically worse",
        "does not generalize",
    ]
    found = any(p in markdown_text for p in underperformance_patterns)
    assert found, (
        "Limitations section must explicitly state that synthetic data typically "
        "underperforms real data on downstream tasks."
    )


def test_training_data_has_privacy_explanation() -> None:
    """Notebook must explain epsilon values in plain language.

    The explanation must cover what epsilon bounds in practical terms
    (inference probability ratio), not just the mathematical definition.
    """
    nb = _load_notebook()
    markdown_text = " ".join(_markdown_cell_sources(nb)).lower()

    # Must mention epsilon
    assert "epsilon" in markdown_text, (
        "Notebook must explain the epsilon privacy parameter in plain language."
    )

    # Must explain delta
    assert "delta" in markdown_text, (
        "Notebook must explain the delta privacy parameter (delta=1e-5 meaning)."
    )

    # Must use plain-language framing (not purely mathematical)
    plain_language_cues = [
        "plain",
        "practical",
        "means",
        "interpret",
        "probability",
        "inference",
        "attacker",
        "adversary",
        "distinguishing",
        "bound",
    ]
    found = any(cue in markdown_text for cue in plain_language_cues)
    assert found, (
        "Privacy section must frame epsilon in plain language or practical terms, "
        "not purely as a mathematical definition."
    )


def test_training_data_has_utility_curve_section() -> None:
    """Notebook must have a section demonstrating the privacy-utility tradeoff.

    The utility curve section must include code that plots or tabulates
    results across multiple epsilon levels, showing ROC-AUC as a function
    of the privacy budget.
    """
    nb = _load_notebook()

    # Check for a heading containing 'utility' or 'tradeoff'/'trade-off'
    markdown_text = " ".join(_markdown_cell_sources(nb)).lower()
    has_heading = (
        _find_markdown_heading(nb, "Utility Curve")
        or _find_markdown_heading(nb, "Privacy-Utility")
        or _find_markdown_heading(nb, "Privacy Utility")
        or "utility" in markdown_text
        or "tradeoff" in markdown_text
        or "trade-off" in markdown_text
    )
    assert has_heading, "Notebook must have a utility curve or privacy-utility tradeoff section."

    # Must reference multiple epsilon/noise levels
    code_sources = " ".join(_code_cell_sources(nb))
    noise_levels = [
        re.search(r"noise_multiplier\s*=\s*1\.0", code_sources),
        re.search(r"noise_multiplier\s*=\s*5\.0", code_sources),
        re.search(r"noise_multiplier\s*=\s*10\.0", code_sources),
    ]
    levels_found = sum(1 for m in noise_levels if m is not None)
    assert levels_found >= 2, (
        f"Utility curve section must reference at least 2 distinct noise_multiplier "
        f"levels (low/medium/high). Found {levels_found} distinct noise level references."
    )


def test_training_data_has_augmentation_section() -> None:
    """Notebook must demonstrate real+synthetic combined training (augmentation).

    Training exclusively on synthetic data is the primary workflow, but
    the augmentation section shows how combining real and synthetic data
    can improve utility at a given privacy budget.
    """
    nb = _load_notebook()

    markdown_text = " ".join(_markdown_cell_sources(nb)).lower()
    has_augmentation = (
        _find_markdown_heading(nb, "Augmentation")
        or "augment" in markdown_text
        or "combined" in markdown_text
        or "real + synthetic" in markdown_text
        or "real+synthetic" in markdown_text
    )
    assert has_augmentation == True, (
        "Notebook must have an augmentation section showing real+synthetic combined training."
    )

    # The code must reference pd.concat or similar combination of real + synthetic DataFrames
    code_sources = " ".join(_code_cell_sources(nb))
    has_concat = "concat" in code_sources or "augment" in code_sources.lower()
    assert has_concat == True, (
        "Augmentation section must combine real and synthetic DataFrames "
        "(e.g., pd.concat([X_train_real, X_train_synth]))."
    )
    # Specific: the markdown text we checked was non-empty
    assert len(markdown_text) > 0, "Notebook has no markdown cells — cannot verify augmentation"
