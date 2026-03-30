"""Hygiene polish validation tests for T43.4.

Tests verifying:
1. dp_training.py: The ``except Exception`` block in ``fit()`` has an inline
   justification comment explaining the broad catch.
2. accountant.py: EPSILON_SPENT_TOTAL counter has documented label strategy
   including cardinality expectations.
3. models.py: ModelArtifact mutability is documented near the class definition.
4. Spike findings documents have a "HISTORICAL — DO NOT USE" header.

Note: The original AC1 tests for job_orchestration.py (epsilon_spent and
audit.log_event ``except Exception as exc:`` handlers) were removed in a later
refactor that replaced the broad ``except Exception as exc:`` catches with typed
exceptions (RuntimeError, OOMGuardrailError, etc.).  Those stale tests were
deleted; the typed exception handlers they guarded are the correct, more
restrictive implementation.

Approach: source-code inspection tests. For comments and docstrings we read the
module source and assert the expected text is present. This is the lightest-weight
approach that is still behaviour-preserving — the tests will fail if the comment is
removed in a future refactor, acting as a regression guard.

Comments placed immediately before ``except Exception as exc:`` (Python style)
are checked in a window of lines around each such statement.

CONSTITUTION Priority 3: TDD
Task: T43.4 — Code Hygiene Polish Batch
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "synth_engine"
_DOCS_ROOT = Path(__file__).parent.parent.parent / "docs"


def _read_source(relative_path: str) -> str:
    """Return the full text of a source file under src/synth_engine/.

    Args:
        relative_path: Path relative to ``src/synth_engine/``.

    Returns:
        The file contents as a string.
    """
    return (_SRC_ROOT / relative_path).read_text(encoding="utf-8")


def _find_except_exception_indices(lines: list[str]) -> list[int]:
    """Return line indices (0-based) of all ``except Exception as exc:`` statements.

    Args:
        lines: Source lines split from the file text.

    Returns:
        Sorted list of 0-based line indices.
    """
    return [i for i, line in enumerate(lines) if "except Exception as exc:" in line]


def _has_comment_in_window(lines: list[str], center_idx: int, window: int = 5) -> bool:
    """Return True if any line in the window around center_idx contains a ``#`` comment.

    Looks in [center_idx - window, center_idx + window].

    Args:
        lines: All source lines.
        center_idx: The index of the ``except`` line.
        window: Number of lines to check on each side.

    Returns:
        True if a comment line is present in the window.
    """
    start = max(0, center_idx - window)
    end = min(len(lines), center_idx + window + 1)
    return any("#" in lines[i] for i in range(start, end))


# ---------------------------------------------------------------------------
# AC2: dp_training.py — exception handler justification comment
# ---------------------------------------------------------------------------


class TestDpTrainingExceptionComment:
    """Verify the fallback except Exception block in fit() has a justification comment."""

    def test_dp_training_fallback_exception_handler_has_justification_comment(self) -> None:
        """The except block for the discriminator DP-SGD fallback has a comment.

        The broad ``except Exception`` in ``DPCompatibleCTGAN.fit()`` is
        intentional: it catches all non-BudgetExhaustionError failures from
        the discriminator DP-SGD training loop and triggers the fallback to
        proxy model + CTGAN. This must be documented with an inline justification
        comment near the ``except Exception`` statement.
        """
        source = _read_source("modules/synthesizer/training/dp_training.py")
        lines = source.splitlines()
        except_indices = _find_except_exception_indices(lines)

        assert except_indices, "No 'except Exception as exc:' found in dp_training.py"

        # Find the except block that is in the fallback context
        fallback_except_idx: int | None = None
        for idx in except_indices:
            context = "\n".join(lines[max(0, idx - 12) : idx + 2])
            if "BudgetExhaustionError" in context and (
                "DpCtganStrategy" in context or "discriminator" in context.lower()
            ):
                fallback_except_idx = idx
                break

        assert fallback_except_idx is not None, (
            "Could not locate the fallback except Exception block in dp_training.py fit()"
        )

        assert _has_comment_in_window(lines, fallback_except_idx, window=5), (
            f"No inline justification comment found within 5 lines of the fallback "
            f"except Exception block (line {fallback_except_idx + 1}) in dp_training.py.\n"
            f"Context:\n"
            + textwrap.indent(
                "\n".join(lines[max(0, fallback_except_idx - 5) : fallback_except_idx + 3]),
                "  ",
            )
        )


# ---------------------------------------------------------------------------
# AC3: accountant.py — Prometheus label strategy documented
# ---------------------------------------------------------------------------


class TestAccountantPrometheusLabelStrategy:
    """Verify EPSILON_SPENT_TOTAL counter has documented label strategy."""

    def test_epsilon_spent_total_comment_documents_cardinality(self) -> None:
        """The EPSILON_SPENT_TOTAL counter comment mentions cardinality expectations.

        Label cardinality must be documented so operators understand the
        risk of high-cardinality labels causing memory bloat in Prometheus.
        """
        source = _read_source("modules/privacy/accountant.py")
        assert "cardinality" in source.lower(), (
            "EPSILON_SPENT_TOTAL counter comment does not mention cardinality expectations. "
            "Add a note about expected label cardinality (e.g. bounded by number of "
            "jobs/ledgers) to the comment block above the Counter definition."
        )

    def test_epsilon_spent_total_comment_documents_label_semantics(self) -> None:
        """The EPSILON_SPENT_TOTAL counter comment explains what each label means."""
        source = _read_source("modules/privacy/accountant.py")

        assert "job_id" in source, "job_id label not documented in accountant.py"
        assert "dataset_id" in source, "dataset_id label not documented in accountant.py"
        assert "EPSILON_SPENT_TOTAL" in source, "EPSILON_SPENT_TOTAL not found in accountant.py"

        # Label strategy comment must appear near the counter definition
        lines = source.splitlines()
        counter_idx = next(
            (
                i
                for i, line in enumerate(lines)
                if "EPSILON_SPENT_TOTAL" in line and "Counter" in line
            ),
            None,
        )
        assert counter_idx is not None, "EPSILON_SPENT_TOTAL Counter definition not found"

        # Within 25 lines above the definition there must be a comment mentioning labels
        comment_region = "\n".join(lines[max(0, counter_idx - 25) : counter_idx + 5])
        assert "#" in comment_region, "No comment block found near EPSILON_SPENT_TOTAL definition"
        assert "label" in comment_region.lower(), (
            "Comment block near EPSILON_SPENT_TOTAL does not mention labels"
        )


# ---------------------------------------------------------------------------
# AC4: models.py — ModelArtifact mutability documented
# ---------------------------------------------------------------------------


class TestModelArtifactMutabilityDocumented:
    """Verify ModelArtifact documents its mutability rationale."""

    def test_model_artifact_mutability_is_documented(self) -> None:
        """ModelArtifact source must document why the dataclass is not frozen.

        A mutable dataclass can be accidentally modified after training,
        corrupting the artifact state. The intentional mutability must be
        documented with explicit rationale near the class definition.
        """
        # T58.4: ModelArtifact moved to artifact.py; check there first, fall back to models.py
        source = _read_source("modules/synthesizer/storage/artifact.py")
        lines = source.splitlines()

        class_line_idx = next(
            (i for i, line in enumerate(lines) if "class ModelArtifact" in line), None
        )
        assert class_line_idx is not None, (
            "ModelArtifact class not found in artifact.py (T58.4: moved from models.py)"
        )
        assert class_line_idx != None  # noqa: E711 — specific check

        # Look for mutability documentation in the 15 lines before the class definition
        # (comment block above) plus the 60 lines of the class body (docstring)
        pre_class = "\n".join(lines[max(0, class_line_idx - 15) : class_line_idx])
        class_body = "\n".join(lines[class_line_idx : class_line_idx + 60])
        all_relevant = pre_class + "\n" + class_body

        mutability_documented = any(
            keyword in all_relevant.lower()
            for keyword in ["mutable", "mutability", "frozen", "incremental", "finalization"]
        )
        assert mutability_documented == True, (
            "ModelArtifact class does not document its mutability rationale. "
            "Add a comment above the @dataclass decorator or a note in the class docstring "
            "explaining why frozen=True is not used "
            "(e.g. fields are set incrementally during job finalization)."
        )
        assert mutability_documented


# ---------------------------------------------------------------------------
# AC5: Spike findings documents have HISTORICAL header
# ---------------------------------------------------------------------------


class TestSpikeFindingsHistoricalHeader:
    """Verify spike findings documents are marked as historical."""

    @pytest.mark.parametrize(
        "spike_file",
        [
            "archive/spikes/findings_spike_a.md",
            "archive/spikes/findings_spike_b.md",
            "archive/spikes/findings_spike_c.md",
        ],
    )
    def test_spike_findings_has_historical_header(self, spike_file: str) -> None:
        """Each spike findings doc must have a HISTORICAL header at the top.

        Spike findings documents are historical artifacts and must be clearly
        marked as "HISTORICAL — DO NOT USE" to prevent future developers from
        treating them as current guidance.

        Args:
            spike_file: Path relative to the docs/ directory.
        """
        doc_path = _DOCS_ROOT / spike_file
        assert doc_path.exists(), f"Spike findings file not found: {doc_path}"

        content = doc_path.read_text(encoding="utf-8")
        # The header must appear in the first 10 lines of the document
        first_lines = "\n".join(content.splitlines()[:10])
        assert "HISTORICAL" in first_lines, (
            f"{spike_file}: Expected 'HISTORICAL' marker in the first 10 lines, "
            f"but got:\n{textwrap.indent(first_lines, '  ')}"
        )
        assert "DO NOT USE" in first_lines, (
            f"{spike_file}: Expected 'DO NOT USE' marker in the first 10 lines, "
            f"but got:\n{textwrap.indent(first_lines, '  ')}"
        )
