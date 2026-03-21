"""Smoke tests for the synth_engine package.

Verifies that the package is importable and exposes a valid semver version string.

CONSTITUTION Priority 3: TDD RED Phase
Task: P1-T1.2 — TDD Framework
Task: T40.1 — Replace Shallow Assertions With Value-Checking Tests
"""

import re

import synth_engine


def test_version_is_set() -> None:
    """synth_engine.__version__ must be a non-empty string.

    This is the minimal contract that proves the package is installed and
    the metadata is correctly initialized.
    """
    assert isinstance(synth_engine.__version__, str)
    assert len(synth_engine.__version__) > 0


def test_version_is_semver() -> None:
    """synth_engine.__version__ must conform to the MAJOR.MINOR.PATCH semver format.

    Allows optional pre-release labels (e.g. 1.2.3.dev0, 1.2.3a1) as produced
    by setuptools-scm, but the leading triplet must be three numeric components.
    """
    semver_pattern = re.compile(r"^\d+\.\d+\.\d+")
    assert semver_pattern.match(synth_engine.__version__), (
        f"__version__ '{synth_engine.__version__}' does not start with MAJOR.MINOR.PATCH"
    )
