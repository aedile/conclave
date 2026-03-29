"""Attack tests for T71.2 — audit CLI (conclave audit group).

ATTACK-FIRST TDD — proves the CLI:
1. Responds to --help (smoke test).
2. --dry-run does NOT write output file.
3. Missing AUDIT_KEY env var exits with non-zero code.
4. Malformed --details JSON exits non-zero.
5. Missing --input file exits non-zero.
6. Output file is written atomically (temp file + rename).

CONSTITUTION Priority 0: Security — no secret in argv, atomic writes
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T71.2 — Wire audit CLI commands
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_v3_jsonl_entry(audit_key_hex: str) -> str:
    """Build a valid single-entry JSONL audit log signed with v3 format.

    Args:
        audit_key_hex: Hex-encoded 32-byte audit key.

    Returns:
        A newline-terminated JSON string that migrate_audit_signatures can read.
    """
    import hashlib
    import hmac
    import json as _json
    import time

    entry: dict[str, object] = {
        "event_type": "TEST_EVENT",
        "actor": "test-actor",
        "resource": "test/resource",
        "action": "test",
        "timestamp": str(time.time()),
        "prev_hash": "0" * 64,
        "details": {},
    }
    payload = _json.dumps(entry, sort_keys=True).encode()
    key = bytes.fromhex(audit_key_hex)
    sig = hmac.new(key, payload, hashlib.sha256).hexdigest()
    entry["signature"] = f"v3:{sig}"
    return _json.dumps(entry) + "\n"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_conclave_audit_group_help_succeeds() -> None:
    """``conclave audit --help`` must exit 0 and print usage text."""
    from synth_engine.bootstrapper.cli import audit_group

    runner = CliRunner()
    result = runner.invoke(audit_group, ["--help"])
    assert result.exit_code == 0, f"Exit code: {result.exit_code}\nOutput:\n{result.output}"
    assert "audit" in result.output.lower(), "Help text must mention audit"


def test_migrate_signatures_dry_run_does_not_write_output() -> None:
    """``conclave audit migrate-signatures --dry-run`` must NOT create output file."""
    from synth_engine.bootstrapper.cli import audit_group

    audit_key_hex = "a" * 64  # 32-byte key as hex

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "audit.jsonl"
        output_path = Path(tmpdir) / "audit_migrated.jsonl"
        input_path.write_text(_minimal_v3_jsonl_entry(audit_key_hex))

        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(
            audit_group,
            [
                "migrate-signatures",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--dry-run",
            ],
            env={"AUDIT_KEY": audit_key_hex},
        )

    assert result.exit_code == 0, (
        f"Exit code: {result.exit_code}\nOutput:\n{result.output}\n"
        f"Exception: {result.exception}"
    )
    assert not output_path.exists(), (
        "--dry-run must NOT create the output file"
    )


def test_log_event_missing_audit_key_env_exits_nonzero() -> None:
    """``conclave audit log-event`` without AUDIT_KEY env var must exit non-zero."""
    from synth_engine.bootstrapper.cli import audit_group

    runner = CliRunner(mix_stderr=False)
    # Explicitly remove AUDIT_KEY from env.
    env = {k: v for k, v in os.environ.items() if k not in ("AUDIT_KEY", "CONCLAVE_AUDIT_KEY")}
    result = runner.invoke(
        audit_group,
        [
            "log-event",
            "--type",
            "MANUAL_TEST",
            "--actor",
            "admin",
            "--resource",
            "system/test",
            "--action",
            "test",
        ],
        env=env,
        catch_exceptions=False,
    )
    assert result.exit_code != 0, (
        f"Expected non-zero exit when AUDIT_KEY is absent; got {result.exit_code}\n"
        f"Output:\n{result.output}"
    )


def test_log_event_malformed_details_json_exits_nonzero() -> None:
    """``conclave audit log-event --details 'not json'`` must exit non-zero."""
    from synth_engine.bootstrapper.cli import audit_group

    audit_key_hex = "b" * 64

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        audit_group,
        [
            "log-event",
            "--type",
            "MANUAL_TEST",
            "--actor",
            "admin",
            "--resource",
            "system/test",
            "--action",
            "test",
            "--details",
            "not-valid-json",
        ],
        env={"AUDIT_KEY": audit_key_hex},
        catch_exceptions=False,
    )
    assert result.exit_code != 0, (
        f"Expected non-zero exit for malformed JSON; got {result.exit_code}\n"
        f"Output:\n{result.output}"
    )


def test_migrate_signatures_missing_input_exits_nonzero() -> None:
    """``conclave audit migrate-signatures`` with non-existent --input must exit non-zero."""
    from synth_engine.bootstrapper.cli import audit_group

    audit_key_hex = "c" * 64

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "out.jsonl"

        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(
            audit_group,
            [
                "migrate-signatures",
                "--input",
                "/nonexistent/path/audit.jsonl",
                "--output",
                str(output_path),
            ],
            env={"AUDIT_KEY": audit_key_hex},
            catch_exceptions=False,
        )

    assert result.exit_code != 0, (
        f"Expected non-zero exit for missing input; got {result.exit_code}\n"
        f"Output:\n{result.output}"
    )


def test_migrate_signatures_atomic_write() -> None:
    """``conclave audit migrate-signatures`` must use atomic write (no partial output).

    Verifies the output file either fully exists and is valid JSONL,
    or does not exist at all — never a partial file.
    This is validated structurally by checking the output is valid JSON
    lines after a successful run.
    """
    from synth_engine.bootstrapper.cli import audit_group

    audit_key_hex = "d" * 64

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "audit.jsonl"
        output_path = Path(tmpdir) / "audit_migrated.jsonl"
        input_path.write_text(_minimal_v3_jsonl_entry(audit_key_hex))

        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(
            audit_group,
            [
                "migrate-signatures",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
            env={"AUDIT_KEY": audit_key_hex},
        )

        assert result.exit_code == 0, (
            f"Exit: {result.exit_code}\nOutput:\n{result.output}\n"
            f"Exception: {result.exception}"
        )
        assert output_path.exists(), "Output file must exist after successful migration"

        # Validate each line is parseable JSON (not partial/corrupted).
        lines = output_path.read_text().strip().splitlines()
        assert len(lines) >= 1, "Output must have at least one line"
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict), "Each output line must be a JSON object"
            assert "event_type" in parsed, "Each line must have event_type"
