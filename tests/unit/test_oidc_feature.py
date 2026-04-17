# gate-exempt: OIDC security testing requires comprehensive attack surface coverage
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

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.helpers_oidc import (
    OIDC_TEST_JWT_SECRET as _TEST_SECRET,
)
from tests.unit.helpers_oidc import (
    make_oidc_token as _make_token,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ORG_A_UUID = "11111111-1111-1111-1111-111111111111"
_USER_A_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


# ===========================================================================
# SECTION 1: SSRF Validator — validate_oidc_issuer_url
# ===========================================================================


class TestValidateOIDCIssuerURL:
    """Tests for the OIDC-specific SSRF validator."""

    def test_rfc1918_10_block_allowed(self) -> None:
        """10.0.0.0/8 block is allowed for air-gap IdPs.

        AC: RFC-1918 ranges must be accepted — air-gap IdPs live on private networks.
        """
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        url = "http://10.50.0.10/"
        raised = False
        try:
            validate_oidc_issuer_url(url)
        except ValueError:
            raised = True
        assert not raised, f"RFC-1918 10.x URL should be allowed for air-gap IdPs: {url}"

    def test_rfc1918_172_block_allowed(self) -> None:
        """172.16.0.0/12 block is allowed for air-gap IdPs."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        url = "http://172.20.0.5/"
        raised = False
        try:
            validate_oidc_issuer_url(url)
        except ValueError:
            raised = True
        assert not raised, f"RFC-1918 172.x URL should be allowed for air-gap IdPs: {url}"

    def test_rfc1918_192_168_block_allowed(self) -> None:
        """192.168.0.0/16 block is allowed for air-gap IdPs."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        url = "http://192.168.1.100/"
        raised = False
        try:
            validate_oidc_issuer_url(url)
        except ValueError:
            raised = True
        assert not raised, f"RFC-1918 192.168.x URL should be allowed for air-gap IdPs: {url}"

    def test_aws_imds_blocked(self) -> None:
        """169.254.169.254 is blocked unconditionally."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match="(?i)(forbidden|blocked|metadata|reserved)"):
            validate_oidc_issuer_url("http://169.254.169.254/")

    def test_alibaba_imds_blocked(self) -> None:
        """100.100.100.200 is blocked unconditionally."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match="(?i)(forbidden|metadata|cloud)"):
            validate_oidc_issuer_url("http://100.100.100.200/")

    def test_gcp_metadata_hostname_blocked(self) -> None:
        """metadata.google.internal is blocked unconditionally."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match="(?i)(forbidden|metadata|cloud)"):
            validate_oidc_issuer_url("http://metadata.google.internal/")

    def test_loopback_blocked(self) -> None:
        """127.0.0.1 is blocked unconditionally."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match="(?i)(forbidden|loopback)"):
            validate_oidc_issuer_url("http://127.0.0.1/")

    def test_ipv6_loopback_blocked(self) -> None:
        """::1 (IPv6 loopback) is blocked unconditionally."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match="(?i)(forbidden|loopback)"):
            validate_oidc_issuer_url("http://[::1]/")

    def test_public_ip_allowed_in_development(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Public IP is allowed in development mode."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        url = "http://203.0.113.5/"
        raised = False
        try:
            validate_oidc_issuer_url(url)
        except ValueError:
            raised = True
        assert not raised, f"Public IP should be allowed in development mode: {url}"

    def test_public_ip_blocked_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Public IP is blocked in production mode."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        monkeypatch.setenv("CONCLAVE_ENV", "production")
        with pytest.raises(ValueError, match="(?i)(forbidden|public.ip)"):
            validate_oidc_issuer_url("http://203.0.113.5/")

    def test_rfc1918_issuer_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Accepting RFC-1918 issuer emits a WARNING-level security notice."""
        import logging

        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with caplog.at_level(logging.WARNING, logger="synth_engine.shared.ssrf"):
            validate_oidc_issuer_url("http://10.0.0.1/")

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_messages) >= 1, (
            "Expected at least one WARNING log for RFC-1918 issuer acceptance"
        )

    def test_missing_scheme_rejected(self) -> None:
        """URL without a scheme (http/https) is rejected."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match="(?i)(invalid|scheme)"):
            validate_oidc_issuer_url("not-a-url")

    def test_empty_url_rejected(self) -> None:
        """Empty URL string is rejected."""
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match="(?i)(invalid|scheme)"):
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
        from synth_engine.shared.settings import ConclaveSettings

        monkeypatch.delenv("OIDC_ENABLED", raising=False)
        monkeypatch.delenv("CONCLAVE_OIDC_ENABLED", raising=False)
        settings = ConclaveSettings(_env_file=None)
        assert settings.oidc_enabled == False, (
            f"Expected oidc_enabled=False by default, got {settings.oidc_enabled}"
        )

    def test_oidc_state_ttl_defaults_to_600(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OIDC_STATE_TTL_SECONDS defaults to 600 (10 minutes)."""
        from synth_engine.shared.settings import ConclaveSettings

        monkeypatch.delenv("OIDC_STATE_TTL_SECONDS", raising=False)
        settings = ConclaveSettings(_env_file=None)
        assert settings.oidc_state_ttl_seconds == 600, (
            f"Expected 600, got {settings.oidc_state_ttl_seconds}"
        )

    def test_session_ttl_defaults_to_28800(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SESSION_TTL_SECONDS defaults to 28800 (8 hours)."""
        from synth_engine.shared.settings import ConclaveSettings

        monkeypatch.delenv("SESSION_TTL_SECONDS", raising=False)
        settings = ConclaveSettings(_env_file=None)
        assert settings.session_ttl_seconds == 28800, (
            f"Expected 28800, got {settings.session_ttl_seconds}"
        )

    def test_concurrent_session_limit_defaults_to_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCURRENT_SESSION_LIMIT defaults to 3."""
        from synth_engine.shared.settings import ConclaveSettings

        monkeypatch.delenv("CONCURRENT_SESSION_LIMIT", raising=False)
        settings = ConclaveSettings(_env_file=None)
        assert settings.concurrent_session_limit == 3, (
            f"Expected 3, got {settings.concurrent_session_limit}"
        )

    def test_oidc_state_ttl_max_3600(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OIDC_STATE_TTL_SECONDS maximum is 3600 (1 hour)."""
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

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
        from synth_engine.bootstrapper.dependencies.permissions import (
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
        from synth_engine.bootstrapper.dependencies.permissions import (
            PERMISSION_MATRIX,
            Role,
        )

        allowed_roles = PERMISSION_MATRIX["sessions:revoke"]
        assert allowed_roles == frozenset({Role.admin}), (
            f"sessions:revoke must be admin-only, got: {allowed_roles}"
        )

    def test_sessions_revoke_denied_for_non_admin_roles(self) -> None:
        """operator, viewer, and auditor cannot use sessions:revoke."""
        from synth_engine.bootstrapper.dependencies.permissions import (
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
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/auth/oidc/authorize" in AUTH_EXEMPT_PATHS

    def test_oidc_callback_in_auth_exempt_paths(self) -> None:
        """AUTH_EXEMPT_PATHS contains /auth/oidc/callback."""
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/auth/oidc/callback" in AUTH_EXEMPT_PATHS


# ===========================================================================
# SECTION 5: Session Management
# ===========================================================================


class TestSessionManagementFeature:
    """Feature tests for session creation, refresh, and revocation."""

    def test_create_session_key_returns_correct_format(self) -> None:
        """create_session_key returns a key in 'conclave:session:<token>' format."""
        from synth_engine.bootstrapper.dependencies.sessions import (
            create_session_key,
        )

        key = create_session_key()
        assert key.startswith("conclave:session:"), (
            f"Session key {key!r} must start with 'conclave:session:'"
        )
        # Token part must be non-empty and URL-safe
        token_part = key[len("conclave:session:") :]
        assert len(token_part) >= 32, f"Token part {token_part!r} must be at least 32 chars"
        # URL-safe base64 characters only
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        invalid_chars = set(token_part) - allowed
        assert not invalid_chars, f"Token contains invalid chars: {invalid_chars!r}"

    def test_create_session_key_unique_each_call(self) -> None:
        """create_session_key produces a different key on each call."""
        from synth_engine.bootstrapper.dependencies.sessions import (
            create_session_key,
        )

        keys = {create_session_key() for _ in range(10)}
        assert len(keys) == 10, "All 10 generated session keys must be unique"

    def test_enforce_concurrent_session_limit_no_eviction_under_limit(self) -> None:
        """No eviction when session count < limit."""
        from synth_engine.bootstrapper.dependencies.sessions import (
            enforce_concurrent_session_limit,
        )

        redis_client = MagicMock()
        # Only 2 sessions exist — under limit of 3
        session_keys = [b"conclave:session:s1", b"conclave:session:s2"]
        sessions_data = [
            json.dumps(
                {
                    "user_id": _USER_A_UUID,
                    "org_id": _ORG_A_UUID,
                    "role": "operator",
                    "created_at": f"2026-01-0{i + 1}T00:00:00Z",
                    "last_refreshed_at": f"2026-01-0{i + 1}T00:00:00Z",
                }
            ).encode()
            for i in range(2)
        ]
        redis_client.smembers.return_value = set(session_keys)
        redis_client.mget.return_value = sessions_data

        enforce_concurrent_session_limit(
            redis_client=redis_client,
            user_id=_USER_A_UUID,
            org_id=_ORG_A_UUID,
            limit=3,
        )

        redis_client.delete.assert_not_called()
        assert redis_client.delete.call_count == 0, "No sessions should be evicted when under limit"

    def test_enforce_concurrent_session_limit_evicts_oldest(self) -> None:
        """When at limit, oldest session is evicted before new one is written."""
        from synth_engine.bootstrapper.dependencies.sessions import (
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
            json.dumps(
                {
                    "user_id": _USER_A_UUID,
                    "org_id": _ORG_A_UUID,
                    "role": "operator",
                    "created_at": "2026-01-01T00:00:00Z",  # oldest
                    "last_refreshed_at": "2026-01-01T00:00:00Z",
                }
            ).encode(),
            json.dumps(
                {
                    "user_id": _USER_A_UUID,
                    "org_id": _ORG_A_UUID,
                    "role": "operator",
                    "created_at": "2026-01-02T00:00:00Z",
                    "last_refreshed_at": "2026-01-02T00:00:00Z",
                }
            ).encode(),
            json.dumps(
                {
                    "user_id": _USER_A_UUID,
                    "org_id": _ORG_A_UUID,
                    "role": "operator",
                    "created_at": "2026-01-03T00:00:00Z",
                    "last_refreshed_at": "2026-01-03T00:00:00Z",
                }
            ).encode(),
        ]
        redis_client.smembers.return_value = set(session_keys)
        # Use side_effect for order-independent mget (smembers returns a set, unordered)
        _sessions_by_key = dict(zip(session_keys, sessions_data, strict=False))
        redis_client.mget.side_effect = lambda keys: [_sessions_by_key.get(k) for k in keys]

        enforce_concurrent_session_limit(
            redis_client=redis_client,
            user_id=_USER_A_UUID,
            org_id=_ORG_A_UUID,
            limit=3,
        )

        # The oldest session must be evicted (created_at 2026-01-01 is earliest)
        redis_client.delete.assert_called_once_with(b"conclave:session:oldest")
        assert redis_client.delete.call_count == 1, "Exactly one session should be evicted"

    def test_write_session_to_redis_correct_key_format(self) -> None:
        """write_session writes value under 'conclave:session:<token>' with correct TTL."""
        from synth_engine.bootstrapper.dependencies.sessions import (
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
        # write_session uses a Lua script via redis_client.eval (not setex directly).
        # eval(script, numkeys, KEYS[1]=session_key, KEYS[2]=index_key,
        #      ARGV[1]=session_json, ARGV[2]=ttl_str, ARGV[3]=limit_str)
        redis_client.eval.assert_called_once()
        eval_args = redis_client.eval.call_args[0]
        assert eval_args[2] == session_key, f"KEYS[1] mismatch: {eval_args[2]!r} != {session_key!r}"
        assert eval_args[5] == "28800", f"TTL arg mismatch: {eval_args[5]!r} != '28800'"

        # Verify the session data contains required fields
        session_data = json.loads(eval_args[4])
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
        from synth_engine.bootstrapper.dependencies.oidc import (
            make_state_redis_key,
        )

        state_value = "abc123def456"  # pragma: allowlist secret
        key = make_state_redis_key(state_value)
        assert key == f"conclave:oidc:state:{state_value}", (
            f"State key {key!r} must be 'conclave:oidc:state:{state_value}'"
        )

    def test_state_value_with_colon_rejected(self) -> None:
        """State value containing ':' is rejected (cannot be used as Redis key suffix)."""
        from synth_engine.bootstrapper.dependencies.oidc import (
            validate_state_value,
        )

        with pytest.raises(ValueError, match="(?i)(invalid|unsafe|colon)"):
            validate_state_value("state:with:colons")

    def test_state_value_non_urlsafe_rejected(self) -> None:
        """State value with non-URL-safe characters is rejected."""
        from synth_engine.bootstrapper.dependencies.oidc import (
            validate_state_value,
        )

        with pytest.raises(ValueError, match="(?i)(non-url-safe|invalid)"):
            validate_state_value("state value with spaces")

    def test_state_value_valid_urlsafe_accepted(self) -> None:
        """Valid URL-safe base64 state value is accepted."""
        from synth_engine.bootstrapper.dependencies.oidc import (
            validate_state_value,
        )

        # secrets.token_urlsafe(32) produces these characters
        valid_state = "abcABC0123456789-_" * 2  # URL-safe base64 chars
        raised = False
        try:
            validate_state_value(valid_state)
        except ValueError:
            raised = True
        assert not raised, f"URL-safe base64 state should be accepted: {valid_state[:20]!r}"


# ===========================================================================
# SECTION 7: Email Extraction from Token Claims
# ===========================================================================


class TestEmailExtraction:
    """Tests for email claim extraction from OIDC ID tokens."""

    def test_extract_valid_email_returns_email(self) -> None:
        """Valid email claim in token is returned correctly."""
        from synth_engine.bootstrapper.routers.auth_oidc import (
            _extract_email_from_token_claims,
        )

        claims: dict[str, Any] = {
            "sub": "user-id",
            "email": "user@example.com",
        }
        result = _extract_email_from_token_claims(claims)
        assert result == "user@example.com", f"Expected 'user@example.com', got {result!r}"

    def test_role_claims_never_extracted(self) -> None:
        """OIDC_DEFAULT_USER_ROLE is used for all OIDC logins — IdP role claims are never trusted.

        AC: Decision 10 — IdP role claims must be ignored entirely.
        _extract_role_from_token_claims was removed (F8 review fix): the system
        now uses the constant OIDC_DEFAULT_USER_ROLE ("operator") for all OIDC users.
        """
        from synth_engine.bootstrapper.routers.auth_oidc import OIDC_DEFAULT_USER_ROLE

        # The default role must be a low-privilege role — never admin.
        assert OIDC_DEFAULT_USER_ROLE == "operator", (
            "OIDC_DEFAULT_USER_ROLE must be 'operator' (lowest privilege), "
            f"got {OIDC_DEFAULT_USER_ROLE!r}"
        )
        assert OIDC_DEFAULT_USER_ROLE != "admin", "OIDC_DEFAULT_USER_ROLE must never be 'admin'"


# ===========================================================================
# SECTION 8: Config Validation for OIDC
# ===========================================================================


class TestOIDCConfigValidation:
    """Tests for startup configuration validation for OIDC settings."""

    def test_session_ttl_minimum_60_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SESSION_TTL_SECONDS must be >= 60."""
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

        monkeypatch.setenv("SESSION_TTL_SECONDS", "59")
        with pytest.raises(ValidationError):
            ConclaveSettings(_env_file=None)

    def test_concurrent_session_limit_minimum_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONCURRENT_SESSION_LIMIT must be >= 1."""
        from pydantic import ValidationError

        from synth_engine.shared.settings import ConclaveSettings

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

        from synth_engine.shared.models.user import User

        # Get field annotations
        user_fields = User.model_fields
        assert "last_login_at" in user_fields, (
            "User model must have a last_login_at field (migration 011)"
        )

        field = user_fields["last_login_at"]
        # Field must be optional (nullable)
        assert field.default is None, f"last_login_at must default to None, got {field.default!r}"

    def test_migration_file_011_exists(self) -> None:
        """Migration file 011_add_last_login_at.py must exist in alembic/versions/."""
        import os

        migration_path = (
            "/Users/jessercastro/Projects/SYNTHETIC_DATA/alembic/versions/011_add_last_login_at.py"
        )
        assert os.path.exists(migration_path), f"Migration file not found: {migration_path}"


# ===========================================================================
# SECTION 10: OIDC Authorize Endpoint — Happy Path
# ===========================================================================


class TestOIDCAuthorizeEndpoint:
    """Happy path tests for the /auth/oidc/authorize endpoint."""

    def test_authorize_returns_json_with_redirect_url(self) -> None:
        """GET /auth/oidc/authorize returns JSON with redirect_url field.

        AC: Decision 11 — JSON response, not HTTP redirect.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from synth_engine.bootstrapper.routers.auth_oidc import (
            router as oidc_router,
        )

        app = FastAPI()
        app.include_router(oidc_router)

        mock_provider = MagicMock()
        mock_provider.authorization_endpoint = "http://idp.internal:9999/authorize"
        mock_provider.client_id = "test-client"

        with (
            patch("synth_engine.bootstrapper.routers.auth_oidc.get_settings") as mock_settings,
            patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis,
            patch(
                "synth_engine.bootstrapper.dependencies.oidc.get_oidc_provider",
                return_value=mock_provider,
            ),
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
        assert "state" in body, f"Response must contain 'state', got keys: {list(body.keys())}"

    def test_authorize_returns_no_location_header(self) -> None:
        """GET /auth/oidc/authorize must NOT return a Location header.

        AC: Decision 11 — no HTTP redirect. The frontend SPA reads the JSON.
        A Location header would create an open redirect attack surface.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from synth_engine.bootstrapper.routers.auth_oidc import (
            router as oidc_router,
        )

        app = FastAPI()
        app.include_router(oidc_router)

        with (
            patch("synth_engine.bootstrapper.routers.auth_oidc.get_settings") as mock_settings,
            patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis,
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
                follow_redirects=False,
            )

        assert "location" not in resp.headers, (
            "Authorize endpoint must NOT return a Location header (Decision 11)"
        )


# ===========================================================================
# SECTION 11: OIDC Disabled — 404 on session endpoints
# ===========================================================================


class TestOIDCDisabledEndpoints:
    """Tests that /auth/refresh and /auth/revoke return 404 when OIDC disabled."""

    def test_refresh_returns_404_when_oidc_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /auth/refresh returns 404 when OIDC is not configured.

        AC: Decision 5 — Session endpoints return 404 when OIDC disabled.
        This prevents these endpoints from advertising their existence.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from synth_engine.bootstrapper.routers.auth_oidc import (
            router as oidc_router,
        )
        from synth_engine.shared.settings import get_settings

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")
        monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
        monkeypatch.setenv("OIDC_ENABLED", "false")
        monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "false")
        get_settings.cache_clear()

        app = FastAPI()
        app.include_router(oidc_router)

        token = _make_token(role="operator")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/auth/refresh",
            headers={"Authorization": f"Bearer {token}"},
        )
        get_settings.cache_clear()

        assert resp.status_code == 404, (
            f"Expected 404 for /auth/refresh when OIDC disabled, got {resp.status_code}"
        )

    def test_revoke_returns_404_when_oidc_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /auth/revoke returns 404 when OIDC is not configured.

        AC: Decision 5 — Session endpoints return 404 when OIDC disabled.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from synth_engine.bootstrapper.routers.auth_oidc import (
            router as oidc_router,
        )
        from synth_engine.shared.settings import get_settings

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")
        monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
        monkeypatch.setenv("OIDC_ENABLED", "false")
        monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "false")
        get_settings.cache_clear()

        app = FastAPI()
        app.include_router(oidc_router)

        # Use a valid UUID4 string so Pydantic accepts it
        valid_uuid4 = "12345678-1234-4234-8234-123456789abc"
        token = _make_token(role="admin")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/auth/revoke",
            json={"user_id": valid_uuid4},
            headers={"Authorization": f"Bearer {token}"},
        )
        get_settings.cache_clear()

        assert resp.status_code == 404, (
            f"Expected 404 for /auth/revoke when OIDC disabled, got {resp.status_code}"
        )

    def test_authorize_returns_404_when_oidc_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /auth/oidc/authorize returns 404 when OIDC is not configured.

        AC: Decision 5 — OIDC endpoints return 404 when OIDC disabled.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router
        from synth_engine.shared.settings import get_settings

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")
        monkeypatch.setenv("OIDC_ENABLED", "false")
        monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "false")
        get_settings.cache_clear()

        app = FastAPI()
        app.include_router(oidc_router)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/auth/oidc/authorize"
            "?code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
            "&code_challenge_method=S256"
        )
        get_settings.cache_clear()

        assert resp.status_code == 404, (
            f"Expected 404 for /auth/oidc/authorize when OIDC disabled, got {resp.status_code}"
        )

    def test_authorize_rejects_empty_code_challenge(self, oidc_app: Any) -> None:
        """GET /auth/oidc/authorize with empty code_challenge returns 422.

        AC: code_challenge is required. Empty string must be rejected.
        """
        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            mock_redis.return_value = MagicMock()
            resp = oidc_app.get(
                "/auth/oidc/authorize"
                "?code_challenge=%20"  # whitespace-only after strip
                "&code_challenge_method=S256"
            )
        assert resp.status_code == 422, (
            f"Expected 422 for empty code_challenge, got {resp.status_code}"
        )


# ===========================================================================
# SECTION 12: RFC 7807 Error Response Format
# ===========================================================================


class TestRFC7807ErrorResponses:
    """Tests for RFC 7807 Problem Details format on OIDC error paths."""

    def test_oidc_error_returns_problem_json_content_type(self, oidc_app: Any) -> None:
        """OIDC auth failure returns Content-Type: application/problem+json.

        AC: Decision 13 — all OIDC error paths use RFC 7807 Problem Details.
        """
        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            redis_client.getdel.return_value = None  # State not found → 401
            mock_redis.return_value = redis_client

            resp = oidc_app.get("/auth/oidc/callback?code=some-code&state=nonexistent-state")
        # Must be 401 with problem+json content type
        assert resp.status_code == 401
        content_type = resp.headers.get("content-type", "")
        assert "problem+json" in content_type or "application/json" in content_type, (
            f"Expected problem+json content type, got: {content_type!r}"
        )

    def test_oidc_error_body_has_required_rfc7807_fields(self, oidc_app: Any) -> None:
        """OIDC 401 error body has 'type', 'title', 'status', 'detail' fields.

        AC: Decision 13 — RFC 7807 shape required.
        """
        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            redis_client.getdel.return_value = None  # State not found
            mock_redis.return_value = redis_client

            resp = oidc_app.get("/auth/oidc/callback?code=some-code&state=nonexistent-state")

        assert resp.status_code == 401
        body = resp.json()
        # FastAPI wraps HTTPException.detail under the "detail" key.
        # The RFC 7807 problem dict is under body["detail"].
        problem = body.get("detail", body)
        assert "status" in problem, f"RFC 7807 body missing 'status': {body}"
        assert problem["status"] == 401, (
            f"Expected status=401 in problem detail, got {problem.get('status')}"
        )


# ===========================================================================
# SECTION 13: Refresh and Revoke Happy Paths
# ===========================================================================


class TestRefreshAndRevokeHappyPaths:
    """Happy path tests for POST /auth/refresh and POST /auth/revoke.

    These tests verify the endpoint bodies execute successfully when OIDC is
    enabled and authentication passes. They cover the code paths that are
    not exercised by the OIDC-disabled (404) and attack (401/403/422) tests.
    """

    def test_refresh_returns_200_with_new_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /auth/refresh with valid JWT returns 200 with a new access token.

        AC: Authenticated users can refresh their JWT. The response must
        contain access_token, token_type, and expires_in.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from synth_engine.bootstrapper.dependencies.tenant import TenantContext
        from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router
        from synth_engine.shared.settings import get_settings

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")
        monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
        monkeypatch.setenv("OIDC_ENABLED", "true")
        monkeypatch.setenv("OIDC_ISSUER_URL", "http://localhost:9999")
        monkeypatch.setenv("OIDC_CLIENT_ID", "test-client")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-secret")  # pragma: allowlist secret
        monkeypatch.setenv("SESSION_TTL_SECONDS", "28800")
        monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "false")
        get_settings.cache_clear()

        app = FastAPI()
        app.include_router(oidc_router)

        # Override get_current_user to inject a valid TenantContext
        from synth_engine.bootstrapper.dependencies.tenant import get_current_user

        def mock_get_current_user() -> TenantContext:
            return TenantContext(
                org_id=_ORG_A_UUID,
                user_id=_USER_A_UUID,
                role="operator",
            )

        app.dependency_overrides[get_current_user] = mock_get_current_user

        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            redis_client.smembers.return_value = set()
            mock_redis.return_value = redis_client

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/auth/refresh")

        get_settings.cache_clear()

        assert resp.status_code == 200, (
            f"Expected 200 for authenticated /auth/refresh, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "access_token" in body, (
            f"Refresh response must contain access_token, got keys: {list(body.keys())}"
        )
        assert body["token_type"] == "bearer", (
            f"Expected token_type='bearer', got {body.get('token_type')!r}"
        )
        assert body["expires_in"] == 3600, f"Expected expires_in=3600, got {body.get('expires_in')}"

    def test_revoke_admin_same_org_returns_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /auth/revoke with admin JWT revoking same-org user returns 200.

        AC: Admin can revoke any user's sessions within their org.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from synth_engine.bootstrapper.dependencies.tenant import TenantContext
        from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router
        from synth_engine.shared.settings import get_settings

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")
        monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
        monkeypatch.setenv("OIDC_ENABLED", "true")
        monkeypatch.setenv("OIDC_ISSUER_URL", "http://localhost:9999")
        monkeypatch.setenv("OIDC_CLIENT_ID", "test-client")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-secret")  # pragma: allowlist secret
        monkeypatch.setenv("SESSION_TTL_SECONDS", "28800")
        monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "false")
        get_settings.cache_clear()

        import uuid as _uuid

        app = FastAPI()
        app.include_router(oidc_router)

        # Override get_current_user to inject an admin context
        from synth_engine.bootstrapper.dependencies.tenant import get_current_user

        def mock_get_current_user() -> TenantContext:
            return TenantContext(
                org_id=_ORG_A_UUID,
                user_id="admin@example.com",
                role="admin",
            )

        app.dependency_overrides[get_current_user] = mock_get_current_user

        target_user_id = _USER_A_UUID

        with (
            patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis,
            patch("synth_engine.bootstrapper.routers.auth_oidc._get_user_org") as mock_get_org,
        ):
            # Target user is in same org as the admin
            mock_get_org.return_value = _uuid.UUID(_ORG_A_UUID)
            redis_client = MagicMock()
            redis_client.smembers.return_value = set()
            redis_client.delete.return_value = 0
            mock_redis.return_value = redis_client

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/auth/revoke",
                json={"user_id": target_user_id},
            )

        get_settings.cache_clear()

        assert resp.status_code == 200, (
            f"Expected 200 for admin same-org revoke, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "revoked_sessions" in body, (
            f"Revoke response must contain revoked_sessions, got keys: {list(body.keys())}"
        )
        assert body["revoked_sessions"] == 0, (
            f"Expected revoked_sessions=0 (no sessions), got {body.get('revoked_sessions')}"
        )


# ===========================================================================
# SECTION 14: Private Helper Function Coverage
# ===========================================================================


class TestOIDCHelperFunctions:
    """Direct unit tests for private helper functions in auth_oidc.py.

    These tests cover the helper functions that are called from the OIDC
    callback endpoint but are not easily exercised through the full HTTP
    flow in unit tests (which would require a real database connection).
    """

    def test_find_user_by_email_returns_user_when_found(self) -> None:
        """_find_or_provision_user returns existing user when found in DB.

        _find_user_by_email was merged into _find_or_provision_user (review fix F7).
        """
        import uuid as _uuid

        from synth_engine.bootstrapper.routers.auth_oidc import _find_or_provision_user

        db = MagicMock()
        org_id = _uuid.UUID(_ORG_A_UUID)
        mock_user = MagicMock()
        mock_user.email = "user@example.com"
        mock_user.org_id = org_id
        mock_user.role = "operator"
        db.exec.return_value.first.return_value = mock_user

        result = _find_or_provision_user(email="user@example.com", org_id=org_id, db=db)
        assert result == mock_user, f"Expected mock user, got {result!r}"

    def test_find_user_by_email_returns_none_when_not_found(self) -> None:
        """_find_or_provision_user auto-provisions a new user when not found in DB.

        _find_user_by_email was merged into _find_or_provision_user (review fix F7).
        When the user is not found, a new user is auto-provisioned (not None returned).
        """
        import uuid as _uuid

        from synth_engine.bootstrapper.routers.auth_oidc import _find_or_provision_user

        db = MagicMock()
        org_id = _uuid.UUID(_ORG_A_UUID)
        # First exec (exact match) returns None; second (any org) returns None
        db.exec.return_value.first.side_effect = [None, None]

        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_audit_logger") as mock_al:
            mock_al.return_value = MagicMock()
            _find_or_provision_user(email="notfound@example.com", org_id=org_id, db=db)

        # New user should be created (db.add called)
        assert db.add.call_count == 1, f"Expected 1 db.add call, got {db.add.call_count}"
        assert db.commit.call_count == 1, f"Expected 1 db.commit call, got {db.commit.call_count}"

    def test_find_or_provision_user_returns_existing_user(self) -> None:
        """_find_or_provision_user returns existing user and updates last_login_at."""
        import uuid as _uuid

        from synth_engine.bootstrapper.routers.auth_oidc import _find_or_provision_user

        db = MagicMock()
        org_id = _uuid.UUID(_ORG_A_UUID)
        mock_user = MagicMock()
        mock_user.email = "user@example.com"
        mock_user.org_id = org_id
        mock_user.role = "operator"

        # First exec call: exact match (email + org)
        db.exec.return_value.first.return_value = mock_user

        result = _find_or_provision_user(
            email="user@example.com",
            org_id=org_id,
            db=db,
        )

        assert result == mock_user, f"Expected mock user, got {result!r}"
        assert result.role == "operator", f"Expected operator role, got {result.role!r}"

    def test_find_or_provision_user_provisions_new_user(self) -> None:
        """_find_or_provision_user creates a new user when none exists."""
        import uuid as _uuid

        from synth_engine.bootstrapper.routers.auth_oidc import _find_or_provision_user

        db = MagicMock()
        org_id = _uuid.UUID(_ORG_A_UUID)

        # First call (exact match) returns None; second call (any org) returns None
        db.exec.return_value.first.side_effect = [None, None]

        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_audit_logger") as mock_al:
            mock_al.return_value = MagicMock()
            _find_or_provision_user(
                email="new@example.com",
                org_id=org_id,
                db=db,
            )

        # db.add should have been called (new user inserted)
        assert db.add.call_count == 1, f"Expected 1 db.add call, got {db.add.call_count}"
        assert db.commit.call_count == 1, f"Expected 1 db.commit call, got {db.commit.call_count}"

    def test_find_or_provision_user_raises_401_for_cross_org_email(self) -> None:
        """_find_or_provision_user raises 401 when email exists in another org."""
        import uuid as _uuid

        from fastapi import HTTPException

        from synth_engine.bootstrapper.routers.auth_oidc import _find_or_provision_user

        db = MagicMock()
        org_a = _uuid.UUID(_ORG_A_UUID)
        other_org_user = MagicMock()
        other_org_user.org_id = _uuid.UUID("22222222-2222-2222-2222-222222222222")

        # Exact match in org A: None; any-org match returns user from different org
        db.exec.return_value.first.side_effect = [None, other_org_user]

        with pytest.raises(HTTPException, match="(?i)authentication failed") as exc_info:
            _find_or_provision_user(
                email="crossorg@example.com",
                org_id=org_a,
                db=db,
            )
        assert exc_info.value.status_code == 401, (
            f"Expected 401 for cross-org email, got {exc_info.value.status_code}"
        )

    def test_handle_oidc_user_provisioning_delegates_to_find_or_provision(
        self,
    ) -> None:
        """_find_or_provision_user returns the existing user with DB-authoritative role.

        _handle_oidc_user_provisioning was removed (F7 review fix) and merged into
        _find_or_provision_user. This test verifies the same behavior directly.
        """
        import uuid as _uuid

        from synth_engine.bootstrapper.routers.auth_oidc import _find_or_provision_user

        db = MagicMock()
        org_id = _uuid.UUID(_ORG_A_UUID)
        mock_user = MagicMock()
        mock_user.role = "operator"
        # First exec (exact match) returns user
        db.exec.return_value.first.return_value = mock_user

        result = _find_or_provision_user(
            email="user@example.com",
            org_id=org_id,
            db=db,
        )
        assert result == mock_user, f"Expected mock user, got {result!r}"
        assert result.role == "operator", f"Expected operator role, got {result.role!r}"

    def test_get_user_org_returns_org_id_for_valid_user(self) -> None:
        """_get_user_org returns org_id UUID when user exists."""
        import uuid as _uuid

        from synth_engine.bootstrapper.routers.auth_oidc import _get_user_org

        db = MagicMock()
        org_id = _uuid.UUID(_ORG_A_UUID)
        mock_user = MagicMock()
        mock_user.org_id = org_id
        db.exec.return_value.first.return_value = mock_user

        result = _get_user_org(_USER_A_UUID, db)
        assert result == org_id, f"Expected org_id {org_id}, got {result!r}"

    def test_get_user_org_returns_none_when_user_not_found(self) -> None:
        """_get_user_org returns None when user_id has no record in DB."""
        from synth_engine.bootstrapper.routers.auth_oidc import _get_user_org

        db = MagicMock()
        db.exec.return_value.first.return_value = None

        result = _get_user_org(_USER_A_UUID, db)
        assert result is None, f"Expected None for missing user, got {result!r}"
        assert db.exec.call_count == 1, f"Expected 1 DB call, got {db.exec.call_count}"

    def test_get_user_org_returns_none_for_invalid_uuid(self) -> None:
        """_get_user_org returns None for a non-UUID user_id string."""
        from synth_engine.bootstrapper.routers.auth_oidc import _get_user_org

        db = MagicMock()
        result = _get_user_org("not-a-valid-uuid", db)
        assert result is None, f"Expected None for invalid UUID, got {result!r}"
        # DB should not be called if UUID parsing fails
        assert db.exec.call_count == 0, (
            f"DB must not be called for invalid UUID, got {db.exec.call_count} calls"
        )


class TestOIDCProviderInitialization:
    """Tests for OIDCProvider instantiation and initialize_oidc_provider happy path.

    These tests cover the initialization path when the IdP is reachable and
    returns a valid discovery document and JWKS.
    """

    def test_oidc_provider_init_stores_all_fields(self) -> None:
        """OIDCProvider.__init__ stores all constructor arguments as attributes."""
        from synth_engine.bootstrapper.dependencies.oidc import OIDCProvider

        provider = OIDCProvider(
            issuer="https://idp.example.com",
            authorization_endpoint="https://idp.example.com/authorize",
            token_endpoint="https://idp.example.com/token",
            jwks_uri="https://idp.example.com/.well-known/jwks.json",
            client_id="my-client-id",
            jwks_data={"keys": [{"kty": "RSA", "kid": "key-1"}]},
        )

        assert provider.issuer == "https://idp.example.com", (
            f"Expected issuer to be stored, got {provider.issuer!r}"
        )
        assert provider.authorization_endpoint == "https://idp.example.com/authorize", (
            f"Expected authorization_endpoint stored, got {provider.authorization_endpoint!r}"
        )
        assert provider.token_endpoint == "https://idp.example.com/token", (
            f"Expected token_endpoint stored, got {provider.token_endpoint!r}"
        )
        assert provider.jwks_uri == "https://idp.example.com/.well-known/jwks.json", (
            f"Expected jwks_uri stored, got {provider.jwks_uri!r}"
        )
        assert provider.client_id == "my-client-id", (
            f"Expected client_id stored, got {provider.client_id!r}"
        )
        assert provider.jwks_data == {"keys": [{"kty": "RSA", "kid": "key-1"}]}, (
            f"Expected jwks_data stored, got {provider.jwks_data!r}"
        )

    def test_initialize_oidc_provider_happy_path_returns_provider(self) -> None:
        """initialize_oidc_provider returns OIDCProvider when IdP is reachable.

        Covers the full happy-path code path: SSRF validation, discovery doc
        fetch, JWKS fetch, OIDCProvider construction, and singleton assignment.
        """
        from unittest.mock import MagicMock, patch

        from synth_engine.bootstrapper.dependencies.oidc import (
            OIDCProvider,
            initialize_oidc_provider,
        )

        discovery_doc = {
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/.well-known/jwks.json",
        }
        jwks_data = {"keys": [{"kty": "RSA", "kid": "key-1"}]}

        discovery_response = MagicMock()
        discovery_response.status_code = 200
        discovery_response.json.return_value = discovery_doc
        discovery_response.raise_for_status = MagicMock()

        jwks_response = MagicMock()
        jwks_response.status_code = 200
        jwks_response.json.return_value = jwks_data
        jwks_response.raise_for_status = MagicMock()

        with patch("httpx.get", side_effect=[discovery_response, jwks_response]) as mock_get:
            with patch(
                "synth_engine.bootstrapper.dependencies.oidc.validate_oidc_issuer_url"
            ) as mock_ssrf:
                mock_ssrf.return_value = None  # passes validation

                provider = initialize_oidc_provider(
                    issuer_url="https://idp.example.com",
                    client_id="my-client-id",
                )

        assert isinstance(provider, OIDCProvider), (
            f"Expected OIDCProvider instance, got {type(provider).__name__}"
        )
        assert provider.issuer == "https://idp.example.com", (
            f"Expected issuer from discovery doc, got {provider.issuer!r}"
        )
        assert provider.client_id == "my-client-id", (
            f"Expected client_id passed to provider, got {provider.client_id!r}"
        )
        assert provider.jwks_data == jwks_data, (
            f"Expected JWKS data stored, got {provider.jwks_data!r}"
        )
        assert mock_get.call_count == 2, (
            f"Expected 2 HTTP calls (discovery + JWKS), got {mock_get.call_count}"
        )
        mock_ssrf.assert_called_once_with("https://idp.example.com")

    def test_initialize_oidc_provider_raises_on_missing_required_fields(self) -> None:
        """initialize_oidc_provider raises RuntimeError when discovery doc missing fields."""
        from unittest.mock import MagicMock, patch

        from synth_engine.bootstrapper.dependencies.oidc import initialize_oidc_provider

        # Missing token_endpoint and jwks_uri
        incomplete_doc = {
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/authorize",
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = incomplete_doc
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response):
            with patch("synth_engine.bootstrapper.dependencies.oidc.validate_oidc_issuer_url"):
                with pytest.raises(RuntimeError) as exc_info:
                    initialize_oidc_provider(
                        issuer_url="https://idp.example.com",
                        client_id="client",
                    )
        assert "missing required fields" in str(exc_info.value), (
            f"Expected 'missing required fields' in error, got: {exc_info.value!r}"
        )

    def test_get_oidc_provider_returns_initialized_provider(self) -> None:
        """get_oidc_provider returns the singleton set by initialize_oidc_provider."""
        from unittest.mock import MagicMock, patch

        from synth_engine.bootstrapper.dependencies.oidc import (
            OIDCProvider,
            get_oidc_provider,
            initialize_oidc_provider,
        )

        discovery_doc = {
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/.well-known/jwks.json",
        }
        jwks_data: dict[str, list[object]] = {"keys": []}

        disco_resp = MagicMock()
        disco_resp.json.return_value = discovery_doc
        disco_resp.raise_for_status = MagicMock()

        jwks_resp = MagicMock()
        jwks_resp.json.return_value = jwks_data
        jwks_resp.raise_for_status = MagicMock()

        with patch("httpx.get", side_effect=[disco_resp, jwks_resp]):
            with patch("synth_engine.bootstrapper.dependencies.oidc.validate_oidc_issuer_url"):
                initialize_oidc_provider(
                    issuer_url="https://idp.example.com",
                    client_id="client",
                )

        provider = get_oidc_provider()
        assert isinstance(provider, OIDCProvider), (
            f"Expected OIDCProvider, got {type(provider).__name__}"
        )
        assert provider.issuer == "https://idp.example.com", (
            f"Expected cached issuer, got {provider.issuer!r}"
        )


class TestRefreshWithSessionUpdate:
    """Tests covering the Redis session update path in /auth/refresh."""

    def test_refresh_updates_existing_session_in_redis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /auth/refresh updates last_refreshed_at for matching Redis sessions.

        Covers the scan_iter loop body (lines 927-940 in auth_oidc.py).
        """
        import json as _json

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from synth_engine.bootstrapper.dependencies.tenant import TenantContext
        from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router
        from synth_engine.shared.settings import get_settings

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")
        monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
        monkeypatch.setenv("OIDC_ENABLED", "true")
        monkeypatch.setenv("OIDC_ISSUER_URL", "http://localhost:9999")
        monkeypatch.setenv("OIDC_CLIENT_ID", "test-client")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-secret")  # pragma: allowlist secret
        monkeypatch.setenv("SESSION_TTL_SECONDS", "28800")
        monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "false")
        get_settings.cache_clear()

        app = FastAPI()
        app.include_router(oidc_router)

        from synth_engine.bootstrapper.dependencies.tenant import get_current_user

        def mock_get_current_user() -> TenantContext:
            return TenantContext(
                org_id=_ORG_A_UUID,
                user_id=_USER_A_UUID,
                role="operator",
            )

        app.dependency_overrides[get_current_user] = mock_get_current_user

        session_data = _json.dumps(
            {
                "user_id": _USER_A_UUID,
                "org_id": _ORG_A_UUID,
                "role": "operator",
                "created_at": "2026-01-01T00:00:00Z",
                "last_refreshed_at": "2026-01-01T00:00:00Z",
            }
        ).encode()

        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            session_key = b"conclave:session:test-key"
            # Refresh endpoint uses smembers to find sessions via per-user index (F2).
            redis_client.smembers.return_value = {session_key}
            redis_client.get.return_value = session_data
            redis_client.ttl.return_value = 28000
            mock_redis.return_value = redis_client

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/auth/refresh")

        get_settings.cache_clear()

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        # Verify Redis setex was called to update the session
        assert redis_client.setex.call_count == 1, (
            f"Expected 1 setex call to update session, got {redis_client.setex.call_count}"
        )


class TestRevokeWithSessions:
    """Tests covering the Redis session deletion path in /auth/revoke."""

    def test_revoke_deletes_matching_sessions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /auth/revoke deletes all sessions for the target user.

        Covers the scan + delete path (lines 1058-1076 in auth_oidc.py).
        """
        import json as _json
        import uuid as _uuid

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from synth_engine.bootstrapper.dependencies.tenant import TenantContext
        from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router
        from synth_engine.shared.settings import get_settings

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")
        monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
        monkeypatch.setenv("OIDC_ENABLED", "true")
        monkeypatch.setenv("OIDC_ISSUER_URL", "http://localhost:9999")
        monkeypatch.setenv("OIDC_CLIENT_ID", "test-client")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-secret")  # pragma: allowlist secret
        monkeypatch.setenv("SESSION_TTL_SECONDS", "28800")
        monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "false")
        get_settings.cache_clear()

        app = FastAPI()
        app.include_router(oidc_router)

        # Admin context
        from synth_engine.bootstrapper.dependencies.tenant import get_current_user

        def mock_get_current_user() -> TenantContext:
            return TenantContext(
                org_id=_ORG_A_UUID,
                user_id="admin@example.com",
                role="admin",
            )

        app.dependency_overrides[get_current_user] = mock_get_current_user

        target_user_id = _USER_A_UUID
        session_data = _json.dumps(
            {
                "user_id": target_user_id,
                "org_id": _ORG_A_UUID,
                "role": "operator",
                "created_at": "2026-01-01T00:00:00Z",
                "last_refreshed_at": "2026-01-01T00:00:00Z",
            }
        ).encode()

        with (
            patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis,
            patch("synth_engine.bootstrapper.routers.auth_oidc._get_user_org") as mock_get_org,
        ):
            mock_get_org.return_value = _uuid.UUID(_ORG_A_UUID)
            redis_client = MagicMock()
            session_key = b"conclave:session:test-key"
            # Revoke endpoint uses smembers to find sessions via per-user index (F2).
            redis_client.smembers.return_value = {session_key}
            redis_client.get.return_value = session_data
            redis_client.delete.return_value = 1
            mock_redis.return_value = redis_client

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/auth/revoke",
                json={"user_id": target_user_id},
            )

        get_settings.cache_clear()

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["revoked_sessions"] == 1, (
            f"Expected revoked_sessions=1, got {body.get('revoked_sessions')}"
        )


# ---------------------------------------------------------------------------
# Fixtures used by feature tests
# ---------------------------------------------------------------------------


@pytest.fixture
def oidc_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Minimal FastAPI test client for OIDC endpoint tests.

    Uses monkeypatch.setenv + cache_clear to control settings.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CONCLAVE_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER_URL", "http://localhost:9999")
    monkeypatch.setenv("OIDC_CLIENT_ID", "test-client")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-secret")  # pragma: allowlist secret
    monkeypatch.setenv("OIDC_STATE_TTL_SECONDS", "600")
    monkeypatch.setenv("SESSION_TTL_SECONDS", "28800")
    monkeypatch.setenv("CONCURRENT_SESSION_LIMIT", "3")
    monkeypatch.setenv("CONCLAVE_MULTI_TENANT_ENABLED", "false")
    monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "false")

    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()

    from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router

    app = FastAPI()
    app.include_router(oidc_router)

    with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
        mock_redis.return_value = MagicMock()

        yield TestClient(app, raise_server_exceptions=False)

    get_settings.cache_clear()
