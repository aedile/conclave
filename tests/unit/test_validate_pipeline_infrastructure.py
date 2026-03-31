"""Infrastructure tests for scripts/validate_full_pipeline.py.

These tests validate the script's structure and security properties WITHOUT
requiring PostgreSQL or the synthesizer dependency group.  They inspect the
script's source code to enforce structural invariants, security guarantees,
and CLI contract requirements.

CONSTITUTION Priority 0: Security — DSN must never appear in output or reports.
Task: T54.2 — Full Pipeline Validation Script
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.infrastructure]

# ---------------------------------------------------------------------------
# Resolve the script path once at module scope so all tests share it.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "validate_full_pipeline.py"


def _load_source() -> str:
    """Return the full source text of the validation script.

    Returns:
        The script source as a string.

    Raises:
        pytest.skip: If the file does not exist (deferred until GREEN phase).
    """
    if not _SCRIPT_PATH.exists():
        pytest.fail(
            f"Script not found at {_SCRIPT_PATH}. "
            "This test is RED — the script has not been created yet."
        )
    return _SCRIPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ATTACK TESTS — written first, per Rule 22 (attack-first TDD)
# ---------------------------------------------------------------------------


class TestSecurityAttacks:
    """Negative / attack tests that must be GREEN before any feature work."""

    def test_validate_script_does_not_hardcode_credentials(self) -> None:
        """The script must contain no hardcoded password or credential literals.

        Searches for patterns commonly associated with credential embedding:
        ``password=``, ``passwd=``, ``secret=``,
        ``postgres://user:password@``.  # pragma: allowlist secret
        A match of any of these patterns is a security violation.
        """
        source = _load_source()

        # These patterns indicate hardcoded credentials.
        forbidden_patterns = [
            r"password\s*=\s*['\"][^'\"]{1,}['\"]",
            r"passwd\s*=\s*['\"][^'\"]{1,}['\"]",
            r"secret\s*=\s*['\"][^'\"]{1,}['\"]",
            # A DSN with a literal password embedded: user:secret@host  # pragma: allowlist secret
            r"postgresql://\w+:[^@'\"\s]{2,}@",
            r"postgres://\w+:[^@'\"\s]{2,}@",
        ]

        for pattern in forbidden_patterns:
            matches = re.findall(pattern, source, re.IGNORECASE)
            # Filter out comments and docstrings (heuristic: lines starting with #)
            non_comment_matches = [
                m
                for m in matches
                if not any(
                    line.lstrip().startswith("#") for line in source.splitlines() if m in line
                )
            ]
            assert not non_comment_matches, (
                f"Forbidden credential pattern {pattern!r} found in script: {non_comment_matches}"
            )

    def test_validate_script_does_not_print_dsn(self) -> None:
        """The DSN variable must never be passed to print(), logging, or f-strings.

        The db_url / DATABASE_URL value must never be formatted into any string
        that reaches stdout, stderr, or the report file.  This test checks that
        the script does not pass the raw DSN variable name to print() or logging
        calls where it could appear in output.
        """
        source = _load_source()

        # The script should not log or print the raw db_url argument variable.
        # Pattern: print(...db_url...) or log.xxx(...db_url...) without masking.
        # We look for print/log calls that contain the bare variable reference.
        dsn_in_print = re.findall(
            r"(?:print|_logger\.\w+|logging\.\w+)\s*\([^)]*\bdb_url\b[^)]*\)",
            source,
            re.DOTALL,
        )
        assert not dsn_in_print, (
            f"Script passes raw db_url to print/log call(s): {dsn_in_print}. "
            "DSN must never be printed — it may contain credentials."
        )

        # The report JSON must not include a 'db_url' or 'database_url' key.
        # Check for dict literals or f-strings that would embed it.
        dsn_in_report_key = re.findall(
            r'["\'](?:db_url|database_url)["\']',
            source,
        )
        assert not dsn_in_report_key, (
            f"Script includes DSN key in report: {dsn_in_report_key}. "
            "The report must not contain the database URL."
        )

    def test_validate_script_validates_input_bounds(self) -> None:
        """The script must validate all numeric argument bounds at parse time.

        Validates that:
        - subset_size has a maximum of 50000
        - epsilon has a maximum of 100.0
        - epochs has a maximum of 500
        All bounds must be checked before any DB connection is attempted.
        """
        source = _load_source()

        # Check that bounds constants or inline checks appear for each arg.
        # We look for the numeric values that define the maximums.
        assert "50000" in source, (
            "subset_size maximum (50000) not found in script. "
            "Bounds must be validated at parse time."
        )
        assert "100" in source, (
            "epsilon maximum (100.0 or 100) not found in script. "
            "Bounds must be validated at parse time."
        )
        assert "500" in source, (
            "epochs maximum (500) not found in script. Bounds must be validated at parse time."
        )

    def test_validate_script_validates_output_dir(self) -> None:
        """The script must check that the output directory is writable before starting.

        This prevents a long pipeline run that fails at the last step because
        it cannot write the report file.
        """
        source = _load_source()

        # A writable check implies: either os.access with W_OK, or a try/open,
        # or Path(...).mkdir, or an explicit writable check function.
        has_writable_check = any(
            [
                "os.access" in source and "W_OK" in source,
                "writable" in source.lower(),
                # The script creates the output dir or checks it
                ".mkdir" in source,
            ]
        )
        assert has_writable_check == True, (
            "Script must validate that the output directory is writable before "
            "starting the pipeline. No writable-check pattern found (os.access+W_OK, "
            ".mkdir, or explicit writable guard)."
        )
        assert has_writable_check

    def test_validate_script_has_nan_detection(self) -> None:
        """The script must detect NaN/inf in generated DataFrames.

        Training divergence can produce NaN or inf values in synthetic output.
        These must be detected and cause exit code 2 with a training_divergence
        indicator, not a silent data quality failure downstream.
        """
        source = _load_source()

        has_nan_check = any(
            [
                "isna()" in source or ".isna(" in source,
                "isnull()" in source or ".isnull(" in source,
                "np.isnan" in source,
                "pd.isna" in source,
                "hasnans" in source,
                "training_divergence" in source,
            ]
        )
        assert has_nan_check, (
            "Script must detect NaN/inf in generated DataFrames to catch "
            "training divergence. No NaN-detection pattern found."
        )

        # Also verify training_divergence is mentioned (as the flag/key in report)
        assert "training_divergence" in source, (
            "Script must set a 'training_divergence' flag in the report when "
            "NaN/inf is detected in synthetic output."
        )

    def test_validate_script_masks_dsn_in_report(self) -> None:
        """The report JSON schema must not include a field for the database URL.

        The report is written to disk and may be shared; it must not contain
        the DSN which may include credentials.  Uses AST inspection to find
        the ``report = {`` dict construction and assert ``db_url`` is not
        among its string keys.
        """
        source = _load_source()
        tree = ast.parse(source)

        # Walk all assignments of the form: report = { ... } or report: T = { ... }
        # Collect every string key from those dict literals.
        report_dict_keys: list[str] = []
        for node in ast.walk(tree):
            # Handle plain assignment: report = {...}
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "report":
                        if isinstance(node.value, ast.Dict):
                            for key in node.value.keys:
                                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                    report_dict_keys.append(key.value)
            # Handle annotated assignment: report: dict[...] = {...}
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == "report":
                    if node.value is not None and isinstance(node.value, ast.Dict):
                        for key in node.value.keys:
                            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                report_dict_keys.append(key.value)

        assert len(report_dict_keys) > 0, (
            "No 'report = {...}' dict assignment found in script AST. "
            "The report construction must be a literal dict assigned to 'report'."
        )
        assert "db_url" not in report_dict_keys, (
            f"The report dict must not contain a 'db_url' key. "
            f"Found keys: {report_dict_keys}. DSN must never appear in the written report."
        )
        assert "database_url" not in [k.lower() for k in report_dict_keys], (
            f"The report dict must not contain a 'database_url' key. "
            f"Found keys: {report_dict_keys}. DSN must never appear in the written report."
        )


# ---------------------------------------------------------------------------
# FEATURE TESTS
# ---------------------------------------------------------------------------


class TestScriptStructure:
    """Feature tests for the validation script's structure and API contract."""

    def test_validate_script_exists_and_has_main(self) -> None:
        """The script must exist at scripts/validate_full_pipeline.py and have a main guard.

        The ``if __name__ == "__main__"`` guard is required so the script can
        be imported by these infrastructure tests without executing the pipeline.
        """
        assert _SCRIPT_PATH.exists(), (
            f"Script not found at {_SCRIPT_PATH}. Create it to make this test pass."
        )

        source = _SCRIPT_PATH.read_text(encoding="utf-8")
        assert '__name__ == "__main__"' in source or "__name__ == '__main__'" in source, (
            "Script must have an 'if __name__ == \"__main__\":' guard so that "
            "infrastructure tests can import it without executing the pipeline."
        )

    def test_validate_script_imports_production_modules(self) -> None:
        """The script must import from synth_engine production modules, not test doubles.

        This confirms the script exercises real code paths rather than mocked
        implementations.
        """
        source = _load_source()

        required_imports = [
            "synth_engine.modules.ingestion",
            "synth_engine.modules.masking",
            "synth_engine.modules.profiler",
            "synth_engine.modules.synthesizer",
            "synth_engine.modules.privacy",
        ]
        for module_path in required_imports:
            assert module_path in source, (
                f"Script must import from '{module_path}' (production module). "
                "Test doubles or mocks are forbidden in this validation script."
            )

    def test_validate_script_has_argparse_with_required_args(self) -> None:
        """The script must define all required CLI arguments via argparse.

        Verifies that all seven documented CLI arguments are present in the
        script source.
        """
        source = _load_source()

        required_args = [
            "--db-url",
            "--subset-size",
            "--epsilon",
            "--delta",
            "--epochs",
            "--output-dir",
            "--force-cpu",
        ]
        for arg in required_args:
            assert arg in source, (
                f"Required CLI argument '{arg}' not found in script. "
                "All documented arguments must be present in the argparse definition."
            )

    def test_validate_script_report_schema_has_required_fields(self) -> None:
        """The report JSON must contain all required top-level fields.

        Checks that the script source defines or references every required
        key in the report structure.
        """
        source = _load_source()

        required_top_level_keys = [
            "timestamp",
            "branch",
            "python_version",
            "config",
            "stages",
            "overall_pass",
            "wall_clock_seconds",
        ]
        for key in required_top_level_keys:
            assert f'"{key}"' in source or f"'{key}'" in source, (
                f"Required report key '{key}' not found in script source. "
                "The JSON report must include all documented top-level fields."
            )

    def test_validate_script_has_epsilon_warning(self) -> None:
        """The script must emit a WARNING when --epsilon exceeds 3.0.

        Per the task specification, high epsilon values (weak privacy) must
        be flagged so operators do not accidentally use production data with
        weak DP guarantees.
        """
        source = _load_source()

        # Check for the threshold value and a warning emission.
        has_threshold = "3.0" in source or "3.0)" in source
        has_warning = "WARNING" in source or "warning" in source.lower()

        assert has_threshold, (
            "Script must check epsilon against the 3.0 threshold. "
            "Value '3.0' not found in script source."
        )
        assert has_warning, (
            "Script must emit a WARNING when epsilon > 3.0. "
            "No warning emission pattern found in script source."
        )

        # More specifically: the warning must be near the epsilon threshold check.
        epsilon_warning_pattern = re.search(
            r"(?:warn|WARNING|logging\.warning|_logger\.warning)[^\n]*3",
            source,
            re.IGNORECASE,
        )
        # Also accept: the 3.0 check is nearby a warning call
        epsilon_section = re.search(
            r"epsilon.*?3\.0|3\.0.*?epsilon",
            source,
            re.IGNORECASE | re.DOTALL,
        )
        assert epsilon_warning_pattern is not None or epsilon_section is not None, (
            "Script must emit a warning specifically when epsilon > 3.0. "
            "No pattern combining epsilon threshold (3.0) with a warning found."
        )

    def test_validate_script_has_timing_per_stage(self) -> None:
        """Each pipeline stage must be individually timed.

        The report includes ``duration_seconds`` for each stage. The script
        must capture start/end times around each stage's execution.
        """
        source = _load_source()

        # Timing implies time.monotonic(), time.perf_counter(), or time.time()
        has_time_call = any(
            [
                "time.monotonic()" in source,
                "time.perf_counter()" in source,
                "time.time()" in source,
            ]
        )
        assert has_time_call, (
            "Script must use a time function (time.monotonic, time.perf_counter, "
            "or time.time) to measure per-stage duration."
        )

        # Each stage should have a duration_seconds key in the report.
        duration_count = source.count("duration_seconds")
        # There are 8 stages (schema_reflection, subsetting, masking, profiling,
        # training, generation, fk_post_processing, and wall_clock).
        # We require at least 7 occurrences (one per stage, excluding wall_clock).
        assert duration_count >= 7, (
            f"Script must record 'duration_seconds' for each stage. "
            f"Found {duration_count} occurrences but expected at least 7 "
            "(one per pipeline stage)."
        )

    def test_validate_script_exit_codes_documented(self) -> None:
        """The script must define named exit code constants for all three exit codes.

        Exit codes must be defined as named constants (e.g. EXIT_VALIDATION_FAILURE = 1)
        to prevent accidental confusion between exit code 1 (validation failure)
        and exit code 2 (infrastructure error).  Named constants are then passed
        to sys.exit() rather than bare integer literals.
        """
        source = _load_source()

        # Named constants for exit codes must be defined.
        # Pattern: EXIT_VALIDATION_FAILURE: int = 1 or EXIT_VALIDATION_FAILURE = 1
        assert re.search(r"EXIT_VALIDATION_FAILURE\s*[=:][^=].*1", source), (
            "Script must define EXIT_VALIDATION_FAILURE constant equal to 1 "
            "for validation failures (FK orphans, epsilon exceeded, masking violation)."
        )
        assert re.search(r"EXIT_INFRASTRUCTURE_ERROR\s*[=:][^=].*2", source), (
            "Script must define EXIT_INFRASTRUCTURE_ERROR constant equal to 2 "
            "for infrastructure errors (DB connection failed, training diverged)."
        )

        # Those constants (or their literal values) must be passed to sys.exit().
        # Accept either: sys.exit(EXIT_VALIDATION_FAILURE) or sys.exit(1)
        has_exit_1 = "sys.exit(EXIT_VALIDATION_FAILURE)" in source or bool(
            re.search(r"sys\.exit\s*\(\s*1\s*\)", source)
        )
        has_exit_2 = "sys.exit(EXIT_INFRASTRUCTURE_ERROR)" in source or bool(
            re.search(r"sys\.exit\s*\(\s*2\s*\)", source)
        )
        assert has_exit_1, (
            "Script must call sys.exit(EXIT_VALIDATION_FAILURE) or sys.exit(1) "
            "for validation failures."
        )
        assert has_exit_2, (
            "Script must call sys.exit(EXIT_INFRASTRUCTURE_ERROR) or sys.exit(2) "
            "for infrastructure errors."
        )

    def test_validate_script_uses_force_cpu_default(self) -> None:
        """The --force-cpu flag must default to True.

        Force-CPU mode prevents accidental GPU usage in non-GPU environments
        and ensures consistent, reproducible results across development machines.
        """
        source = _load_source()

        # The argparse definition for --force-cpu should have default=True
        # OR action="store_true" with a documented default of True.
        has_force_cpu_true = any(
            [
                "force_cpu" in source and "True" in source,
                "--force-cpu" in source and "store_true" in source,
                "force-cpu" in source and "default=True" in source,
            ]
        )
        assert has_force_cpu_true, (
            "The --force-cpu flag must default to True. "
            "No pattern combining 'force_cpu' and 'True' found in script."
        )

        # Verify CUDA_VISIBLE_DEVICES is set when force_cpu is active.
        assert "CUDA_VISIBLE_DEVICES" in source, (
            "Script must set CUDA_VISIBLE_DEVICES to disable GPU when "
            "--force-cpu is active. 'CUDA_VISIBLE_DEVICES' not found in script."
        )


class TestScriptAst:
    """AST-level checks for script correctness that go beyond text search."""

    def test_validate_script_is_valid_python(self) -> None:
        """The script must parse as valid Python 3 syntax.

        This catches syntax errors before any runtime execution attempt.
        """
        source = _load_source()
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            pytest.fail(f"Script at {_SCRIPT_PATH} has a Python syntax error: {exc}")
        # Verify we got a real AST Module node, not an empty parse.
        assert isinstance(tree, ast.Module), "Parsing the script should produce an ast.Module node."
        # The module must have at least one statement.
        assert len(tree.body) > 0, (
            "The script AST body must not be empty — it contains no statements."
        )

    def test_validate_script_defines_stage_names(self) -> None:
        """The script must reference all eight documented pipeline stage names.

        Each stage name corresponds to a key in the 'stages' section of the
        JSON report.
        """
        source = _load_source()

        required_stages = [
            "schema_reflection",
            "subsetting",
            "masking",
            "profiling",
            "training",
            "generation",
            "fk_post_processing",
            "validation",
        ]
        for stage in required_stages:
            assert stage in source, (
                f"Stage name '{stage}' not found in script source. "
                "All documented stages must appear in the report schema."
            )

    def test_validate_script_defines_validation_sub_keys(self) -> None:
        """The validation stage must define all required sub-check keys.

        Per the report schema, the validation section must include:
        fk_integrity, epsilon_budget, masking_verification,
        statistical_comparison, row_counts.
        """
        source = _load_source()

        required_validation_keys = [
            "fk_integrity",
            "epsilon_budget",
            "masking_verification",
            "statistical_comparison",
            "row_counts",
        ]
        for key in required_validation_keys:
            assert key in source, (
                f"Validation sub-key '{key}' not found in script source. "
                "All documented validation checks must appear in the report."
            )

    def test_validate_script_uses_budget_exhaustion_error(self) -> None:
        """The script must import and catch BudgetExhaustionError.

        Per the task spec, BudgetExhaustionError must be caught and produce
        exit code 1 with a descriptive message.
        """
        source = _load_source()

        assert "BudgetExhaustionError" in source, (
            "Script must import BudgetExhaustionError from "
            "synth_engine.modules.privacy.dp_engine (or synth_engine.shared.exceptions) "
            "and catch it with exit code 1."
        )

        # Must be used in an except clause, not just imported.
        budget_except = re.search(
            r"except\s+BudgetExhaustionError",
            source,
        )
        assert budget_except is not None, (
            "Script must have an 'except BudgetExhaustionError:' clause. "
            "Importing it but not catching it is insufficient."
        )

    def test_validate_script_uses_apply_fk_post_processing(self) -> None:
        """The script must import and call apply_fk_post_processing.

        FK post-processing is Step 7 of the pipeline and is required to
        produce a report section on orphan FK removal.
        """
        source = _load_source()

        assert "apply_fk_post_processing" in source, (
            "Script must import and call apply_fk_post_processing from "
            "synth_engine.modules.synthesizer.training.engine. "
            "This is Step 7 of the documented pipeline."
        )

    def test_validate_script_uses_ks_2samp(self) -> None:
        """The script must use scipy.stats.ks_2samp for KS statistics.

        Per the task specification, statistical comparison uses ks_2samp
        from scipy.stats (already a project dependency).
        """
        source = _load_source()

        assert "ks_2samp" in source, (
            "Script must use scipy.stats.ks_2samp for KS statistics. "
            "'ks_2samp' not found in script source."
        )

    def test_validate_script_reads_db_url_from_env(self) -> None:
        """The script must support reading the DB URL from the DATABASE_URL env var.

        Per the security requirement: DSN should not be in shell history.
        The env var takes precedence over the CLI argument.
        """
        source = _load_source()

        assert "DATABASE_URL" in source, (
            "Script must support the DATABASE_URL environment variable as an "
            "alternative to --db-url. The env var takes precedence for security "
            "(DSN should not appear in shell history)."
        )


# ---------------------------------------------------------------------------
# Structural tests for _sanitize_dataframe_for_sdv (added P56 fix)
# ---------------------------------------------------------------------------


class TestSanitizeDataframeForSdv:
    """Source-level structural tests for the SDV pre-processing helper.

    These tests use AST/source inspection rather than dynamic import because
    the script has heavy optional dependencies (SDV, torch, opacus) that are
    not available in the unit test environment.
    """

    def test_sanitize_function_is_defined(self) -> None:
        """The script must define ``_sanitize_dataframe_for_sdv``.

        This function is required to strip timezone-aware timestamps and date
        columns that SDV's CTGAN metadata inference cannot handle.
        """
        source = _load_source()

        assert "_sanitize_dataframe_for_sdv" in source, (
            "Helper function '_sanitize_dataframe_for_sdv' not found in script. "
            "It is required to sanitize DataFrame column types before SDV ingestion."
        )

    def test_sanitize_function_is_called_in_subsetting_stage(self) -> None:
        """``_sanitize_dataframe_for_sdv`` must be called after DataFrame construction.

        The function must be called before ``df.to_parquet()`` to ensure that
        sanitized data is what gets written, not the raw PostgreSQL-typed frame.
        """
        source = _load_source()

        # The call must appear in the source — confirmed via string presence.
        assert "df = _sanitize_dataframe_for_sdv(df," in source, (
            "Script must call _sanitize_dataframe_for_sdv(df, table) "
            "after constructing the DataFrame and before writing to Parquet."
        )

    def test_sanitize_function_strips_timezone_aware_columns(self) -> None:
        """The sanitize function must strip tz info with ``dt.tz_localize(None)``.

        Pagila's ``last_update`` column is timestamptz (timezone-aware).  SDV
        raises ``InvalidMetadataError`` on tz-aware datetimes.  The fix is to
        call ``dt.tz_localize(None)`` to produce naive datetime64.
        """
        source = _load_source()

        assert "tz_localize(None)" in source, (
            "Sanitize function must call dt.tz_localize(None) to strip timezone "
            "info from tz-aware timestamp columns (e.g. Pagila's last_update timestamptz)."
        )

    def test_sanitize_function_converts_date_columns(self) -> None:
        """The sanitize function must convert ``datetime.date`` columns to datetime64.

        Pagila's ``create_date`` column arrives as Python ``datetime.date`` objects
        in an object-dtype Series.  SDV cannot handle raw date objects — they must
        be converted to ``datetime64`` via ``pd.to_datetime()``.
        """
        source = _load_source()

        assert "pd.to_datetime" in source, (
            "Sanitize function must call pd.to_datetime() to convert date columns "
            "to datetime64 (e.g. Pagila's create_date column)."
        )

    def test_sanitize_function_drops_unsupported_types(self) -> None:
        """The sanitize function must drop columns with array/bytea/composite types.

        PostgreSQL array, bytea, and composite columns arrive as Python lists,
        bytes, or dicts.  SDV raises ``InvalidMetadataError`` on these.  The
        function must drop them rather than passing them through.
        """
        source = _load_source()

        # The drop is implemented via df.drop(columns=...) with a ``dropped`` list.
        assert "df.drop(columns=" in source, (
            "Sanitize function must drop unsupported columns via df.drop(columns=...). "
            "PostgreSQL array/bytea/composite columns cause SDV InvalidMetadataError."
        )

    def test_sanitize_function_has_google_docstring(self) -> None:
        """The sanitize function must have a Google-style docstring.

        Per CONSTITUTION Priority 5: all public (and private utility) functions
        must have docstrings documenting Args and Returns.
        """
        source = _load_source()

        # Find the function body and check for Args: and Returns: sections.
        func_start = source.find("def _sanitize_dataframe_for_sdv(")
        assert func_start != -1, "Function definition not found"

        func_body = source[func_start : func_start + 800]
        assert "Args:" in func_body, (
            "_sanitize_dataframe_for_sdv is missing 'Args:' section in docstring."
        )
        assert "Returns:" in func_body, (
            "_sanitize_dataframe_for_sdv is missing 'Returns:' section in docstring."
        )
