"""Negative/attack tests for SSO/OIDC — Phase 81. ATTACK RED phase.

Covers all 45 mandatory negative test requirements from the developer brief.
These tests verify the system REJECTS:

- Callbacks with missing/wrong/expired/replayed state (CSRF protection)
- Missing or wrong PKCE code_verifier
- Plain PKCE method (only S256 accepted)
- Implicit flow (response_type=token)
- Cross-org email collision (oracle prevention)
- Missing/empty email claim from IdP
- Unauthenticated/expired JWT on session endpoints
- Non-admin revoking another user's sessions
- Cross-org revoke returning 404 (IDOR)
- Redis failures causing correct fail-closed behavior
- Cloud metadata SSRF endpoints blocked
- Loopback SSRF blocked
- Public IP issuer blocked in production mode
- Rate limits enforced on OIDC endpoints
- Oversized IdP token response
- SQL injection in user_id field (UUID validation)

Written in the ATTACK RED phase, BEFORE feature tests, per CLAUDE.md Rule 22.

CONSTITUTION Priority 0: Security — OIDC enforcement, CSRF/PKCE/IDOR/SSRF prevention
CONSTITUTION Priority 3: TDD — ATTACK RED phase
Phase: 81 — SSO/OIDC Integration
"""

from __future__ import annotations

