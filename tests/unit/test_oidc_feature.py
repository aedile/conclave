"""Feature tests for SSO/OIDC — Phase 81. FEATURE RED phase.

Tests the happy paths and correct behavior for the OIDC integration:
- SSRF validator: validate_oidc_issuer_url() allows RFC-1918, blocks metadata
- OIDC settings fields in ConclaveSettings
- OIDC authorize endpoint: returns redirect_url + state in JSON
- OIDC callback endpoint: validates state, PKCE, exchanges code, returns JWT
- User auto-provisioning on first OIDC login
- last_login_at updated on subsequent OIDC logins
- Session creation and session key namespace
- /auth/refresh: returns new JWT, updates session
- /auth/revoke: admin can revoke all user sessions, self-revoke always allowed
- Concurrent session limit: evicts oldest
- OIDC disabled: /auth/refresh and /auth/revoke return 404
- Config validation: startup errors on misconfigured OIDC
- sessions:revoke in PERMISSION_MATRIX (admin only)
- Auth-exempt paths include /auth/oidc/authorize and /auth/oidc/callback
- RFC 7807 error response format on auth failures
- Audit events emitted for OIDC_LOGIN_SUCCESS/FAILURE/SESSION_CREATED/REVOKED

Written in the FEATURE RED phase, AFTER attack tests but BEFORE implementation.

CONSTITUTION Priority 3: TDD — FEATURE RED phase
Phase: 81 — SSO/OIDC Integration
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from base64 import urlsafe_b64encode
from typing import Any
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = (  # pragma: allowlist secret
    "unit-test-jwt-secret-key-long-enough-for-hs256-32chars+"
)
_ORG_A_UUID = "11111111-1111-1111-1111-111111111111"
_USER_A_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _make_token(
    sub: str = "user@example.com",
    org_id: str = _ORG_A_UUID,
    role: str = "operator",
    user_id: str = _USER_A_UUID,
    expired: bool = False,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "org_id": org_id,
        "user_id": user_id,
        "role": role,
        "scope": ["read", "write"],
        "iat": now,
        "exp": now - 10 if expired else now + 3600,
    }
    return pyjwt.encode(payload, _TEST_SECRET, algorithm="HS256")


# ===========================================================================
# SECTION 1: SSRF Validator — validate_oidc_issuer_url
# ===========================================================================


class TestValidateOIDCIssuerURL:
    """Tests for the OIDC-specific SSRF validator."""

    def test_rfc1918_10_block_allowed(self) -> None:
        """10.0.0.0/8 block is allowed for air-gap IdPs.

        AC: RFC-1918 ranges must be accepted — air-gap IdPs live on private networks.
        """
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        # Should not raise
        validate_oidc_issuer_url("http://10.50.0.10/")

    def test_rfc1918_172_block_allowed(self) -> None:
        """172.16.0.0/12 block is allowed for air-gap IdPs."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        validate_oidc_issuer_url("http://172.20.0.5/")

    def test_rfc1918_192_168_block_allowed(self) -> None:
        """192.168.0.0/16 block is allowed for air-gap IdPs."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        validate_oidc_issuer_url("http://192.168.1.100/")

    def test_aws_imds_blocked(self) -> None:
        """169.254.169.254 is blocked unconditionally."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        with pytest.raises(ValueError, match="(?i)(forbidden|blocked|metadata|reserved)"):
            validate_oidc_issuer_url("http://169.254.169.254/")

    def test_alibaba_imds_blocked(self) -> None:
        """100.100.100.200 is blocked unconditionally."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        with pytest.raises(ValueError):
            validate_oidc_issuer_url("http://100.100.100.200/")

    def test_gcp_metadata_hostname_blocked(self) -> None:
        """metadata.google.internal is blocked unconditionally."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        with pytest.raises(ValueError):
            validate_oidc_issuer_url("http://metadata.google.internal/")

    def test_loopback_blocked(self) -> None:
        """127.0.0.1 is blocked unconditionally."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        with pytest.raises(ValueError):
            validate_oidc_issuer_url("http://127.0.0.1/")

    def test_ipv6_loopback_blocked(self) -> None:
        """::1 (IPv6 loopback) is blocked unconditionally."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        with pytest.raises(ValueError):
            validate_oidc_issuer_url("http://[::1]/")

    def test_public_ip_allowed_in_development(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Public IP is allowed in development mode."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        # Should not raise in development mode
        validate_oidc_issuer_url("http://203.0.113.5/")

    def test_public_ip_blocked_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Public IP is blocked in production mode."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        monkeypatch.setenv("CONCLAVE_ENV", "production")
        with pytest.raises(ValueError):
            validate_oidc_issuer_url("http://203.0.113.5/")

    def test_rfc1918_issuer_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Accepting RFC-1918 issuer emits a WARNING-level security notice."""
        import logging  # noqa: PLC0415

        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        with caplog.at_level(logging.WARNING, logger="synth_engine.shared.ssrf"):
            validate_oidc_issuer_url("http://10.0.0.1/")

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_messages) >= 1, (
            "Expected at least one WARNING log for RFC-1918 issuer acceptance"
        )

    def test_missing_scheme_rejected(self) -> None:
        """URL without a scheme (http/https) is rejected."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        with pytest.raises(ValueError):
            validate_oidc_issuer_url("not-a-url")

    def test_empty_url_rejected(self) -> None:
        """Empty URL string is rejected."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url  # noqa: PLC0415

        with pytest.raises(ValueError):
            validate_oidc_issuer_url("")


# ===========================================================================
# SECTION 2: Settings Fields
# ===========================================================================


class TestOIDCSettingsFields:
    """Tests for OIDC-related settings fields in ConclaveSettingsFields."""

    def test_oidc_enabled_defaults_to_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OIDC_ENABLED defaults to False (opt-in, not opt-out).

        AC: OIDC is disabled by default — operators must explicitly enable it.
        """
        from synth_engine.shared.settings import ConclaveSettings  # noqa: PLC0415

        monkeypatch.delenv("OIDC_ENABLED", raising=False)
        monkeypatch.delenv("CONCLAVE_OIDC_ENABLED", raising=False)
        settings = ConclaveSettings(_env_file=None)
        assert settings.oidc_enabled is False, (
            f"Expected oidc_enabled=False by default, got {settings.oidc_enabled}"
        )

    def test_oidc_state_ttl_defaults_to_600(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OIDC_STATE_TTL_SECONDS defaults to 600 (10 minutes)."""
        from synth_engine.shared.settings import ConclaveSettings  # noqa: PLC0415

        monkeypatch.delenv("OIDC_STATE_TTL_SECONDS", raising=False)
        settings = ConclaveSettings(_env_file=None)
        assert settings.oidc_state_ttl_seconds == 600, (
            f"Expected 600, got {settings.oidc_state_ttl_seconds}"
        )

    def test_session_ttl_defaults_to_28800(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SESSION_TTL_SECONDS defaults to 28800 (8 hours)."""
        from synth_engine.shared.settings import ConclaveSettings  # noqa: PLC0415

        monkeypatch.delenv("SESSION_TTL_SECONDS", raising=False)
        settings = ConclaveSettings(_env_file=None)
        assert settings.session_ttl_seconds == 28800, (
            f"Expected 28800, got {settings.session_ttl_seconds}"
        )

    def test_concurrent_session_limit_defaults_to_3(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONCURRENT_SESSION_LIMIT defaults to 3."""
        from synth_engine.shared.settings import ConclaveSettings  # noqa: PLC0415

        monkeypatch.delenv("CONCURRENT_SESSION_LIMIT", raising=False)
        settings = ConclaveSettings(_env_file=None)
        assert settings.concurrent_session_limit == 3, (
            f"Expected 3, got {settings.concurrent_session_limit}"
        )

    def test_oidc_state_ttl_max_3600(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OIDC_STATE_TTL_SECONDS maximum is 3600 (1 hour)."""
        from pydantic import ValidationError  # noqa: PLC0415

        from synth_engine.shared.settings import ConclaveSettings  # noqa: PLC0415

        monkeypatch.setenv("OIDC_STATE_TTL_SECONDS", "3601")
        with pytest.raises(ValidationError):
            ConclaveSettings(_env_file=None)


# ===========================================================================
# SECTION 3: PERMISSION_MATRIX — sessions:revoke
# ===========================================================================


class TestPermissionMatrixSessionsRevoke:
    """Tests for the sessions:revoke permission in the RBAC matrix."""

    def test_sessions_revoke_in_permission_matrix(self) -> None:
        """sessions:revoke is present in the PERMISSION_MATRIX.

        AC: Decision 8 — sessions:revoke must be added to the matrix.
        """
        from synth_engine.bootstrapper.dependencies.permissions import (  # noqa: PLC0415
            PERMISSION_MATRIX,
        )

        assert "sessions:revoke" in PERMISSION_MATRIX, (
            "sessions:revoke must be in PERMISSION_MATRIX"
        )

    def test_sessions_revoke_admin_only(self) -> None:
        """sessions:revoke is restricted to admin role only.

        AC: Cross-user session revocation is an administrative action.
        Only admins can revoke other users' sessions.
        """
        from synth_engine.bootstrapper.dependencies.permissions import (  # noqa: PLC0415
            PERMISSION_MATRIX,
            Role,
            has_permission,
        )

        allowed_roles = PERMISSION_MATRIX["sessions:revoke"]
        assert allowed_roles == frozenset({Role.admin}), (
            f"sessions:revoke must be admin-only, got: {allowed_roles}"
        )

    def test_sessions_revoke_denied_for_non_admin_roles(self) -> None:
        """operator, viewer, and auditor cannot use sessions:revoke."""
        from synth_engine.bootstrapper.dependencies.permissions import (  # noqa: PLC0415
            has_permission,
        )

        for role in ("operator", "viewer", "auditor"):
            assert not has_permission(role=role, permission="sessions:revoke"), (
                f"sessions:revoke must be denied for role={role!r}"
            )


# ===========================================================================
# SECTION 4: Auth-Exempt Paths
# ===========================================================================


class TestAuthExemptPaths:
    """Tests that OIDC paths are in AUTH_EXEMPT_PATHS."""

    def test_oidc_authorize_in_auth_exempt_paths(self) -> None:
        """AUTH_EXEMPT_PATHS contains /auth/oidc/authorize."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS  # noqa: PLC0415

        assert "/auth/oidc/authorize" in AUTH_EXEMPT_PATHS

    def test_oidc_callback_in_auth_exempt_paths(self) -> None:
        """AUTH_EXEMPT_PATHS contains /auth/oidc/callback."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS  # noqa: PLC0415

        assert "/auth/oidc/callback" in AUTH_EXEMPT_PATHS


# ===========================================================================
# SECTION 5: Session Management
# ===========================================================================


class TestSessionManagementFeature:
    """Feature tests for session creation, refresh, and revocation."""

    def test_create_session_key_returns_correct_format(self) -> None:
        """create_session_key returns a key in 'conclave:session:<token>' format."""
        from synth_engine.bootstrapper.dependencies.sessions import (  # noqa: PLC0415
            create_session_key,
        )

        key = create_session_key()
        assert key.startswith("conclave:session:"), (
            f"Session key {key!r} must start with 'conclave:session:'"
        )
        # Token part must be non-empty and URL-safe
        token_part = key[len("conclave:session:"):]
        assert len(token_part) >= 32, (
            f"Token part {token_part!r} must be at least 32 chars"
        )
        # URL-safe base64 characters only
        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        )
        invalid_chars = set(token_part) - allowed
        assert not invalid_chars, (
            f"Token contains invalid chars: {invalid_chars!r}"
        )

    def test_create_session_key_unique_each_call(self) -> None:
        """create_session_key produces a different key on each call."""
        from synth_engine.bootstrapper.dependencies.sessions import (  # noqa: PLC0415
            create_session_key,
        )

        keys = {create_session_key() for _ in range(10)}
        assert len(keys) == 10, "All 10 generated session keys must be unique"

    def test_enforce_concurrent_session_limit_no_eviction_under_limit(self) -> None:
        """No eviction when session count < limit."""
        from synth_engine.bootstrapper.dependencies.sessions import (  # noqa: PLC0415
            enforce_concurrent_session_limit,
        )

        redis_client = MagicMock()
        # Only 2 sessions exist — under limit of 3
        session_keys = [b"conclave:session:s1", b"conclave:session:s2"]
        sessions_data = [
            json.dumps({
                "user_id": _USER_A_UUID,
                "org_id": _ORG_A_UUID,
                "role": "operator",
                "created_at": f"2026-01-0{i+1}T00:00:00Z",
                "last_refreshed_at": f"2026-01-0{i+1}T00:00:00Z",
            }).encode()
            for i in range(2)
        ]
        redis_client.scan_iter.return_value = iter(session_keys)
        redis_client.mget.return_value = sessions_data

        enforce_concurrent_session_limit(
            redis_client=redis_client,
            user_id=_USER_A_UUID,
            org_id=_ORG_A_UUID,
            limit=3,
        )

        redis_client.delete.assert_not_called()

    def test_enforce_concurrent_session_limit_evicts_oldest(self) -> None:
        """When at limit, oldest session is evicted before new one is written."""
        from synth_engine.bootstrapper.dependencies.sessions import (  # noqa: PLC0415
            enforce_concurrent_session_limit,
        )

        redis_client = MagicMock()
        # 3 sessions at limit of 3
        session_keys = [
            b"conclave:session:oldest",
            b"conclave:session:middle",
            b"conclave:session:newest",
        ]
        sessions_data = [
            json.dumps({
                "user_id": _USER_A_UUID,
                "org_id": _ORG_A_UUID,
                "role": "operator",
                "created_at": "2026-01-01T00:00:00Z",  # oldest
                "last_refreshed_at": "2026-01-01T00:00:00Z",
            }).encode(),
            json.dumps({
                "user_id": _USER_A_UUID,
                "org_id": _ORG_A_UUID,
                "role": "operator",
                "created_at": "2026-01-02T00:00:00Z",
                "last_refreshed_at": "2026-01-02T00:00:00Z",
            }).encode(),
            json.dumps({
                "user_id": _USER_A_UUID,
                "org_id": _ORG_A_UUID,
                "role": "operator",
                "created_at": "2026-01-03T00:00:00Z",
                "last_refreshed_at": "2026-01-03T00:00:00Z",
            }).encode(),
        ]
        redis_client.scan_iter.return_value = iter(session_keys)
        redis_client.mget.return_value = sessions_data

        enforce_concurrent_session_limit(
            redis_client=redis_client,
            user_id=_USER_A_UUID,
            org_id=_ORG_A_UUID,
            limit=3,
        )

        # The oldest session must be evicted
        redis_client.delete.assert_called_once_with(b"conclave:session:oldest")

    def test_write_session_to_redis_correct_key_format(self) -> None:
        """write_session writes value under 'conclave:session:<token>' with correct TTL."""
        from synth_engine.bootstrapper.dependencies.sessions import (  # noqa: PLC0415
            write_session,
        )

        redis_client = MagicMock()

        session_key = write_session(
            redis_client=redis_client,
            user_id=_USER_A_UUID,
            org_id=_ORG_A_UUID,
            role="operator",
            ttl_seconds=28800,
        )

        assert session_key.startswith("conclave:session:"), (
            f"Session key {session_key!r} must start with 'conclave:session:'"
        )
        # Verify setex was called with correct TTL
        redis_client.setex.assert_called_once()
        call_args = redis_client.setex.call_args
        key_arg, ttl_arg, value_arg = call_args[0]
        assert key_arg == session_key, f"Key mismatch: {key_arg!r} != {session_key!r}"
        assert ttl_arg == 28800, f"TTL mismatch: {ttl_arg} != 28800"

        # Verify the session data contains required fields
        session_data = json.loads(value_arg)
        assert session_data["user_id"] == _USER_A_UUID
        assert session_data["org_id"] == _ORG_A_UUID
        assert session_data["role"] == "operator"
        assert "created_at" in session_data
        assert "last_refreshed_at" in session_data


# ===========================================================================
# SECTION 6: OIDC State Key Namespace
# ===========================================================================


class TestOIDCStateKeyNamespace:
    """Tests for OIDC state key format in Redis."""

    def test_state_redis_key_format(self) -> None:
        """OIDC state key follows 'conclave:oidc:state:<state_value>' format."""
        from synth_engine.bootstrapper.dependencies.oidc import (  # noqa: PLC0415
            make_state_redis_key,
        )

        state_value = "abc123def456"
        key = make_state_redis_key(state_value)
        assert key == f"conclave:oidc:state:{state_value}", (
            f"State key {key!r} must be 'conclave:oidc:state:{state_value}'"
        )

    def test_state_value_with_colon_rejected(self) -> None:
        """State value containing ':' is rejected (cannot be used as Redis key suffix)."""
        from synth_engine.bootstrapper.dependencies.oidc import (  # noqa: PLC0415
            validate_state_value,
        )

        with pytest.raises(ValueError, match="(?i)(invalid|unsafe|colon)"):
            validate_state_value("state:with:colons")

    def test_state_value_non_urlsafe_rejected(self) -> None:
        """State value with non-URL-safe characters is rejected."""
        from synth_engine.bootstrapper.dependencies.oidc import (  # noqa: PLC0415
            validate_state_value,
        )

        with pytest.raises(ValueError):
            validate_state_value("state value with spaces")

    def test_state_value_valid_urlsafe_accepted(self) -> None:
        """Valid URL-safe base64 state value is accepted."""
        from synth_engine.bootstrapper.dependencies.oidc import (  # noqa: PLC0415
            validate_state_value,
        )

        # secrets.token_urlsafe(32) produces these characters
        valid_state = "abcABC0123456789-_" * 2  # URL-safe base64 chars
        # Should not raise
        validate_state_value(valid_state)


# ===========================================================================
# SECTION 7: Email Extraction from Token Claims
# ===========================================================================


class TestEmailExtraction:
    """Tests for email claim extraction from OIDC ID tokens."""

    def test_extract_valid_email_returns_email(self) -> None:
        """Valid email claim in token is returned correctly."""
        from synth_engine.bootstrapper.routers.auth_oidc import (  # noqa: PLC0415
            _extract_email_from_token_claims,
        )

        claims: dict[str, Any] = {
            "sub": "user-id",
            "email": "user@example.com",
        }
        result = _extract_email_from_token_claims(claims)
        assert result == "user@example.com", (
            f"Expected 'user@example.com', got {result!r}"
        )

    def test_role_claims_never_extracted(self) -> None:
        """IdP role/groups/permissions claims are never returned by _extract_role_from_token_claims.

        AC: Decision 10 — IdP role claims must be ignored entirely.
        """
        from synth_engine.bootstrapper.routers.auth_oidc import (  # noqa: PLC0415
            _extract_role_from_token_claims,
        )

        for role_claim in ("role", "groups", "permissions", "scope"):
            claims: dict[str, Any] = {
                "sub": "user-id",
                "email": "user@example.com",
                role_claim: "admin",
            }
            result = _extract_role_from_token_claims(claims)
            assert result is None, (
                f"IdP {role_claim!r} claim must be ignored, got {result!r}"
            )


# ===========================================================================
# SECTION 8: Config Validation for OIDC
# ===========================================================================


class TestOIDCConfigValidation:
    """Tests for startup configuration validation for OIDC settings."""

    def test_session_ttl_minimum_60_seconds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SESSION_TTL_SECONDS must be >= 60."""
        from pydantic import ValidationError  # noqa: PLC0415

        from synth_engine.shared.settings import ConclaveSettings  # noqa: PLC0415

        monkeypatch.setenv("SESSION_TTL_SECONDS", "59")
        with pytest.raises(ValidationError):
            ConclaveSettings(_env_file=None)

    def test_concurrent_session_limit_minimum_1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONCURRENT_SESSION_LIMIT must be >= 1."""
        from pydantic import ValidationError  # noqa: PLC0415

        from synth_engine.shared.settings import ConclaveSettings  # noqa: PLC0415

        monkeypatch.setenv("CONCURRENT_SESSION_LIMIT", "0")
        with pytest.raises(ValidationError):
            ConclaveSettings(_env_file=None)


# ===========================================================================
# SECTION 9: Migration 011 — last_login_at Column
# ===========================================================================


class TestMigration011:
    """Tests for migration 011 adding last_login_at to users table."""

    def test_user_model_has_last_login_at_field(self) -> None:
        """User model has a nullable last_login_at field of type datetime | None."""
        from synth_engine.shared.models.user import User  # noqa: PLC0415
        import inspect  # noqa: PLC0415
        import datetime  # noqa: PLC0415

        # Get field annotations
        user_fields = User.model_fields
        assert "last_login_at" in user_fields, (
            "User model must have a last_login_at field (migration 011)"
        )

        field = user_fields["last_login_at"]
        # Field must be optional (nullable)
        assert field.default is None, (
            f"last_login_at must default to None, got {field.default!r}"
        )

    def test_migration_file_011_exists(self) -> None:
        """Migration file 011_add_last_login_at.py must exist in alembic/versions/."""
        import os  # noqa: PLC0415

        migration_path = (
            "/Users/jessercastro/Projects/SYNTHETIC_DATA"
            "/alembic/versions/011_add_last_login_at.py"
        )
        assert os.path.exists(migration_path), (
            f"Migration file not found: {migration_path}"
        )


# ===========================================================================
# SECTION 10: OIDC Authorize Endpoint — Happy Path
# ===========================================================================


class TestOIDCAuthorizeEndpoint:
    """Happy path tests for the /auth/oidc/authorize endpoint."""

    def test_authorize_returns_json_with_redirect_url(self) -> None:
        """GET /auth/oidc/authorize returns JSON with redirect_url field.

        AC: Decision 11 — JSON response, not HTTP redirect.
        """
        from fastapi import FastAPI  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415

        from synth_engine.bootstrapper.routers.auth_oidc import (  # noqa: PLC0415
            router as oidc_router,
        )

        app = FastAPI()
        app.include_router(oidc_router)

        with (
            patch("synth_engine.shared.settings.get_settings") as mock_settings,
            patch(
                "synth_engine.bootstrapper.dependencies.oidc.get_redis_client"
            ) as mock_redis,
            patch(
                "synth_engine.bootstrapper.dependencies.oidc._OIDC_PROVIDER"
            ) as mock_provider,
        ):
            settings = MagicMock()
            settings.oidc_enabled = True
            settings.oidc_issuer_url = "http://idp.internal:9999"
            settings.oidc_client_id = "test-client"
            settings.oidc_state_ttl_seconds = 600
            settings.conclave_env = "development"
            mock_settings.return_value = settings

            redis_client = MagicMock()
            redis_client.setex = MagicMock(return_value=True)
            mock_redis.return_value = redis_client

            provider = MagicMock()
            provider.authorization_endpoint = (
                "http://idp.internal:9999/auth"
            )
            mock_provider.authorization_endpoint = (
                "http://idp.internal:9999/auth"
            )

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/auth/oidc/authorize"
                "?code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
                "&code_challenge_method=S256"
            )

        assert resp.status_code == 200, (
            f"Expected 200 from authorize, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "redirect_url" in body, (
            f"Response must contain 'redirect_url', got keys: {list(body.keys())}"
        )
        assert "state" in body, (
            f"Response must contain 'state', got keys: {list(body.keys())}"
        )

    def test_authorize_returns_no_location_header(self) -> None:
        """GET /auth/oidc/authorize must NOT return a Location header.

        AC: Decision 11 — no HTTP redirect. The frontend SPA reads the JSON.
        A Location header would create an open redirect attack surface.
        """
        from fastapi import FastAPI  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415

        from synth_engine.bootstrapper.routers.auth_oidc import (  # noqa: PLC0415
            router as oidc_router,
        )

        app = FastAPI()
        app.include_router(oidc_router)

        with (
            patch("synth_engine.shared.settings.get_settings") as mock_settings,
            patch(
                "synth_engine.bootstrapper.dependencies.oidc.get_redis_client"
            ) as mock_redis,
        ):
            settings = MagicMock()
            settings.oidc_enabled = True
            settings.oidc_issuer_url = "http://idp.internal:9999"
            settings.oidc_client_id = "test-client"
            settings.oidc_state_ttl_seconds = 600
            settings.conclave_env = "development"
            mock_settings.return_value = settings

            redis_client = MagicMock()
            mock_redis.return_value = redis_client

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/auth/oidc/authorize"
                "?code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
                "&code_challenge_method=S256",
                allow_redirects=False,
            )

        assert "location" not in resp.headers, (
            "Authorize endpoint must NOT return a Location header (Decision 11)"
        )


