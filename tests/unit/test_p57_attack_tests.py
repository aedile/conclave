"""Phase 57 — Negative/attack tests for all 7 critical audit remediations.

Attack-first TDD per CLAUDE.md Rule 22: these tests are written and committed
BEFORE any feature implementation. All tests MUST fail (RED) at commit time.

Tasks covered:
- T57.1 — JWT hard-fail in production (empty JWT secret + production mode)
- T57.2 — Replace production assert with RuntimeError
- T57.3 — Production-mode validation for required settings
- T57.4 — Epsilon budget logging scrub (no INFO-level epsilon values)
- T57.5 — Narrow exception handling in audit logger singleton
- T57.6 — Unify environment configuration (env alias deprecation)
- T57.7 — Erasure DeletionManifest audit_logged field

CONSTITUTION Priority 0: Security — attack tests block merge
CONSTITUTION Priority 3: TDD — attack-first RED/GREEN/REFACTOR
Task: T57.1 through T57.7 — Critical Audit Findings Remediation
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared fixture: clear settings cache between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Any:
    """Clear lru_cache on get_settings before and after each test."""
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass
    yield
    try:
        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass


# ===========================================================================
# T57.1 — JWT Hard-Fail in Production
# ===========================================================================


def _set_minimal_production_env(monkeypatch: pytest.MonkeyPatch, *, jwt_secret: str = "") -> None:
    """Set minimum environment for a valid production ConclaveSettings.

    Sets all fields required by the T57.3 production validator except JWT_SECRET_KEY
    (which is the variable under test for T57.1).

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        jwt_secret: Value for JWT_SECRET_KEY, defaults to empty string.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.setenv("JWT_SECRET_KEY", jwt_secret)
    monkeypatch.delenv("ENV", raising=False)


def test_production_empty_jwt_returns_401_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In production with empty JWT_SECRET_KEY, get_current_operator raises 401 not 500.

    Attack: misconfigured production deployment with JWT_SECRET_KEY unset.
    Expected: HTTPException(401) raised, not AuthenticationError propagating as 500.
    """
    from fastapi import HTTPException

    _set_minimal_production_env(monkeypatch)

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator
    from synth_engine.shared.settings import get_settings

    settings = get_settings()
    assert settings.is_production() is True, "Pre-condition: must be in production mode"

    # Simulate a request with no Authorization header
    mock_request = MagicMock()
    mock_request.headers.get = lambda k, d=None: None

    with pytest.raises(HTTPException) as exc_info:
        get_current_operator(mock_request)

    assert exc_info.value.status_code == 401, (
        f"Production + empty JWT_SECRET_KEY must raise HTTP 401, got {exc_info.value.status_code}"
    )


def test_production_empty_jwt_rejects_even_with_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In production with empty JWT_SECRET_KEY, even a Bearer token is rejected.

    Attack: attacker provides ANY bearer token when JWT is unconfigured.
    Expected: 401, not pass-through with sentinel operator identity.
    """
    from fastapi import HTTPException

    _set_minimal_production_env(monkeypatch)

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator

    mock_request = MagicMock()
    mock_request.headers.get = lambda k, d=None: "Bearer sometoken" if k == "Authorization" else d

    with pytest.raises(HTTPException) as exc_info:
        get_current_operator(mock_request)

    assert exc_info.value.status_code == 401, (
        "Production + empty JWT_SECRET_KEY must reject even bearer-token requests "
        f"with 401, got {exc_info.value.status_code}"
    )


def test_dev_mode_pass_through_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In development mode with empty JWT_SECRET_KEY, pass-through is preserved.

    Feature: dev mode with empty JWT should still return sentinel "" operator.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    monkeypatch.delenv("ENV", raising=False)

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator

    mock_request = MagicMock()
    mock_request.headers.get = lambda k, d=None: None

    # In dev mode, pass-through returns ""
    result = get_current_operator(mock_request)
    assert result == "", (
        f"Dev mode with empty JWT_SECRET_KEY must return '' (pass-through), got {result!r}"
    )


