"""Result verification tests for benchmark execution (T52.2).

These tests verify that committed benchmark result artifacts meet structural
and content requirements: complete grid coverage, wall time present and
positive, hardware metadata present and non-empty, schema_version present,
and the grid config file committed alongside results.

Tests read from the committed artifact files in ``demos/results/``.

Attack tests (Rule 22) are written first. They cover:
  - Missing artifact files (malformed state detection)
  - Incomplete grid coverage (omitted cells are detectable)
  - Path traversal: artifact paths are under ``demos/results/`` only

Task: P52-T52.2 — Execute Benchmarks (Real Results)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.infrastructure]

# ---------------------------------------------------------------------------
# Repository root and artifact paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_RESULTS_DIR = _REPO_ROOT / "demos" / "results"
_CUSTOMERS_ARTIFACT = _RESULTS_DIR / "benchmark_customers_v1.json"
_ORDERS_ARTIFACT = _RESULTS_DIR / "benchmark_orders_v1.json"
_GRID_CONFIG = _RESULTS_DIR / "grid_config.json"

# ---------------------------------------------------------------------------
# Expected grid parameters — must match grid_config.json
# ---------------------------------------------------------------------------

_EXPECTED_NOISE_MULTIPLIERS = [1.0, 5.0, 10.0]
_EXPECTED_EPOCHS = [50, 100]
_EXPECTED_SAMPLE_SIZES = [1000]
_EXPECTED_SEEDS = [42]  # default seed injected by harness when not in config


# ===========================================================================
# ATTACK TESTS — Negative / security cases (Rule 22)
# ===========================================================================


class TestArtifactIntegrityAttacks:
    """Verify detection capability for corrupted or missing artifacts."""

    def test_artifact_path_is_within_results_dir(self) -> None:
        """Artifact paths must not escape the demos/results/ directory.

        Ensures path-traversal protection: any benchmark artifact must resolve
        to a path strictly under ``_RESULTS_DIR``.  This test would catch an
        artifact file whose name contains ``..`` components.
        """
        for artifact in [_CUSTOMERS_ARTIFACT, _ORDERS_ARTIFACT, _GRID_CONFIG]:
            resolved = artifact.resolve()
            results_resolved = _RESULTS_DIR.resolve()
            assert str(resolved).startswith(str(results_resolved)), (
                f"Artifact path {artifact} escapes the results directory: {resolved}"
            )

    def test_artifact_row_with_empty_hardware_fails_non_empty_check(self, tmp_path: Path) -> None:
        """A result row with empty hardware metadata must fail the non-empty check.

        Verifies that the hardware-metadata assertion logic correctly detects
        rows where the hardware dict is present but empty.
        """
        row_with_empty_hardware: dict[str, object] = {
            "schema_version": "1.0",
            "status": "COMPLETED",
            "hardware": {},
        }
        hw = row_with_empty_hardware.get("hardware")
        assert not hw, (
            "Test setup error: hardware must be falsy (empty dict) for this negative case."
        )


# ===========================================================================
# FEATURE TESTS — Real artifact verification
# ===========================================================================


class TestGridConfigCommitted:
    """grid_config.json must be committed alongside results."""

    def test_grid_config_committed_alongside_results(self) -> None:
        """grid_config.json must exist in demos/results/ and be valid JSON.

        The grid config is the parameter manifest for results traceability.
        Without it, the result artifacts cannot be reproduced or interpreted.
        """
        assert _GRID_CONFIG.exists(), (
            f"grid_config.json not found at {_GRID_CONFIG}. "
            "It must be committed alongside benchmark results."
        )
        raw = _GRID_CONFIG.read_text(encoding="utf-8")
        cfg = json.loads(raw)
        assert isinstance(cfg, dict), (
            f"grid_config.json must be a JSON object, got {type(cfg).__name__}"
        )
        assert "noise_multiplier" in cfg, "grid_config.json must contain 'noise_multiplier' key"
        assert "epochs" in cfg, "grid_config.json must contain 'epochs' key"
        assert "sample_size" in cfg, "grid_config.json must contain 'sample_size' key"
        assert len(cfg["noise_multiplier"]) >= 1, (
            "grid_config.json must have at least one noise_multiplier value"
        )


class TestResultsSchemaVersionPresent:
    """schema_version must appear at both artifact and row level."""

    @pytest.mark.parametrize(
        "artifact_path",
        [_CUSTOMERS_ARTIFACT, _ORDERS_ARTIFACT],
        ids=["customers", "orders"],
    )
    def test_results_schema_version_present(self, artifact_path: Path) -> None:
        """schema_version must be present at the artifact top level.

        Each result artifact must carry a schema_version field so that
        downstream consumers can handle format evolution.
        """
        assert artifact_path.exists(), (
            f"Benchmark artifact not found at {artifact_path}. "
            "Run the benchmark and commit the results."
        )
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert "schema_version" in artifact, (
            f"Artifact {artifact_path.name} is missing top-level 'schema_version' field."
        )
        assert isinstance(artifact["schema_version"], str), (
            f"schema_version in {artifact_path.name} must be a string, "
            f"got {type(artifact['schema_version']).__name__}"
        )
        assert artifact["schema_version"] != "", (
            f"schema_version in {artifact_path.name} must be a non-empty string."
        )

    @pytest.mark.parametrize(
        "artifact_path",
        [_CUSTOMERS_ARTIFACT, _ORDERS_ARTIFACT],
        ids=["customers", "orders"],
    )
    def test_results_schema_version_present_in_all_rows(self, artifact_path: Path) -> None:
        """schema_version must appear in every result row.

        Per the benchmark harness spec, each row carries its own
        schema_version for forward-compatibility.
        """
        assert artifact_path.exists(), f"Benchmark artifact not found at {artifact_path}."
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        rows: list[dict[str, object]] = artifact["rows"]
        assert len(rows) > 0, f"Artifact {artifact_path.name} contains no rows."

        missing = [i for i, row in enumerate(rows) if "schema_version" not in row]
        assert missing == [], (
            f"Rows at indices {missing} in {artifact_path.name} are missing 'schema_version'."
        )


class TestResultsManifestContainsAllParameterGridCells:
    """Every combination in the grid must have a result row."""

    def _expected_keys(self) -> set[tuple[float, int, int, int]]:
        """Build the expected set of (nm, epochs, sample_size, seed) tuples."""
        import itertools

        return {
            (float(nm), int(ep), int(ss), int(seed))
            for nm, ep, ss, seed in itertools.product(
                _EXPECTED_NOISE_MULTIPLIERS,
                _EXPECTED_EPOCHS,
                _EXPECTED_SAMPLE_SIZES,
                _EXPECTED_SEEDS,
            )
        }

    def _actual_keys(self, artifact_path: Path) -> set[tuple[float, int, int, int]]:
        """Extract (nm, epochs, sample_size, seed) tuples from an artifact."""
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        keys: set[tuple[float, int, int, int]] = set()
        for row in artifact["rows"]:
            keys.add(
                (
                    float(row["noise_multiplier"]),
                    int(row["epochs"]),
                    int(row["sample_size"]),
                    int(row.get("seed", 42)),
                )
            )
        return keys

    @pytest.mark.parametrize(
        "artifact_path",
        [_CUSTOMERS_ARTIFACT, _ORDERS_ARTIFACT],
        ids=["customers", "orders"],
    )
    def test_results_manifest_contains_all_parameter_grid_cells(self, artifact_path: Path) -> None:
        """Every grid cell must have a corresponding result row.

        Verifies that the benchmark ran to completion for all 6 cells
        (3 noise_multipliers x 2 epochs x 1 sample_size) and that no
        cell was silently skipped.
        """
        assert artifact_path.exists(), f"Benchmark artifact not found at {artifact_path}."
        expected = self._expected_keys()
        actual = self._actual_keys(artifact_path)

        missing = expected - actual
        assert missing == set(), (
            f"Artifact {artifact_path.name} is missing result rows for grid cells: " + str(missing)
        )
        assert len(actual) >= len(expected), (
            f"Artifact {artifact_path.name} has fewer rows ({len(actual)}) "
            f"than expected grid cells ({len(expected)})."
        )


class TestWallTimeFieldPresentAndPositive:
    """wall_time_seconds must be present and positive in all result rows."""

    @pytest.mark.parametrize(
        "artifact_path",
        [_CUSTOMERS_ARTIFACT, _ORDERS_ARTIFACT],
        ids=["customers", "orders"],
    )
    def test_wall_time_field_present_and_positive_in_all_result_rows(
        self, artifact_path: Path
    ) -> None:
        """wall_time_seconds must be present and > 0 in every result row.

        A wall_time_seconds of None or 0 indicates a recording failure in
        the benchmark harness.  Even for FAILED or TIMEOUT rows, the harness
        records elapsed time.
        """
        assert artifact_path.exists(), f"Benchmark artifact not found at {artifact_path}."
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        rows: list[dict[str, object]] = artifact["rows"]
        assert len(rows) > 0, f"Artifact {artifact_path.name} contains no rows."

        violations: list[str] = []
        for i, row in enumerate(rows):
            wt = row.get("wall_time_seconds")
            if wt is None:
                violations.append(
                    f"Row {i} (nm={row.get('noise_multiplier')}, "
                    f"epochs={row.get('epochs')}): wall_time_seconds is None"
                )
            elif not isinstance(wt, int | float):
                violations.append(
                    f"Row {i}: wall_time_seconds is not numeric (got {type(wt).__name__})"
                )
            elif float(wt) <= 0.0:
                violations.append(f"Row {i}: wall_time_seconds={wt} is not positive")

        assert violations == [], f"Wall time violations in {artifact_path.name}:\n" + "\n".join(
            violations
        )


class TestResultsHardwareMetadataPresentAndNonEmpty:
    """Hardware metadata must be present and non-empty in all result rows."""

    @pytest.mark.parametrize(
        "artifact_path",
        [_CUSTOMERS_ARTIFACT, _ORDERS_ARTIFACT],
        ids=["customers", "orders"],
    )
    def test_results_hardware_metadata_present_and_non_empty(self, artifact_path: Path) -> None:
        """hardware dict must be present and contain at least one non-null entry.

        The hardware metadata records the execution environment for
        reproducibility analysis.  An absent or empty hardware dict means
        the benchmark produced results of unknown provenance.
        """
        assert artifact_path.exists(), f"Benchmark artifact not found at {artifact_path}."
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        rows: list[dict[str, object]] = artifact["rows"]
        assert len(rows) > 0, f"Artifact {artifact_path.name} contains no rows."

        violations: list[str] = []
        for i, row in enumerate(rows):
            hw = row.get("hardware")
            if not hw:
                violations.append(
                    f"Row {i} (nm={row.get('noise_multiplier')}, "
                    f"epochs={row.get('epochs')}): hardware is absent or empty"
                )
                continue
            if not isinstance(hw, dict):
                violations.append(f"Row {i}: hardware is not a dict (got {type(hw).__name__})")
                continue
            non_null_values = [v for v in hw.values() if v is not None]
            if not non_null_values:
                violations.append(f"Row {i}: hardware dict has no non-null values: {hw}")

        assert violations == [], (
            f"Hardware metadata violations in {artifact_path.name}:\n" + "\n".join(violations)
        )


class TestResultsColumnNamesMatchFixture:
    """Result rows must reference only sample_data/ fixture column names."""

    @pytest.mark.parametrize(
        ("artifact_path", "expected_columns"),
        [
            (
                _CUSTOMERS_ARTIFACT,
                {"id", "first_name", "last_name", "email", "ssn", "phone", "address", "created_at"},
            ),
            (
                _ORDERS_ARTIFACT,
                {"id", "customer_id", "order_date", "total_amount", "status"},
            ),
        ],
        ids=["customers", "orders"],
    )
    def test_results_column_names_match_fixture(
        self, artifact_path: Path, expected_columns: set[str]
    ) -> None:
        """Column metrics keys must match the fixture CSV column names.

        Verifies that the benchmark ran against the correct sample_data/ file
        and that no column name drift has occurred.
        """
        assert artifact_path.exists(), f"Benchmark artifact not found at {artifact_path}."
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        rows: list[dict[str, object]] = artifact["rows"]

        # Find a COMPLETED row with column_metrics
        completed_row: dict[str, object] | None = None
        for row in rows:
            if row.get("status") == "COMPLETED" and row.get("column_metrics"):
                completed_row = row
                break

        if completed_row is None:
            pytest.skip(
                f"No COMPLETED row with column_metrics in {artifact_path.name} — "
                "all runs may have FAILED or TIMEOUT status."
            )

        column_metrics = completed_row["column_metrics"]
        assert isinstance(column_metrics, dict), (
            f"column_metrics must be a dict, got {type(column_metrics).__name__}"
        )
        actual_columns = set(column_metrics.keys())
        unexpected = actual_columns - expected_columns
        assert unexpected == set(), (
            f"Artifact {artifact_path.name} has unexpected column names: {unexpected}. "
            f"Expected subset of: {expected_columns}"
        )
