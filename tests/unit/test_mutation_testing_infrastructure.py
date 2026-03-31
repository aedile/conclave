"""Mutation testing infrastructure tests (T53.1).

Validates that the cosmic-ray configuration is present, valid, correctly scoped,
and that the local tooling (cosmic-ray.toml, check_mutation_score.py) is correctly
configured for PM-gated local runs.

Mutation testing runs locally as a PM gate before merge. GitHub Actions shared
runners cannot complete 789 mutants within a reasonable timeout (ADR-0054,
amended 2026-03-24). The CI mutation-test job has been removed; cosmic-ray runs
are verified by the PM locally using:
  cosmic-ray init cosmic-ray.toml session.sqlite
  cosmic-ray exec cosmic-ray.toml session.sqlite
  python scripts/check_mutation_score.py session.sqlite

Attack/negative test cases (per spec-challenger):
  - Zero-mutant case must fail loudly (not silently claim 100% on 0 mutants)
  - Incomplete run detection must be wired (pending work == incomplete)
  - Module scope must cover all .py files in shared/security/ and modules/privacy/
    (excluding trivial __init__.py)

Test order: ATTACK RED tests are committed first, GREEN commit adds the infra.
"""

import tomllib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.infrastructure]

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


# ---------------------------------------------------------------------------
# Test: config file is valid TOML and has required top-level keys
# ---------------------------------------------------------------------------


def test_cosmic_ray_config_is_valid_toml(cosmic_ray_config: dict) -> None:
    """Config file parses as TOML without errors (validated by fixture load)."""
    assert isinstance(cosmic_ray_config, dict), "Parsed [cosmic-ray] section must be a dict."
    assert len(cosmic_ray_config) > 0, "[cosmic-ray] section must contain at least one key"


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

    The threshold is enforced by a helper script invoked locally by the PM
    after exec.  This test verifies the script exists and contains the
    correct threshold value.
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
        "A partial session must fail the local gate."
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
    assert has_init_exclusion == True, (
        "excluded-modules must exclude __init__ files from mutation scope. "
        "Mutating __init__.py produces noise without testing real behavior. "
        f"Current exclusions: {excluded}"
    )
    assert has_init_exclusion


def test_cosmic_ray_excluded_modules_skip_non_security_shared(cosmic_ray_config: dict) -> None:
    """excluded-modules must exclude all non-security files in shared/.

    ADR-0047 scope is shared/security/ and modules/privacy/ ONLY. The rest of
    shared/ (cert_metrics, db, errors, exceptions, middleware, protocols,
    schema_topology, settings, ssrf, task_queue, tasks, telemetry, tls) is
    infrastructure that must NOT receive mutations.

    Without this exclusion, cosmic-ray generates 1,260+ mutants (hitting all of
    shared/) instead of the expected ~700-900 for the 10 target files.
    This test is a regression guard against that scope creep.
    """
    import glob as _glob

    excluded_patterns = cosmic_ray_config.get("excluded-modules", [])
    assert isinstance(excluded_patterns, list), "excluded-modules must be a list."

    # Resolve the full excluded set exactly as cosmic-ray does (from project root).
    from pathlib import Path as _Path

    excluded: set[_Path] = {
        _Path(f) for pattern in excluded_patterns for f in _glob.glob(pattern, recursive=True)
    }

    # These non-security shared files must ALL be excluded from mutation scope.
    non_security_shared = [
        "src/synth_engine/shared/cert_metrics.py",
        "src/synth_engine/shared/db.py",
        "src/synth_engine/shared/errors.py",
        "src/synth_engine/shared/exceptions.py",
        "src/synth_engine/shared/middleware/idempotency.py",
        "src/synth_engine/shared/protocols.py",
        "src/synth_engine/shared/schema_topology.py",
        "src/synth_engine/shared/settings.py",
        "src/synth_engine/shared/ssrf.py",
        "src/synth_engine/shared/task_queue.py",
        "src/synth_engine/shared/tasks/reaper.py",
        "src/synth_engine/shared/tasks/repository.py",
        "src/synth_engine/shared/telemetry.py",
        "src/synth_engine/shared/tls/config.py",
    ]
    missing_exclusions = [p for p in non_security_shared if _Path(p) not in excluded]
    assert not missing_exclusions, (
        f"These non-security shared/ files are not excluded from cosmic-ray scope: "
        f"{missing_exclusions}. "
        "Add them to excluded-modules in cosmic-ray.toml to prevent scope creep. "
        "ADR-0047: only shared/security/ and modules/privacy/ are in mutation scope."
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


# ---------------------------------------------------------------------------
# Test: mutation testing is configured as a local PM gate, not CI
# ---------------------------------------------------------------------------


def test_constitution_mutation_gate_is_local_not_ci() -> None:
    """CONSTITUTION.md enforcement row must state mutation testing is a local PM gate.

    After ADR-0054 amendment (2026-03-24), mutation testing moved from the CI
    mutation-test job to a local PM gate due to GitHub Actions budget constraints.
    The enforcement table must reflect this.
    """
    constitution_path = REPO_ROOT / "CONSTITUTION.md"
    assert constitution_path.exists(), "CONSTITUTION.md must exist."
    content = constitution_path.read_text()
    assert "locally" in content or "local" in content, (
        "CONSTITUTION.md mutation score row must state that cosmic-ray runs locally "
        "as a PM gate. ADR-0054 (amended 2026-03-24): moved from CI to local gate."
    )


def test_ci_workflow_does_not_contain_mutation_test_job() -> None:
    """ci.yml must NOT contain a mutation-test job.

    After ADR-0054 amendment (2026-03-24), the mutation-test CI job was removed.
    GitHub Actions shared runners cannot complete 789 mutants within a reasonable
    timeout. cosmic-ray runs locally; PM verifies score before merge.
    """
    workflow_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow_path.exists(), f"CI workflow not found at {workflow_path}."
    content = workflow_path.read_text()
    assert "mutation-test:" not in content, (
        "ci.yml must NOT contain a 'mutation-test:' job. "
        "ADR-0054 (amended 2026-03-24): mutation testing moved to local PM gate. "
        "GitHub Actions shared runners cannot complete 789 mutants within budget."
    )