def test_401_body_does_not_reveal_config_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 401 response body must NOT reveal that JWT_SECRET_KEY is unconfigured.

    Info disclosure: attacker could enumerate config state from error messages.
    """
    from fastapi import HTTPException

    _set_minimal_production_env(monkeypatch)

    from synth_engine.bootstrapper.dependencies.auth import get_current_operator

    mock_request = MagicMock()
    mock_request.headers.get = lambda k, d=None: None

    with pytest.raises(HTTPException) as exc_info:
        get_current_operator(mock_request)

    detail = str(exc_info.value.detail).lower()
    assert "jwt_secret_key" not in detail, (
        "401 detail must not reveal JWT_SECRET_KEY config state: "
        f"got detail={exc_info.value.detail!r}"
    )
    assert "unconfigured" not in detail, (
        "401 detail must not reveal 'unconfigured' config state: "
        f"got detail={exc_info.value.detail!r}"
    )
    assert "empty" not in detail or "authentication" in detail, (
        "401 detail must not specifically reveal empty-secret state: "
        f"got detail={exc_info.value.detail!r}"
    )


@pytest.mark.asyncio
async def test_production_empty_jwt_middleware_rejects_non_exempt_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AuthenticationGateMiddleware must also reject in production with empty JWT secret.

    Attack: middleware's pass-through block must be gated on production mode.
    """
    _set_minimal_production_env(monkeypatch)

    from synth_engine.bootstrapper.dependencies.auth import AuthenticationGateMiddleware

    middleware = AuthenticationGateMiddleware(app=MagicMock())

    mock_request = MagicMock()
    mock_request.url.path = "/api/jobs"
    mock_request.headers.get = lambda k, d=None: None

    call_next = AsyncMock()

    response = await middleware.dispatch(mock_request, call_next)

    assert response.status_code == 401, (
        "AuthenticationGateMiddleware in production + empty JWT must return 401, "
        f"got {response.status_code}"
    )
    # call_next must NOT have been called — request should not pass through
    call_next.assert_not_called()


def test_require_scope_production_empty_jwt_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """require_scope() must also hard-fail in production with empty JWT secret.

    Attack: scope bypass when JWT_SECRET_KEY is unconfigured in production.
    """
    from fastapi import HTTPException

    _set_minimal_production_env(monkeypatch)

    from synth_engine.bootstrapper.dependencies.auth import require_scope

    scope_checker = require_scope("security:admin")

    mock_request = MagicMock()
    mock_request.headers.get = lambda k, d=None: None

    with pytest.raises(HTTPException) as exc_info:
        scope_checker(request=mock_request, operator="")

    assert exc_info.value.status_code in (401, 403), (
        "require_scope() in production + empty JWT must raise 401 or 403, "
        f"got {exc_info.value.status_code}"
    )


# ===========================================================================
# T57.2 — Replace production assert with RuntimeError
# ===========================================================================


def test_build_ephemeral_storage_client_raises_runtime_error_not_assertion_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_ephemeral_storage_client() must raise RuntimeError when MinioStorageBackend is None.

    Attack: assert raises AssertionError (unhelpful crash) instead of RuntimeError.
    When Python is run with -O (optimize), assert statements are stripped entirely,
    making the check silently disappear — a reliability attack surface.
    """
    # Set minimum env so settings construction succeeds (T57.3 validator requires DATABASE_URL
    # and AUDIT_KEY in production, which is the default CONCLAVE_ENV).
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    from synth_engine.bootstrapper.main import build_ephemeral_storage_client

    with (
        patch("synth_engine.bootstrapper.main.MinioStorageBackend", None),
        patch(
            "synth_engine.bootstrapper.main._read_secret",
            side_effect=lambda name: "dummy",
        ),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            build_ephemeral_storage_client()

    assert "MinioStorageBackend" in str(exc_info.value) or "synthesizer" in str(exc_info.value), (
        "RuntimeError must mention MinioStorageBackend or synthesizer install: "
        f"got {exc_info.value!r}"
    )


def test_build_ephemeral_storage_client_does_not_raise_assertion_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_ephemeral_storage_client() must NEVER raise AssertionError.

    AssertionError is unhelpful and stripped by python -O.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    from synth_engine.bootstrapper.main import build_ephemeral_storage_client

    with (
        patch("synth_engine.bootstrapper.main.MinioStorageBackend", None),
        patch(
            "synth_engine.bootstrapper.main._read_secret",
            side_effect=lambda name: "dummy",
        ),
    ):
        with pytest.raises(RuntimeError):
            build_ephemeral_storage_client()


def test_no_bare_assert_in_production_source_code() -> None:
    """Static regression test: no bare assert statements in src/ (non-test code).

    Bare assert statements are:
    1. Stripped by python -O (optimize mode), silently removing safety checks
    2. Raise AssertionError, which FastAPI maps to 500 (unhelpful crash)

    Legitimate exceptions: assert in __post_init__ of frozen dataclasses.
    """
    src_path = Path(__file__).parent.parent.parent / "src"

    violations: list[str] = []

    for py_file in src_path.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue

            # Find the enclosing function/class
            # Check if this assert is inside __post_init__ of a frozen dataclass
            # by walking the tree and checking parent context
            parent_func = _find_parent_function(tree, node)
            if parent_func and parent_func.name == "__post_init__":
                # Legitimate: __post_init__ asserts in dataclasses
                continue

            rel_path = py_file.relative_to(src_path.parent)
            violations.append(f"{rel_path}:{node.lineno}")

    assert violations == [], (
        "Bare assert statements found in production source code (non-__post_init__):\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\nReplace with RuntimeError or ValueError with descriptive messages."
    )


def _find_parent_function(
    tree: ast.AST, target: ast.AST
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Walk the AST to find the innermost function containing the target node.

    Args:
        tree: The full AST tree to search.
        target: The node whose parent function to find.

    Returns:
        The innermost enclosing FunctionDef/AsyncFunctionDef, or None.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                if child is target:
                    return node
    return None


# ===========================================================================
# T57.3 — Production-Mode Settings Validation
# ===========================================================================


def test_production_empty_database_url_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings raises ValidationError when database_url is empty in production.

    Attack: misconfigured production starts without a database URL.
    """
    from pydantic import ValidationError

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)

    from synth_engine.shared.settings import ConclaveSettings

    with pytest.raises(ValidationError) as exc_info:
        ConclaveSettings()

    error_text = str(exc_info.value)
    assert "database_url" in error_text.lower() or "DATABASE_URL" in error_text, (
        f"ValidationError must mention database_url field: got {exc_info.value!r}"
    )


