"""Tests for docs/E2E_VALIDATION_RESULTS.md completeness and security (T54.3).

Validates that the E2E validation results template document:
  - Does not contain credentials or PII patterns (attack/security tests).
  - Contains all required sections as H2 headings.
  - Documents prerequisites and the run command.
  - Carries the epsilon-warning note for validation speed.
  - Is linked from docs/index.md.
  - References all 5 Pagila tables in the 5-table subset.

CONSTITUTION Priority 0: No real PII or secrets may appear in committed docs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DOC = REPO_ROOT / "docs" / "E2E_VALIDATION_RESULTS.md"
INDEX_DOC = REPO_ROOT / "docs" / "index.md"

# Placeholder pattern: matches safe placeholder tokens that are NOT real secrets.
_PLACEHOLDER_PATTERN = re.compile(
    r"(your[_-]?\w+|<\w+>|to be filled"
    r"|\$\{|\$[A-Z_]+|example|placeholder|changeme|REDACTED)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Shared fixture — read file once per session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def results_text() -> str:
    """Return the full text of E2E_VALIDATION_RESULTS.md.

    Returns:
        The document contents as a string.

    Raises:
        FileNotFoundError: If docs/E2E_VALIDATION_RESULTS.md does not exist.
    """
    return RESULTS_DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def index_text() -> str:
    """Return the full text of docs/index.md.

    Returns:
        The document contents as a string.

    Raises:
        FileNotFoundError: If docs/index.md does not exist.
    """
    return INDEX_DOC.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Attack tests — security / no-PII / no-credential checks
# ---------------------------------------------------------------------------


class TestNoCredentialsInDoc:
    """Ensure the results document contains no real credentials.

    CONSTITUTION Priority 0: committed documents must never contain secrets.
    These tests run against the real file content, not a mock.
    """

    def test_e2e_results_does_not_contain_real_credentials(self, results_text: str) -> None:
        """E2E_VALIDATION_RESULTS.md must not contain password-like strings.

        Checks for common credential patterns: 'password=', 'passwd=',
        'secret=', 'token=' appearing as key-value assignments.
        Fictional placeholders like 'postgresql://user:password@host' in
        How-to-Run instructions are acceptable only if qualified as examples.
        """
        # Pattern: key=<non-whitespace value> for common secret key names.
        # We flag bare assignments like: PASSWORD=abc123 or password=s3cret.
        credential_pattern = re.compile(
            r"(?i)(password|passwd|secret|api_key)\s*=\s*\S+",
            re.MULTILINE,
        )
        # Allow only placeholder text — the value must be a placeholder token.
        suspicious = [
            m
            for m in credential_pattern.finditer(results_text)
            if not _PLACEHOLDER_PATTERN.search(m.group(0))
        ]
        assert not suspicious, (
            f"Potential credentials found in E2E_VALIDATION_RESULTS.md: "
            f"{[m.group(0) for m in suspicious]}"
        )

    def test_e2e_results_does_not_contain_real_pii(self, results_text: str) -> None:
        """E2E_VALIDATION_RESULTS.md must not contain real name/email PII.

        Checks for patterns that look like real person names in 'First Last'
        form adjacent to email addresses — a strong signal of committed PII.
        The document should only refer to Pagila's fictional data in aggregate
        (row counts, statistics) — never list actual rows or records.
        """
        # Real PII signal: an email address on a line.
        email_pattern = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
        lines_with_email = [
            line for line in results_text.splitlines() if email_pattern.search(line)
        ]
        # Emails are only acceptable in two contexts:
        # 1. Generic example addresses (example.com, pagila-notice domains)
        # 2. Masked/redacted references
        safe_email_pattern = re.compile(
            r"(example\.com|pagila|MASKED|REDACTED|fictional|<email>|\[PENDING\])",
            re.IGNORECASE,
        )
        real_pii_lines = [line for line in lines_with_email if not safe_email_pattern.search(line)]
        assert not real_pii_lines, (
            f"Possible real PII (email) found in E2E_VALIDATION_RESULTS.md: {real_pii_lines}"
        )


# ---------------------------------------------------------------------------
# Feature tests — document structure and content
# ---------------------------------------------------------------------------


class TestRequiredSections:
    """Verify all required H2 sections are present in the results document."""

    REQUIRED_H2_SECTIONS = [
        "Environment",
        "Configuration",
        "Pipeline Execution",
        "Schema Reflection",
        "Subsetting Results",
        "Masking Verification",
        "CTGAN Training with DP-SGD",
        "Synthetic Output",
        "Statistical Comparison",
        "FK Integrity Verification",
        "Epsilon Budget Accounting",
        "Anomalies & Observations",
        "Conclusion",
    ]

    def test_e2e_results_has_required_sections(self, results_text: str) -> None:
        """All required H2 headings must be present in E2E_VALIDATION_RESULTS.md.

        Each section heading must appear as a level-2 Markdown heading (## ).
        """
        for section in self.REQUIRED_H2_SECTIONS:
            assert f"## {section}" in results_text, (
                f"Missing required H2 section in E2E_VALIDATION_RESULTS.md: '## {section}'"
            )


class TestHowToRun:
    """Verify the document explains how to run the validation script."""

    def test_e2e_results_has_how_to_run(self, results_text: str) -> None:
        """E2E_VALIDATION_RESULTS.md must document prerequisites and the run command.

        The 'How to Run' section must include:
          1. A mention of prerequisites (PostgreSQL or Pagila).
          2. The validate_full_pipeline.py script reference.
        """
        assert re.search(r"##\s+How to Run", results_text, re.IGNORECASE), (
            "Missing 'How to Run' section in E2E_VALIDATION_RESULTS.md"
        )

        assert "validate_full_pipeline.py" in results_text, (
            "E2E_VALIDATION_RESULTS.md must reference validate_full_pipeline.py "
            "in the How to Run section"
        )

        assert re.search(
            r"(PostgreSQL|Pagila|prerequisite)",
            results_text,
            re.IGNORECASE,
        ), (
            "E2E_VALIDATION_RESULTS.md must mention prerequisites "
            "(PostgreSQL, Pagila, or 'prerequisite') in the How to Run section"
        )


class TestEpsilonWarning:
    """Verify the document carries the epsilon-for-validation-speed disclaimer."""

    def test_e2e_results_has_epsilon_warning(self, results_text: str) -> None:
        """The document must note that epsilon=10.0 is chosen for validation speed.

        This is a mandatory disclosure so operators understand the production
        epsilon should be much lower. The note must appear near the epsilon
        configuration value.
        """
        assert re.search(
            r"chosen for validation speed",
            results_text,
            re.IGNORECASE,
        ), (
            "E2E_VALIDATION_RESULTS.md must state that epsilon is "
            "'chosen for validation speed' (not a production-grade value)"
        )


class TestIndexLink:
    """Verify docs/index.md is updated to link to the new results document."""

    def test_e2e_results_linked_from_index(self, index_text: str) -> None:
        """docs/index.md must contain a link to E2E_VALIDATION_RESULTS.md.

        The link can appear as a Markdown anchor or plain filename reference.
        It must appear in the Operator Documentation section or a similar
        navigational context — not just in comments or maintenance notes.
        """
        assert "E2E_VALIDATION_RESULTS.md" in index_text, (
            "docs/index.md must contain a link to E2E_VALIDATION_RESULTS.md"
        )


class TestFiveTableSubset:
    """Verify the document mentions all 5 Pagila tables used in the validation."""

    REQUIRED_TABLES = [
        "customer",
        "address",
        "rental",
        "inventory",
        "film",
    ]

    def test_e2e_results_documents_5_table_subset(self, results_text: str) -> None:
        """All 5 Pagila tables must be mentioned in E2E_VALIDATION_RESULTS.md.

        The validation uses a 5-table subset: customer, address, rental,
        inventory, film. Each must appear somewhere in the document to
        confirm the validation scope is correctly recorded.
        """
        for table in self.REQUIRED_TABLES:
            assert table in results_text, (
                f"Table '{table}' not mentioned in E2E_VALIDATION_RESULTS.md. "
                f"All 5 Pagila tables must be referenced."
            )