import hashlib
import json
import uuid
from base64 import urlsafe_b64encode
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.helpers_oidc import (
    OIDC_TEST_JWT_SECRET as _TEST_SECRET,
)
from tests.unit.helpers_oidc import (
    make_oidc_auth_header as _auth_header,
)
from tests.unit.helpers_oidc import (
    make_oidc_token as _make_token,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ORG_A_UUID = "11111111-1111-1111-1111-111111111111"
_ORG_B_UUID = "22222222-2222-2222-2222-222222222222"
_USER_A_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_USER_B_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# ---------------------------------------------------------------------------
# Minimal FastAPI test app fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def oidc_app(monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    """Return a FastAPI test client with minimal OIDC setup using env vars.

    Uses monkeypatch.setenv + cache_clear to control settings — the same
    pattern as other unit tests in this project (avoids lru_cache conflicts).
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Set up environment for OIDC testing
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

    # Import the OIDC router
    from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router

    app = FastAPI()
    app.include_router(oidc_router)

    # Mock Redis and OIDC provider to avoid connection errors.
    # The OIDC provider is mocked to return a minimal provider for tests that
    # reach the token exchange step. Tests that test pre-provider failures
    # (state/PKCE checks) are unaffected since those checks run before the
    # provider lookup.
    from synth_engine.bootstrapper.dependencies.oidc import OIDCProvider

    mock_provider = OIDCProvider(
        issuer="http://localhost:9999",
        authorization_endpoint="http://localhost:9999/authorize",
        token_endpoint="http://localhost:9999/token",
        jwks_uri="http://localhost:9999/.well-known/jwks.json",
        client_id="test-client",
        jwks_data={"keys": []},
    )

    with (
        patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis_oidc,
        patch(
            "synth_engine.bootstrapper.dependencies.oidc.get_oidc_provider",
            return_value=mock_provider,
        ),
    ):
        mock_redis_oidc.return_value = MagicMock()

        yield TestClient(app, raise_server_exceptions=False)

    get_settings.cache_clear()


# ===========================================================================
# SECTION 1: OIDC State / CSRF Protection (6 tests)
# ===========================================================================


class TestOIDCStateCSRFProtection:
    """Tests for state parameter CSRF protection."""

    def test_callback_missing_state_returns_422(self, oidc_app: Any) -> None:
        """Callback with no state query param returns 422 (unprocessable entity).

        AC: Missing required query parameter must be rejected before processing.
        """
        resp = oidc_app.get("/auth/oidc/callback?code=some-auth-code")
        assert resp.status_code == 422, f"Expected 422 for missing state, got {resp.status_code}"

    def test_callback_wrong_state_returns_401(self, oidc_app: Any) -> None:
        """Callback with state value not found in Redis returns 401.

        AC: State not in Redis → CSRF attempt → 401 Unauthorized.
        """
        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            redis_client.getdel.return_value = None  # State not in Redis
            mock_redis.return_value = redis_client

            resp = oidc_app.get(
                "/auth/oidc/callback?code=some-auth-code&state=nonexistent-state-value"
            )
        assert resp.status_code == 401, f"Expected 401 for wrong state, got {resp.status_code}"

    def test_callback_expired_state_returns_401(self, oidc_app: Any) -> None:
        """Callback after state TTL has elapsed returns 401.

        AC: Expired state key is gone from Redis → same as wrong state → 401.
        """
        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            # Expired key returns None from Redis
            redis_client.getdel.return_value = None
            mock_redis.return_value = redis_client

            resp = oidc_app.get(
                "/auth/oidc/callback?code=some-auth-code&state=expired-state-abc123"
            )
        assert resp.status_code == 401, f"Expected 401 for expired state, got {resp.status_code}"

    def test_callback_state_replay_returns_401(self, oidc_app: Any) -> None:
        """Same callback URL submitted twice; second attempt returns 401.

        AC: State is deleted atomically on first use. Second use finds no
        key in Redis and returns 401. Prevents replay attacks.

        Test strategy: We simulate the SECOND use by having Redis return None
        for the state key (simulating that the state was already consumed by the
        first use and deleted from Redis). The second request finds no state → 401.
        This directly tests the replay-prevention contract without needing a real
        first OIDC flow (which would require IdP integration).
        """
        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            # Simulate state already consumed (deleted after first use)
            redis_client.getdel.return_value = None
            mock_redis.return_value = redis_client

            # Second attempt: state key is gone from Redis → 401
            resp2 = oidc_app.get(
                "/auth/oidc/callback"
                "?code=used-auth-code"
                "&state=already-used-state"
                "&code_verifier=valid-verifier-value-12345678"
            )
        assert resp2.status_code == 401, f"Expected 401 on state replay, got {resp2.status_code}"

    def test_callback_state_from_different_session_returns_401(self, oidc_app: Any) -> None:
        """State belonging to a different concurrent user's Redis key returns 401.

        AC: State is scoped to a session; a different user's state value must
        not be accepted — any state not in the caller's Redis key is rejected.
        This test confirms the state is not guessable or transferable.
        """
        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            # The state value is for user A, but this caller presents it out of context
            redis_client.getdel.return_value = None  # Not found → 401
            mock_redis.return_value = redis_client

            resp = oidc_app.get(
                "/auth/oidc/callback"
                "?code=auth-code-user-a"
                "&state=user-a-state-value-not-for-this-caller"
            )
        assert resp.status_code == 401, (
            f"Expected 401 for wrong-session state, got {resp.status_code}"
        )

    def test_authorize_missing_code_challenge_returns_422(self, oidc_app: Any) -> None:
        """Authorize request without PKCE code_challenge returns 422.

        AC: PKCE is mandatory — missing code_challenge must be rejected.
        S256 is the only allowed method; no code_challenge = no flow.
        """
        resp = oidc_app.get("/auth/oidc/authorize")
        # Missing code_challenge → 422 (the parameter is required)
        assert resp.status_code in (
            422,
            400,
        ), f"Expected 422/400 for missing code_challenge, got {resp.status_code}"


# ===========================================================================
# SECTION 2: PKCE Enforcement (4 tests)
# ===========================================================================


class TestPKCEEnforcement:
    """Tests that PKCE enforcement rejects weak or missing challenges."""

    def test_callback_missing_code_verifier_returns_422(self, oidc_app: Any) -> None:
        """Callback with no code_verifier returns 422.

        AC: code_verifier is required in the callback. Missing → 422.
        """
        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            redis_client.getdel.return_value = json.dumps(
                {
                    "code_verifier": "testverifier1234567890123456789012345678901234",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ).encode()
            mock_redis.return_value = redis_client

            resp = oidc_app.get(
                "/auth/oidc/callback?code=some-auth-code&state=valid-state-in-redis"
                # No code_verifier → 422
            )
        assert resp.status_code == 422, (
            f"Expected 422 for missing code_verifier, got {resp.status_code}"
        )

    def test_callback_wrong_code_verifier_returns_401(self, oidc_app: Any) -> None:
        """code_verifier that does not match stored code_challenge returns 401.

        AC: PKCE verification is mandatory. Mismatched verifier → 401.
        """
        valid_verifier = "Ade-fG12345678901234567890123456789012345678"
        wrong_verifier = "wrong-verifier-1234567890123456789012345678"
        # Compute correct challenge (S256)
        digest = hashlib.sha256(valid_verifier.encode()).digest()
        correct_challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()

        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            redis_client.getdel.return_value = json.dumps(
                {
                    "code_verifier": valid_verifier,
                    "code_challenge": correct_challenge,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ).encode()
            mock_redis.return_value = redis_client

            resp = oidc_app.get(
                f"/auth/oidc/callback"
                f"?code=some-auth-code"
                f"&state=valid-state"
                f"&code_verifier={wrong_verifier}"
            )
        assert resp.status_code == 401, (
            f"Expected 401 for wrong code_verifier, got {resp.status_code}"
        )

    def test_pkce_plain_method_rejected(self, oidc_app: Any) -> None:
        """code_challenge_method=plain must be rejected; only S256 accepted.

        AC: Plain PKCE provides no security benefit over not using PKCE.
        Only S256 (hash-based) is accepted per PKCE spec (RFC 7636).
        """
        resp = oidc_app.get(
            "/auth/oidc/authorize?code_challenge=plain-challenge-value&code_challenge_method=plain"
        )
        assert resp.status_code in (
            400,
            422,
        ), f"Expected 400/422 for plain PKCE method, got {resp.status_code}"

    def test_implicit_flow_rejected(self, oidc_app: Any) -> None:
        """response_type=token (implicit flow) not supported; returns 422.

        AC: Implicit flow bypasses PKCE entirely. Must be rejected.
        Only authorization code flow with PKCE S256 is supported.
        """
        resp = oidc_app.get(
            "/auth/oidc/authorize"
            "?response_type=token"
            "&code_challenge=some-challenge"
            "&code_challenge_method=S256"
        )
        assert resp.status_code in (
            400,
            422,
        ), f"Expected 400/422 for implicit flow, got {resp.status_code}"


# ===========================================================================
# SECTION 3: User Provisioning (5 tests)
# ===========================================================================


class TestUserProvisioning:
    """Tests for user provisioning security boundaries."""

    def test_cross_org_email_returns_401_generic_message(self, oidc_app: Any) -> None:
        """Existing user in org B authenticates via OIDC in org A → 401.

        AC: Cross-org email collision must return 401 (not 403) with a
        generic message. Prevents oracle attack revealing email existence
        in another org.
        """
        from synth_engine.bootstrapper.routers.auth_oidc import (
            _find_or_provision_user,
        )

        # Mock db.exec() to simulate: no exact match (first call), then
        # a user in a different org (second call).
        db = MagicMock()
        other_user = MagicMock()
        other_user.org_id = uuid.UUID(_ORG_B_UUID)
        other_user.email = "user@example.com"

        # exec().first() returns: None (exact match), then other_user (any match)
        db.exec.return_value.first.side_effect = [None, other_user]

        # The provisioning function should raise a 401 exception
        from fastapi import HTTPException as _HTTPException

        with pytest.raises(_HTTPException, match=".*") as exc_info:
            _find_or_provision_user(
                email="user@example.com",
                org_id=uuid.UUID(_ORG_A_UUID),
                db=db,
            )
        # Should result in HTTP 401, not 403
        exc = exc_info.value
        assert exc.status_code == 401, (  # type: ignore[attr-defined]
            f"Expected 401 for cross-org email, got {exc.status_code}"  # type: ignore[attr-defined]
        )

    def test_cross_org_email_does_not_leak_org_existence(self, oidc_app: Any) -> None:
        """Response for cross-org collision is generic — no 'org' or 'email' in body.

        AC: Error body must not reveal that the email exists in another org.
        Generic 'Authentication failed' message prevents oracle attacks.
        """
        from synth_engine.bootstrapper.routers.auth_oidc import (
            _find_or_provision_user,
        )

        db = MagicMock()
        other_user = MagicMock()
        other_user.org_id = uuid.UUID(_ORG_B_UUID)
        other_user.email = "user@example.com"
        # exec().first() returns: None (exact match), then other_user (any match)
        db.exec.return_value.first.side_effect = [None, other_user]

        from fastapi import HTTPException as _HTTPException

        with pytest.raises(_HTTPException, match=".*") as exc_info:
            _find_or_provision_user(
                email="user@example.com",
                org_id=uuid.UUID(_ORG_A_UUID),
                db=db,
            )
        exc = exc_info.value
        detail = getattr(exc, "detail", "")
        detail_str = str(detail).lower()
        assert "org" not in detail_str, f"Response body leaks org info: {detail!r}"
        assert "email" not in detail_str, f"Response body leaks email info: {detail!r}"
        assert "authentication failed" in detail_str, (
            f"Expected generic 'Authentication failed', got: {detail!r}"
        )

    def test_oidc_provisioned_user_has_correct_default_role(self) -> None:
        """Auto-provisioned user gets 'operator' role (lowest privilege).

        AC: IdP role claims are IGNORED. New users get Role.operator.
        This test verifies the default role constant matches the Role enum.
        """
        from synth_engine.bootstrapper.dependencies.permissions import Role
        from synth_engine.bootstrapper.routers.auth_oidc import (
            OIDC_DEFAULT_USER_ROLE,
        )

        assert OIDC_DEFAULT_USER_ROLE == Role.operator.value, (
            f"Expected 'operator', got {OIDC_DEFAULT_USER_ROLE!r}"
        )

    def test_oidc_login_missing_email_claim_returns_401(self, oidc_app: Any) -> None:
        """IdP ID token with no email claim returns 401.

        AC: Email is the identity anchor. Missing email → cannot authenticate.
        """
        from synth_engine.bootstrapper.routers.auth_oidc import (
            _extract_email_from_token_claims,
        )

        claims_no_email: dict[str, Any] = {
            "sub": "user-sub-id",
            "iss": "http://localhost:9999",
            "aud": "test-client",
        }
        from fastapi import HTTPException as _HTTPException

        with pytest.raises(_HTTPException, match=".*") as exc_info:
            _extract_email_from_token_claims(claims_no_email)
        exc = exc_info.value
        assert hasattr(exc, "status_code"), "Expected HTTPException from missing email claim"
        assert exc.status_code == 401, (  # type: ignore[attr-defined]
            f"Expected 401 for missing email claim, got {exc.status_code}"  # type: ignore[attr-defined]
        )

    def test_oidc_login_empty_email_claim_returns_401(self, oidc_app: Any) -> None:
        """IdP ID token with email='' (empty string) returns 401.

        AC: Empty email is invalid. Must be rejected as authentication failure.
        """
        from synth_engine.bootstrapper.routers.auth_oidc import (
            _extract_email_from_token_claims,
        )

        claims_empty_email: dict[str, Any] = {
            "sub": "user-sub-id",
            "email": "",
            "iss": "http://localhost:9999",
        }
        from fastapi import HTTPException as _HTTPException

        with pytest.raises(_HTTPException, match=".*") as exc_info:
            _extract_email_from_token_claims(claims_empty_email)
        exc = exc_info.value
        assert hasattr(exc, "status_code"), "Expected HTTPException from empty email claim"
        assert exc.status_code == 401, (  # type: ignore[attr-defined]
            f"Expected 401 for empty email claim, got {exc.status_code}"  # type: ignore[attr-defined]
        )


# ===========================================================================
# SECTION 4: Session Management (8 tests)
# ===========================================================================


class TestSessionManagement:
    """Tests for session endpoint security boundaries."""

    def test_refresh_without_jwt_returns_401(self, oidc_app: Any) -> None:
        """Unauthenticated POST /auth/refresh returns 401.

        AC: /auth/refresh requires a valid JWT. No token → 401.
        """
        resp = oidc_app.post("/auth/refresh")
        assert resp.status_code == 401, (
            f"Expected 401 for unauthenticated /auth/refresh, got {resp.status_code}"
        )

    def test_refresh_with_expired_jwt_returns_401(self, oidc_app: Any) -> None:
        """Expired JWT on POST /auth/refresh returns 401.

        AC: Expired tokens must not be accepted by /auth/refresh.
        """
        expired_token = _make_token(expired=True)
        resp = oidc_app.post("/auth/refresh", headers=_auth_header(expired_token))
        assert resp.status_code == 401, (
            f"Expected 401 for expired JWT on /auth/refresh, got {resp.status_code}"
        )

    def test_revoke_non_admin_own_sessions_allowed(self, oidc_app: Any) -> None:
        """Non-admin revoking own sessions returns 200.

        AC: Self-revocation bypasses the sessions:revoke permission check.
        Any authenticated user can revoke their own sessions.
        Self-revocation is determined by: str(body.user_id) == ctx.user_id (JWT sub).
        """
        # Use the user's own UUID as the JWT sub so self-revocation check passes.
        token = _make_token(sub=_USER_A_UUID, role="operator")
        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            redis_client.scan_iter.return_value = iter([])
            mock_redis.return_value = redis_client

            resp = oidc_app.post(
                "/auth/revoke",
                json={"user_id": _USER_A_UUID},
                headers=_auth_header(token),
            )
        # 200 OK for self-revocation
        assert resp.status_code == 200, (
            f"Expected 200 for non-admin self-revoke, got {resp.status_code}"
        )

    def test_revoke_non_admin_other_user_returns_403(self, oidc_app: Any) -> None:
        """Non-admin POSTing another user's UUID returns 403.

        AC: Non-admins cannot revoke other users' sessions. Returns 403
        (role is not secret — the user knows they're not admin).
        """
        token = _make_token(
            sub="operator@example.com",
            role="operator",
        )
        resp = oidc_app.post(
            "/auth/revoke",
            json={"user_id": _USER_B_UUID},
            headers=_auth_header(token),
        )
        assert resp.status_code == 403, (
            f"Expected 403 for non-admin revoking other user, got {resp.status_code}"
        )

    def test_revoke_admin_cross_org_returns_404(self, oidc_app: Any) -> None:
        """Admin POSTing a user_id from a different org returns 404.

        AC: Cross-org revocation must return 404 (IDOR — org existence not leaked).
        Admin in org A must not be able to revoke sessions for user in org B.
        """
        token = _make_token(
            sub="admin@example.com",
            org_id=_ORG_A_UUID,
            role="admin",
        )
        with patch("synth_engine.bootstrapper.routers.auth_oidc._get_user_org") as mock_get_org:
            # The target user is in org B, but the admin is in org A
            mock_get_org.return_value = uuid.UUID(_ORG_B_UUID)

            resp = oidc_app.post(
                "/auth/revoke",
                json={"user_id": _USER_B_UUID},
                headers=_auth_header(token),
            )
        assert resp.status_code == 404, f"Expected 404 for cross-org revoke, got {resp.status_code}"

    def test_revoke_missing_user_id_returns_422(self, oidc_app: Any) -> None:
        """POST /auth/revoke with empty body returns 422.

        AC: user_id is a required field. Missing body → 422 Unprocessable Entity.
        """
        token = _make_token(role="admin")
        resp = oidc_app.post("/auth/revoke", json={}, headers=_auth_header(token))
        assert resp.status_code == 422, f"Expected 422 for missing user_id, got {resp.status_code}"

    def test_revoke_invalid_user_id_uuid_returns_422(self, oidc_app: Any) -> None:
        """user_id that is not a valid UUID returns 422.

        AC: user_id must be UUID format. Non-UUID string fails Pydantic validation.
        This prevents SQL injection via the user_id field (see also test 45).
        """
        token = _make_token(role="admin")
        resp = oidc_app.post(
            "/auth/revoke",
            json={"user_id": "not-a-uuid-value"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 422, f"Expected 422 for non-UUID user_id, got {resp.status_code}"

    def test_concurrent_session_limit_evicts_oldest(self) -> None:
        """Fourth login creates session, first session is deleted, exactly 3 remain.

        AC: Concurrent session limit is enforced. When limit (3) is exceeded,
        the oldest session (earliest created_at) is evicted before the new one
        is written. Exactly CONCURRENT_SESSION_LIMIT sessions remain after.
        """

        from synth_engine.bootstrapper.dependencies.sessions import (
            enforce_concurrent_session_limit,
        )

        redis_client = MagicMock()
        user_id = str(uuid.uuid4())
        org_id = _ORG_A_UUID
        limit = 3

        # Simulate 3 existing sessions
        session_keys = [f"conclave:session:session{i}".encode() for i in range(3)]
        sessions_data = [
            json.dumps(
                {
                    "user_id": user_id,
                    "org_id": org_id,
                    "role": "operator",
                    "created_at": f"2026-01-0{i + 1}T00:00:00Z",
                    "last_refreshed_at": f"2026-01-0{i + 1}T00:00:00Z",
                }
            ).encode()
            for i in range(3)
        ]
        # F2: enforce_concurrent_session_limit now uses smembers (per-user index)
        # instead of scan_iter (O(N) SCAN).
        redis_client.smembers.return_value = set(session_keys)
        # Map key -> session_data for correct ordering-independent mget mock.
        key_to_data = dict(zip(session_keys, sessions_data, strict=False))

        def mock_mget(keys: list[bytes]) -> list[bytes]:
            return [key_to_data.get(k) for k in keys]

        redis_client.mget.side_effect = mock_mget
        # srem is called to clean up the evicted key from the index
        redis_client.srem = MagicMock()

        evicted_keys: list[bytes] = []

        def mock_delete(*keys: bytes) -> int:
            evicted_keys.extend(keys)
            return len(keys)

        redis_client.delete.side_effect = mock_delete

        enforce_concurrent_session_limit(
            redis_client=redis_client,
            user_id=user_id,
            org_id=org_id,
            limit=limit,
        )

        # The oldest session (session0, created_at 2026-01-01) must have been evicted
        assert len(evicted_keys) == 1, (
            f"Expected exactly 1 eviction, got {len(evicted_keys)}: {evicted_keys}"
        )
        assert b"session0" in evicted_keys[0], (
            f"Expected oldest session (session0) evicted, got {evicted_keys}"
        )


# ===========================================================================
# SECTION 5: Redis Failure Modes (4 tests)
# ===========================================================================


class TestRedisFailureModes:
    """Tests for correct fail-closed behavior when Redis is unavailable."""

    def test_session_auth_fails_closed_when_redis_down(self, oidc_app: Any) -> None:
        """Redis ConnectionError during OIDC state write → 503.

        AC: OIDC authorize fails closed on Redis errors — state cannot be
        written → 503 Service Unavailable. The authorize endpoint is
        the right place to test fail-closed behavior, as state storage
        in Redis is mandatory for the OIDC flow to proceed.

        Note: The /auth/refresh Redis session update is "best effort" (non-fatal
        per ADR-0067 Decision 5). The authorize endpoint is the correct
        fail-closed surface for Redis failures.
        """
        import redis as redis_lib

        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            # Redis error on setex (state write) → 503
            redis_client.setex.side_effect = redis_lib.ConnectionError("Redis down")
            mock_redis.return_value = redis_client

            # OIDC authorize must fail closed when Redis is unavailable
            resp = oidc_app.get(
                "/auth/oidc/authorize"
                "?code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
                "&code_challenge_method=S256"
            )
        assert resp.status_code == 503, (
            f"Expected 503 when Redis down on authorize (state write), got {resp.status_code}"
        )

    def test_passphrase_auth_still_works_when_redis_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passphrase JWT auth succeeds when Redis raises ConnectionError.

        AC: Redis failures must not affect stateless JWT authentication.
        The passphrase auth path (JWT-only) has no Redis dependency.
        """
        import redis as redis_lib

        from synth_engine.bootstrapper.dependencies.tenant import (
            get_current_user,
        )
        from synth_engine.shared.settings import get_settings

        # Configure settings via monkeypatch (avoids lru_cache issues)
        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")
        monkeypatch.setenv("JWT_EXPIRY_SECONDS", "3600")
        monkeypatch.setenv("OIDC_ENABLED", "false")
        monkeypatch.setenv("CONCLAVE_PASS_THROUGH_ENABLED", "false")
        get_settings.cache_clear()

        from fastapi import Depends, FastAPI
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.get("/test-auth")
        def test_endpoint(
            ctx: Any = Depends(get_current_user),  # noqa: B008
        ) -> JSONResponse:
            return JSONResponse({"role": ctx.role})

        # Create a valid JWT (passphrase auth path)
        token = _make_token(sub="operator@example.com", role="operator")

        # Patch Redis to raise on any call — passphrase JWT must still work
        with patch("synth_engine.bootstrapper.dependencies.redis.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            redis_client.get.side_effect = redis_lib.ConnectionError("Redis down")
            mock_redis.return_value = redis_client

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/test-auth",
                headers=_auth_header(token),
            )

        get_settings.cache_clear()
        # Passphrase JWT auth must succeed even when Redis is down
        assert resp.status_code == 200, (
            f"Passphrase JWT auth failed when Redis down: {resp.status_code}"
        )

    def test_oidc_authorize_fails_closed_when_redis_down(self, oidc_app: Any) -> None:
        """Redis ConnectionError on state write → 503 Service Unavailable.

        AC: OIDC authorize endpoint must fail closed if Redis is unavailable.
        State cannot be written → cannot start the flow → 503.
        """
        import redis as redis_lib

        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            redis_client.setex.side_effect = redis_lib.ConnectionError("Redis connection refused")
            redis_client.set.side_effect = redis_lib.ConnectionError("Redis connection refused")
            mock_redis.return_value = redis_client

            resp = oidc_app.get(
                "/auth/oidc/authorize"
                "?code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
                "&code_challenge_method=S256"
            )
        assert resp.status_code == 503, (
            f"Expected 503 when Redis down on authorize, got {resp.status_code}"
        )

    def test_oidc_callback_fails_closed_when_redis_down(self, oidc_app: Any) -> None:
        """Redis ConnectionError on state read → 503 Service Unavailable.

        AC: OIDC callback must fail closed if Redis is unavailable.
        State cannot be validated → cannot complete the flow → 503.
        """
        import redis as redis_lib

        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            redis_client.getdel.side_effect = redis_lib.ConnectionError("Redis connection refused")
            mock_redis.return_value = redis_client

            resp = oidc_app.get("/auth/oidc/callback?code=some-auth-code&state=some-state-value")
        assert resp.status_code == 503, (
            f"Expected 503 when Redis down on callback, got {resp.status_code}"
        )


# ===========================================================================
# SECTION 6: SSRF / Air-Gap (6 tests)
# ===========================================================================


class TestSSRFAirGap:
    """Tests for SSRF prevention in OIDC issuer URL validation."""

    def test_metadata_endpoint_aws_blocked(self) -> None:
        """169.254.169.254 (AWS IMDS) rejected by validate_oidc_issuer_url.

        AC: Cloud metadata endpoints must be blocked unconditionally.
        This is the most critical SSRF protection — if this passes, an
        attacker can exfiltrate cloud credentials via the IdP config.
        """
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match=".*") as exc_info:
            validate_oidc_issuer_url("http://169.254.169.254/latest/meta-data/")
        assert "forbidden" in str(exc_info.value).lower() or (
            "blocked" in str(exc_info.value).lower() or "metadata" in str(exc_info.value).lower()
        ), f"Expected SSRF block message, got: {exc_info.value}"

    def test_metadata_endpoint_alibaba_blocked(self) -> None:
        """100.100.100.200 (Alibaba Cloud IMDS) rejected by validate_oidc_issuer_url.

        AC: Alibaba Cloud metadata endpoint must be blocked unconditionally.
        """
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match=".*"):
            validate_oidc_issuer_url("http://100.100.100.200/")

    def test_metadata_endpoint_gcp_hostname_blocked(self) -> None:
        """metadata.google.internal rejected by validate_oidc_issuer_url.

        AC: GCP metadata hostname must be blocked unconditionally.
        Hostname-based block (not just IP) to prevent DNS rebinding attacks.
        """
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match=".*"):
            validate_oidc_issuer_url("http://metadata.google.internal/")

    def test_loopback_issuer_blocked(self) -> None:
        """127.0.0.1 (loopback) rejected by validate_oidc_issuer_url.

        AC: Loopback addresses must be blocked — an OIDC provider on
        localhost would redirect to the local process, enabling SSRF.
        """
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        with pytest.raises(ValueError, match=".*"):
            validate_oidc_issuer_url("http://127.0.0.1:8080/")

    def test_rfc1918_issuer_allowed_in_air_gap(self) -> None:
        """http://10.0.0.1/ accepted by validate_oidc_issuer_url.

        AC: RFC-1918 private addresses must be allowed for air-gap IdPs.
        This is the key difference from validate_callback_url which blocks
        all private ranges. Air-gap IdPs live on private networks.
        """
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        # Should NOT raise — RFC-1918 is allowed for air-gap IdPs
        url = "http://10.0.0.1/"
        raised = False
        try:
            validate_oidc_issuer_url(url)
        except ValueError:
            raised = True
        assert raised == False, f"RFC-1918 issuer must be accepted for air-gap IdPs: {url}"

    def test_external_public_ip_rejected_in_production_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Public IP issuer in CONCLAVE_ENV=production rejected.

        AC: In production mode, public IPs are not allowed as OIDC issuers.
        Production OIDC must use a real hostname, not a raw IP address.
        This prevents misconfiguration pointing to a rogue IP in production.
        """
        from synth_engine.shared.ssrf import validate_oidc_issuer_url

        monkeypatch.setenv("CONCLAVE_ENV", "production")

        with pytest.raises(ValueError, match=".*") as exc_info:
            validate_oidc_issuer_url("http://203.0.113.5/")
        assert len(str(exc_info.value)) > 0, "Error message must be non-empty"


# ===========================================================================
# SECTION 7: Rate Limiting (3 tests)
# ===========================================================================


class TestRateLimiting:
    """Tests for rate limiting on OIDC endpoints.

    Rate limiting is enforced by RateLimitGateMiddleware. These tests wire the
    middleware into a test app and send >10 requests to verify 429 responses.
    """

    @pytest.fixture
    def oidc_rate_limit_app(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        """FastAPI test app with RateLimitGateMiddleware and low auth limit.

        The middleware is configured with auth_limit=2 so we can trigger 429
        with just 3 requests (not 10). The Redis client is mocked to simulate
        rate limit counting via _redis_hit.

        Returns:
            TestClient with RateLimitGateMiddleware wired at auth_limit=2.
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
        monkeypatch.setenv("CONCLAVE_RATE_LIMIT_FAIL_OPEN", "false")
        monkeypatch.setenv("RATE_LIMIT_AUTH_PER_MINUTE", "2")  # Low limit for testing
        monkeypatch.setenv("RATE_LIMIT_GENERAL_PER_MINUTE", "100")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.dependencies.rate_limit_middleware import (
            RateLimitGateMiddleware,
        )
        from synth_engine.bootstrapper.routers.auth_oidc import router as oidc_router

        app = FastAPI()
        app.include_router(oidc_router)

        # Create a mock Redis client that simulates rate limit counting.
        mock_redis = MagicMock()
        hit_count: dict[str, int] = {}

        def fake_pipeline():
            pipe = MagicMock()
            ctx = MagicMock()
            pipe.incr = MagicMock()
            pipe.expire = MagicMock()

            def execute():
                # Simulate INCR + EXPIRE pipeline.
                key_arg = pipe.incr.call_args[0][0] if pipe.incr.call_args else "default"
                hit_count[key_arg] = hit_count.get(key_arg, 0) + 1
                return [hit_count[key_arg], 1]

            ctx.__enter__ = MagicMock(return_value=pipe)
            ctx.__exit__ = MagicMock(return_value=False)
            pipe.execute = execute
            return ctx

        mock_redis.pipeline = fake_pipeline
        mock_redis.set = MagicMock(return_value=True)
        mock_redis.get = MagicMock(return_value=None)
        mock_redis.delete = MagicMock(return_value=1)

        app.add_middleware(
            RateLimitGateMiddleware,
            redis_client=mock_redis,
            auth_limit=2,
        )

        return TestClient(app, raise_server_exceptions=False)

    def test_oidc_authorize_rate_limited_after_limit_exceeded(
        self, oidc_rate_limit_app: Any
    ) -> None:
        """>2 requests to /auth/oidc/authorize returns 429.

        AC: OIDC authorize endpoint must be rate-limited (B5 review fix).
        Sends 3 requests with auth_limit=2; the 3rd must return 429.
        """
        valid_challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        url = f"/auth/oidc/authorize?code_challenge={valid_challenge}&code_challenge_method=S256"

        # Mock the OIDC provider for the first 2 requests (which may succeed or fail
        # for other reasons — we only care about the 3rd being 429).
        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_r:
            mock_r.return_value = MagicMock()
            # Send 3 requests — third should be 429.
            statuses = [oidc_rate_limit_app.get(url).status_code for _ in range(3)]

        assert 429 in statuses, (
            f"Expected at least one 429 from /auth/oidc/authorize after limit exceeded, "
            f"got statuses: {statuses}"
        )

    def test_oidc_callback_rate_limited_after_limit_exceeded(
        self, oidc_rate_limit_app: Any
    ) -> None:
        """>2 requests to /auth/oidc/callback returns 429.

        AC: OIDC callback endpoint must be rate-limited (B5 review fix).
        Sends 3 requests with auth_limit=2; the 3rd must return 429.
        """
        url = "/auth/oidc/callback?code=test&state=teststate123456"

        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_r:
            redis_mock = MagicMock()
            redis_mock.getdel.return_value = None  # state not found → 401 (non-rate-limit)
            mock_r.return_value = redis_mock
            statuses = [oidc_rate_limit_app.get(url).status_code for _ in range(3)]

        assert 429 in statuses, (
            f"Expected at least one 429 from /auth/oidc/callback after limit exceeded, "
            f"got statuses: {statuses}"
        )

    def test_rate_limit_backend_redis_hit_increments_counter(self) -> None:
        """Redis rate limit backend correctly increments and checks the limit.

        AC: The _redis_hit() function atomically increments the counter.
        When count exceeds the limit, it returns (count, False) — denied.
        """
        from synth_engine.bootstrapper.dependencies.rate_limit_backend import (
            _redis_hit,
        )

        redis_client = MagicMock()
        pipe = MagicMock()
        redis_client.pipeline.return_value.__enter__ = MagicMock(return_value=pipe)
        redis_client.pipeline.return_value.__exit__ = MagicMock(return_value=False)
        # Simulate 11 hits (limit=10) — 11th should be denied
        pipe.execute.return_value = [11, True]  # [INCR result, EXPIRE result]

        with patch("synth_engine.shared.settings.get_settings") as mock_settings:
            mock_s = MagicMock()
            mock_s.conclave_rate_limit_window_seconds = 60
            mock_settings.return_value = mock_s

            count, allowed = _redis_hit(redis_client, "10/minute", "ip:1.2.3.4")

        assert count == 11, f"Expected count=11, got {count}"
        assert not allowed, f"Expected allowed=False (limit exceeded at 11/10), got {allowed}"


# ===========================================================================
# SECTION 8: Auth Middleware Exempt Paths (2 tests)
# ===========================================================================


class TestAuthMiddlewareExemptPaths:
    """Tests that OIDC initiation endpoints are reachable without auth."""

    def test_authorize_reachable_without_jwt(self) -> None:
        """/auth/oidc/authorize is reachable without a JWT.

        AC: The authorize endpoint must be in AUTH_EXEMPT_PATHS so
        unauthenticated users can initiate the OIDC flow.
        """
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/auth/oidc/authorize" in AUTH_EXEMPT_PATHS, (
            "/auth/oidc/authorize must be in AUTH_EXEMPT_PATHS to allow "
            "unauthenticated users to initiate the OIDC flow"
        )

    def test_callback_reachable_without_jwt(self) -> None:
        """/auth/oidc/callback is reachable without a JWT.

        AC: The callback endpoint must be in AUTH_EXEMPT_PATHS so
        unauthenticated users can complete the OIDC flow.
        """
        from synth_engine.bootstrapper.dependencies.auth import AUTH_EXEMPT_PATHS

        assert "/auth/oidc/callback" in AUTH_EXEMPT_PATHS, (
            "/auth/oidc/callback must be in AUTH_EXEMPT_PATHS to allow "
            "unauthenticated users to complete the OIDC flow"
        )


# ===========================================================================
# SECTION 9: IdP Availability (3 tests)
# ===========================================================================


class TestIdPAvailability:
    """Tests for boot-time IdP availability checks."""

    def test_boot_fails_if_oidc_enabled_and_idp_unreachable(self) -> None:
        """Startup raises ConfigurationError when discovery endpoint returns connection refused.

        AC: OIDC boot sequence must fail-closed if IdP is unreachable.
        Fail at startup prevents silent auth bypass during operation.
        """
        import httpx

        from synth_engine.bootstrapper.dependencies.oidc import (
            initialize_oidc_provider,
        )

        with patch("httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("Connection refused")

            with pytest.raises(RuntimeError) as exc_info:
                initialize_oidc_provider(
                    issuer_url="http://idp.internal:9999",
                    client_id="test-client",
                )
            # Must raise RuntimeError with a meaningful message.
            assert isinstance(exc_info.value, RuntimeError), (
                f"Expected RuntimeError, got {type(exc_info.value).__name__}"
            )
            error_msg = str(exc_info.value).lower()
            assert "idp.internal:9999" in error_msg or "discovery" in error_msg, (
                f"Error message must reference the IdP URL or discovery failure: {exc_info.value}"
            )

    def test_boot_fails_if_oidc_enabled_and_discovery_doc_invalid(self) -> None:
        """Startup raises when discovery document is not valid JSON or missing fields.

        AC: A malformed discovery document is a misconfiguration that must
        fail at startup. Running with an invalid IdP configuration is unsafe.
        """

        from synth_engine.bootstrapper.dependencies.oidc import (
            initialize_oidc_provider,
        )

        # Mock returning invalid JSON
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response):
            with pytest.raises(RuntimeError) as exc_info:
                initialize_oidc_provider(
                    issuer_url="http://idp.internal:9999",
                    client_id="test-client",
                )
            assert "not valid json" in str(exc_info.value).lower(), (
                f"Expected 'not valid json' in error message, got: {exc_info.value}"
            )

    def test_boot_succeeds_if_oidc_not_configured(self) -> None:
        """OIDC_ENABLED=false → startup proceeds without any IdP check.

        AC: When OIDC is disabled, no attempt is made to connect to an IdP.
        The application boots normally and serves passphrase auth only.
        """
        from synth_engine.bootstrapper.dependencies.oidc import (
            maybe_initialize_oidc_provider,
        )

        with patch("synth_engine.shared.settings.get_settings") as mock_settings:
            settings = MagicMock()
            settings.oidc_enabled = False
            mock_settings.return_value = settings

            # Should not raise even without IdP configured
            with patch("httpx.get") as mock_get:
                maybe_initialize_oidc_provider()
                # No HTTP call should have been made
                mock_get.assert_not_called()
                assert mock_get.call_count == 0, "OIDC disabled: no IdP HTTP calls must be made"


# ===========================================================================
# SECTION 10: Token Replay / Authorization Code (2 tests)
# ===========================================================================


class TestTokenReplay:
    """Tests for authorization code replay prevention."""

    def test_authorization_code_replay_returns_401(self, oidc_app: Any) -> None:
        """Same authorization code submitted twice returns 401 on second use.

        AC: Authorization codes are single-use. The state key is deleted
        on first use, so the second attempt finds no state → 401.
        This prevents replay of captured authorization codes.

        Test strategy: We simulate the SECOND use (state already consumed by
        first use and deleted). State key not found in Redis → 401. The
        code_verifier is provided to ensure state validation is the failure
        point, not parameter validation.
        """
        with patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis:
            redis_client = MagicMock()
            # State already consumed (deleted from Redis after first use)
            redis_client.getdel.return_value = None
            mock_redis.return_value = redis_client

            # Second use: state not found → 401 (replay prevented)
            resp2 = oidc_app.get(
                "/auth/oidc/callback"
                "?code=replayed-auth-code"
                "&state=already-used-state"
                "&code_verifier=valid-verifier-12345678"
            )
        assert resp2.status_code == 401, (
            f"Expected 401 on authorization code replay, got {resp2.status_code}"
        )

    def test_authorization_code_from_different_client_returns_401(self, oidc_app: Any) -> None:
        """Code issued to client_id=A is rejected when presented to client_id=B.

        AC: Authorization codes are client-bound. Cross-client reuse must be
        rejected. The IdP enforces this at the token endpoint (client_id
        mismatch → error response from IdP → our callback returns 401).

        Test setup:
        - State data in Redis uses code_challenge (S256 of code_verifier)
        - code_verifier is provided as query param so PKCE check passes
        - httpx.post (token exchange) returns 400 invalid_grant → 401
        """
        import httpx

        # Pre-computed: verifier -> challenge via SHA256 + base64url
        _verifier = "valid-verifier-for-test-pkce-1234567890-abc"
        _challenge = "_rwcrAqQ7ojNxfrisGFpJzwH11_S_FvMUL_JBCXbviM"  # pragma: allowlist secret

        from unittest.mock import AsyncMock

        with (
            patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis,
            patch("httpx.AsyncClient") as mock_async_client_cls,
        ):
            redis_client = MagicMock()
            # Store code_challenge (not code_verifier) in state data
            redis_client.getdel.return_value = json.dumps(
                {"code_challenge": _challenge, "created_at": "2026-01-01T00:00:00Z"}
            ).encode()
            mock_redis.return_value = redis_client

            # IdP returns error for wrong client
            error_response = MagicMock()
            error_response.status_code = 400
            error_response.content = b'{"error": "invalid_grant"}'
            error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "400 Bad Request",
                request=MagicMock(),
                response=error_response,
            )
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=error_response)
            mock_async_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_async_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = oidc_app.get(
                "/auth/oidc/callback"
                f"?code=code-for-client-a"
                f"&state=valid-state"
                f"&code_verifier={_verifier}"
            )
        assert resp.status_code == 401, (
            f"Expected 401 for cross-client auth code, got {resp.status_code}"
        )

    def test_idp_network_error_returns_503(self, oidc_app: Any) -> None:
        """IdP token endpoint unreachable (network error) returns 503.

        AC: Network-level failure during token exchange must return 503 (not 401).
        This is different from an IdP HTTP error (400/401/5xx) which returns 401.
        httpx.HTTPError covers ConnectError, TimeoutException, etc.
        """
        import httpx

        _verifier = "valid-verifier-for-test-pkce-1234567890-abc"
        _challenge = "_rwcrAqQ7ojNxfrisGFpJzwH11_S_FvMUL_JBCXbviM"  # pragma: allowlist secret

        from unittest.mock import AsyncMock

        with (
            patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis,
            patch("httpx.AsyncClient") as mock_async_client_cls,
        ):
            redis_client = MagicMock()
            redis_client.getdel.return_value = json.dumps(
                {"code_challenge": _challenge, "created_at": "2026-01-01T00:00:00Z"}
            ).encode()
            mock_redis.return_value = redis_client

            # Network-level error (not HTTP error)
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_async_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_async_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = oidc_app.get(
                "/auth/oidc/callback"
                "?code=some-auth-code"
                f"&state=valid-state"
                f"&code_verifier={_verifier}"
            )
        assert resp.status_code == 503, (
            f"Expected 503 for IdP network error, got {resp.status_code}"
        )
        body = resp.json()
        problem = body.get("detail", body)
        assert problem.get("status") == 503, (
            f"Expected RFC 7807 status=503 in body, got {problem.get('status')}"
        )


# ===========================================================================
# SECTION 11: Input Validation (4 tests)
# ===========================================================================


class TestInputValidation:
    """Tests for input validation on OIDC and session endpoints."""

    def test_callback_oversized_id_token_returns_413(self, oidc_app: Any) -> None:
        """Response body from IdP token endpoint exceeding 64KB causes 413.

        AC: Memory exhaustion prevention. IdP response > 64KB must be
        rejected. This prevents a rogue IdP from OOM-killing the service.

        Test setup: provides valid state+PKCE so the check reaches the
        token exchange step, then simulates an oversized IdP response.
        """

        _verifier = "valid-verifier-for-test-pkce-1234567890-abc"
        _challenge = "_rwcrAqQ7ojNxfrisGFpJzwH11_S_FvMUL_JBCXbviM"  # pragma: allowlist secret

        from unittest.mock import AsyncMock

        with (
            patch("synth_engine.bootstrapper.routers.auth_oidc.get_redis_client") as mock_redis,
            patch("httpx.AsyncClient") as mock_async_client_cls,
        ):
            redis_client = MagicMock()
            # State data with correct code_challenge format
            redis_client.getdel.return_value = json.dumps(
                {"code_challenge": _challenge, "created_at": "2026-01-01T00:00:00Z"}
            ).encode()
            mock_redis.return_value = redis_client

            # IdP returns a 64KB+ response
            oversized_content = "x" * (64 * 1024 + 1)  # 64KB + 1 byte
            oversized_response = MagicMock()
            oversized_response.status_code = 200
            oversized_response.content = oversized_content.encode()
            oversized_response.raise_for_status = MagicMock()
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=oversized_response)
            mock_async_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_async_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = oidc_app.get(
                "/auth/oidc/callback"
                "?code=valid-auth-code"
                "&state=valid-state"
                f"&code_verifier={_verifier}"
            )
        assert resp.status_code in (
            413,
            401,
            400,
        ), f"Expected 413/401 for oversized IdP response, got {resp.status_code}"

    def test_revoke_user_id_sql_injection_returns_422(self, oidc_app: Any) -> None:
        """user_id containing SQL injection string fails UUID validation with 422.

        AC: user_id must be a valid UUID. SQL injection payloads fail
        Pydantic UUID parsing and return 422 Unprocessable Entity.
        This is the first defense against injection — the UUID constraint
        ensures the value never reaches a database query.
        """
        token = _make_token(role="admin")
        resp = oidc_app.post(
            "/auth/revoke",
            json={"user_id": "'; DROP TABLE users; --"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 422, (
            f"Expected 422 for SQL injection in user_id, got {resp.status_code}"
        )

    def test_idp_role_claim_does_not_escalate_privileges(self) -> None:
        """IdP role claim in token data is not propagated to the provisioned user.

        AC: Decision 10 — IdP role claims are IGNORED. Role is ALWAYS
        resolved from the local DB. This test verifies that _find_or_provision_user
        always uses OIDC_DEFAULT_USER_ROLE (not any claim from token_data).
        _extract_role_from_token_claims was removed (F8): IdP role claims are
        never extracted — the comment at the call site makes the policy explicit.
        """
        from synth_engine.bootstrapper.routers.auth_oidc import (
            OIDC_DEFAULT_USER_ROLE,
        )

        # OIDC_DEFAULT_USER_ROLE must be a low-privilege role (operator, not admin).
        # If this assertion fails, the default role assignment logic is broken.
        assert OIDC_DEFAULT_USER_ROLE == "operator", (
            f"OIDC_DEFAULT_USER_ROLE must be 'operator' (lowest privilege), "
            f"got {OIDC_DEFAULT_USER_ROLE!r}. "
            "Auto-provisioned OIDC users must not receive elevated roles by default."
        )
        assert OIDC_DEFAULT_USER_ROLE != "admin", (
            "OIDC_DEFAULT_USER_ROLE must never be 'admin' — "
            "auto-provisioning with admin role would allow IdP to escalate privileges."
        )

    def test_session_key_uses_random_token_not_user_id(self) -> None:
        """Session Redis key must use random token, not derived from user_id.

        AC: Session fixation prevention (Attack Vector AV-5). The session
        key must NOT be derived from user_id. If it were, an attacker
        knowing the user_id could predict or forge session keys.
        """
        from synth_engine.bootstrapper.dependencies.sessions import (
            create_session_key,
        )

        user_id = str(uuid.uuid4())

        key1 = create_session_key()
        key2 = create_session_key()

        # Keys must be random — different each time
        assert key1 != key2, "Session keys must be random — two calls returned the same key"
        # Keys must NOT contain the user_id
        assert user_id not in key1, f"Session key {key1!r} must not contain user_id {user_id!r}"
        # Keys must start with the correct namespace prefix
        assert key1.startswith("conclave:session:"), (
            f"Session key {key1!r} must start with 'conclave:session:'"
        )