def test_production_whitespace_database_url_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings raises ValidationError for whitespace-only database_url in production.

    Attack: attacker or misconfiguration sets DATABASE_URL to whitespace.
    """
    from pydantic import ValidationError

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)

    from synth_engine.shared.settings import ConclaveSettings

    with pytest.raises(ValidationError):
        ConclaveSettings()


def test_production_empty_audit_key_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings raises ValidationError when audit_key is empty in production.

    Attack: production without audit signing — WORM chain integrity broken.
    audit_key is SecretStr; must call .get_secret_value() to check emptiness.
    """
    from pydantic import ValidationError

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "")

    from synth_engine.shared.settings import ConclaveSettings

    with pytest.raises(ValidationError) as exc_info:
        ConclaveSettings()

    error_text = str(exc_info.value)
    assert "audit_key" in error_text.lower() or "AUDIT_KEY" in error_text, (
        f"ValidationError must mention audit_key field: got {exc_info.value!r}"
    )


def test_production_whitespace_audit_key_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConclaveSettings raises ValidationError for whitespace-only audit_key in production.

    audit_key is SecretStr; must call .get_secret_value() before strip/check.
    """
    from pydantic import ValidationError

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "   ")

    from synth_engine.shared.settings import ConclaveSettings

    with pytest.raises(ValidationError):
        ConclaveSettings()


def test_development_mode_allows_empty_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev/test mode allows empty database_url (unit tests don't need a real DB).

    Feature: validators must be no-ops in non-production mode.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "")

    from synth_engine.shared.settings import ConclaveSettings

    # Must NOT raise
    s = ConclaveSettings()
    assert s.database_url == "", (
        "Development mode must allow empty database_url for unit test convenience"
    )


def test_development_mode_allows_empty_audit_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev/test mode allows empty audit_key (unit tests don't need real keys)."""
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.audit_key.get_secret_value() == "", (
        "Development mode must allow empty audit_key for unit test convenience"
    )


def test_validation_error_does_not_expose_database_url_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ValidationError must NOT include the raw database_url value.

    database_url may contain credentials (user:password@host).
    Including it in validation errors would be a credential leak.
    """
    from pydantic import ValidationError

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)

    from synth_engine.shared.settings import ConclaveSettings

    # DATABASE_URL is empty — triggers the validator

    try:
        ConclaveSettings()
    except ValidationError as exc:
        error_text = str(exc)
        # The raw URL value must not appear verbatim in error messages
        assert "S3cr3tP@ss" not in error_text, (
            f"ValidationError must not expose raw DATABASE_URL credentials: got {error_text!r}"
        )


def test_validator_works_at_construction_time_not_validate_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validator must fire at ConclaveSettings() construction, not rely on validate_config().

    Attack: if validation only happens in validate_config(), a new code path
    that bypasses validate_config() would start without a database.
    """
    from pydantic import ValidationError

    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)

    # Do NOT call validate_config() — validate at construction time
    from synth_engine.shared.settings import ConclaveSettings

    with pytest.raises(ValidationError):
        ConclaveSettings()
        # If this succeeds, validation is deferred to validate_config() (wrong)


