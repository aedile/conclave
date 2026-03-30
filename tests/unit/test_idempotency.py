"""Unit tests for idempotency middleware (T45.1).

Tests follow the Rule 22 ordering: attack/negative tests first, then
feature/positive tests.

Attack/negative tests:
1.  GET request WITH Idempotency-Key — header ignored, pass-through
2.  POST WITHOUT Idempotency-Key — pass-through (optional header)
3.  Empty Idempotency-Key header ("") — HTTP 400
4.  Key with exactly 128 chars — accepted (boundary)
5.  Key with 129 chars — rejected with 400
6.  Two operators same key — no collision (per-operator scoping)
7.  Non-JSON handler response (plain text) — middleware handles gracefully
8.  Concurrent identical requests — SET NX atomicity (one 200, one 409)
9.  Key release on exception — DELETE fires, retry succeeds
10. Key release when Redis down during DELETE — graceful degradation, log warning
11. PATCH method intercepted correctly
12. Redis down — WARNING log, pass-through for all requests
13. TTL=0 or negative — ConclaveSettings validation rejects
14. Whitespace-only key — HTTP 400
15. Exempt paths (/health, /unseal) — pass-through even with idempotency key

Feature/positive tests:
16. New key on POST — SET NX succeeds, handler called, 200 returned
17. Duplicate key on POST — 409 with correct JSON body
18. Key TTL read from settings
19. Key format is idempotency:{operator_id}:{user_key}
20. All mutating methods intercepted: POST, PUT, PATCH, DELETE
21. Redis AuthenticationError degrades gracefully

CONSTITUTION Priority 0: Security — idempotency prevents duplicate job creation
CONSTITUTION Priority 3: TDD — RED phase
Task: T45.1 — Reintroduce Idempotency Middleware (TBD-07)
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# State isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(
    redis_client: object | None = None,
    *,
    exempt_paths: frozenset[str] | None = None,
    ttl: int = 300,
) -> object:
    """Build a minimal Starlette/FastAPI test app with IdempotencyMiddleware.

    The route handler returns 200 with a plain text body so that
    non-JSON response handling is also exercised.

    Args:
        redis_client: Redis client to inject; defaults to a MagicMock.
        exempt_paths: Paths to exempt from idempotency; defaults to
            frozenset({"/health", "/unseal"}).
        ttl: Idempotency TTL seconds passed to the middleware.

    Returns:
        A Starlette application instance with IdempotencyMiddleware wired.
    """
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    from synth_engine.shared.middleware.idempotency import IdempotencyMiddleware

    if redis_client is None:
        redis_client = MagicMock()
    if exempt_paths is None:
        exempt_paths = frozenset({"/health", "/unseal"})

    app = FastAPI()

    @app.get("/items")
    async def get_items() -> dict[str, str]:
        return {"method": "GET"}

    @app.post("/items")
    async def create_item() -> dict[str, str]:
        return {"method": "POST"}

    @app.put("/items/{item_id}")
    async def update_item(item_id: int) -> dict[str, str]:
        return {"method": "PUT"}

    @app.patch("/items/{item_id}")
    async def patch_item(item_id: int) -> dict[str, str]:
        return {"method": "PATCH"}

    @app.delete("/items/{item_id}")
    async def delete_item(item_id: int) -> dict[str, str]:
        return {"method": "DELETE"}

    @app.post("/text")
    async def create_text() -> PlainTextResponse:
        return PlainTextResponse("plain text response")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/unseal")
    async def unseal() -> dict[str, str]:
        return {"status": "unsealed"}

    app.add_middleware(
        IdempotencyMiddleware,
        redis_client=redis_client,
        exempt_paths=exempt_paths,
        ttl_seconds=ttl,
    )
    return app


def _make_redis_mock(*, key_exists: bool = False) -> MagicMock:
    """Create a MagicMock Redis client simulating SET NX behavior.

    Args:
        key_exists: When True, ``set(..., nx=True)`` returns ``None``
            (key already exists); when False, returns ``True`` (key set).

    Returns:
        MagicMock with ``set`` and ``delete`` configured.
    """
    mock = MagicMock()
    mock.set.return_value = None if key_exists else True
    return mock


# ===========================================================================
# ATTACK / NEGATIVE TESTS
# ===========================================================================


class TestAttackAndNegative:
    """Attack and boundary tests — these must be written before feature tests."""

    # -----------------------------------------------------------------------
    # 1. Safe methods with Idempotency-Key are ignored
    # -----------------------------------------------------------------------

    def test_get_with_idempotency_key_passes_through(self) -> None:
        """GET + Idempotency-Key — header must be ignored, request passes through.

        Verifies that safe (read-only) methods are never intercepted, even when
        an Idempotency-Key header is present.
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.get("/items", headers={"Idempotency-Key": "key-abc"})

        assert response.status_code == 200
        redis_mock.set.assert_not_called()

    def test_head_with_idempotency_key_passes_through(self) -> None:
        """HEAD + Idempotency-Key — header must be ignored, never touches Redis.

        FastAPI may return 200 or 405 depending on whether HEAD is inferred
        from GET.  The critical assertion is that the middleware never calls
        Redis SET for safe methods — the status code is a routing concern.
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.head("/items", headers={"Idempotency-Key": "key-abc"})

        # 200 or 405 is acceptable — routing detail; Redis must NOT be touched.
        assert response.status_code in {200, 405}
        redis_mock.set.assert_not_called()

    def test_options_with_idempotency_key_passes_through(self) -> None:
        """OPTIONS + Idempotency-Key — header must be ignored."""
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.options("/items", headers={"Idempotency-Key": "key-abc"})

        # OPTIONS may return 200 or 405; the key point is Redis is untouched.
        assert response.status_code in {200, 405}
        redis_mock.set.assert_not_called()

    # -----------------------------------------------------------------------
    # 2. POST without Idempotency-Key passes through
    # -----------------------------------------------------------------------

    def test_post_without_idempotency_key_passes_through(self) -> None:
        """POST with no Idempotency-Key header — pass-through (header is optional).

        The spec explicitly states: requests without the header are not subject
        to deduplication.
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.post("/items")

        assert response.status_code == 200
        redis_mock.set.assert_not_called()

    # -----------------------------------------------------------------------
    # 3. Empty Idempotency-Key header → 400
    # -----------------------------------------------------------------------

    def test_empty_idempotency_key_returns_400(self) -> None:
        """POST with empty Idempotency-Key header must return HTTP 400.

        An empty string is not a valid key per minimum-length constraint
        (Architectural Decision #9: minimum key length is 1 character).
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.post("/items", headers={"Idempotency-Key": ""})

        assert response.status_code == 400
        body = response.json()
        assert "idempotency" in body["detail"].lower() or "key" in body["detail"].lower()
        redis_mock.set.assert_not_called()

    # -----------------------------------------------------------------------
    # 14. Whitespace-only key → 400
    # -----------------------------------------------------------------------

    def test_whitespace_only_key_returns_400(self) -> None:
        """POST with whitespace-only Idempotency-Key must return HTTP 400.

        A key that is only whitespace has no meaningful identity. It must be
        rejected to prevent silent key collisions between operators using
        trailing-space typos.
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.post("/items", headers={"Idempotency-Key": "   "})

        assert response.status_code == 400
        redis_mock.set.assert_not_called()

    # -----------------------------------------------------------------------
    # 4. Exactly 128 chars → accepted
    # -----------------------------------------------------------------------

    def test_key_exactly_128_chars_is_accepted(self) -> None:
        """POST with exactly 128-character Idempotency-Key must be accepted (boundary).

        128 characters is the maximum allowed key length per the spec.
        A key of exactly 128 characters must pass validation.
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)
        key_128 = "a" * 128

        response = client.post("/items", headers={"Idempotency-Key": key_128})

        assert response.status_code == 200
        redis_mock.set.assert_called_once()

    # -----------------------------------------------------------------------
    # 5. 129 chars → rejected with 400
    # -----------------------------------------------------------------------

    def test_key_129_chars_returns_400(self) -> None:
        """POST with 129-character Idempotency-Key must return HTTP 400.

        One character over the 128-char maximum must be rejected.
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)
        key_129 = "a" * 129

        response = client.post("/items", headers={"Idempotency-Key": key_129})

        assert response.status_code == 400
        redis_mock.set.assert_not_called()

    # -----------------------------------------------------------------------
    # 6. Two operators same key — no collision
    # -----------------------------------------------------------------------

    def test_two_operators_same_user_key_no_collision(self) -> None:
        """Two operators using the same Idempotency-Key must not collide.

        The Redis key format is idempotency:{operator_id}:{user_key}, so
        operator A and operator B using the same user-supplied key map to
        different Redis entries.  Verify the Redis keys differ.
        """
        import jwt as pyjwt

        secret = "test-secret-long-enough-for-hs256-32chars"  # pragma: allowlist secret

        token_a = pyjwt.encode({"sub": "operator-a", "exp": 9999999999, "iat": 1}, secret)
        token_b = pyjwt.encode({"sub": "operator-b", "exp": 9999999999, "iat": 1}, secret)

        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)
        user_key = "same-key-for-both"

        client.post(
            "/items",
            headers={"Idempotency-Key": user_key, "Authorization": f"Bearer {token_a}"},
        )
        client.post(
            "/items",
            headers={"Idempotency-Key": user_key, "Authorization": f"Bearer {token_b}"},
        )

        assert redis_mock.set.call_count == 2
        calls = redis_mock.set.call_args_list
        key_arg_0 = calls[0][0][0]  # first positional arg of first call
        key_arg_1 = calls[1][0][0]  # first positional arg of second call
        assert key_arg_0 != key_arg_1
        assert "operator-a" in key_arg_0
        assert "operator-b" in key_arg_1

    # -----------------------------------------------------------------------
    # 7. Non-JSON handler response handled gracefully
    # -----------------------------------------------------------------------

    def test_non_json_handler_response_passes_through(self) -> None:
        """Middleware must handle plain-text handler responses without crashing.

        The spec explicitly states response caching is not implemented; the
        middleware only tracks key existence.  Binary/non-JSON responses must
        pass through unmodified.
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.post("/text", headers={"Idempotency-Key": "plain-text-key"})

        assert response.status_code == 200
        assert response.text == "plain text response"

    # -----------------------------------------------------------------------
    # 8. Concurrent identical requests — SET NX atomicity
    # -----------------------------------------------------------------------

    def test_concurrent_identical_requests_one_succeeds_one_409(self) -> None:
        """Second request with same key must receive HTTP 409.

        Simulates atomic SET NX: first call returns True (key set), second
        call returns None (key already exists → 409).
        """
        call_count = 0

        def side_effect_set(*args: object, **kwargs: object) -> bool | None:
            nonlocal call_count
            call_count += 1
            return True if call_count == 1 else None

        redis_mock = MagicMock()
        redis_mock.set.side_effect = side_effect_set
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)
        headers = {"Idempotency-Key": "concurrent-key"}

        resp1 = client.post("/items", headers=headers)
        resp2 = client.post("/items", headers=headers)

        assert resp1.status_code == 200
        assert resp2.status_code == 409
        body = resp2.json()
        assert body["detail"] == "Duplicate request"
        assert body["idempotency_key"] == "concurrent-key"

    # -----------------------------------------------------------------------
    # 9. Key release on exception — retry succeeds
    # -----------------------------------------------------------------------

    def test_key_released_on_handler_exception(self) -> None:
        """On handler exception, the idempotency key must be DELETEd from Redis.

        This ensures the request is retryable — if the handler fails, the
        client can retry with the same key.
        """
        from fastapi import FastAPI

        from synth_engine.shared.middleware.idempotency import IdempotencyMiddleware

        redis_mock = _make_redis_mock()
        app = FastAPI()

        @app.post("/boom")
        async def boom() -> dict[str, str]:
            raise RuntimeError("handler exploded")

        app.add_middleware(
            IdempotencyMiddleware,
            redis_client=redis_mock,
            exempt_paths=frozenset(),
            ttl_seconds=300,
        )

        client = TestClient(app, raise_server_exceptions=False)
        client.post("/boom", headers={"Idempotency-Key": "retry-key"})

        # Redis SET was called (key was acquired)
        redis_mock.set.assert_called_once()
        # Redis DELETE was called (key was released)
        redis_mock.delete.assert_called_once()
        deleted_key = redis_mock.delete.call_args[0][0]
        assert "retry-key" in deleted_key

    # -----------------------------------------------------------------------
    # 10. Key release when Redis down during DELETE — graceful degradation
    # -----------------------------------------------------------------------

    def test_key_release_redis_down_during_delete_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """If Redis is down when deleting key on exception, log WARNING, re-raise.

        The handler exception must still propagate; the Redis failure during
        cleanup must not swallow the original error.
        """
        import redis as redis_lib
        from fastapi import FastAPI

        from synth_engine.shared.middleware.idempotency import IdempotencyMiddleware

        redis_mock = MagicMock()
        redis_mock.set.return_value = True  # key acquired
        redis_mock.delete.side_effect = redis_lib.ConnectionError("down during delete")

        app = FastAPI()

        @app.post("/boom2")
        async def boom2() -> dict[str, str]:
            raise RuntimeError("handler failed")

        app.add_middleware(
            IdempotencyMiddleware,
            redis_client=redis_mock,
            exempt_paths=frozenset(),
            ttl_seconds=300,
        )

        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.WARNING, logger="synth_engine.shared.middleware.idempotency"):
            client.post("/boom2", headers={"Idempotency-Key": "delete-fail-key"})

        assert any("delete" in record.message.lower() for record in caplog.records)

    # -----------------------------------------------------------------------
    # 11. PATCH method intercepted correctly
    # -----------------------------------------------------------------------

    def test_patch_method_is_intercepted(self) -> None:
        """PATCH requests with Idempotency-Key must be intercepted by middleware.

        Per spec Architectural Decision #2, PATCH is explicitly included in
        the intercepted methods (POST, PUT, PATCH, DELETE).
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.patch("/items/1", headers={"Idempotency-Key": "patch-key"})

        assert response.status_code == 200
        redis_mock.set.assert_called_once()

    # -----------------------------------------------------------------------
    # 12. Redis down — WARNING log, pass-through for all requests
    # -----------------------------------------------------------------------

    def test_redis_down_logs_warning_and_passes_through(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When Redis raises ConnectionError on SET, log WARNING and pass through.

        Graceful degradation: an unavailable Redis must not block legitimate
        requests.  The middleware degrades to pass-through with a WARNING.
        """
        import redis as redis_lib

        redis_mock = MagicMock()
        redis_mock.set.side_effect = redis_lib.ConnectionError("Redis is down")
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        with caplog.at_level(logging.WARNING, logger="synth_engine.shared.middleware.idempotency"):
            response = client.post("/items", headers={"Idempotency-Key": "some-key"})

        assert response.status_code == 200
        assert any("redis" in record.message.lower() for record in caplog.records)

    def test_redis_authentication_error_logs_warning_and_passes_through(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When Redis raises AuthenticationError, log WARNING and pass through.

        Known failure pattern: Redis requirepass causes AuthenticationError.
        This must degrade gracefully just like ConnectionError.
        """
        import redis as redis_lib

        redis_mock = MagicMock()
        redis_mock.set.side_effect = redis_lib.AuthenticationError("NOAUTH")
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        with caplog.at_level(logging.WARNING, logger="synth_engine.shared.middleware.idempotency"):
            response = client.post("/items", headers={"Idempotency-Key": "auth-err-key"})

        assert response.status_code == 200
        assert any("redis" in record.message.lower() for record in caplog.records)

    # -----------------------------------------------------------------------
    # 13. TTL=0 or negative — ConclaveSettings validation rejects
    # -----------------------------------------------------------------------

    def test_settings_idempotency_ttl_zero_is_rejected(self) -> None:
        """ConclaveSettings must reject idempotency_ttl_seconds=0.

        The spec mandates ge=1 on the Pydantic field; zero means keys expire
        instantly, which would defeat the purpose of idempotency protection.
        """
        import os

        from pydantic import ValidationError

        os.environ["IDEMPOTENCY_TTL_SECONDS"] = "0"
        try:
            from synth_engine.shared.settings import ConclaveSettings

            with pytest.raises(ValidationError):
                ConclaveSettings()
        finally:
            os.environ.pop("IDEMPOTENCY_TTL_SECONDS", None)

    def test_settings_idempotency_ttl_negative_is_rejected(self) -> None:
        """ConclaveSettings must reject idempotency_ttl_seconds=-1.

        Negative TTL values are nonsensical and must be rejected at
        construction time.
        """
        import os

        from pydantic import ValidationError

        os.environ["IDEMPOTENCY_TTL_SECONDS"] = "-1"
        try:
            from synth_engine.shared.settings import ConclaveSettings

            with pytest.raises(ValidationError):
                ConclaveSettings()
        finally:
            os.environ.pop("IDEMPOTENCY_TTL_SECONDS", None)

    def test_settings_idempotency_ttl_one_is_accepted(self) -> None:
        """ConclaveSettings must accept idempotency_ttl_seconds=1 (minimum valid value)."""
        import os

        os.environ["IDEMPOTENCY_TTL_SECONDS"] = "1"
        try:
            from synth_engine.shared.settings import ConclaveSettings

            s = ConclaveSettings()
            assert s.idempotency_ttl_seconds == 1
        finally:
            os.environ.pop("IDEMPOTENCY_TTL_SECONDS", None)

    # -----------------------------------------------------------------------
    # 15. Exempt paths pass through even with Idempotency-Key
    # -----------------------------------------------------------------------

    def test_exempt_path_health_passes_through_with_key(self) -> None:
        """/health with Idempotency-Key must pass through (path is exempt).

        Infrastructure endpoints must always be reachable and must never
        consume Redis capacity for idempotency checks.
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.get("/health", headers={"Idempotency-Key": "health-key"})

        assert response.status_code == 200
        redis_mock.set.assert_not_called()

    def test_exempt_path_unseal_passes_through_with_key(self) -> None:
        """/unseal with Idempotency-Key must pass through (path is exempt)."""
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.post("/unseal", headers={"Idempotency-Key": "unseal-key"})

        assert response.status_code == 200
        redis_mock.set.assert_not_called()


# ===========================================================================
# FEATURE / POSITIVE TESTS
# ===========================================================================


class TestFeature:
    """Positive feature tests — written after attack tests."""

    # -----------------------------------------------------------------------
    # 16. New key on POST → SET NX succeeds, handler called, 200 returned
    # -----------------------------------------------------------------------

    def test_new_post_key_sets_redis_and_returns_200(self) -> None:
        """First POST with Idempotency-Key must call SET NX and return 200.

        The handler response must be forwarded unchanged when the key is new.
        """
        redis_mock = _make_redis_mock(key_exists=False)
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.post("/items", headers={"Idempotency-Key": "new-key-001"})

        assert response.status_code == 200
        redis_mock.set.assert_called_once()
        call_kwargs = redis_mock.set.call_args
        assert call_kwargs.kwargs.get("nx") is True or True in call_kwargs.args

    # -----------------------------------------------------------------------
    # 17. Duplicate key → 409 with correct JSON body
    # -----------------------------------------------------------------------

    def test_duplicate_post_key_returns_409_with_json_body(self) -> None:
        """POST with duplicate Idempotency-Key must return HTTP 409.

        The response body must be: {"detail": "Duplicate request",
        "idempotency_key": "<key>"} with Content-Type: application/json.
        """
        redis_mock = _make_redis_mock(key_exists=True)
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.post("/items", headers={"Idempotency-Key": "dup-key-001"})

        assert response.status_code == 409
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert body["detail"] == "Duplicate request"
        assert body["idempotency_key"] == "dup-key-001"

    # -----------------------------------------------------------------------
    # 18. TTL read from settings
    # -----------------------------------------------------------------------

    def test_ttl_passed_to_redis_set(self) -> None:
        """The TTL passed to Redis SET must match the ttl_seconds constructor arg."""
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock, ttl=600)
        client = TestClient(app, raise_server_exceptions=True)

        client.post("/items", headers={"Idempotency-Key": "ttl-test-key"})

        call_kwargs = redis_mock.set.call_args
        # ex= kwarg or positional — check the ex keyword argument
        ex_value = call_kwargs.kwargs.get("ex")
        assert ex_value == 600

    # -----------------------------------------------------------------------
    # 19. Key format: idempotency:{operator_id}:{user_key}
    # -----------------------------------------------------------------------

    def test_redis_key_format_with_authenticated_operator(self) -> None:
        """Redis key must be idempotency:{operator_id}:{user_key} for auth requests.

        The middleware must extract the operator_id from the JWT sub claim
        and scope keys per-operator to prevent cross-operator collisions.
        """
        import jwt as pyjwt

        secret = "test-secret-long-enough-for-hs256-32chars"  # pragma: allowlist secret
        token = pyjwt.encode({"sub": "operator-xyz", "exp": 9999999999, "iat": 1}, secret)

        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        client.post(
            "/items",
            headers={"Idempotency-Key": "my-key", "Authorization": f"Bearer {token}"},
        )

        redis_key = redis_mock.set.call_args[0][0]
        assert redis_key == "idempotency:operator-xyz:my-key"

    def test_redis_key_format_without_auth_uses_anonymous(self) -> None:
        """Redis key uses 'anonymous' as operator_id when no JWT is present.

        In unconfigured/pass-through auth mode, the operator ID falls back to
        'anonymous' to maintain a consistent key format.
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        client.post("/items", headers={"Idempotency-Key": "anon-key"})

        redis_key = redis_mock.set.call_args[0][0]
        assert redis_key == "idempotency:anonymous:anon-key"

    # -----------------------------------------------------------------------
    # 20. All mutating methods intercepted
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/items"),
            ("PUT", "/items/1"),
            ("PATCH", "/items/1"),
            ("DELETE", "/items/1"),
        ],
    )
    def test_all_mutating_methods_intercepted(self, method: str, path: str) -> None:
        """All mutating HTTP methods must be intercepted by the middleware.

        POST, PUT, PATCH, and DELETE all trigger the idempotency check.
        """
        redis_mock = _make_redis_mock()
        app = _make_app(redis_mock)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.request(method, path, headers={"Idempotency-Key": f"{method}-key"})

        assert response.status_code == 200
        redis_mock.set.assert_called_once()

    # -----------------------------------------------------------------------
    # 21. ConclaveSettings default TTL is 300
    # -----------------------------------------------------------------------

    def test_settings_default_idempotency_ttl(self) -> None:
        """ConclaveSettings must default idempotency_ttl_seconds to 300."""
        from synth_engine.shared.settings import ConclaveSettings

        s = ConclaveSettings()
        assert s.idempotency_ttl_seconds == 300

    # -----------------------------------------------------------------------
    # Redis client module: get_redis_client returns a Redis singleton
    # -----------------------------------------------------------------------

    def test_get_redis_client_returns_redis_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_redis_client() must return a redis.Redis instance.

        The function constructs the client from settings.redis_url.
        """
        import importlib

        import redis as redis_lib

        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        # Patch redis.Redis.from_url to avoid actual network connection
        with patch("redis.Redis.from_url") as mock_from_url:
            mock_client = MagicMock(spec=redis_lib.Redis)
            mock_from_url.return_value = mock_client

            # Force reimport to pick up monkeypatched env
            import synth_engine.bootstrapper.dependencies.redis as redis_dep

            importlib.reload(redis_dep)
            client = redis_dep.get_redis_client()
            assert isinstance(client, redis_lib.Redis), (
                f"get_redis_client() must return a redis.Redis instance, got {type(client)}"
            )
            assert mock_from_url.call_count == 1, "from_url must be called exactly once"
