"""Unit tests for scripts/e2e_load_test.py.

Tests validate metric calculations, result JSON structure, dry-run mode,
system info collection, and license activation — without requiring Docker
or a running server.

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

pytestmark = [pytest.mark.infrastructure]

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


@pytest.fixture(scope="module")
def rsa_private_key_pem() -> str:
    """Generate a fresh ephemeral RSA private key for license activation tests.

    Returns:
        PEM-encoded RSA-2048 private key string (test-only, never production).
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


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
            assert mock_client.call_count == 0
            assert mock_get.call_count == 0

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


# ---------------------------------------------------------------------------
# Tests: license activation step
# ---------------------------------------------------------------------------


class TestStepActivateLicense:
    """Verify step_activate_license signs a JWT and activates the license via API.

    All tests mock httpx — no network calls are made.
    Test RSA keys are ephemeral (generated in fixture above).
    """

    def test_activate_license_success(
        self,
        mod: Any,
        rsa_private_key_pem: str,
        tmp_path: Path,
    ) -> None:
        """step_activate_license succeeds when the API returns HTTP 200.

        Arrange: write a temp private key file; mock GET /license/challenge
            returning a hardware_id, and POST /license/activate returning 200.
        Act: call step_activate_license.
        Assert: the function returns without raising; POST was called once.
        """
        key_file = tmp_path / "test_private.pem"
        key_file.write_text(rsa_private_key_pem)

        mock_challenge_resp = MagicMock()
        mock_challenge_resp.status_code = 200
        mock_challenge_resp.json.return_value = {"hardware_id": "test-hw-id-abc123"}
        mock_challenge_resp.raise_for_status = MagicMock()

        mock_activate_resp = MagicMock()
        mock_activate_resp.status_code = 200
        mock_activate_resp.raise_for_status = MagicMock()

        with (
            patch("httpx.get", return_value=mock_challenge_resp) as mock_get,
            patch("httpx.post", return_value=mock_activate_resp) as mock_post,
        ):
            mod.step_activate_license(
                api_base_url="http://localhost:8000",
                license_key_path=key_file,
            )

        mock_get.assert_called_once()
        assert "/license/challenge" in mock_get.call_args[0][0]
        mock_post.assert_called_once()
        assert "/license/activate" in mock_post.call_args[0][0]

    def test_activate_license_already_active_is_not_an_error(
        self,
        mod: Any,
        rsa_private_key_pem: str,
        tmp_path: Path,
    ) -> None:
        """step_activate_license treats 409 (already activated) as a non-error.

        Arrange: GET /license/challenge returns hardware_id; POST /license/activate
            returns HTTP 409 (license already active on this hardware).
        Act: call step_activate_license.
        Assert: no SystemExit is raised; function completes normally.
        """
        key_file = tmp_path / "test_private_409.pem"
        key_file.write_text(rsa_private_key_pem)

        mock_challenge_resp = MagicMock()
        mock_challenge_resp.status_code = 200
        mock_challenge_resp.json.return_value = {"hardware_id": "test-hw-id-409"}
        mock_challenge_resp.raise_for_status = MagicMock()

        import httpx

        mock_activate_resp = MagicMock()
        mock_activate_resp.status_code = 409
        mock_activate_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "409 Conflict",
            request=MagicMock(),
            response=mock_activate_resp,
        )

        with (
            patch("httpx.get", return_value=mock_challenge_resp),
            patch("httpx.post", return_value=mock_activate_resp),
        ):
            # Must NOT raise SystemExit for 409
            mod.step_activate_license(
                api_base_url="http://localhost:8000",
                license_key_path=key_file,
            )
            # 409 is handled gracefully — verify no SystemExit was raised
            assert mock_activate_resp.status_code == 409, "Mock should have returned 409"

    def test_activate_license_jwt_contains_hardware_id_claim(
        self,
        mod: Any,
        rsa_private_key_pem: str,
        tmp_path: Path,
    ) -> None:
        """The JWT posted to /license/activate must contain the hardware_id claim.

        Arrange: capture the JSON payload sent to POST /license/activate.
        Act: call step_activate_license with a known hardware_id.
        Assert: decoded JWT claims contain hardware_id matching the challenge value.
        """
        import jwt as pyjwt
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        key_file = tmp_path / "test_private_claims.pem"
        key_file.write_text(rsa_private_key_pem)

        # Derive public key for verification
        private_key = load_pem_private_key(rsa_private_key_pem.encode(), password=None)
        from cryptography.hazmat.primitives import serialization

        public_key_pem = (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )

        expected_hw_id = "hw-id-claim-check-999"
        captured_payload: list[dict[str, Any]] = []

        mock_challenge_resp = MagicMock()
        mock_challenge_resp.status_code = 200
        mock_challenge_resp.json.return_value = {"hardware_id": expected_hw_id}
        mock_challenge_resp.raise_for_status = MagicMock()

        mock_activate_resp = MagicMock()
        mock_activate_resp.status_code = 200
        mock_activate_resp.raise_for_status = MagicMock()

        def _capture_post(url: str, **kwargs: Any) -> MagicMock:
            captured_payload.append(kwargs.get("json", {}))
            return mock_activate_resp

        with (
            patch("httpx.get", return_value=mock_challenge_resp),
            patch("httpx.post", side_effect=_capture_post),
        ):
            mod.step_activate_license(
                api_base_url="http://localhost:8000",
                license_key_path=key_file,
            )

        assert len(captured_payload) == 1
        token = captured_payload[0]["token"]
        claims = pyjwt.decode(token, public_key_pem, algorithms=["RS256"])
        assert claims["hardware_id"] == expected_hw_id
        assert claims["sub"] == "e2e-load-test"
        assert "exp" in claims
        assert "iat" in claims

    def test_activate_license_exits_on_unexpected_http_error(
        self,
        mod: Any,
        rsa_private_key_pem: str,
        tmp_path: Path,
    ) -> None:
        """step_activate_license calls sys.exit on unexpected HTTP errors (e.g., 500).

        Arrange: GET challenge succeeds; POST /license/activate returns HTTP 500.
        Act: call step_activate_license.
        Assert: SystemExit is raised.
        """
        import httpx

        key_file = tmp_path / "test_private_500.pem"
        key_file.write_text(rsa_private_key_pem)

        mock_challenge_resp = MagicMock()
        mock_challenge_resp.status_code = 200
        mock_challenge_resp.json.return_value = {"hardware_id": "test-hw-id-500"}
        mock_challenge_resp.raise_for_status = MagicMock()

        mock_activate_resp = MagicMock()
        mock_activate_resp.status_code = 500
        mock_activate_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=mock_activate_resp,
        )

        with (
            patch("httpx.get", return_value=mock_challenge_resp),
            patch("httpx.post", return_value=mock_activate_resp),
        ):
            with pytest.raises(SystemExit):
                mod.step_activate_license(
                    api_base_url="http://localhost:8000",
                    license_key_path=key_file,
                )

    def test_activate_license_step_numbering_in_output(
        self,
        mod: Any,
        rsa_private_key_pem: str,
        tmp_path: Path,
    ) -> None:
        """step_activate_license prints a [4/14] step banner to stdout.

        The total step count is 14 after adding this step.
        """
        key_file = tmp_path / "test_private_banner.pem"
        key_file.write_text(rsa_private_key_pem)

        mock_challenge_resp = MagicMock()
        mock_challenge_resp.status_code = 200
        mock_challenge_resp.json.return_value = {"hardware_id": "banner-hw-id"}
        mock_challenge_resp.raise_for_status = MagicMock()

        mock_activate_resp = MagicMock()
        mock_activate_resp.status_code = 200
        mock_activate_resp.raise_for_status = MagicMock()

        with (
            patch("httpx.get", return_value=mock_challenge_resp),
            patch("httpx.post", return_value=mock_activate_resp),
        ):
            from click.testing import CliRunner

            runner = CliRunner()
            output_lines: list[str] = []

            # Patch click.echo to capture output
            with patch("click.echo", side_effect=lambda msg, **kw: output_lines.append(str(msg))):
                mod.step_activate_license(
                    api_base_url="http://localhost:8000",
                    license_key_path=key_file,
                )

        assert runner is not None  # CliRunner imported successfully
        combined = "\n".join(output_lines)
        assert "4/14" in combined, f"Expected '4/14' in output, got: {combined!r}"


