"""Advisory drain validation tests for T37.2.

Validates fixes for:
- ADV-P34-01: operator_error_response() logs str(exc) without safe_error_msg() wrapping.
- ADV-P34-02: PIIFilter referenced in agent docs does not exist in src/.
- ADV-P36-01: config_validation.py uses os.environ.get() directly instead of get_settings().

CONSTITUTION Priority 0: Security — prevent sensitive exception content in server logs.
CONSTITUTION Priority 3: TDD — RED phase (all tests must fail before implementation).
Task: T37.2 — Drain ADV-P34-01, ADV-P34-02, ADV-P36-01
"""

from __future__ import annotations

import ast
import logging
import pathlib

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# ADV-P34-01: operator_error_response() must use safe_error_msg() for log output
# ---------------------------------------------------------------------------


class TestOperatorErrorResponseLogSanitization:
    """ADV-P34-01: server-side WARNING log must pass exc message through safe_error_msg."""

    def test_operator_error_response_sanitizes_exception_in_log(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """operator_error_response() must pass exc message through safe_error_msg().

        When a domain exception message contains a filesystem path or other
        sensitive detail, the server-side WARNING log must not expose the raw
        str(exc) value.  Instead it must log a sanitized version produced by
        safe_error_msg().

        Arrange: create a domain exception whose str() contains a UNIX path.
        Act:     call operator_error_response(exc).
        Assert:  the raw path does NOT appear in the WARNING log record.
        """
        from synth_engine.bootstrapper.errors.formatter import operator_error_response
        from synth_engine.shared.exceptions import BudgetExhaustionError

        sensitive_path = "/var/lib/postgresql/data/pg_hba.conf"
        exc = BudgetExhaustionError(f"Exceeded budget reading {sensitive_path}")

        with caplog.at_level(
            logging.WARNING,
            logger="synth_engine.bootstrapper.errors.formatter",
        ):
            operator_error_response(exc)

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_messages, "A WARNING log record must be emitted"
        # The raw sensitive path must NOT appear in the warning log
        assert not any(sensitive_path in msg for msg in warning_messages), (
            f"Raw sensitive path '{sensitive_path}' must not appear in the WARNING log. "
            f"Got: {warning_messages}"
        )

    def test_operator_error_response_log_contains_exception_class_name(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """WARNING log must still identify the exception class name after sanitization.

        The class name is not sensitive — it is safe to log and aids debugging.
        """
        from synth_engine.bootstrapper.errors.formatter import operator_error_response
        from synth_engine.shared.exceptions import BudgetExhaustionError

        exc = BudgetExhaustionError("epsilon budget gone")

        with caplog.at_level(
            logging.WARNING,
            logger="synth_engine.bootstrapper.errors.formatter",
        ):
            operator_error_response(exc)

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("BudgetExhaustionError" in msg for msg in warning_messages), (
            "WARNING log must include the exception class name for operator diagnostics. "
            f"Got: {warning_messages}"
        )

    def test_operator_error_response_source_uses_safe_error_msg(self) -> None:
        """formatter.py source code must call safe_error_msg() when logging exc message.

        This is a static analysis assertion to enforce the implementation contract:
        any logging call in operator_error_response that logs str(exc) must wrap
        it with safe_error_msg().

        Reads the source, parses the AST, finds the _logger.warning call inside
        operator_error_response and asserts the exc argument is wrapped.
        """
        formatter_path = (
            _PROJECT_ROOT / "src" / "synth_engine" / "bootstrapper" / "errors" / "formatter.py"
        )
        source = formatter_path.read_text()
        assert "safe_error_msg" in source, (
            "formatter.py must import and call safe_error_msg() — "
            "raw str(exc) must not be passed directly to _logger.warning()"
        )


# ---------------------------------------------------------------------------
# ADV-P34-02: PIIFilter must not be referenced in committed source / agent docs
# ---------------------------------------------------------------------------


class TestPIIFilterReferencesRemoved:
    """ADV-P34-02: stale PIIFilter references must be removed from agent docs."""

    def test_devops_reviewer_agent_does_not_reference_pii_filter(self) -> None:
        """devops-reviewer.md must not reference non-existent PIIFilter.

        The PIIFilter logging handler does not exist in src/.  Checklist
        items referencing it mislead reviewers into expecting a guard that
        is not there.  The reference must be removed or replaced with
        accurate documentation.
        """
        agent_path = _PROJECT_ROOT / ".claude" / "agents" / "devops-reviewer.md"
        content = agent_path.read_text()
        # Neither the check description nor the body should mention PIIFilter
        assert "PIIFilter" not in content, (
            "devops-reviewer.md must not reference PIIFilter — "
            "this logging handler does not exist in src/. "
            "Remove or replace the stale reference."
        )

    def test_no_pii_filter_in_source_code(self) -> None:
        """No Python source file in src/ may reference PIIFilter.

        Confirms PIIFilter is not a dead import or phantom reference in
        production code — it simply does not exist, so no src/ file should
        name it.
        """
        src_dir = _PROJECT_ROOT / "src"
        for py_file in src_dir.rglob("*.py"):
            content = py_file.read_text()
            assert "PIIFilter" not in content, (
                f"{py_file.relative_to(_PROJECT_ROOT)} references PIIFilter "
                f"which does not exist in src/. Remove the reference."
            )


# ---------------------------------------------------------------------------
# ADV-P36-01: config_validation.py must not use os.environ.get() directly
# ---------------------------------------------------------------------------


class TestConfigValidationNoDirectEnvAccess:
    """ADV-P36-01: validate_config() must use get_settings() not os.environ.get()."""

    def test_config_validation_has_no_os_environ_get_calls(self) -> None:
        """config_validation.py must not call os.environ.get() directly.

        The T36.1 centralization goal requires all environment variable access
        to flow through the ConclaveSettings singleton (get_settings()).
        Direct os.environ.get() calls bypass the single source of truth.

        This test reads the source file and parses it as an AST to detect
        any Call node whose function is the attribute chain os.environ.get.
        """
        config_validation_path = (
            _PROJECT_ROOT / "src" / "synth_engine" / "bootstrapper" / "config_validation.py"
        )
        source = config_validation_path.read_text()
        tree = ast.parse(source)

        offending_calls: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match os.environ.get(...)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "os"
            ):
                offending_calls.append(node.lineno)

        assert not offending_calls, (
            f"config_validation.py still calls os.environ.get() on line(s) "
            f"{offending_calls}. Replace with get_settings() access per T36.1."
        )

    def test_config_validation_missing_vars_check_uses_settings(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """validate_config() must still detect missing DATABASE_URL via get_settings().

        After replacing os.environ.get() with settings access, the functional
        contract must be preserved: missing DATABASE_URL still causes SystemExit.
        This regression guard ensures the refactoring did not break detection.
        """
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("CONCLAVE_ENV", raising=False)
        get_settings.cache_clear()

        from synth_engine.bootstrapper.config_validation import validate_config

        with pytest.raises(SystemExit) as exc_info:
            validate_config()

        assert "DATABASE_URL" in str(exc_info.value)

    def test_config_validation_ssl_warning_uses_settings(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """CONCLAVE_SSL_REQUIRED=false warning must work after os.environ.get() removal.

        After replacing os.environ.get("CONCLAVE_SSL_REQUIRED", ...) with
        get_settings().conclave_ssl_required, the SSL override warning must
        still be emitted correctly.
        """
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("AUDIT_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "cafecafecafecafecafecafecafecafe")
        monkeypatch.setenv("MASKING_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
        monkeypatch.setenv("CONCLAVE_SSL_REQUIRED", "false")
        monkeypatch.delenv("CONCLAVE_ENV", raising=False)
        get_settings.cache_clear()

        from synth_engine.bootstrapper.config_validation import validate_config

        with caplog.at_level(
            logging.WARNING,
            logger="synth_engine.bootstrapper.config_validation",
        ):
            result = validate_config()

        assert result is None
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("CONCLAVE_SSL_REQUIRED=false" in msg for msg in warning_messages), (
            f"Expected WARNING with 'CONCLAVE_SSL_REQUIRED=false'. Got: {warning_messages}"
        )
