"""Attack and negative tests for benchmark infrastructure (T52.1).

This module contains negative/attack tests written BEFORE the implementation,
following Rule 22 (Attack-First TDD). These tests verify that the benchmark
harness correctly rejects malformed inputs, records failure rows, enforces
security constraints (YAML safety), and maintains artifact integrity.

Task: P52-T52.1 — Benchmark Infrastructure
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import tempfile
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Repository root helper
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_SRC_ROOT = _REPO_ROOT / "src" / "synth_engine"
_SCRIPTS_ROOT = _REPO_ROOT / "scripts"


# ===========================================================================
# ATTACK TESTS — Negative / security cases (Rule 22)
# ===========================================================================


class TestDemoDepsIsolation:
    """demos/ group must NOT be imported by production modules."""

    def test_demo_dependencies_not_imported_in_production_modules(self) -> None:
        """Import every module in src/synth_engine/ without demos group; assert no ImportError.

        The demos dependency group (matplotlib, seaborn, jupyter, scikit-learn)
        must be fully optional.  Production modules must never import them at
        module scope or inside any hot path, so that `poetry install` (without
        --with demos) produces a working system.
        """
        demo_packages = {"matplotlib", "seaborn", "jupyter", "sklearn", "nbstripout"}

        # Walk src/synth_engine/ and collect all Python module names
        synth_pkg_dir = _SRC_ROOT
        assert synth_pkg_dir.is_dir(), f"src/synth_engine not found at {synth_pkg_dir}"

        violations: list[str] = []
        for py_file in synth_pkg_dir.rglob("*.py"):
            # Skip __pycache__ artefacts
            if "__pycache__" in py_file.parts:
                continue
            source = py_file.read_text(encoding="utf-8")
            for demo_pkg in demo_packages:
                # Check for import statements referencing demo packages
                if f"import {demo_pkg}" in source or f"from {demo_pkg}" in source:
                    violations.append(f"{py_file}: imports '{demo_pkg}'")

        assert violations == [], (
            "Production modules must not import demos-group packages. Violations:\n"
            + "\n".join(violations)
        )


class TestBenchmarkHarnessRejectionCases:
    """Benchmark harness must reject invalid inputs with clear errors."""

    def test_benchmark_harness_rejects_run_without_dataset_fixture(self) -> None:
        """Run harness with no dataset; verify a clear ValueError is raised.

        The harness must validate that a dataset exists before attempting to
        train CTGAN.  Running with a missing dataset must raise ValueError
        immediately, not produce a silent partial result.
        """
        scripts_dir = _SCRIPTS_ROOT
        harness_path = scripts_dir / "benchmark_epsilon_curves.py"

        if not harness_path.exists():
            pytest.fail(
                f"benchmark_epsilon_curves.py not found at {harness_path}. "
                "This test MUST fail (RED) before the implementation is written."
            )

        # Load the module dynamically so we can call internal helpers
        spec = importlib.util.spec_from_file_location("benchmark_epsilon_curves", harness_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        with pytest.raises(
            (ValueError, TypeError, FileNotFoundError),
            match=r"source_df|connection_string|table_name",
        ):
            module.run_grid(  # type: ignore[attr-defined]
                connection_string=None,
                table_name=None,
                grid_config={},
                output_dir=tempfile.mkdtemp(),
            )

    def test_benchmark_harness_records_failure_row_on_run_error(self) -> None:
        """Inject a failure; verify a failure row is recorded, never silently omitted.

        Grid cells that error must produce a result row with status='FAILED'
        and the error_type/error_message fields populated.  Silent omission
        of failed cells is forbidden — operators must be able to distinguish
        'not run' from 'run and failed'.
        """
        scripts_dir = _SCRIPTS_ROOT
        harness_path = scripts_dir / "benchmark_epsilon_curves.py"

        if not harness_path.exists():
            pytest.fail(
                f"benchmark_epsilon_curves.py not found at {harness_path}. "
                "This test MUST fail (RED) before the implementation is written."
            )

        spec = importlib.util.spec_from_file_location("benchmark_epsilon_curves", harness_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        import pandas as pd

        # Minimal 1-cell grid config for fast test execution
        grid_config = {
            "noise_multiplier": [1.0],
            "epochs": [1],
            "sample_size": [10],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            # Pass a DataFrame that will cause a training failure (empty data)
            empty_df = pd.DataFrame()

            rows = module.run_grid(  # type: ignore[attr-defined]
                connection_string=None,
                table_name=None,
                grid_config=grid_config,
                output_dir=tmp_dir,
                source_df=empty_df,
            )

            assert len(rows) >= 1, "run_grid must return at least one row even on failure"
            failed_rows = [r for r in rows if r.get("status") == "FAILED"]
            assert len(failed_rows) >= 1, (
                "At least one FAILED row must be recorded when training on empty data. "
                f"Got rows: {rows}"
            )
            first_failure = failed_rows[0]
            assert "error_type" in first_failure, "Failure row must have 'error_type' field"
            assert "error_message" in first_failure, "Failure row must have 'error_message' field"
            assert first_failure["error_type"] != "", "error_type must not be empty"


class TestBenchmarkYAMLSecurity:
    """YAML config loading must use safe_load only — never load() with arbitrary tags."""

    def test_benchmark_harness_rejects_malicious_yaml_config(self) -> None:
        """YAML with !!python/object/apply:os.system payload must be rejected.

        This is a Bandit B506 enforcement test.  The harness must use
        yaml.safe_load() exclusively.  A malicious YAML payload that attempts
        to execute os.system via !!python/object/apply must be rejected with
        a yaml.constructor.ConstructorError (raised by safe_load), not executed.
        """
        import yaml

        scripts_dir = _SCRIPTS_ROOT
        harness_path = scripts_dir / "benchmark_epsilon_curves.py"

        if not harness_path.exists():
            pytest.fail(
                f"benchmark_epsilon_curves.py not found at {harness_path}. "
                "This test MUST fail (RED) before the implementation is written."
            )

        spec = importlib.util.spec_from_file_location("benchmark_epsilon_curves", harness_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        # YAML payload that would execute os.system("echo pwned") if loaded unsafely
        malicious_yaml = textwrap.dedent("""\
            noise_multiplier:
              !!python/object/apply:os.system
              - echo pwned
        """)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(malicious_yaml)
            malicious_path = f.name

        try:
            with pytest.raises(
                (yaml.constructor.ConstructorError, ValueError, TypeError),
                match=r"could not determine a constructor|unsafe|python/object",
            ):
                module.load_grid_config(malicious_path)  # type: ignore[attr-defined]
        finally:
            os.unlink(malicious_path)

    def test_bandit_scan_passes_on_benchmark_harness(self) -> None:
        """bandit must find no B506 (yaml.load without SafeLoader) in benchmark_epsilon_curves.py.

        This is a static-analysis guard, not a runtime test.  We parse the
        source and confirm yaml.load() is not called without SafeLoader.
        """
        harness_path = _SCRIPTS_ROOT / "benchmark_epsilon_curves.py"

        if not harness_path.exists():
            pytest.fail(
                f"benchmark_epsilon_curves.py not found at {harness_path}. "
                "This test MUST fail (RED) before the implementation is written."
            )

        source = harness_path.read_text(encoding="utf-8")

        # If yaml.load( appears (not yaml.safe_load), that is a B506 violation
        import re

        unsafe_load_calls = re.findall(r"\byaml\.load\s*\(", source)
        # yaml.safe_load is allowed; yaml.load is not
        assert not any("safe_load" not in call for call in unsafe_load_calls), (
            f"Found unsafe yaml.load() calls: {unsafe_load_calls}"
        )


class TestArtifactIntegrity:
    """Benchmark result artifacts must include required integrity fields."""

    def test_results_artifact_contains_schema_version_field(self) -> None:
        """Parse results artifact; assert schema_version is present and non-empty.

        Every output artifact must carry a schema_version field so consumers
        can detect format changes without inspecting the raw structure.
        """
        scripts_dir = _SCRIPTS_ROOT
        harness_path = scripts_dir / "benchmark_epsilon_curves.py"

        if not harness_path.exists():
            pytest.fail(
                f"benchmark_epsilon_curves.py not found at {harness_path}. "
                "This test MUST fail (RED) before the implementation is written."
            )

        spec = importlib.util.spec_from_file_location("benchmark_epsilon_curves", harness_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        import pandas as pd

        # Build a minimal 1-row result from a trivial 3-row DataFrame
        source_df = pd.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
        grid_config = {
            "noise_multiplier": [1.0],
            "epochs": [1],
            "sample_size": [3],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            rows = module.run_grid(  # type: ignore[attr-defined]
                connection_string=None,
                table_name=None,
                grid_config=grid_config,
                output_dir=tmp_dir,
                source_df=source_df,
            )

            # Write results JSON and verify schema_version
            output_path = module.write_results(  # type: ignore[attr-defined]
                rows=rows,
                output_dir=tmp_dir,
                grid_config=grid_config,
            )

            with open(output_path, encoding="utf-8") as f:
                artifact = json.load(f)

            assert "schema_version" in artifact, (
                f"Artifact must contain 'schema_version' field. "
                f"Keys present: {list(artifact.keys())}"
            )
            assert artifact["schema_version"] != "", "schema_version must not be empty"
            assert isinstance(artifact["schema_version"], str), (
                f"schema_version must be a string, got {type(artifact['schema_version'])}"
            )

    def test_committed_results_contain_no_real_column_names(self) -> None:
        """Column IDs in committed results must match fixture schema only.

        This is a PII guard: committed demo results must be produced from
        test fixtures, never from real database tables with real column names.
        We verify this by running the harness with a known-safe fixture and
        asserting that the output column names match only the fixture schema.
        """
        scripts_dir = _SCRIPTS_ROOT
        harness_path = scripts_dir / "benchmark_epsilon_curves.py"

        if not harness_path.exists():
            pytest.fail(
                f"benchmark_epsilon_curves.py not found at {harness_path}. "
                "This test MUST fail (RED) before the implementation is written."
            )

        spec = importlib.util.spec_from_file_location("benchmark_epsilon_curves", harness_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        import pandas as pd

        # Known-safe fixture with artificial column names
        fixture_columns = ["metric_a", "metric_b", "category_x"]
        source_df = pd.DataFrame(
            {
                "metric_a": [10.0, 20.0, 30.0],
                "metric_b": [1.5, 2.5, 3.5],
                "category_x": ["cat1", "cat2", "cat1"],
            }
        )

        grid_config = {
            "noise_multiplier": [1.0],
            "epochs": [1],
            "sample_size": [3],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            rows = module.run_grid(  # type: ignore[attr-defined]
                connection_string=None,
                table_name=None,
                grid_config=grid_config,
                output_dir=tmp_dir,
                source_df=source_df,
            )

            for row in rows:
                col_metrics = row.get("column_metrics")
                if col_metrics is not None:
                    reported_columns = list(col_metrics.keys())
                    unexpected = [c for c in reported_columns if c not in fixture_columns]
                    assert unexpected == [], (
                        f"Result row contains unexpected column names not in fixture: "
                        f"{unexpected}. Fixture columns: {fixture_columns}"
                    )

    def test_parameter_grid_is_committed_alongside_results(self) -> None:
        """Grid config must be an artifact present in the output directory.

        When results are written, the grid config used must also be saved
        so that any later reader can reproduce the exact run conditions.
        """
        scripts_dir = _SCRIPTS_ROOT
        harness_path = scripts_dir / "benchmark_epsilon_curves.py"

        if not harness_path.exists():
            pytest.fail(
                f"benchmark_epsilon_curves.py not found at {harness_path}. "
                "This test MUST fail (RED) before the implementation is written."
            )

        spec = importlib.util.spec_from_file_location("benchmark_epsilon_curves", harness_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        import pandas as pd

        source_df = pd.DataFrame({"x": [1, 2, 3]})
        grid_config = {
            "noise_multiplier": [1.0],
            "epochs": [1],
            "sample_size": [3],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            rows = module.run_grid(  # type: ignore[attr-defined]
                connection_string=None,
                table_name=None,
                grid_config=grid_config,
                output_dir=tmp_dir,
                source_df=source_df,
            )

            module.write_results(  # type: ignore[attr-defined]
                rows=rows,
                output_dir=tmp_dir,
                grid_config=grid_config,
            )

            output_path = Path(tmp_dir)
            grid_files = list(output_path.glob("grid_config*.json"))
            assert len(grid_files) >= 1, (
                f"No grid_config*.json found in output directory {tmp_dir}. "
                f"Files present: {list(output_path.iterdir())}"
            )


class TestEpsilonDeltaConsistency:
    """Benchmark delta must match the production constant _DP_EPSILON_DELTA."""

    def test_benchmark_epsilon_delta_matches_production_constant(self) -> None:
        """Assert benchmark delta equals production _DP_EPSILON_DELTA (1e-5).

        The benchmark harness must use the same delta as production to ensure
        that epsilon values reported in benchmark results are directly comparable
        to production epsilon accounting.  Using a different delta would produce
        meaningless comparisons.
        """
        scripts_dir = _SCRIPTS_ROOT
        harness_path = scripts_dir / "benchmark_epsilon_curves.py"

        if not harness_path.exists():
            pytest.fail(
                f"benchmark_epsilon_curves.py not found at {harness_path}. "
                "This test MUST fail (RED) before the implementation is written."
            )

        spec = importlib.util.spec_from_file_location("benchmark_epsilon_curves", harness_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        from synth_engine.modules.synthesizer.dp_accounting import _DP_EPSILON_DELTA

        # The harness must expose its delta constant so we can assert equality
        assert hasattr(module, "_BENCHMARK_DP_DELTA"), (
            "benchmark_epsilon_curves.py must expose '_BENCHMARK_DP_DELTA' constant "
            "so it can be compared to the production _DP_EPSILON_DELTA"
        )
        assert module._BENCHMARK_DP_DELTA == _DP_EPSILON_DELTA, (  # type: ignore[attr-defined]
            f"Benchmark delta {module._BENCHMARK_DP_DELTA!r} != "  # type: ignore[attr-defined]
            f"production _DP_EPSILON_DELTA {_DP_EPSILON_DELTA!r}. "
            "The two must match so epsilon reporting is comparable."
        )


class TestReproducibility:
    """Fixed-seed runs must produce identical metrics."""

    @pytest.mark.cpu_only
    def test_benchmark_run_produces_identical_metrics_given_fixed_seed(self) -> None:
        """Run harness twice with same seed; assert metrics match.

        Reproducibility is a core requirement: given the same seed, the same
        noise_multiplier, and the same training data, two runs must produce
        identical epsilon, KS statistics, and MAE values.

        Marked cpu_only because GPU non-determinism may prevent bit-exact
        reproducibility even with fixed seeds.
        """
        import pandas as pd

        scripts_dir = _SCRIPTS_ROOT
        harness_path = scripts_dir / "benchmark_epsilon_curves.py"

        if not harness_path.exists():
            pytest.fail(
                f"benchmark_epsilon_curves.py not found at {harness_path}. "
                "This test MUST fail (RED) before the implementation is written."
            )

        spec = importlib.util.spec_from_file_location("benchmark_epsilon_curves", harness_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        source_df = pd.DataFrame(
            {
                "age": [25, 30, 35, 40, 45, 50, 55, 60, 65, 70],
                "salary": [
                    50000,
                    60000,
                    70000,
                    80000,
                    90000,
                    100000,
                    110000,
                    120000,
                    130000,
                    140000,
                ],
            }
        )
        grid_config = {
            "noise_multiplier": [1.0],
            "epochs": [1],
            "sample_size": [10],
            "seed": [42],
        }

        with tempfile.TemporaryDirectory() as tmp_dir1:
            rows1 = module.run_grid(  # type: ignore[attr-defined]
                connection_string=None,
                table_name=None,
                grid_config=grid_config,
                output_dir=tmp_dir1,
                source_df=source_df,
            )

        with tempfile.TemporaryDirectory() as tmp_dir2:
            rows2 = module.run_grid(  # type: ignore[attr-defined]
                connection_string=None,
                table_name=None,
                grid_config=grid_config,
                output_dir=tmp_dir2,
                source_df=source_df,
            )

        assert len(rows1) == len(rows2), (
            f"Run 1 produced {len(rows1)} rows, run 2 produced {len(rows2)} rows"
        )

        # For completed (non-failed) rows, epsilon must match exactly
        completed1 = [r for r in rows1 if r.get("status") != "FAILED"]
        completed2 = [r for r in rows2 if r.get("status") != "FAILED"]

        for r1, r2 in zip(completed1, completed2, strict=True):
            assert r1.get("actual_epsilon") == r2.get("actual_epsilon"), (
                f"Epsilon differs across runs with same seed: "
                f"{r1.get('actual_epsilon')} != {r2.get('actual_epsilon')}"
            )
