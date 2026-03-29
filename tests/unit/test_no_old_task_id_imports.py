"""Test T71.6 — verifies no old task-ID-based test file names remain.

After renaming P68-P70 test files from task-ID names to module-based names,
this test ensures no file in the repo still uses the old naming pattern.

CONSTITUTION Priority 5: Code Quality — consistent naming conventions
Task: T71.6 — Rename P68-P70 test files to module-based names
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# These old task-ID-based names must no longer exist in tests/unit/.
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