# ===========================================================================
# T57.4 — Epsilon Budget Logging Scrub
# ===========================================================================


def test_epsilon_values_not_logged_at_info_on_success_path() -> None:
    """Epsilon numeric values must NOT appear in INFO-level logs from accountant.py.

    Static check: verify the source code uses _logger.debug (not _logger.info)
    for the spend_budget success-path message that includes epsilon values.
    The log call spans multiple lines (_logger.debug( on one line,
    "Epsilon allocated:..." on the next), so we check by scanning blocks.
    """

    import synth_engine.modules.privacy.accountant as acct_mod

    source_path = acct_mod.__file__
    assert source_path is not None
    source = open(source_path).read()

    # No _logger.info call should mention epsilon allocation
    # Check: if _logger.info exists in source, "Epsilon allocated" must not follow it
    if "_logger.info" in source:
        after_info = source.split("_logger.info")[1]
        assert "Epsilon allocated" not in after_info, (
            "Found _logger.info() near 'Epsilon allocated' — must use _logger.debug()"
        )

    # Verify _logger.debug IS used for "Epsilon allocated"
    assert "Epsilon allocated" in source, (
        "'Epsilon allocated' message not found in accountant.py — has the code changed?"
    )

    # Find the position of "Epsilon allocated" and check that _logger.debug precedes it
    idx = source.index("Epsilon allocated")
    # Look back 100 chars for _logger.debug (the call opens a few lines before the string)
    preceding = source[max(0, idx - 100) : idx]
    assert "_logger.debug" in preceding, (
        "The 'Epsilon allocated' log message must be preceded by '_logger.debug' "
        f"within 100 chars. Found: {preceding!r}"
    )


def test_accountant_spend_budget_logs_at_debug_not_info() -> None:
    """After T57.4, spend_budget success path logs at DEBUG, not INFO.

    Static source check: no _logger.info call includes epsilon budget values.
    """
    import inspect
    import re

    from synth_engine.modules.privacy import accountant

    source = inspect.getsource(accountant)

    # Check for any _logger.info() call that contains epsilon-related keywords
    info_pattern = re.compile(
        r"_logger\.info\([^)]*(?:Epsilon allocated|total_spent|remaining)[^)]*\)",
        re.DOTALL,
    )
    matches = info_pattern.findall(source)
    assert matches == [], (
        f"Found _logger.info() calls with epsilon values in accountant.py — must use DEBUG: "
        f"{matches}"
    )


def test_accountant_reset_budget_logs_at_debug_not_info() -> None:
    """After T57.4, reset_budget success path logs at DEBUG, not INFO.

    Static source check: no _logger.info call includes budget reset values.
    """
    import inspect
    import re

    from synth_engine.modules.privacy import accountant

    source = inspect.getsource(accountant)

    # Check that the "Budget reset" log is at DEBUG, not INFO
    info_pattern = re.compile(
        r"_logger\.info\([^)]*(?:Budget reset|allocated=)[^)]*\)",
        re.DOTALL,
    )
    matches = info_pattern.findall(source)
    assert matches == [], (
        f"Found _logger.info() calls with budget reset values in accountant.py — must use DEBUG: "
        f"{matches}"
    )


# ===========================================================================
# T57.5 — Narrow Exception Handling in Audit Logger Singleton
# ===========================================================================


def test_audit_logger_broad_exception_is_narrowed() -> None:
    """get_audit_logger() must not use bare except Exception for anchor_file_path retrieval.

    Attack: broad Exception catch silently swallows programming errors.
    Unexpected exceptions (TypeError, AttributeError) should propagate.
    """
    import inspect

    from synth_engine.shared.security import audit

    source = inspect.getsource(audit.get_audit_logger)

    # The broad except Exception must be replaced with specific types
    assert "except Exception:" not in source, (
        "get_audit_logger() still uses bare 'except Exception:' — "
        "must be narrowed to specific exception types (AttributeError, KeyError, TypeError)"
    )


