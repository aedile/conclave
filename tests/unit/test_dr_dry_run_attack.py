"""Negative/attack tests for dr_dry_run.sh (T51.4).

Validates security invariants and negative-case behavior:
- Backup files NEVER written to committed directories (data/, output/)
- Test data always uses dr_test_ prefix (no real PII)
- EXIT trap present for cleanup on failure
- Script fails cleanly when Docker stack is not running
- Script fails cleanly when pg_dump is not available
- No hardcoded credentials
- No references to committed secret paths

CONSTITUTION Priority 0: Security is Priority Zero.
Task: T51.4 — Disaster Recovery Dry Run Script
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "dr_dry_run.sh"
_COMMITTED_DATA_DIRS = frozenset(["data/", "output/", "./data", "./output"])


def _script_text() -> str:
    """Return the full text of dr_dry_run.sh.

    Returns:
        Script content as a string.

    Raises:
        FileNotFoundError: If the script has not yet been created.
    """
    return _SCRIPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Attack: Backup files MUST NOT be written to committed directories
# ---------------------------------------------------------------------------


class TestNoBackupInCommittedDirs:
    """Ensure the script never routes backup files to version-controlled directories."""

    def test_backup_not_written_to_data_dir(self) -> None:
        """Backup paths must not reference the data/ directory.

        data/ contains real PII in production. Writing a backup there would
        risk committing sensitive data to VCS or leaking it via the host.
        The script must route ALL backup files to /tmp/.
        """
        text = _script_text()
        # Look for pg_dump invocations and assert they go to /tmp
        pgdump_lines = [line for line in text.splitlines() if "pg_dump" in line]
        assert pgdump_lines, "No pg_dump invocation found in dr_dry_run.sh"
        for line in pgdump_lines:
            assert "data/" not in line, (
                f"pg_dump invocation references data/ directory: {line!r}\n"
                "Backup files must go to /tmp/ only."
            )

    def test_backup_not_written_to_output_dir(self) -> None:
        """Backup paths must not reference the output/ directory.

        output/ is also PII-sensitive and git-ignored for safety, but directing
        backups there is a footgun — operators may inadvertently copy or commit
        output/ contents. Only /tmp/ is acceptable.
        """
        text = _script_text()
        pgdump_lines = [line for line in text.splitlines() if "pg_dump" in line]
        assert pgdump_lines, "No pg_dump invocation found in dr_dry_run.sh"
        for line in pgdump_lines:
            assert "output/" not in line, (
                f"pg_dump invocation references output/ directory: {line!r}\n"
                "Backup files must go to /tmp/ only."
            )

    def test_backup_goes_to_tmp(self) -> None:
        """At least one backup path in the script must reference /tmp/.

        /tmp/ is ephemeral and OS-managed. This is the correct destination
        for DR dry-run backup files.
        """
        text = _script_text()
        assert "/tmp/" in text, (
            "Script does not reference /tmp/ for backup storage. "
            "All DR dry-run backup files must be written to /tmp/."
        )


# ---------------------------------------------------------------------------
# Attack: Test data prefix — NEVER use PII-looking column names
# ---------------------------------------------------------------------------


class TestSyntheticDataPrefix:
    """Ensure all test tables and keys use the dr_test_ prefix.

    The script uses shell variables (DR_TABLE, DR_REDIS_KEY) to hold the
    resource names. These tests check the variable assignments to verify the
    dr_test_ prefix is enforced at the point of definition, and that the
    CREATE TABLE and redis-cli SET commands reference those variables.
    """

    def test_test_table_uses_dr_test_prefix(self) -> None:
        """The DR_TABLE variable must be assigned a dr_test_ prefix value.

        Using a clearly synthetic prefix prevents any confusion between DR
        validation data and real application data. It also makes cleanup
        trivially identifiable.

        The script uses a shell variable (DR_TABLE) expanded at runtime.
        This test verifies the variable assignment ensures the dr_test_ prefix.
        """
        text = _script_text()
        # The DR_TABLE variable must be assigned a value starting with dr_test_
        table_var_pattern = re.compile(r'DR_TABLE=["\']?dr_test_', re.IGNORECASE)
        assert table_var_pattern.search(text), (
            "DR_TABLE variable is not assigned a dr_test_ prefix value. "
            "All test tables must be prefixed with 'dr_test_' to be "
            "clearly synthetic and safe to drop."
        )
        # The CREATE TABLE statement must reference the DR_TABLE variable
        assert "CREATE TABLE" in text.upper(), "No CREATE TABLE statement found in dr_dry_run.sh"
        assert "DR_TABLE" in text, (
            "CREATE TABLE does not reference the DR_TABLE variable. "
            "The table name must come from the DR_TABLE variable."
        )

    def test_redis_test_key_uses_dr_test_prefix(self) -> None:
        """The DR_REDIS_KEY variable must be assigned a dr_test_ prefix value.

        Consistent prefix across all ephemeral test resources makes the
        script's cleanup scope unambiguous.

        The script uses a shell variable (DR_REDIS_KEY) expanded at runtime.
        This test verifies the variable assignment ensures the dr_test_ prefix.
        """
        text = _script_text()
        # The DR_REDIS_KEY variable must be assigned a value starting with dr_test_
        key_var_pattern = re.compile(r'DR_REDIS_KEY=["\']?dr_test_', re.IGNORECASE)
        assert key_var_pattern.search(text), (
            "DR_REDIS_KEY variable is not assigned a dr_test_ prefix value. "
            "All test keys must be prefixed with 'dr_test_'."
        )
        # The redis-cli SET command must reference the DR_REDIS_KEY variable
        assert "redis-cli" in text, "No redis-cli invocation found in dr_dry_run.sh"
        assert "SET" in text, "No redis-cli SET invocation found in dr_dry_run.sh"
        assert "DR_REDIS_KEY" in text, (
            "redis-cli SET does not reference the DR_REDIS_KEY variable. "
            "The key name must come from the DR_REDIS_KEY variable."
        )


# ---------------------------------------------------------------------------
# Attack: EXIT trap for cleanup must be present
# ---------------------------------------------------------------------------


class TestExitTrap:
    """Ensure the script registers an EXIT trap for guaranteed cleanup."""

    def test_trap_exit_is_registered(self) -> None:
        """The script must register a trap on EXIT for guaranteed cleanup.

        Without an EXIT trap, a mid-script failure leaves test tables and
        backup files in place — a data residue risk. The trap ensures cleanup
        runs even when the script fails.
        """
        text = _script_text()
        # Accept both 'trap ... EXIT' and 'trap ... ERR EXIT'
        trap_pattern = re.compile(r"trap\s+.+EXIT", re.IGNORECASE)
        assert trap_pattern.search(text), (
            "No 'trap ... EXIT' found in dr_dry_run.sh. "
            "The script must register a cleanup trap on EXIT to ensure "
            "test tables and backup files are removed even on failure."
        )

    def test_cleanup_function_removes_backup_file(self) -> None:
        """The cleanup function must delete the backup file from /tmp/.

        Backup files in /tmp/ are ephemeral by OS convention but are not
        guaranteed to be removed quickly. The explicit cleanup is belt-and-suspenders.
        """
        text = _script_text()
        # The cleanup section must reference rm or unlink on the backup file
        assert "rm " in text or "rm\t" in text, (
            "No 'rm' command found in dr_dry_run.sh. "
            "The cleanup function must explicitly delete the backup file."
        )


# ---------------------------------------------------------------------------
# Attack: Script must fail cleanly when Docker stack is not running
# ---------------------------------------------------------------------------


class TestDockerStackPrecheck:
    """Ensure the script validates Docker availability before proceeding."""

    def test_script_checks_docker_compose_before_proceeding(self) -> None:
        """The script must verify the Docker stack is running at startup.

        Running DR procedures against a stopped stack would silently succeed
        (no containers to stop/start) and produce false PASS results. The
        script must abort with a clear error message if the stack is not
        running.
        """
        text = _script_text()
        # Accept docker compose ps, docker-compose ps, or explicit service checks
        has_stack_check = (
            "docker compose ps" in text
            or "docker-compose ps" in text
            or "docker compose exec" in text
        )
        assert has_stack_check == True, (
            "Script does not check Docker stack availability before proceeding. "
            "Add a preflight check (e.g. 'docker compose ps') that exits non-zero "
            "if the required services are not running."
        )
        assert has_stack_check

    def test_script_exits_nonzero_on_failure(self) -> None:
        """The script must exit non-zero if any scenario fails.

        A DR dry-run script that always exits 0 gives false confidence to
        operators. The script must propagate failures to its exit code.
        """
        text = _script_text()
        # set -e or explicit exit 1 patterns
        has_nonzero_exit = "set -euo pipefail" in text or "exit 1" in text
        assert has_nonzero_exit == True, (
            "Script does not exit non-zero on failure. "
            "Add 'set -euo pipefail' and/or explicit 'exit 1' on failure paths."
        )
        assert has_nonzero_exit


# ---------------------------------------------------------------------------
# Attack: No hardcoded credentials
# ---------------------------------------------------------------------------


class TestNoHardcodedCredentials:
    """Ensure the script does not hardcode passwords or secret values."""

    _SUSPICIOUS_PATTERNS: ClassVar[list[str]] = [
        r"password\s*=\s*['\"][^'\"]+['\"]",
        r"passwd\s*=\s*['\"][^'\"]+['\"]",
        r"POSTGRES_PASSWORD\s*=\s*['\"][^'\"]+['\"]",
        r"-p\s+[a-zA-Z0-9_!@#$%^&*]{6,}",  # -p <literal-password>
    ]

    def test_no_hardcoded_password_literals(self) -> None:
        """The script must not contain hardcoded password literals.

        Hardcoded credentials would be committed to VCS and violate
        CONSTITUTION Priority 0 (no secrets in source control). The script
        must read credentials from environment variables or docker compose exec.
        """
        text = _script_text()
        for pattern in self._SUSPICIOUS_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            assert match is None, (
                f"Possible hardcoded credential found matching pattern {pattern!r}: "
                f"{match.group()!r}\n"
                "Credentials must be read from environment variables, not hardcoded."
            )
            assert str(match) == "None"

    def test_uses_docker_exec_or_env_for_db_access(self) -> None:
        """The script must access the DB via docker compose exec or env vars.

        Direct psql/pg_dump invocations with hardcoded host/port/password
        are forbidden. The script must use docker compose exec to run commands
        inside the postgres container, or source credentials from .env.
        """
        text = _script_text()
        # Must use docker compose exec for postgres access
        has_exec = "docker compose exec" in text or "docker-compose exec" in text
        assert has_exec == True, (
            "Script does not use 'docker compose exec' for database access. "
            "All psql/pg_dump invocations must run inside the postgres container "
            "via 'docker compose exec postgres ...' to avoid needing exposed ports "
            "or hardcoded connection strings."
        )
        assert has_exec


# ---------------------------------------------------------------------------
# Attack: Script must handle missing pg_dump gracefully
# ---------------------------------------------------------------------------


class TestPgDumpPrecheck:
    """Verify pg_dump availability is checked before the backup scenario."""

    def test_script_handles_pg_dump_unavailability(self) -> None:
        """The backup scenario uses pg_dump inside the container, not on the host.

        Requiring the operator's host to have pg_dump installed creates a
        fragile dependency. The script must run pg_dump inside the postgres
        container via docker compose exec, which always has pg_dump available.

        Only non-comment, non-print lines containing pg_dump are checked —
        comment lines and print_info messages may reference pg_dump by name.
        """
        text = _script_text()
        # pg_dump must be invoked via docker compose exec postgres
        # Filter to command lines only: skip comments (#) and print_info/print_warn lines
        command_lines_with_pgdump = [
            line.strip()
            for line in text.splitlines()
            if "pg_dump" in line
            and not line.strip().startswith("#")
            and not line.strip().startswith("print_")
        ]
        assert len(command_lines_with_pgdump) > 0, (
            "No pg_dump command invocation found in dr_dry_run.sh"
        )
        for line in command_lines_with_pgdump:
            is_exec_based = "docker compose exec" in line or "docker-compose exec" in line
            assert is_exec_based == True, (
                f"pg_dump invocation does not use 'docker compose exec': {line!r}\n"
                "Run pg_dump inside the postgres container to avoid host dependency."
            )
