"""Attack tests for PII logging in the auth router (T66.1).

Tests verify that the POST /auth/token endpoint never logs the operator
username in plaintext — whether the attempt succeeds or fails.

CONSTITUTION Priority 0: Security — PII must not appear in logs.
Task: T66.1 — Fix PII Logging in Auth Router.

Negative/attack tests (committed before feature tests per Rule 22):
- auth failure WARNING log must not contain the raw username.
- auth success INFO log must not contain the raw username.
- the opaque identifier must be HMAC-keyed, not a plain SHA-256 hash.
- username longer than 255 characters must be rejected with 422.
- opaque identifier is deterministic for the same username.
- opaque identifier differs for different usernames.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Generator

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    """Clear LRU cache before and after each test."""
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Minimal TestClient with only the auth router mounted.

    Uses a bare FastAPI() app (not create_app()) to avoid the lifespan
    startup needing a real database.
    """
    passphrase = b"test-passphrase"
    hashed = bcrypt.hashpw(passphrase, bcrypt.gensalt()).decode()

    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-32-characters-long!")
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", hashed)
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.routers.auth import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Attack tests — must all FAIL (RED) before T66.1 implementation
# ---------------------------------------------------------------------------


def test_auth_failure_warning_log_contains_no_pii(
    auth_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """WARNING log on failed authentication must NOT contain the raw username.

    Security requirement: PII (username) must never appear in log output,
    even when the authentication attempt fails.
    """
    test_username = "secret-operator-alice"

    with caplog.at_level(logging.WARNING, logger="synth_engine.bootstrapper.routers.auth"):
        response = auth_client.post(
            "/auth/token",
            json={"username": test_username, "passphrase": "wrong-passphrase"},
        )

    assert response.status_code == 401
    for record in caplog.records:
        assert test_username not in record.getMessage(), (
            f"Username {test_username!r} found in log record: {record.getMessage()!r}"
        )


def test_auth_success_info_log_contains_no_pii(
    auth_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """INFO log on successful token issuance must NOT contain the raw username.

    Security requirement: PII (username) must never appear in log output,
    even on the happy path.
    """
    test_username = "secret-operator-bob"

    with caplog.at_level(logging.INFO, logger="synth_engine.bootstrapper.routers.auth"):
        response = auth_client.post(
            "/auth/token",
            json={"username": test_username, "passphrase": "test-passphrase"},
        )

    assert response.status_code == 200
    for record in caplog.records:
        assert test_username not in record.getMessage(), (
            f"Username {test_username!r} found in log record: {record.getMessage()!r}"
        )


def test_auth_log_opaque_identifier_is_keyed_hash_not_plain_sha256(
    auth_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The opaque log identifier must NOT be a plain SHA-256 of the username.

    Using a keyed HMAC ensures the identifier cannot be reversed without the
    audit key — a plain SHA-256 can be reversed via rainbow table or brute force
    on a short username space.
    """
    test_username = "operator-carol"
    plain_sha256_prefix = hashlib.sha256(test_username.encode()).hexdigest()[:12]

    with caplog.at_level(logging.INFO, logger="synth_engine.bootstrapper.routers.auth"):
        auth_client.post(
            "/auth/token",
            json={"username": test_username, "passphrase": "test-passphrase"},
        )

    # At least one log record should exist
    auth_records = [r for r in caplog.records if r.name == "synth_engine.bootstrapper.routers.auth"]
    assert len(auth_records) >= 1, "Expected at least one log record from auth router"

    for record in auth_records:
        msg = record.getMessage()
        assert plain_sha256_prefix not in msg, (
            f"Log record contains plain SHA-256 prefix {plain_sha256_prefix!r}, "
            f"which indicates the opaque identifier is not HMAC-keyed: {msg!r}"
        )


def test_auth_token_rejects_username_exceeding_max_length(
    auth_client: TestClient,
) -> None:
    """Username exceeding 255 characters must be rejected with HTTP 422.

    Prevents DoS via oversized username in hash computation and truncation
    ambiguities in log correlation.
    """
    oversized_username = "a" * 256

    response = auth_client.post(
        "/auth/token",
        json={"username": oversized_username, "passphrase": "test-passphrase"},
    )

    assert response.status_code == 422, (
        f"Expected 422 for username > 255 chars, got {response.status_code}"
    )


def test_auth_log_identifier_stable_for_same_username(
    auth_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Opaque identifier must be deterministic for the same username.

    SIEM correlation requires that the same operator always maps to the same
    opaque token so that multiple log entries from one session can be correlated.
    """
    test_username = "operator-dave"

    identifiers: list[str] = []
    for _ in range(2):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="synth_engine.bootstrapper.routers.auth"):
            auth_client.post(
                "/auth/token",
                json={"username": test_username, "passphrase": "test-passphrase"},
            )
        auth_records = [
            r for r in caplog.records if r.name == "synth_engine.bootstrapper.routers.auth"
        ]
        assert len(auth_records) >= 1
        identifiers.append(auth_records[-1].getMessage())

    assert identifiers[0] == identifiers[1], (
        f"Opaque identifier is not deterministic: {identifiers[0]!r} != {identifiers[1]!r}"
    )


def test_auth_log_identifier_differs_for_different_usernames(
    auth_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Opaque identifier must differ between different usernames.

    If two operators share the same opaque token, SIEM alerts cannot
    distinguish between them, defeating the purpose of the identifier.
    """
    messages: dict[str, str] = {}
    for username in ("operator-eve", "operator-frank"):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="synth_engine.bootstrapper.routers.auth"):
            auth_client.post(
                "/auth/token",
                json={"username": username, "passphrase": "test-passphrase"},
            )
        auth_records = [
            r for r in caplog.records if r.name == "synth_engine.bootstrapper.routers.auth"
        ]
        assert len(auth_records) >= 1
        messages[username] = auth_records[-1].getMessage()

    assert messages["operator-eve"] != messages["operator-frank"], (
        "Opaque identifiers must differ for different usernames; "
        f"both mapped to: {messages['operator-eve']!r}"
    )
