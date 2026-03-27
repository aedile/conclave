"""Negative/attack tests for bcrypt error hardening in 401 response (T63.4).

Tests verify that:
- The 401 response body contains ONLY the static message "Invalid credentials",
  never bcrypt exception detail.
- Bcrypt exceptions are logged at DEBUG with exc_info=True, NOT at INFO or above.
- The same static message is used for wrong-password AND bcrypt error paths
  (no oracle: both paths are indistinguishable to the caller).
- The DEBUG log does NOT contain the user passphrase.

CONSTITUTION Priority 0: Security — no internal error detail in 401 responses
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T63.4 — Harden bcrypt Error Message in 401 Response
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# State isolation fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_auth_app(monkeypatch: pytest.MonkeyPatch) -> object:
    """Build a minimal FastAPI app with the auth router.

    Configures a non-production environment so startup validation
    does not require DATABASE_URL / AUDIT_KEY.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        A FastAPI app instance with the auth router registered.
    """
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-long-enough-32-chars")
    # Set a real bcrypt hash for "correct-passphrase"
    monkeypatch.setenv(
        "OPERATOR_CREDENTIALS_HASH",
        "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/L/Ldv5t.iifcXiJea",  # pragma: allowlist secret
    )

    from fastapi import FastAPI

    from synth_engine.bootstrapper.routers.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    return app


# ---------------------------------------------------------------------------
# ATTACK: 401 response body must contain ONLY static message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bcrypt_401_response_body_contains_only_static_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """401 response for wrong credentials must return a static message, no bcrypt detail.

    Arrange: configure a valid bcrypt hash; send wrong passphrase.
    Act: POST /auth/token with an incorrect passphrase.
    Assert: response body detail is the static 'Invalid credentials' string.
    Assert: response body does NOT contain bcrypt exception text.

    CONSTITUTION Priority 0: no internal error detail in auth failure responses.
    """
    import bcrypt as _bcrypt
    from httpx import ASGITransport, AsyncClient

    from synth_engine.shared.settings import get_settings

    # Set a real bcrypt hash so verify_operator_credentials() calls bcrypt.checkpw()
    real_hash = _bcrypt.hashpw(b"correct-passphrase", _bcrypt.gensalt()).decode()
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-long-enough-32-chars")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", real_hash)
    get_settings.cache_clear()

    from fastapi import FastAPI

    from synth_engine.bootstrapper.routers.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/auth/token",
            json={"username": "operator", "passphrase": "wrong-passphrase"},
        )

    assert response.status_code == 401, f"Expected 401; got {response.status_code}"
    body = response.json()
    # Must contain "Invalid credentials" or similar static message — NOT bcrypt internals
    detail: str = body.get("detail", "")
    assert "Invalid credentials" in detail, (
        f"401 body must contain 'Invalid credentials'; got: {detail!r}"
    )


@pytest.mark.asyncio
async def test_bcrypt_error_401_response_body_contains_only_static_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When bcrypt raises an exception, 401 must return same static message.

    Arrange: patch bcrypt.checkpw to raise ValueError (malformed hash).
    Act: POST /auth/token.
    Assert: 401 response detail is the static 'Invalid credentials' string.
    Assert: response does NOT contain the exception type or message text.

    This prevents a bcrypt error oracle: callers cannot distinguish a wrong
    password from a bcrypt library error via the response body.
    """
    import bcrypt as _bcrypt
    from httpx import ASGITransport, AsyncClient

    from synth_engine.shared.settings import get_settings

    real_hash = _bcrypt.hashpw(b"correct-passphrase", _bcrypt.gensalt()).decode()
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-long-enough-32-chars")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", real_hash)
    get_settings.cache_clear()

    from fastapi import FastAPI

    from synth_engine.bootstrapper.routers.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    # Patch bcrypt.checkpw to simulate a bcrypt library error
    with patch(
        "synth_engine.bootstrapper.dependencies.auth._bcrypt.checkpw",
        side_effect=ValueError("Invalid salt: bcrypt internal error detail"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/token",
                json={"username": "operator", "passphrase": "any-passphrase"},
            )

    assert response.status_code == 401, f"Expected 401 on bcrypt error; got {response.status_code}"
    body = response.json()
    detail = body.get("detail", "")
    # Static message only — no bcrypt exception detail
    assert "Invalid credentials" in detail, (
        f"Bcrypt error 401 must contain 'Invalid credentials'; got: {detail!r}"
    )
    assert "bcrypt internal error detail" not in detail, (
        f"Bcrypt exception text must NOT appear in 401 response; got: {detail!r}"
    )
    assert "Invalid salt" not in detail, (
        f"Bcrypt exception type must NOT appear in 401 response; got: {detail!r}"
    )


@pytest.mark.asyncio
async def test_wrong_password_and_bcrypt_error_return_identical_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong password and bcrypt error must produce indistinguishable 401 responses.

    An oracle vulnerability exists if a bcrypt error produces a different
    response than a wrong password — callers could enumerate valid hashes.

    Arrange: send wrong password (normal path); then send any password with
    bcrypt.checkpw patched to raise (error path).
    Assert: both responses have status_code=401 and identical 'detail' content.
    """
    import bcrypt as _bcrypt
    from httpx import ASGITransport, AsyncClient

    from synth_engine.shared.settings import get_settings

    real_hash = _bcrypt.hashpw(b"correct-passphrase", _bcrypt.gensalt()).decode()
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-long-enough-32-chars")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", real_hash)

    from fastapi import FastAPI

    from synth_engine.bootstrapper.routers.auth import router as auth_router

    # --- Wrong password path ---
    get_settings.cache_clear()
    app1 = FastAPI()
    app1.include_router(auth_router)
    async with AsyncClient(transport=ASGITransport(app=app1), base_url="http://test") as client:
        r_wrong = await client.post(
            "/auth/token",
            json={"username": "operator", "passphrase": "definitely-wrong"},
        )

    # --- bcrypt error path ---
    get_settings.cache_clear()
    app2 = FastAPI()
    app2.include_router(auth_router)
    with patch(
        "synth_engine.bootstrapper.dependencies.auth._bcrypt.checkpw",
        side_effect=ValueError("bcrypt lib error"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
            r_error = await client.post(
                "/auth/token",
                json={"username": "operator", "passphrase": "any-passphrase"},
            )

    assert r_wrong.status_code == 401, f"Wrong password must return 401; got {r_wrong.status_code}"
    assert r_error.status_code == 401, f"Bcrypt error must return 401; got {r_error.status_code}"
    detail_wrong = r_wrong.json().get("detail", "")
    detail_error = r_error.json().get("detail", "")
    assert detail_wrong == detail_error, (
        f"Wrong password and bcrypt error must produce identical 401 detail; "
        f"wrong={detail_wrong!r}, error={detail_error!r}"
    )


# ---------------------------------------------------------------------------
# ATTACK: bcrypt exception logged at DEBUG, not INFO or above
# ---------------------------------------------------------------------------


def test_bcrypt_exception_logged_at_debug_not_info(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When bcrypt.checkpw raises, exception must be logged at DEBUG, not INFO/WARNING/ERROR.

    Logging at INFO or above would surface error details in production log aggregators.
    DEBUG is acceptable because debug-level logging is disabled by default in production.

    Arrange: set a real bcrypt hash; patch checkpw to raise.
    Act: call verify_operator_credentials() directly.
    Assert: a DEBUG record is present for the auth module logger.
    Assert: no INFO/WARNING/ERROR record is present for the bcrypt error.
    """
    import bcrypt as _bcrypt

    from synth_engine.shared.settings import get_settings

    real_hash = _bcrypt.hashpw(b"correct-passphrase", _bcrypt.gensalt()).decode()
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", real_hash)
    get_settings.cache_clear()

    from synth_engine.bootstrapper.dependencies.auth import verify_operator_credentials

    auth_logger = "synth_engine.bootstrapper.dependencies.auth"
    with caplog.at_level(logging.DEBUG, logger=auth_logger):
        with patch(
            "synth_engine.bootstrapper.dependencies.auth._bcrypt.checkpw",
            side_effect=ValueError("bcrypt hash error for test"),
        ):
            result = verify_operator_credentials("any-passphrase")

    assert result is False, "verify_operator_credentials must return False on bcrypt error"

    # Must have at least one DEBUG record
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debug_records) >= 1, (
        f"Bcrypt error must be logged at DEBUG level; got records: "
        f"{[(r.levelname, r.message) for r in caplog.records]}"
    )

    # Must NOT have INFO/WARNING/ERROR records for this bcrypt error
    elevated_records = [
        r
        for r in caplog.records
        if r.levelno >= logging.INFO
        and r.name == auth_logger
        and "bcrypt" in r.message.lower()
    ]
    assert len(elevated_records) == 0, (
        f"Bcrypt error must NOT be logged at INFO/WARNING/ERROR; "
        f"got elevated records: {[(r.levelname, r.message) for r in elevated_records]}"
    )


