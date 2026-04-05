"""Attack/negative tests for input validation hardening (T67.1–T67.3).

Covers:
- T67.1: Settings key path parameter max_length=255
- T67.2: TokenRequest.passphrase max_length=1024
- T67.3: TLSCertificateError presence in OPERATOR_ERROR_MAP

ATTACK-FIRST TDD — these tests are written before the GREEN phase.
CONSTITUTION Priority 0: Security — unbounded inputs create DoS/log-injection vectors.

Negative Test Requirements (from spec-challenger):
- Settings key of 256+ chars must → 422
- Settings key of exactly 255 chars must → normal flow (not validation rejection)
- TokenRequest.passphrase of 1025+ chars must → 422
- TokenRequest.passphrase of exactly 1024 chars must → auth flow (not validation rejection)
- TLSCertificateError must be in OPERATOR_ERROR_MAP with status 400
- TLSCertificateError must appear in auto-derived _OPERATOR_ERROR_HANDLERS
- TLSCertificateError handler must produce RFC 7807 400 response
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared JWT secret used across fixtures
# ---------------------------------------------------------------------------

_TEST_JWT_SECRET = "test-jwt-secret-key-long-enough-for-hs256-algo"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a TestClient for the settings router using a bare FastAPI app.

    Uses CONCLAVE_ENV=development with an empty JWT_SECRET_KEY so that
    both ``get_current_operator`` and ``require_scope`` operate in
    development pass-through mode — auth is bypassed without mocking,
    letting validation behaviour be the focus of these tests.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        Synchronous TestClient wrapping the settings-router app.
    """
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from synth_engine.bootstrapper.dependencies.db import get_db_session
    from synth_engine.bootstrapper.routers.settings import router as settings_router
    from synth_engine.shared.settings import get_settings

    # Development pass-through: JWT_SECRET_KEY empty + explicit opt-in → auth bypassed entirely.
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "true")
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    # Use bare FastAPI — no create_app() to avoid task_queue module-level
    # get_settings() call that needs production env vars.
    app = FastAPI()
    app.include_router(settings_router)

    def _override_db() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_db

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a TestClient for the auth router using a bare FastAPI app.

    Uses a bare FastAPI() (not create_app()) to avoid the lifespan startup
    needing a real database — same pattern as test_auth_pii_logging_attack.py.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        Synchronous TestClient wrapping the auth-router app.
    """
    from synth_engine.bootstrapper.routers.auth import router
    from synth_engine.shared.settings import get_settings

    pw_hash = bcrypt.hashpw(b"correct-password", bcrypt.gensalt()).decode()
    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_JWT_SECRET)
    monkeypatch.setenv("OPERATOR_CREDENTIALS_HASH", pw_hash)
    monkeypatch.setenv("AUDIT_KEY", "a" * 64)
    get_settings.cache_clear()

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# T67.1 — Settings key path parameter max_length attacks
# ---------------------------------------------------------------------------


class TestSettingsKeyMaxLength:
    """Attack tests for unbounded settings key path parameter (ADV-P66-01).

    An unbounded key allows an attacker to submit arbitrarily large strings
    that end up stored in the database primary key column and written to logs.
    max_length=255 caps this to a sane upper bound.
    """

    def test_settings_key_rejects_oversized_key_on_put(self, settings_client: Any) -> None:
        """PUT /settings/{key} with a 256-char key must return 422.

        A key longer than 255 characters must be rejected by FastAPI path
        validation before the route handler is invoked. This prevents
        oversized strings from reaching the DB primary key or log entries.
        """
        oversized_key = "a" * 256
        response = settings_client.put(
            f"/settings/{oversized_key}",
            json={"value": "test-value"},
        )
        assert response.status_code == 422, (
            f"Expected 422 for 256-char key on PUT, got {response.status_code}"
        )

    def test_settings_key_rejects_oversized_key_on_get(self, settings_client: Any) -> None:
        """GET /settings/{key} with a 256-char key must return 422."""
        oversized_key = "b" * 256
        response = settings_client.get(f"/settings/{oversized_key}")
        assert response.status_code == 422, (
            f"Expected 422 for 256-char key on GET, got {response.status_code}"
        )

    def test_settings_key_rejects_oversized_key_on_delete(self, settings_client: Any) -> None:
        """DELETE /settings/{key} with a 256-char key must return 422."""
        oversized_key = "c" * 256
        response = settings_client.delete(f"/settings/{oversized_key}")
        assert response.status_code == 422, (
            f"Expected 422 for 256-char key on DELETE, got {response.status_code}"
        )

    def test_settings_key_accepts_max_length_key_on_put(self, settings_client: Any) -> None:
        """PUT /settings/{key} with exactly 255-char key must not be rejected by validation.

        A 255-char key is at the boundary and must pass validation — the route
        may respond with 200/201/404/500 but NOT 422 (validation failure).
        """
        max_key = "d" * 255
        response = settings_client.put(
            f"/settings/{max_key}",
            json={"value": "boundary-value"},
        )
        assert response.status_code != 422, (
            f"Expected non-422 for 255-char key (at boundary), got {response.status_code}"
        )

    def test_settings_key_accepts_max_length_key_on_get(self, settings_client: Any) -> None:
        """GET /settings/{key} with exactly 255-char key must not be rejected by validation."""
        max_key = "e" * 255
        response = settings_client.get(f"/settings/{max_key}")
        # Should be 404 (not found) or 200, never 422
        assert response.status_code != 422, (
            f"Expected non-422 for 255-char key on GET, got {response.status_code}"
        )

    def test_settings_key_accepts_max_length_key_on_delete(self, settings_client: Any) -> None:
        """DELETE /settings/{key} with exactly 255-char key must not be rejected by validation.

        A 255-char key is at the boundary and must pass validation — the route
        may respond with 200/204/404/500 but NOT 422 (validation failure).
        """
        max_key = "f" * 255
        response = settings_client.delete(f"/settings/{max_key}")
        assert response.status_code != 422, (
            f"Expected non-422 for 255-char key on DELETE (at boundary), got {response.status_code}"
        )

    def test_settings_key_rejects_very_long_key_on_put(self, settings_client: Any) -> None:
        """PUT /settings/{key} with a 1024-char key must return 422.

        Tests that rejection holds for significantly oversized keys, not only
        at the 256-char boundary.
        """
        very_long_key = "x" * 1024
        response = settings_client.put(
            f"/settings/{very_long_key}",
            json={"value": "test-value"},
        )
        assert response.status_code == 422, (
            f"Expected 422 for 1024-char key on PUT, got {response.status_code}"
        )


# ---------------------------------------------------------------------------
# T67.2 — TokenRequest.passphrase max_length attacks
# ---------------------------------------------------------------------------


class TestAuthTokenPassphraseMaxLength:
    """Attack tests for unbounded passphrase in TokenRequest (ADV-P66-02).

    Without max_length, an attacker can send a 1 MiB passphrase body.
    bcrypt truncates at 72 bytes, so the hash comparison is fast, but
    FastAPI still deserialises the entire body — a CPU/memory DoS vector.
    max_length=1024 caps the passphrase to a safe upper bound.
    """

    def test_auth_token_rejects_oversized_passphrase(self, auth_client: Any) -> None:
        """POST /auth/token with 1025-char passphrase must return 422.

        Validation must reject the oversized passphrase before bcrypt
        is ever invoked — preventing a CPU DoS via bcrypt on huge inputs.
        """
        oversized_passphrase = "p" * 1025
        response = auth_client.post(
            "/auth/token",
            json={"username": "operator", "passphrase": oversized_passphrase},
        )
        assert response.status_code == 422, (
            f"Expected 422 for 1025-char passphrase, got {response.status_code}"
        )

    def test_auth_token_rejects_very_oversized_passphrase(self, auth_client: Any) -> None:
        """POST /auth/token with 10 000-char passphrase must return 422."""
        very_long = "q" * 10_000
        response = auth_client.post(
            "/auth/token",
            json={"username": "operator", "passphrase": very_long},
        )
        assert response.status_code == 422, (
            f"Expected 422 for 10000-char passphrase, got {response.status_code}"
        )

    def test_auth_token_accepts_max_length_passphrase(self, auth_client: Any) -> None:
        """POST /auth/token with exactly 1024-char passphrase must not be rejected by validation.

        A 1024-char passphrase is at the boundary — it must pass validation
        (Pydantic accepts it) and reach the auth layer. The response may be
        401 (wrong credentials) but MUST NOT be 422 (validation rejection).
        """
        max_passphrase = "r" * 1024
        response = auth_client.post(
            "/auth/token",
            json={"username": "operator", "passphrase": max_passphrase},
        )
        # Validation must pass — response is 401 (wrong creds) not 422 (validation)
        assert response.status_code != 422, (
            f"Expected non-422 for 1024-char passphrase (at boundary), got {response.status_code}"
        )

    def test_auth_token_rejects_empty_passphrase(self, auth_client: Any) -> None:
        """POST /auth/token with empty passphrase must return 422 (min_length=1).

        The min_length=1 constraint is pre-existing; this test confirms it
        still holds after adding max_length=1024.
        """
        response = auth_client.post(
            "/auth/token",
            json={"username": "operator", "passphrase": ""},
        )
        assert response.status_code == 422, (
            f"Expected 422 for empty passphrase, got {response.status_code}"
        )


# ---------------------------------------------------------------------------
# T67.3 — TLSCertificateError in OPERATOR_ERROR_MAP
# ---------------------------------------------------------------------------


class TestTLSCertificateErrorMapping:
    """Tests for TLSCertificateError in OPERATOR_ERROR_MAP (ADV-P66-03).

    TLSCertificateError is a SynthEngineError subclass declared HTTP-safe in
    shared/exceptions.py but previously missing from OPERATOR_ERROR_MAP.
    Without the mapping it falls through to the 500 catch-all — wrong status
    and potentially leaks context.
    """

    def test_tls_certificate_error_in_operator_error_map(self) -> None:
        """TLSCertificateError must be present as a key in OPERATOR_ERROR_MAP."""
        from synth_engine.bootstrapper.errors.mapping import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import TLSCertificateError

        assert TLSCertificateError in OPERATOR_ERROR_MAP, (
            "TLSCertificateError must be in OPERATOR_ERROR_MAP (ADV-P66-03). "
            "It is a SynthEngineError subclass that would otherwise fall through "
            "to the 500 catch-all handler."
        )

    def test_tls_certificate_error_maps_to_400(self) -> None:
        """TLSCertificateError entry in OPERATOR_ERROR_MAP must have status_code=400.

        HTTP 400 (Bad Request) is the correct code: the client provided or the
        system was configured with an invalid certificate — a bad-input situation,
        not an internal server error.
        """
        from synth_engine.bootstrapper.errors.mapping import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import TLSCertificateError

        entry = OPERATOR_ERROR_MAP[TLSCertificateError]
        assert entry["status_code"] == 400, (
            f"TLSCertificateError must map to HTTP 400, got {entry['status_code']}"
        )

    def test_tls_certificate_error_entry_has_required_fields(self) -> None:
        """TLSCertificateError entry must have all required OperatorErrorEntry fields.

        All four fields (title, detail, status_code, type_uri) must be present
        and non-empty so any RFC 7807 handler can build a complete response.
        """
        from synth_engine.bootstrapper.errors.mapping import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import TLSCertificateError

        entry = OPERATOR_ERROR_MAP[TLSCertificateError]
        assert entry["title"] == "TLS Certificate Validation Failed"
        assert entry["detail"] == "TLS certificate validation failed."
        assert entry["status_code"] == 400
        assert isinstance(entry["type_uri"], str), "type_uri must be a string"

    def test_tls_certificate_error_in_operator_error_handlers(self) -> None:
        """TLSCertificateError must appear in _OPERATOR_ERROR_HANDLERS.

        Since _OPERATOR_ERROR_HANDLERS is derived from OPERATOR_ERROR_MAP.keys(),
        adding TLSCertificateError to the map auto-registers its HTTP handler.
        This test is the regression guard for that auto-derivation.
        """
        from synth_engine.bootstrapper.router_registry import _OPERATOR_ERROR_HANDLERS
        from synth_engine.shared.exceptions import TLSCertificateError

        assert TLSCertificateError in _OPERATOR_ERROR_HANDLERS, (
            "TLSCertificateError must be in _OPERATOR_ERROR_HANDLERS. "
            "Verify that _OPERATOR_ERROR_HANDLERS is derived from OPERATOR_ERROR_MAP.keys()."
        )

    @pytest.mark.asyncio
    async def test_tls_certificate_error_raises_400_through_middleware(self) -> None:
        """TLSCertificateError raised in a route must produce RFC 7807 400 response.

        End-to-end test: verifies the full handler chain from exception to
        HTTP response, including the RFC 7807 body shape.
        """
        from httpx import ASGITransport, AsyncClient

        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import TLSCertificateError

        app = create_app()

        @app.get("/test-tls-cert-error")
        async def _raise_tls_cert() -> None:
            raise TLSCertificateError("Certificate has expired: CN=example.com")

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/test-tls-cert-error")

        assert response.status_code == 400, (
            f"TLSCertificateError must produce HTTP 400, got {response.status_code}"
        )
        body = response.json()
        assert "type" in body, "Response must have 'type' field (RFC 7807)"
        assert "title" in body, "Response must have 'title' field (RFC 7807)"
        assert "status" in body, "Response must have 'status' field (RFC 7807)"
        assert "detail" in body, "Response must have 'detail' field (RFC 7807)"
        assert body["status"] == 400

    @pytest.mark.asyncio
    async def test_tls_certificate_error_detail_does_not_leak_certificate_content(
        self,
    ) -> None:
        """TLSCertificateError HTTP response must not echo the raw exception message.

        The raw exception message may contain certificate CN/SAN fields that are
        security-sensitive. The HTTP response must use the static detail from
        OPERATOR_ERROR_MAP, never str(exc).
        """
        from httpx import ASGITransport, AsyncClient

        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import TLSCertificateError

        sentinel = "SENTINEL-CERT-DETAIL-XYZ"
        app = create_app()

        @app.get("/test-tls-cert-no-leak")
        async def _raise_tls_sentinel() -> None:
            raise TLSCertificateError(f"Certificate error: {sentinel}")

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/test-tls-cert-no-leak")

        assert response.status_code == 400
        body_str = response.text
        assert sentinel not in body_str, (
            f"HTTP response must not contain the raw exception message sentinel: {sentinel}"
        )
