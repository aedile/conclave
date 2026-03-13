"""Smoke tests for the synth_engine package.

Verifies that the package is importable and exposes a valid version string.

CONSTITUTION Priority 3: TDD RED Phase
Task: P1-T1.2 — TDD Framework
"""


import synth_engine


def test_version_is_set() -> None:
    """synth_engine.__version__ must be a non-empty string.

    This is the minimal contract that proves the package is installed and
    the metadata is correctly initialized.
    """
    assert isinstance(synth_engine.__version__, str)
    assert len(synth_engine.__version__) > 0