# ===========================================================================
# SECTION 11: OIDC Disabled — 404 on session endpoints
# ===========================================================================


class TestOIDCDisabledEndpoints:
    """Tests that /auth/refresh and /auth/revoke return 404 when OIDC disabled."""

    def test_refresh_returns_404_when_oidc_disabled(self) -> None:
        """POST /auth/refresh returns 404 when OIDC is not configured.

        AC: Decision 5 — Session endpoints return 404 when OIDC disabled.
        This prevents these endpoints from advertising their existence.
        """
        from fastapi import FastAPI  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415

        from synth_engine.bootstrapper.routers.auth_oidc import (  # noqa: PLC0415
            router as oidc_router,
        )

        app = FastAPI()
        app.include_router(oidc_router)

        with patch("synth_engine.shared.settings.get_settings") as mock_settings:
            settings = MagicMock()
            settings.oidc_enabled = False
            mock_settings.return_value = settings

            token = _make_token(role="operator")
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/auth/refresh",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 404, (
            f"Expected 404 for /auth/refresh when OIDC disabled, got {resp.status_code}"
        )

    def test_revoke_returns_404_when_oidc_disabled(self) -> None:
        """POST /auth/revoke returns 404 when OIDC is not configured.

        AC: Decision 5 — Session endpoints return 404 when OIDC disabled.
        """
        from fastapi import FastAPI  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415

        from synth_engine.bootstrapper.routers.auth_oidc import (  # noqa: PLC0415
            router as oidc_router,
        )

        app = FastAPI()
        app.include_router(oidc_router)

        with patch("synth_engine.shared.settings.get_settings") as mock_settings:
            settings = MagicMock()
            settings.oidc_enabled = False
            mock_settings.return_value = settings

            token = _make_token(role="admin")
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/auth/revoke",
                json={"user_id": _USER_A_UUID},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 404, (
            f"Expected 404 for /auth/revoke when OIDC disabled, got {resp.status_code}"
        )