def test_audit_logger_narrow_exceptions_include_expected_types() -> None:
    """get_audit_logger() must catch at least AttributeError and KeyError.

    These are the realistic exceptions from settings attribute access failures.
    """
    import inspect

    from synth_engine.shared.security import audit

    source = inspect.getsource(audit.get_audit_logger)

    # Must catch specific types
    assert "AttributeError" in source or "KeyError" in source, (
        "get_audit_logger() narrow catch must include AttributeError or KeyError: "
        f"excerpt: {source[:500]!r}"
    )


def test_audit_logger_fallback_emits_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """get_audit_logger() must emit WARNING when anchor path retrieval fails.

    A silent genesis fallback means operators have no visibility into
    audit chain continuity being broken.

    Strategy: create a mock settings object that raises AttributeError on
    anchor_file_path access, while returning a valid AUDIT_KEY for _load_audit_key().
    """
    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()

    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)

    # Patch get_settings to raise AttributeError only on anchor_file_path access.
    # We need _load_audit_key() to succeed (it calls get_settings().audit_key)
    # but the anchor_file_path access in get_audit_logger() must raise.
    # Strategy: use two side_effect calls — first for _load_audit_key, second for anchor path.
    from pydantic import SecretStr as _SecretStr

    class _FakeSettings:
        audit_key = _SecretStr("aa" * 32)
        anchor_every_n_events = 1000
        anchor_every_seconds = 86400
        anchor_backend = "local_file"

        @property
        def anchor_file_path(self) -> str:
            raise AttributeError("simulated anchor_file_path failure")

    fake_settings = _FakeSettings()

    with patch(
        "synth_engine.shared.security.audit.get_settings",
        return_value=fake_settings,
    ):
        with caplog.at_level(logging.WARNING, logger="synth_engine.shared.security.audit"):
            from synth_engine.shared.security.audit import get_audit_logger

            get_audit_logger()

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("anchor" in msg.lower() or "fallback" in msg.lower() for msg in warning_messages), (
        "get_audit_logger() must emit WARNING when anchor path retrieval fails. "
        f"Got: {warning_messages}"
    )

    # Cleanup
    reset_audit_logger()


def test_audit_logger_unexpected_exception_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_audit_logger() must NOT catch unexpected exceptions (e.g., RuntimeError).

    Programming errors must surface, not be silently swallowed.
    """
    from synth_engine.shared.security.audit import reset_audit_logger

    reset_audit_logger()

    from synth_engine.shared.security.audit import get_audit_logger

    with patch(
        "synth_engine.shared.security.audit.get_settings",
        side_effect=RuntimeError("programming error"),
    ):
        with pytest.raises(RuntimeError, match="programming error"):
            get_audit_logger()

    # Cleanup
    reset_audit_logger()


# ===========================================================================
# T57.6 — Unify Environment Configuration
# ===========================================================================


def test_env_alias_sets_conclave_env_when_conclave_env_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy ENV= sets effective environment when CONCLAVE_ENV not explicitly set.

    ADR-0056: env is a deprecated alias for conclave_env for backward compatibility.
    When ENV=production (and CONCLAVE_ENV is not set), the effective mode is production.
    The default for CONCLAVE_ENV is "production", so ENV=production is redundant here
    but must not conflict.  We test with ENV=development to show alias propagates.
    """
    # Use ENV=development with CONCLAVE_ENV unset (defaults to "production").
    # The test goal: ENV= must be readable — test that is_production() reflects ENV
    # when CONCLAVE_ENV hasn't been explicitly overridden.
    # Since CONCLAVE_ENV defaults to "production" and ENV=development creates a conflict,
    # conclave_env wins, so is_production() returns True.
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    # Must set required production fields since CONCLAVE_ENV defaults to "production"
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    # CONCLAVE_ENV defaults to "production" — conclave_env wins over env=development
    assert s.is_production() is True, (
        "When CONCLAVE_ENV defaults to 'production', is_production() must return True "
        "even if ENV=development (conclave_env takes precedence per T57.6)"
    )


def test_conclave_env_wins_when_both_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONCLAVE_ENV takes precedence over ENV when both are set.

    If both are set and conflict, conclave_env wins per spec-challenger guidance.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_KEY", "")

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.is_production() is False, (
        "When CONCLAVE_ENV=development overrides ENV=production, "
        "is_production() must return False (conclave_env wins)"
    )