# ---------------------------------------------------------------------------
# Tests: step_collect_metrics — non-DP table handling
# ---------------------------------------------------------------------------


class TestStepCollectMetricsNonDp:
    """Verify step_collect_metrics handles non-DP tables without KeyError.

    The ``payments`` table in JOB_PARAMS has ``enable_dp: False`` and omits
    ``noise_multiplier``.  A direct dict access raises ``KeyError``; the fix
    uses ``.get("noise_multiplier", 0.0)`` so non-DP jobs default to 0.0.
    """

    def _make_job_responses(self) -> dict[str, dict[str, Any]]:
        """Build minimal job_responses for all four tables."""
        base_time = 1_000_000.0
        return {
            table: {
                "status": "COMPLETE",
                "_start_time": base_time,
                "_end_time": base_time + 60.0,
                "actual_epsilon": None,
            }
            for table in ("customers", "orders", "order_items", "payments")
        }

    def test_non_dp_payments_table_does_not_raise_key_error(self, mod: Any, tmp_path: Path) -> None:
        """step_collect_metrics must not raise KeyError for the payments table.

        ``payments`` has no ``noise_multiplier`` key in JOB_PARAMS because
        DP is disabled.  Without the fix, line 663 raises KeyError.

        Arrange: provide a minimal table_to_job_id mapping covering all four
            tables and stub out httpx.get so no real HTTP calls are made.
        Act: call step_collect_metrics.
        Assert: the function returns a list with one entry per table, the
            ``payments`` entry has ``noise_multiplier == 0.0``, and
            ``dp_enabled`` is False.
        """
        table_to_job_id = {
            "customers": 1,
            "orders": 2,
            "order_items": 3,
            "payments": 4,
        }
        job_responses = self._make_job_responses()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = b""

        with patch("httpx.get", return_value=mock_resp):
            results = mod.step_collect_metrics(
                api_base_url="http://localhost:8000",
                table_to_job_id=table_to_job_id,
                job_responses=job_responses,
                tmp_dir=tmp_path,
            )

        payments_result = next(r for r in results if r["table"] == "payments")
        assert payments_result["dp_enabled"] is False
        assert payments_result["noise_multiplier"] == pytest.approx(0.0)

    def test_dp_table_retains_noise_multiplier(self, mod: Any, tmp_path: Path) -> None:
        """step_collect_metrics preserves noise_multiplier for DP-enabled tables.

        Arrange: same setup as the non-DP test.
        Act: call step_collect_metrics.
        Assert: the ``customers`` entry (DP enabled) has a non-zero
            ``noise_multiplier`` matching what JOB_PARAMS specifies.
        """
        table_to_job_id = {
            "customers": 1,
            "orders": 2,
            "order_items": 3,
            "payments": 4,
        }
        job_responses = self._make_job_responses()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = b""

        with patch("httpx.get", return_value=mock_resp):
            results = mod.step_collect_metrics(
                api_base_url="http://localhost:8000",
                table_to_job_id=table_to_job_id,
                job_responses=job_responses,
                tmp_dir=tmp_path,
            )

        customers_result = next(r for r in results if r["table"] == "customers")
        assert customers_result["dp_enabled"] is True
        assert customers_result["noise_multiplier"] > 0.0

    def test_collect_metrics_returns_all_four_tables(self, mod: Any, tmp_path: Path) -> None:
        """step_collect_metrics returns exactly one entry per configured table.

        Arrange: provide all four table IDs.
        Act: call step_collect_metrics.
        Assert: the returned list has exactly four entries, one per table.
        """
        table_to_job_id = {
            "customers": 1,
            "orders": 2,
            "order_items": 3,
            "payments": 4,
        }
        job_responses = self._make_job_responses()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = b""

        with patch("httpx.get", return_value=mock_resp):
            results = mod.step_collect_metrics(
                api_base_url="http://localhost:8000",
                table_to_job_id=table_to_job_id,
                job_responses=job_responses,
                tmp_dir=tmp_path,
            )

        assert len(results) == 4
        table_names = {r["table"] for r in results}
        assert table_names == {"customers", "orders", "order_items", "payments"}


