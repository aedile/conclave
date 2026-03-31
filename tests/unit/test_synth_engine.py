"""Smoke tests for the synth_engine package and repo-level naming conventions.

Verifies:
- Package is importable and exposes a valid semver version string.
- No old task-ID-based test file names remain (post-T71.6 rename).

CONSTITUTION Priority 3: TDD RED Phase
Task: P1-T1.2 — TDD Framework
Task: T40.1 — Replace Shallow Assertions With Value-Checking Tests
Task: T71.6 — Rename P68-P70 test files to module-based names
"""

from __future__ import annotations

import re

import pytest

import synth_engine

pytestmark = pytest.mark.unit


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


# ---------------------------------------------------------------------------
# Naming convention enforcement: no old task-ID-based test file names (T71.6)
# ---------------------------------------------------------------------------

# Old task-ID-based prefixes that must no longer exist in tests/unit/.
_OLD_TASK_ID_PATTERNS = [
    "test_p68_t68",
    "test_p69_t69",
    "test_p70_t70",
]


def test_no_old_task_id_imports_remain() -> None:
    """No test file in tests/unit/ should have the old task-ID naming pattern.

    Files matching test_p<NN>_t<NN>*.py must all be renamed to module-based names
    (e.g., test_masking_thread_safety_attack.py).
    """
    from pathlib import Path

    tests_dir = Path(__file__).parent
    old_files: list[str] = []

    for pattern in _OLD_TASK_ID_PATTERNS:
        matches = list(tests_dir.glob(f"{pattern}*.py"))
        old_files.extend(str(f.name) for f in matches)

    assert len(old_files) == 0, (
        f"Old task-ID-named test files still exist (must be renamed): {old_files}"
    )


def test_renamed_module_based_test_files_exist() -> None:
    """After T71.6 renaming, expected module-based test file names must exist."""
    from pathlib import Path

    tests_dir = Path(__file__).parent
    expected_files = [
        "test_masking_thread_safety_attack.py",
        "test_admin_audit_attack.py",
        "test_health_bcrypt_bounds_failopen_attack.py",
        "test_dns_pinning_attack.py",
        "test_profiler_pii_attack.py",
        "test_concurrent_load.py",
        "test_timeout_simulation.py",
        "test_webhook_deliveries_attack.py",
        "test_erasure_idor_attack.py",
        "test_parquet_sandbox_attack.py",
        "test_subsetting_composite_fk_attack.py",
        "test_signature_removal_attack.py",
        "test_vault_memory_safe_attack.py",
        "test_settings_decomposition.py",
        "test_audit_path_param_attack.py",
    ]
    missing = [name for name in expected_files if not (tests_dir / name).exists()]
    assert len(missing) == 0, (
        f"Expected module-based test files missing after T71.6 rename: {missing}"
    )