def test_bcrypt_exception_logged_with_exc_info(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When bcrypt.checkpw raises, the DEBUG log must include exc_info=True.

    exc_info=True preserves the full traceback in the log record, allowing
    post-incident forensics without leaking the exception into response bodies.

    Arrange: patch checkpw to raise; set up caplog at DEBUG.
    Act: call verify_operator_credentials().
    Assert: the DEBUG record has exc_info (traceback attached).
    """
    import bcrypt as _bcrypt

    from synth_engine.shared.settings import get_settings

    real_hash = _bcrypt.hashpw(b"correct-passphrase", _bcrypt.gensalt()).decode()
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", real_hash)
    get_settings.cache_clear()

    from synth_engine.bootstrapper.dependencies.auth import verify_operator_credentials

    auth_logger = "synth_engine.bootstrapper.dependencies.auth"
    with caplog.at_level(logging.DEBUG, logger=auth_logger):
        with patch(
            "synth_engine.bootstrapper.dependencies.auth._bcrypt.checkpw",
            side_effect=ValueError("exc_info test error"),
        ):
            verify_operator_credentials("any-passphrase")

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debug_records) >= 1, "Expected at least one DEBUG record"
    # exc_info is set when the record has an exc_info tuple that is not (None, None, None)
    record_with_exc = [r for r in debug_records if r.exc_info and r.exc_info[0] is not None]
    assert len(record_with_exc) >= 1, (
        f"DEBUG record must have exc_info=True (traceback attached); "
        f"got debug records: {[(r.message, r.exc_info) for r in debug_records]}"
    )


def test_bcrypt_debug_log_does_not_contain_passphrase(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DEBUG log for a bcrypt error must NOT contain the user's passphrase.

    CONSTITUTION Priority 0: credentials must never appear in logs.

    Arrange: configure a recognizable passphrase; patch checkpw to raise.
    Act: call verify_operator_credentials().
    Assert: caplog.text does not contain the passphrase.
    """
    import bcrypt as _bcrypt

    from synth_engine.shared.settings import get_settings

    real_hash = _bcrypt.hashpw(b"correct-passphrase", _bcrypt.gensalt()).decode()
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", real_hash)
    get_settings.cache_clear()

    from synth_engine.bootstrapper.dependencies.auth import verify_operator_credentials

    sentinel_passphrase = "SENTINEL_SECRET_PASSPHRASE_MUST_NOT_APPEAR_IN_LOG"
    auth_logger = "synth_engine.bootstrapper.dependencies.auth"
    with caplog.at_level(logging.DEBUG, logger=auth_logger):
        with patch(
            "synth_engine.bootstrapper.dependencies.auth._bcrypt.checkpw",
            side_effect=ValueError("bcrypt error"),
        ):
            verify_operator_credentials(sentinel_passphrase)

    assert sentinel_passphrase not in caplog.text, (
        f"Passphrase must NOT appear in any log output; "
        f"found in: {caplog.text!r}"
    )