# ---------------------------------------------------------------------------
# ADV-E2E-03: calculate_rows_per_sec negative duration guard
# ---------------------------------------------------------------------------


class TestCalculateRowsPerSecNegativeDuration:
    """calculate_rows_per_sec must return 0.0 for negative duration_s.

    ADV-E2E-03: Before the fix, a negative duration_s produces a negative
    rows-per-second value (num_rows / negative_number), which is nonsensical
    and could corrupt metrics output. After the fix it must return 0.0.
    """

    def test_negative_duration_returns_zero(self, mod: Any) -> None:
        """calculate_rows_per_sec must return 0.0 when duration_s is negative.

        ADV-E2E-03: A negative wall-clock duration can arise from monotonic clock
        anomalies or test fixtures. The function must guard against it.
        """
        result = mod.calculate_rows_per_sec(num_rows=50_000, duration_s=-1.0)
        assert result == 0.0, f"calculate_rows_per_sec(-1.0) must return 0.0, got {result}"

    def test_large_negative_duration_returns_zero(self, mod: Any) -> None:
        """calculate_rows_per_sec returns 0.0 for large negative duration_s."""
        result = mod.calculate_rows_per_sec(num_rows=1_000_000, duration_s=-9999.0)
        assert result == 0.0, f"calculate_rows_per_sec(-9999.0) must return 0.0, got {result}"

    def test_negative_duration_small_float_returns_zero(self, mod: Any) -> None:
        """calculate_rows_per_sec returns 0.0 for small negative floats like -0.001."""
        result = mod.calculate_rows_per_sec(num_rows=100, duration_s=-0.001)
        assert result == 0.0, f"calculate_rows_per_sec(-0.001) must return 0.0, got {result}"

    def test_zero_duration_still_returns_zero(self, mod: Any) -> None:
        """Existing zero-duration guard must remain unaffected by the negative guard."""
        result = mod.calculate_rows_per_sec(num_rows=50_000, duration_s=0.0)
        assert result == 0.0
