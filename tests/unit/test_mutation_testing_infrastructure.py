"""Mutation testing infrastructure tests (T53.1).

Validates that the cosmic-ray configuration is present, valid, correctly scoped,
and that the CI workflow includes a properly-guarded mutation testing gate.

Attack/negative test cases (per spec-challenger):
  - Zero-mutant case must fail loudly (not silently claim 100% on 0 mutants)
  - Incomplete run detection must be wired (pending work == incomplete)
  - CI timeout budget must be enforced (max 30 minutes)
  - Module scope must cover all .py files in shared/security/ and modules/privacy/
    (excluding trivial __init__.py)

Test order: ATTACK RED tests are committed first, GREEN commit adds the infra.
"""

import tomllib
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture
def cosmic_ray_config() -> dict:  # type: ignore[return]
    """Load and return the parsed cosmic-ray.toml config."""
    config_path = REPO_ROOT / "cosmic-ray.toml"
    assert config_path.exists(), (
        f"cosmic-ray.toml not found at {config_path}. "
        "T53.1 requires a cosmic-ray config in the project root."
    )
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)
    assert "cosmic-ray" in raw, "cosmic-ray.toml must have a [cosmic-ray] section."
    return raw["cosmic-ray"]


@pytest.fixture
def ci_workflow() -> dict:  # type: ignore[return]
    """Load and return the parsed CI workflow YAML."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow_path.exists(), f"CI workflow not found at {workflow_path}."
    with workflow_path.open() as fh:
        data = yaml.safe_load(fh)
    return data  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Test: config file is valid TOML and has required top-level keys
# ---------------------------------------------------------------------------


def test_cosmic_ray_config_is_valid_toml(cosmic_ray_config: dict) -> None:
    """Config file parses as TOML without errors (validated by fixture load)."""
    assert isinstance(cosmic_ray_config, dict), "Parsed [cosmic-ray] section must be a dict."


def test_cosmic_ray_config_has_required_keys(cosmic_ray_config: dict) -> None:
    """Config must declare module-path, timeout, test-command, and distributor."""
    required_keys = {"module-path", "timeout", "test-command", "distributor"}
    missing = required_keys - cosmic_ray_config.keys()
    assert not missing, f"cosmic-ray.toml [cosmic-ray] section is missing required keys: {missing}"


def test_cosmic_ray_distributor_is_local(cosmic_ray_config: dict) -> None:
    """Distributor must be 'local' (subprocess-per-mutant; avoids SIGSEGV on CPython 3.14)."""
    distributor = cosmic_ray_config.get("distributor", {})
    assert isinstance(distributor, dict), "distributor config must be a dict"
    assert distributor.get("name") == "local", (
        "cosmic-ray must use the 'local' distributor. "
        "ADR-0054: subprocess isolation per mutant avoids CPython 3.14 SIGSEGV."
    )


# ---------------------------------------------------------------------------
# Test: mutation score threshold is configured at >= 60%
# ---------------------------------------------------------------------------


def test_cosmic_ray_mutation_threshold_is_configured() -> None:
    """A mutation-score threshold script/checker must exist and enforce >= 60%.

    The threshold is enforced by a helper script invoked from CI after exec.
    This test verifies the script exists and contains the correct threshold value.
    """
    script_path = REPO_ROOT / "scripts" / "check_mutation_score.py"
    assert script_path.exists(), (
        f"scripts/check_mutation_score.py not found at {script_path}. "
        "T53.1 requires a threshold enforcement script."
    )
    content = script_path.read_text()
    # The script must reference the 60% threshold (from ADR-0047)
    assert "60" in content, (
        "check_mutation_score.py must enforce the 60% threshold from ADR-0047. "
        "Ensure the script contains the threshold value 60."
    )


# ---------------------------------------------------------------------------
# Test: zero-mutant case fails loudly (spec-challenger attack case)
# ---------------------------------------------------------------------------


def test_zero_mutant_guard_is_present() -> None:
    """check_mutation_score.py must guard against zero mutants.

    A score of 0 killed / 0 total would produce 100% if not guarded, or 0% —
    both are incorrect for a session with no mutants executed.
    The script must explicitly reject 0-mutant sessions.
    """
    script_path = REPO_ROOT / "scripts" / "check_mutation_score.py"
    assert script_path.exists(), (
        "check_mutation_score.py must exist "
        "(checked by test_cosmic_ray_mutation_threshold_is_configured)."
    )
    content = script_path.read_text()
    # The script must have an explicit zero-mutant guard
    assert (
        "zero" in content.lower() or "0 mutant" in content.lower() or "no mutant" in content.lower()
    ), (
        "check_mutation_score.py must guard against 0-mutant sessions. "
        "A session with no mutants executed must fail loudly, not report 100% or 0%."
    )


# ---------------------------------------------------------------------------
# Test: incomplete run detection
# ---------------------------------------------------------------------------


def test_incomplete_run_detection_is_present() -> None:
    """check_mutation_score.py must detect incomplete runs (pending work remaining).

    If cosmic-ray exec is interrupted, some mutants remain in 'pending' state.
    A partial session must fail the gate rather than reporting a misleading score.
    """
    script_path = REPO_ROOT / "scripts" / "check_mutation_score.py"
    assert script_path.exists(), "check_mutation_score.py must exist."
    content = script_path.read_text()
    # The script must check for pending/incomplete work
    assert "pending" in content.lower() or "incomplete" in content.lower(), (
        "check_mutation_score.py must detect incomplete runs (pending mutants). "
        "A partial session must fail the CI gate."
    )


# ---------------------------------------------------------------------------
# Test: CI workflow includes mutation testing gate
# ---------------------------------------------------------------------------


def test_ci_workflow_has_mutation_testing_job(ci_workflow: dict) -> None:
    """CI workflow must have a mutation-test job."""
    jobs = ci_workflow.get("jobs", {})
    assert "mutation-test" in jobs, (
        "ci.yml must contain a 'mutation-test' job. "
        "T53.1 requires the mutation gate to be wired into CI."
    )


def test_ci_mutation_job_has_timeout(ci_workflow: dict) -> None:
    """CI mutation job must have a timeout-minutes set to <= 30.

    Spec-challenger requirement: CI timeout budget max 30 minutes.
    1,260+ mutants require more time on GitHub runners than the original 15m budget.
    """
    jobs = ci_workflow.get("jobs", {})
    mutation_job = jobs.get("mutation-test", {})
    timeout = mutation_job.get("timeout-minutes")
    assert timeout is not None, (
        "mutation-test job must have 'timeout-minutes' set. "
        "Spec-challenger: max 30 minutes for mutation gate."
    )
    assert isinstance(timeout, int), (
        f"timeout-minutes must be an integer, got {type(timeout).__name__}"
    )
    assert timeout <= 30, (
        f"mutation-test timeout-minutes must be <= 30 (got {timeout}). "
        "Spec-challenger: CI timeout budget max 30 minutes."
    )


def test_ci_mutation_job_depends_on_test(ci_workflow: dict) -> None:
    """Mutation job must depend on the test job (run after unit tests pass)."""
    jobs = ci_workflow.get("jobs", {})
    mutation_job = jobs.get("mutation-test", {})
    needs = mutation_job.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    assert "test" in needs or "lint" in needs, (
        "mutation-test job must depend on 'test' or 'lint' to run after quality gates. "
        f"Got needs: {needs}"
    )


def test_ci_mutation_job_runs_cosmic_ray(ci_workflow: dict) -> None:
    """Mutation job steps must invoke cosmic-ray exec and check_mutation_score.py."""
    jobs = ci_workflow.get("jobs", {})
    mutation_job = jobs.get("mutation-test", {})
    steps = mutation_job.get("steps", [])
    all_step_text = " ".join(str(step.get("run", "")) for step in steps)
    assert "cosmic-ray" in all_step_text, (
        "mutation-test job must invoke 'cosmic-ray' in at least one step."
    )
    assert "check_mutation_score" in all_step_text, (
        "mutation-test job must invoke check_mutation_score.py for threshold enforcement."
    )


# ---------------------------------------------------------------------------
# Test: module scope covers all target files
# ---------------------------------------------------------------------------


def test_cosmic_ray_scope_covers_shared_security(cosmic_ray_config: dict) -> None:
    """module-path must target shared/security/ source files.

    The module-path field names the package; all non-trivial files in
    shared/security/ and modules/privacy/ must be in scope.
    """
    module_path = cosmic_ray_config.get("module-path", "")
    # module-path is a Python dotted module name pointing to the package root
    assert "synth_engine" in module_path or "shared" in module_path or "security" in module_path, (
        f"cosmic-ray module-path '{module_path}' does not appear to target "
        "synth_engine.shared.security or synth_engine. "
        "Ensure the config targets the correct module scope."
    )


def test_cosmic_ray_excluded_modules_skip_init_files(cosmic_ray_config: dict) -> None:
    """excluded-modules must exclude trivial __init__.py files from mutation scope.

    Mutating __init__.py files produces noise (import-order mutations) without
    testing real behavior. The excluded-modules list must prevent this.
    """
    excluded = cosmic_ray_config.get("excluded-modules", [])
    assert isinstance(excluded, list), "excluded-modules must be a list."
    # At least one exclusion must target __init__ or init files
    has_init_exclusion = any("__init__" in str(ex) or "init" in str(ex).lower() for ex in excluded)
    assert has_init_exclusion, (
        "excluded-modules must exclude __init__ files from mutation scope. "
        "Mutating __init__.py produces noise without testing real behavior. "
        f"Current exclusions: {excluded}"
    )


# ---------------------------------------------------------------------------
# Test: ADR-0054 document exists
# ---------------------------------------------------------------------------


def test_adr_0054_exists() -> None:
    """ADR-0054 (cosmic-ray adoption) must exist."""
    adr_path = REPO_ROOT / "docs" / "adr" / "ADR-0054-cosmic-ray-adoption.md"
    assert adr_path.exists(), (
        f"ADR-0054 not found at {adr_path}. "
        "T53.1 requires documenting the tool substitution per PM Rule 6."
    )


def test_adr_0054_references_adr_0052() -> None:
    """ADR-0054 must reference ADR-0052 (the superseded decision)."""
    adr_path = REPO_ROOT / "docs" / "adr" / "ADR-0054-cosmic-ray-adoption.md"
    if not adr_path.exists():
        pytest.skip("ADR-0054 does not exist yet (RED phase).")
    content = adr_path.read_text()
    assert "ADR-0052" in content, "ADR-0054 must reference ADR-0052 which it supersedes."


def test_adr_0052_status_is_superseded() -> None:
    """ADR-0052 must have its status updated to 'Superseded by ADR-0054'."""
    adr_path = REPO_ROOT / "docs" / "adr" / "ADR-0052-mutmut-python-314-gap.md"
    assert adr_path.exists(), "ADR-0052 must exist."
    content = adr_path.read_text()
    assert "Superseded" in content, (
        "ADR-0052 status must be updated to 'Superseded by ADR-0054'. "
        "T53.1 requires updating the old ADR when adopting cosmic-ray."
    )


# ---------------------------------------------------------------------------
# Test: Constitution enforcement table references cosmic-ray
# ---------------------------------------------------------------------------


def test_constitution_references_cosmic_ray() -> None:
    """CONSTITUTION.md enforcement table must reference cosmic-ray, not mutmut."""
    constitution_path = REPO_ROOT / "CONSTITUTION.md"
    assert constitution_path.exists(), "CONSTITUTION.md must exist."
    content = constitution_path.read_text()
    # The enforcement table row for mutation score must mention cosmic-ray
    assert "cosmic-ray" in content, (
        "CONSTITUTION.md Priority 4 mutation score row must reference cosmic-ray. "
        "Update the enforcement table to replace 'mutmut run' with the cosmic-ray command."
    )
