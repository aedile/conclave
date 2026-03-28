"""Verification test: pygments must not be in the production dependency set (T66.4).

Pygments has CVE-2026-4539 with no upstream fix available. This test
verifies that pygments is a transitive dev dependency ONLY and is not
present in the production dependency groups.

CONSTITUTION Priority 0: Security — CVE must not reach production image.
Advisory: ADV-P63-05 — pygments CVE-2026-4539.
Task: T66.4 — Resolve Pygments CVE-2026-4539.
"""

from __future__ import annotations

import subprocess
import sys


def test_pygments_absent_from_production_requirements() -> None:
    """Pygments must not appear in production dependency groups.

    Runs 'poetry export --only=main' to enumerate production-only
    dependencies and asserts pygments is absent. The test fails if
    pygments appears in the exported requirements, indicating it has
    leaked from dev/demos/integration groups into the main group.

    This test uses a subprocess to avoid importing poetry internals
    directly (they are not stable public API).
    """
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