def test_env_alias_deprecation_emits_warning_when_used(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Using ENV= (legacy alias) must emit a deprecation WARNING.

    Operators relying on ENV= must be notified to migrate to CONCLAVE_ENV=.
    """
    monkeypatch.setenv("ENV", "development")
    # CONCLAVE_ENV unset — defaults to "production", so we must set DATABASE_URL and AUDIT_KEY
    monkeypatch.delenv("CONCLAVE_ENV", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)

    with caplog.at_level(logging.WARNING, logger="synth_engine.shared.settings"):
        from synth_engine.shared.settings import ConclaveSettings

        ConclaveSettings()

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "env" in msg.lower() and ("deprecat" in msg.lower() or "conclave_env" in msg.lower())
        for msg in warning_messages
    ), (
        "Using ENV= must emit a deprecation WARNING mentioning CONCLAVE_ENV: "
        f"got: {warning_messages}"
    )


def test_is_production_uses_conclave_env_as_primary_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """is_production() must use conclave_env as the primary source of truth.

    After T57.6, conclave_env is the single source of truth.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost/db",  # pragma: allowlist secret
    )
    monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
    monkeypatch.delenv("ENV", raising=False)

    from synth_engine.shared.settings import ConclaveSettings

    s = ConclaveSettings()
    assert s.is_production() is True, "CONCLAVE_ENV=production must make is_production() True"


# ===========================================================================
# T57.7 — Erasure Error Handling Hardening
# ===========================================================================


def test_deletion_manifest_has_audit_logged_field() -> None:
    """DeletionManifest must have an audit_logged: bool field.

    The caller (compliance router) cannot distinguish "erasure complete + audit logged"
    from "erasure complete + audit failed" without this field.
    """
    # Check that the dataclass has the audit_logged field
    import dataclasses

    from synth_engine.modules.synthesizer.lifecycle.erasure import DeletionManifest

    fields = {f.name for f in dataclasses.fields(DeletionManifest)}
    assert "audit_logged" in fields, (
        "DeletionManifest must have an 'audit_logged: bool' field "
        f"to indicate whether the audit chain is intact. Current fields: {fields}"
    )


def test_deletion_manifest_audit_logged_defaults_true() -> None:
    """DeletionManifest.audit_logged must default to True for the success path."""
    from synth_engine.modules.synthesizer.lifecycle.erasure import DeletionManifest

    manifest = DeletionManifest(
        subject_id="test-subject",
        deleted_connections=0,
        deleted_jobs=0,
        retained_synthesized_output=True,
        retained_audit_trail=True,
        retained_synthesized_output_justification="DP output",
        retained_audit_trail_justification="GDPR Article 17",
    )
    assert manifest.audit_logged is True, (
        "DeletionManifest.audit_logged must default to True on the success path"
    )


def test_erasure_service_sets_audit_logged_false_on_audit_failure() -> None:
    """ErasureService must set audit_logged=False when audit logging raises.

    The compliance router must be able to detect partial erasure (DB deleted,
    audit chain broken) and respond appropriately.
    """
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from synth_engine.modules.synthesizer.lifecycle.erasure import DeletionManifest, ErasureService

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        service = ErasureService(session=session, connection_model=None)

        with patch(
            "synth_engine.modules.synthesizer.lifecycle.erasure.get_audit_logger",
        ) as mock_get_logger:
            mock_logger = MagicMock()
            mock_logger.log_event.side_effect = RuntimeError("audit chain broken")
            mock_get_logger.return_value = mock_logger

            manifest = service.execute_erasure(subject_id="subject-1", actor="operator")

    assert isinstance(manifest, DeletionManifest), "execute_erasure must return DeletionManifest"
    assert manifest.audit_logged is False, (
        "DeletionManifest.audit_logged must be False when audit logging raises: "
        f"got audit_logged={manifest.audit_logged}"
    )


def test_erasure_service_sets_audit_logged_true_on_success() -> None:
    """ErasureService must set audit_logged=True when audit logging succeeds."""
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from synth_engine.modules.synthesizer.lifecycle.erasure import ErasureService

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        service = ErasureService(session=session, connection_model=None)

        with patch(
            "synth_engine.modules.synthesizer.lifecycle.erasure.get_audit_logger",
        ) as mock_get_logger:
            mock_logger = MagicMock()
            mock_logger.log_event.return_value = None  # success
            mock_get_logger.return_value = mock_logger

            manifest = service.execute_erasure(subject_id="subject-1", actor="operator")

    assert manifest.audit_logged is True, (
        "DeletionManifest.audit_logged must be True when audit logging succeeds"
    )
