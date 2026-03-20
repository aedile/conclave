"""Unit tests for scripts/e2e_load_test.py.

Tests validate metric calculations, result JSON structure, dry-run mode,
and system info collection without requiring Docker or a running server.

Task: E2E 1M-row load test script
CONSTITUTION Priority 0: No real PII or live services required for these tests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module loader -- scripts/ is not a package on sys.path by default
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Dev DSN constant used across dry-run tests.
# This is a fictional dev credential, not a real secret.
_DEV_SOURCE_DSN = "postgresql://dev:dev@localhost:5432/conclave_source"  # pragma: allowlist secret


def _import_load_test_module() -> Any:
    """Dynamically import e2e_load_test from scripts/ directory.

    Returns:
        The imported module object.
    """
    spec = importlib.util.spec_from_file_location(
        "e2e_load_test",
        SCRIPTS_DIR / "e2e_load_test.py",
    )
    assert spec is not None, "Could not locate scripts/e2e_load_test.py"
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register so relative imports from the module resolve correctly
    sys.modules.setdefault("e2e_load_test", module)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mod() -> Any:
    """Return the imported e2e_load_test module."""
    return _import_load_test_module()


# ---------------------------------------------------------------------------
# Tests: metric calculations
# ---------------------------------------------------------------------------


class TestMetricCalculations:
    """Verify pure metric helper functions."""

    def test_calculate_rows_per_sec_normal(self, mod: Any) -> None:
        """rows_per_sec = num_rows / duration_s for normal values."""
        result = mod.calculate_rows_per_sec(num_rows=50_000, duration_s=100.0)
        assert result == pytest.approx(500.0)

    def test_calculate_rows_per_sec_zero_duration(self, mod: Any) -> None:
        """rows_per_sec returns 0.0 when duration is zero to avoid ZeroDivisionError."""
        result = mod.calculate_rows_per_sec(num_rows=50_000, duration_s=0.0)
        assert result == 0.0

    def test_calculate_rows_per_sec_large_dataset(self, mod: Any) -> None:
        """rows_per_sec handles 1M+ rows correctly."""
        result = mod.calculate_rows_per_sec(num_rows=1_012_500, duration_s=3600.0)
        assert result == pytest.approx(281.25)

    def test_mb_from_bytes_zero(self, mod: Any) -> None:
        """mb_from_bytes returns 0.0 for 0 bytes."""
        assert mod.mb_from_bytes(0) == 0.0

    def test_mb_from_bytes_typical(self, mod: Any) -> None:
        """mb_from_bytes converts bytes to MB with 2 decimal rounding."""
        result = mod.mb_from_bytes(12_300_000)
        assert result == pytest.approx(11.73, rel=1e-2)

    def test_mb_from_bytes_one_megabyte(self, mod: Any) -> None:
        """mb_from_bytes returns exactly 1.0 for 1_048_576 bytes."""
        assert mod.mb_from_bytes(1_048_576) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Tests: result JSON structure
# ---------------------------------------------------------------------------


class TestResultJsonStructure:
    """Validate the structure of the results dict written to JSON."""

    def _make_job_result(
        self,
        table: str = "customers",
        status: str = "COMPLETED",
        duration_s: float = 120.0,
        num_rows: int = 50_000,
        dp_enabled: bool = True,
        noise_multiplier: float = 1.1,
        epsilon_spent: float | None = 1.89,
        artifact_size_mb: float = 12.3,
    ) -> dict[str, Any]:
        """Build a minimal job result dict matching the expected schema."""
        return {
            "table": table,
            "status": status,
            "duration_s": duration_s,
            "rows_per_sec": round(num_rows / duration_s, 2) if duration_s else 0.0,
            "epsilon_spent": epsilon_spent,
            "artifact_size_mb": artifact_size_mb,
            "dp_enabled": dp_enabled,
            "noise_multiplier": noise_multiplier,
        }

    def test_build_results_dict_top_level_keys(self, mod: Any) -> None:
        """build_results_dict includes all required top-level keys."""
        job_results = [self._make_job_result()]
        cli_result: dict[str, Any] = {
            "status": "success",
            "duration_s": 45.0,
            "seed_rows": 100,
            "total_rows_subsetted": 1234,
        }
        shred_results = [{"job_id": 1, "status": "success"}]
        system_info: dict[str, Any] = {
            "platform": "darwin",
            "ram_gb": 16.0,
            "cpu_count": 8,
        }

        result = mod.build_results_dict(
            run_date="2026-03-19T12:00:00",
            total_source_rows=1_012_500,
            dataset={
                "customers": 50_000,
                "orders": 175_000,
                "order_items": 612_500,
                "payments": 175_000,
            },
            job_results=job_results,
            cli_subsetting=cli_result,
            shred_results=shred_results,
            system_info=system_info,
        )

        required_keys = {
            "run_date",
            "total_source_rows",
            "dataset",
            "jobs",
            "cli_subsetting",
            "shred_results",
            "system",
        }
        assert required_keys.issubset(result.keys())

    def test_build_results_dict_total_source_rows(self, mod: Any) -> None:
        """build_results_dict preserves total_source_rows correctly."""
        job_results: list[dict[str, Any]] = []
        result = mod.build_results_dict(
            run_date="2026-03-19T12:00:00",
            total_source_rows=1_012_500,
            dataset={},
            job_results=job_results,
            cli_subsetting={},
            shred_results=[],
            system_info={},
        )
        assert result["total_source_rows"] == 1_012_500

    def test_build_results_dict_jobs_list(self, mod: Any) -> None:
        """build_results_dict embeds job_results under 'jobs' key."""
        job1 = self._make_job_result("customers")
        job2 = self._make_job_result("orders", num_rows=175_000)
        result = mod.build_results_dict(
            run_date="2026-03-19T12:00:00",
            total_source_rows=1_012_500,
            dataset={},
            job_results=[job1, job2],
            cli_subsetting={},
            shred_results=[],
            system_info={},
        )
        assert len(result["jobs"]) == 2
        assert result["jobs"][0]["table"] == "customers"
        assert result["jobs"][1]["table"] == "orders"

    def test_results_dict_is_json_serializable(self, mod: Any) -> None:
        """build_results_dict output can be round-tripped through JSON without error."""
        job_results = [self._make_job_result()]
        result = mod.build_results_dict(
            run_date="2026-03-19T12:00:00",
            total_source_rows=1_012_500,
            dataset={"customers": 50_000},
            job_results=job_results,
            cli_subsetting={"status": "success", "duration_s": 45.0},
            shred_results=[{"job_id": 1, "status": "success"}],
            system_info={"platform": "darwin", "ram_gb": 16.0, "cpu_count": 8},
        )
        serialised = json.dumps(result)
        restored = json.loads(serialised)
        assert restored["total_source_rows"] == 1_012_500

    def test_job_result_keys(self, mod: Any) -> None:
        """Each job entry in the results dict contains all required fields."""
        job = self._make_job_result()
        required_job_keys = {
            "table",
            "status",
            "duration_s",
            "rows_per_sec",
            "epsilon_spent",
            "artifact_size_mb",
            "dp_enabled",
            "noise_multiplier",
        }
        assert required_job_keys.issubset(job.keys())


# ---------------------------------------------------------------------------
# Tests: dry-run mode
# ---------------------------------------------------------------------------


class TestDryRunMode:
    """Verify --dry-run produces a plan without making HTTP calls."""

    def test_build_dry_run_plan_returns_string(self, mod: Any) -> None:
        """build_dry_run_plan returns a non-empty string."""
        plan = mod.build_dry_run_plan(
            source_dsn=_DEV_SOURCE_DSN,
            api_base_url="http://localhost:8000",
            n_customers=50_000,
            n_orders=175_000,
        )
        assert isinstance(plan, str)
        assert len(plan) > 0

    def test_build_dry_run_plan_mentions_row_counts(self, mod: Any) -> None:
        """build_dry_run_plan includes the dataset row counts in its output."""
        plan = mod.build_dry_run_plan(
            source_dsn=_DEV_SOURCE_DSN,
            api_base_url="http://localhost:8000",
            n_customers=50_000,
            n_orders=175_000,
        )
        assert "50000" in plan or "50,000" in plan
        assert "175000" in plan or "175,000" in plan

    def test_build_dry_run_plan_no_http_calls(self, mod: Any) -> None:
        """build_dry_run_plan must not make any HTTP requests."""
        with patch("httpx.Client") as mock_client, patch("httpx.get") as mock_get:
            mod.build_dry_run_plan(
                source_dsn=_DEV_SOURCE_DSN,
                api_base_url="http://localhost:8000",
                n_customers=50_000,
                n_orders=175_000,
            )
            mock_client.assert_not_called()
            mock_get.assert_not_called()

    def test_build_dry_run_plan_shows_api_url(self, mod: Any) -> None:
        """build_dry_run_plan echoes the configured API base URL."""
        custom_url = "http://custom-host:9000"
        plan = mod.build_dry_run_plan(
            source_dsn=_DEV_SOURCE_DSN,
            api_base_url=custom_url,
            n_customers=50_000,
            n_orders=175_000,
        )
        assert custom_url in plan


# ---------------------------------------------------------------------------
# Tests: system info collection
# ---------------------------------------------------------------------------


class TestSystemInfoCollection:
    """Verify collect_system_info returns the expected structure."""

    def test_collect_system_info_keys(self, mod: Any) -> None:
        """collect_system_info returns dict with platform, ram_gb, cpu_count."""
        info = mod.collect_system_info()
        assert "platform" in info
        assert "ram_gb" in info
        assert "cpu_count" in info

    def test_collect_system_info_platform_is_string(self, mod: Any) -> None:
        """platform value is a non-empty string."""
        info = mod.collect_system_info()
        assert isinstance(info["platform"], str)
        assert len(info["platform"]) > 0

    def test_collect_system_info_ram_gb_positive(self, mod: Any) -> None:
        """ram_gb is a positive float."""
        info = mod.collect_system_info()
        assert isinstance(info["ram_gb"], float)
        assert info["ram_gb"] > 0

    def test_collect_system_info_cpu_count_positive(self, mod: Any) -> None:
        """cpu_count is a positive integer."""
        info = mod.collect_system_info()
        assert isinstance(info["cpu_count"], int)
        assert info["cpu_count"] > 0

    def test_collect_system_info_uses_psutil(self, mod: Any) -> None:
        """collect_system_info delegates RAM measurement to psutil."""
        mock_vm = MagicMock()
        mock_vm.total = 17_179_869_184  # 16 GiB in bytes
        with patch("psutil.virtual_memory", return_value=mock_vm):
            info = mod.collect_system_info()
        assert info["ram_gb"] == pytest.approx(16.0, rel=1e-2)