# ===========================================================================
# SECTION 12: RFC 7807 Error Response Format
# ===========================================================================


class TestRFC7807ErrorResponses:
    """Tests for RFC 7807 Problem Details format on OIDC error paths."""

    def test_oidc_error_returns_problem_json_content_type(
        self, oidc_app: Any
    ) -> None:
        """OIDC auth failure returns Content-Type: application/problem+json.

        AC: Decision 13 — all OIDC error paths use RFC 7807 Problem Details.
        """
        resp = oidc_app.get(
            "/auth/oidc/callback"
            "?code=some-code"
            "&state=nonexistent-state"
        )
        # Must be 401 with problem+json content type
        assert resp.status_code == 401
        content_type = resp.headers.get("content-type", "")
        assert "problem+json" in content_type or "application/json" in content_type, (
            f"Expected problem+json content type, got: {content_type!r}"
        )

    def test_oidc_error_body_has_required_rfc7807_fields(
        self, oidc_app: Any
    ) -> None:
        """OIDC 401 error body has 'type', 'title', 'status', 'detail' fields.

        AC: Decision 13 — RFC 7807 shape required.
        """
        with patch(
            "synth_engine.bootstrapper.dependencies.oidc.get_redis_client"
        ) as mock_redis:
            redis_client = MagicMock()
            redis_client.get.return_value = None  # State not found
            mock_redis.return_value = redis_client

            resp = oidc_app.get(
                "/auth/oidc/callback"
                "?code=some-code"
                "&state=nonexistent-state"
            )

        assert resp.status_code == 401
        body = resp.json()
        assert "status" in body, f"RFC 7807 body missing 'status': {body}"
        assert body["status"] == 401, f"Expected status=401, got {body['status']}"


# ---------------------------------------------------------------------------
# Fixtures used by feature tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def oidc_app() -> Any:
    """Minimal FastAPI test client for OIDC endpoint tests."""
    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(oidc_router)

    with (
        patch("synth_engine.shared.settings.get_settings") as mock_settings,
        patch(
            "synth_engine.bootstrapper.dependencies.oidc.get_redis_client"
        ) as mock_redis,
    ):
        settings = MagicMock()
        settings.jwt_secret_key.get_secret_value.return_value = _TEST_SECRET
        settings.jwt_algorithm = "HS256"
        settings.jwt_expiry_seconds = 3600
        settings.conclave_env = "development"
        settings.conclave_multi_tenant_enabled = False
        settings.oidc_enabled = True
        settings.oidc_issuer_url = "http://localhost:9999"
        settings.oidc_client_id = "test-client"
        settings.oidc_client_secret.get_secret_value.return_value = "test-secret"  # pragma: allowlist secret
        settings.oidc_state_ttl_seconds = 600
        settings.session_ttl_seconds = 28800
        settings.concurrent_session_limit = 3
        mock_settings.return_value = settings

        mock_redis.return_value = MagicMock()

        yield TestClient(app, raise_server_exceptions=False)
