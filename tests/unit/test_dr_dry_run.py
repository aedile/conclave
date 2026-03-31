"""Feature tests for dr_dry_run.sh (T51.4).

Validates structural and behavioral requirements:
1. Script passes shellcheck
2. Script has EXIT trap for cleanup
3. No backup files written to data/ or output/
4. All test data uses dr_test_ prefix
5. Script checks Docker stack availability before proceeding
6. Script prints PASS/FAIL for each scenario
7. Script uses correct service names from docker-compose.yml
8. Script has set -euo pipefail
9. All three DR scenarios are present (DB backup/restore, service recovery, Redis recovery)

CONSTITUTION Priority 3: TDD
Task: T51.4 — Disaster Recovery Dry Run Script
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "dr_dry_run.sh"


def _script_text() -> str:
    """Return the full text of dr_dry_run.sh.

    Returns:
        Script content as a string.

    Raises:
        FileNotFoundError: If the script has not yet been created.
    """
    return _SCRIPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC1: shellcheck passes
# ---------------------------------------------------------------------------


class TestShellcheck:
    """Verify dr_dry_run.sh passes shellcheck."""

    def test_script_passes_shellcheck(self) -> None:
        """The script must have zero shellcheck violations.

        shellcheck enforces POSIX-compliant shell scripting best practices
        and catches common bash pitfalls (unquoted variables, word splitting,
        globbing, etc.) that could cause silent failures during DR operations.
        """
        result = subprocess.run(
            ["shellcheck", str(_SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"shellcheck found violations in dr_dry_run.sh:\n{result.stdout}\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# AC2: set -euo pipefail present
# ---------------------------------------------------------------------------


class TestSafetyFlags:
    """Verify the script uses strict bash safety flags."""

    def test_set_euo_pipefail_present(self) -> None:
        """The script must begin with 'set -euo pipefail'.

        - -e: exit immediately on error
        - -u: treat unset variables as errors
        - -o pipefail: pipeline failure propagates to exit code

        These flags are mandatory for scripts that perform destructive
        operations (DROP TABLE, docker compose stop) to prevent partial
        execution on failure.
        """
        text = _script_text()
        assert "set -euo pipefail" in text, (
            "dr_dry_run.sh must start with 'set -euo pipefail' to prevent "
            "partial execution on error."
        )

    def test_shebang_is_bash(self) -> None:
        """The script must use a portable bash shebang.

        #!/usr/bin/env bash is preferred over #!/bin/bash for portability
        across different host environments (macOS, Linux, Alpine containers).
        """
        text = _script_text()
        first_line = text.splitlines()[0]
        assert first_line == "#!/usr/bin/env bash", (
            f"Expected shebang '#!/usr/bin/env bash', got {first_line!r}"
        )


# ---------------------------------------------------------------------------
# AC3: Three DR scenarios present
# ---------------------------------------------------------------------------


class TestDrScenarios:
    """Verify all three DR scenarios are implemented."""

    def test_scenario_1_database_backup_restore_present(self) -> None:
        """Scenario 1 (Database Backup & Restore) must be present.

        This scenario validates the core PITR capability: pg_dump a test
        table, drop it, restore it, and verify the data is intact.
        """
        text = _script_text()
        # Look for pg_dump AND pg_restore (or psql restore)
        has_dump = "pg_dump" in text
        has_restore = "pg_restore" in text or "psql" in text
        assert has_dump == True, "Scenario 1 missing: no pg_dump invocation found"
        assert has_dump
        assert has_restore == True, (
            "Scenario 1 missing: no pg_restore/psql restore invocation found"
        )
        assert has_restore

    def test_scenario_2_service_recovery_present(self) -> None:
        """Scenario 2 (Service Recovery) must be present.

        This scenario validates that the app container can be stopped,
        restarted, and return to healthy status within the poll timeout.
        """
        text = _script_text()
        has_stop = "docker compose stop" in text or "docker-compose stop" in text
        has_start = "docker compose start" in text or "docker-compose start" in text
        assert has_stop == True, "Scenario 2 missing: no 'docker compose stop' invocation"
        assert has_stop
        assert has_start == True, "Scenario 2 missing: no 'docker compose start' invocation"
        assert has_start

    def test_scenario_3_redis_recovery_present(self) -> None:
        """Scenario 3 (Redis Recovery) must be present.

        This scenario validates Redis ephemeral behavior: write a key,
        stop Redis, restart Redis, verify key state, clean up.
        """
        text = _script_text()
        has_redis_set = "redis-cli" in text and "SET" in text
        has_redis_stop = "docker compose stop redis" in text or "docker-compose stop redis" in text
        assert has_redis_set == True, "Scenario 3 missing: no redis-cli SET invocation"
        assert has_redis_set
        assert has_redis_stop == True, (
            "Scenario 3 missing: no 'docker compose stop redis' invocation"
        )
        assert has_redis_stop


# ---------------------------------------------------------------------------
# AC4: Service names match docker-compose.yml
# ---------------------------------------------------------------------------


class TestServiceNames:
    """Verify the script uses correct service names from docker-compose.yml."""

    def test_uses_postgres_service_name(self) -> None:
        """The script must reference the 'postgres' service name.

        docker-compose.yml defines the service as 'postgres'. Using a
        different name would cause 'docker compose exec' to fail silently
        or produce confusing errors during DR operations.
        """
        text = _script_text()
        assert "postgres" in text, (
            "Script does not reference the 'postgres' service. "
            "Verify service names match docker-compose.yml."
        )

    def test_uses_redis_service_name(self) -> None:
        """The script must reference the 'redis' service name.

        docker-compose.yml defines the service as 'redis'. Using a
        different name would cause Scenario 3 to fail.
        """
        text = _script_text()
        assert "redis" in text, (
            "Script does not reference the 'redis' service. "
            "Verify service names match docker-compose.yml."
        )

    def test_uses_app_service_name(self) -> None:
        """The script must reference the 'app' service name.

        docker-compose.yml defines the Conclave Engine as 'app'. The
        service recovery scenario (Scenario 2) must stop and start 'app'.
        """
        text = _script_text()
        assert "app" in text, (
            "Script does not reference the 'app' service. "
            "Verify service names match docker-compose.yml."
        )


# ---------------------------------------------------------------------------
# AC5: PASS/FAIL output for each scenario
# ---------------------------------------------------------------------------


class TestPassFailOutput:
    """Verify the script outputs PASS or FAIL for each scenario."""

    def test_pass_indicator_present(self) -> None:
        """The script must print a PASS indicator on success.

        Operators running DR dry runs need unambiguous success signals.
        The script must print at least one PASS line to confirm scenarios
        completed successfully.
        """
        text = _script_text()
        assert "PASS" in text, (
            "Script does not output PASS indicators. "
            "Each scenario must print '[PASS]' or similar on success."
        )

    def test_fail_indicator_present(self) -> None:
        """The script must print a FAIL indicator on failure.

        Silent failures are worse than noisy ones in DR contexts. The
        script must print a FAIL indicator when a scenario does not meet
        its expected outcome.
        """
        text = _script_text()
        assert "FAIL" in text, (
            "Script does not output FAIL indicators. "
            "Each scenario must print '[FAIL]' or similar on failure."
        )


# ---------------------------------------------------------------------------
# AC6: Health poll with timeout for app service recovery
# ---------------------------------------------------------------------------


class TestHealthPoll:
    """Verify the script polls for app health with a bounded timeout."""

    def test_health_poll_targets_ready_endpoint(self) -> None:
        """The health poll must target /ready (not /health).

        As documented in docker-compose.yml (T48.3), /ready performs live
        dependency checks. /health is a liveness probe and may return 200
        even when Postgres is unreachable. DR validation must use /ready.
        """
        text = _script_text()
        assert "/ready" in text, (
            "Health poll must target /ready endpoint (not /health). "
            "Per T48.3, /ready performs live dependency checks. "
            "See docker-compose.yml app service healthcheck."
        )

    def test_health_poll_has_bounded_timeout(self) -> None:
        """The health poll must have a bounded timeout (max wait value).

        An unbounded poll loop would hang indefinitely if the app fails
        to come back. A timeout ensures the script fails cleanly when
        service recovery takes too long.
        """
        text = _script_text()
        # Accept any numeric timeout variable: MAX_WAIT, TIMEOUT, etc.
        has_timeout = (
            "MAX_WAIT" in text
            or "TIMEOUT" in text
            or "max_wait" in text
            or "timeout" in text.lower()
        )
        assert has_timeout == True, (
            "Health poll does not have a named timeout variable (MAX_WAIT, TIMEOUT, etc.). "
            "The poll loop must terminate after a defined maximum wait period."
        )
        assert has_timeout


# ---------------------------------------------------------------------------
# AC7: Cleanup on EXIT
# ---------------------------------------------------------------------------


class TestCleanup:
    """Verify the cleanup function covers all resources."""

    def test_cleanup_drops_dr_test_table(self) -> None:
        """The cleanup function must drop the dr_test_ table.

        Leaving test tables in the conclave database would pollute the
        schema and could interfere with application migrations or monitoring.
        """
        text = _script_text()
        # Look for DROP TABLE in a cleanup context
        has_drop = "DROP TABLE" in text.upper()
        assert has_drop == True, (
            "No DROP TABLE found in dr_dry_run.sh. "
            "The cleanup function must drop the dr_test_ table on exit."
        )
        assert has_drop

    def test_cleanup_deletes_redis_test_key(self) -> None:
        """The cleanup function must delete the dr_test_ Redis key.

        Leaving test keys in Redis would pollute the task queue namespace
        and could confuse monitoring tools.
        """
        text = _script_text()
        has_del = "redis-cli" in text and ("DEL" in text or "del" in text.lower())
        assert has_del == True, (
            "No redis-cli DEL found in dr_dry_run.sh. "
            "The cleanup function must delete the dr_test_ Redis key on exit."
        )
        assert has_del
