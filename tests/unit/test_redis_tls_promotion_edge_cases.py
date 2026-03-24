"""Edge-case tests for promote_redis_url_to_tls in shared/task_queue.py (T53.4).

Covers negative/attack cases (written first per Rule 22) and feature cases for
all spec-challenger inputs: already-TLS URLs, empty strings, non-redis schemes,
sentinel URLs, unix socket URLs, IPv6 literals, query parameters, and
percent-encoded credentials.

CONSTITUTION Priority 0: Security — single source of truth, no silent coercion
CONSTITUTION Priority 3: TDD — ATTACK RED before FEATURE RED before GREEN
Task: T53.4 — Redis TLS Promotion Deduplication
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ===========================================================================
# ATTACK / NEGATIVE TESTS — written first (Rule 22: attack-first TDD)
# ===========================================================================


class TestAttackAlreadyTlsUrl:
    """Attack: already-rediss:// URL must NOT be double-promoted."""

    def test_already_tls_url_is_returned_unchanged(self) -> None:
        """An already-TLS ``rediss://`` URL must be returned without modification.

        Double-promotion would corrupt the URL scheme (``redisss://``) and
        break the TLS handshake — this is a correctness-critical guard.
        """
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "rediss://redis:6379/0"
        result = promote_redis_url_to_tls(url)
        assert result == "rediss://redis:6379/0"

    def test_already_tls_url_with_auth_is_returned_unchanged(self) -> None:
        """A ``rediss://`` URL with embedded credentials must be unchanged."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "rediss://user:secret@redis:6379/0"
        result = promote_redis_url_to_tls(url)
        assert result == "rediss://user:secret@redis:6379/0"

    def test_already_tls_url_does_not_double_promote(self) -> None:
        """Calling promote twice on a URL must yield the same result as calling once."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "redis://host:6379"
        once = promote_redis_url_to_tls(url)
        twice = promote_redis_url_to_tls(once)
        assert once == twice == "rediss://host:6379"


class TestAttackEmptyString:
    """Attack: empty string URL must be handled gracefully (no exception)."""

    def test_empty_string_is_returned_unchanged(self) -> None:
        """An empty string input must not raise and must be returned as-is."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        result = promote_redis_url_to_tls("")
        assert result == ""


class TestAttackNonRedisScheme:
    """Attack: non-redis schemes must NOT be promoted."""

    def test_http_url_is_not_promoted(self) -> None:
        """An ``http://`` URL must pass through unchanged."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "http://example.com:6379"
        result = promote_redis_url_to_tls(url)
        assert result == "http://example.com:6379"

    def test_https_url_is_not_promoted(self) -> None:
        """An ``https://`` URL must pass through unchanged."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "https://example.com:6379"
        result = promote_redis_url_to_tls(url)
        assert result == "https://example.com:6379"

    def test_amqp_url_is_not_promoted(self) -> None:
        """An ``amqp://`` URL must pass through unchanged."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "amqp://broker:5672/vhost"
        result = promote_redis_url_to_tls(url)
        assert result == "amqp://broker:5672/vhost"


class TestAttackSentinelUrl:
    """Attack: redis+sentinel:// URLs must NOT be promoted (different protocol)."""

    def test_sentinel_url_is_not_promoted(self) -> None:
        """A ``redis+sentinel://`` URL must pass through unchanged.

        Sentinel URLs use a different connection model and must not be
        silently coerced to ``rediss+sentinel://``.
        """
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "redis+sentinel://sentinel1:26379,sentinel2:26379/mymaster"
        result = promote_redis_url_to_tls(url)
        assert result == "redis+sentinel://sentinel1:26379,sentinel2:26379/mymaster"

    def test_sentinel_url_with_auth_is_not_promoted(self) -> None:
        """A ``redis+sentinel://`` URL with auth must pass through unchanged."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "redis+sentinel://user:pass@sentinel:26379/mymaster"
        result = promote_redis_url_to_tls(url)
        assert result == "redis+sentinel://user:pass@sentinel:26379/mymaster"


class TestAttackUnixSocketUrl:
    """Attack: redis+socket:// URLs must pass through unchanged."""

    def test_unix_socket_url_is_not_promoted(self) -> None:
        """A ``redis+socket://`` URL (Unix domain socket) must pass through unchanged."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "redis+socket:///var/run/redis/redis.sock"
        result = promote_redis_url_to_tls(url)
        assert result == "redis+socket:///var/run/redis/redis.sock"


# ===========================================================================
# FEATURE TESTS — happy-path and functional edge cases
# ===========================================================================


class TestBasicPromotion:
    """Feature: standard redis:// URLs are promoted to rediss://."""

    def test_plain_redis_url_promoted(self) -> None:
        """``redis://host:6379`` must become ``rediss://host:6379``."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        result = promote_redis_url_to_tls("redis://host:6379")
        assert result == "rediss://host:6379"

    def test_redis_url_with_database_path_promoted(self) -> None:
        """``redis://host:6379/0`` must become ``rediss://host:6379/0``."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        result = promote_redis_url_to_tls("redis://host:6379/0")
        assert result == "rediss://host:6379/0"

    def test_redis_url_with_database_path_preserved(self) -> None:
        """The database index path ``/3`` must be preserved after promotion."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        result = promote_redis_url_to_tls("redis://host:6379/3")
        assert result == "rediss://host:6379/3"

    def test_redis_default_url_promoted(self) -> None:
        """Default project URL ``redis://redis:6379/0`` must become ``rediss://redis:6379/0``."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        result = promote_redis_url_to_tls("redis://redis:6379/0")
        assert result == "rediss://redis:6379/0"


class TestCredentialPreservation:
    """Feature: embedded credentials are preserved after promotion."""

    def test_url_with_password_preserves_auth(self) -> None:
        """Password in ``redis://:password@host:6379/0`` must survive promotion."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        result = promote_redis_url_to_tls("redis://:password@host:6379/0")
        assert result == "rediss://:password@host:6379/0"

    def test_url_with_username_and_password_preserves_auth(self) -> None:
        """Username and password must both be preserved after promotion."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        result = promote_redis_url_to_tls("redis://user:secret@host:6379/1")
        assert result == "rediss://user:secret@host:6379/1"

    def test_url_with_percent_encoded_credentials_preserved(self) -> None:
        """Percent-encoded credentials (e.g. ``p%40ss``) must not be decoded or altered."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "redis://user:p%40ss@host:6379"
        result = promote_redis_url_to_tls(url)
        assert result == "rediss://user:p%40ss@host:6379"

    def test_url_with_special_chars_in_password_preserved(self) -> None:
        """Percent-encoded special characters in password must survive promotion."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "redis://:p%21%40%23%24@redis:6379/0"
        result = promote_redis_url_to_tls(url)
        assert result == "rediss://:p%21%40%23%24@redis:6379/0"


class TestQueryParameterPreservation:
    """Feature: URL query parameters are preserved after promotion."""

    def test_url_with_timeout_query_param_preserved(self) -> None:
        """Query parameter ``?timeout=5`` must survive promotion."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "redis://host:6379/0?timeout=5"
        result = promote_redis_url_to_tls(url)
        assert result == "rediss://host:6379/0?timeout=5"

    def test_url_with_multiple_query_params_preserved(self) -> None:
        """Multiple query parameters must all survive promotion."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "redis://host:6379/0?timeout=5&retry_on_timeout=true"
        result = promote_redis_url_to_tls(url)
        assert result == "rediss://host:6379/0?timeout=5&retry_on_timeout=true"

    def test_url_with_socket_keepalive_query_param_preserved(self) -> None:
        """``socket_keepalive`` query parameter must survive promotion."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "redis://host:6379/0?socket_keepalive=1"
        result = promote_redis_url_to_tls(url)
        assert result == "rediss://host:6379/0?socket_keepalive=1"


class TestIpv6Handling:
    """Feature: IPv6 literal host addresses are handled correctly."""

    def test_ipv6_loopback_url_promoted(self) -> None:
        """``redis://[::1]:6379`` must become ``rediss://[::1]:6379``."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "redis://[::1]:6379"
        result = promote_redis_url_to_tls(url)
        assert result == "rediss://[::1]:6379"

    def test_ipv6_full_address_url_promoted(self) -> None:
        """IPv6 full address literal must be preserved after scheme promotion."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        url = "redis://[2001:db8::1]:6379/0"
        result = promote_redis_url_to_tls(url)
        assert result == "rediss://[2001:db8::1]:6379/0"


class TestSchemeOnlyPreservation:
    """Feature: scheme is changed and only scheme — all other components preserved."""

    def test_scheme_change_is_minimal(self) -> None:
        """Promotion must change only the scheme prefix, nothing else in the URL."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        original = "redis://user:p%40ss@[::1]:6379/2?timeout=10&retry=1"
        result = promote_redis_url_to_tls(original)
        # After promotion, only "redis://" → "rediss://" changes
        expected = "rediss://user:p%40ss@[::1]:6379/2?timeout=10&retry=1"
        assert result == expected

    def test_function_is_idempotent_on_already_promoted_url(self) -> None:
        """promote_redis_url_to_tls must be idempotent for already-TLS URLs."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        promoted = promote_redis_url_to_tls("redis://redis:6379/0")
        assert promote_redis_url_to_tls(promoted) == promoted


class TestSingleImplementationInvariant:
    """Feature: verify single-implementation contract across the codebase."""

    def test_promote_function_is_defined_in_shared_task_queue(self) -> None:
        """promote_redis_url_to_tls must be defined in shared.task_queue, not elsewhere."""
        from synth_engine.shared.task_queue import promote_redis_url_to_tls

        assert promote_redis_url_to_tls.__module__ == "synth_engine.shared.task_queue"

    def test_shared_tls_config_does_not_define_promote_function(self) -> None:
        """shared/tls/config.py must NOT define its own promote_redis_url_to_tls.

        The function belongs in shared/task_queue.py (ADV-P47-02).  If
        shared/tls/config.py ever defines it, we have a new duplication.
        """
        from synth_engine.shared.tls import config as tls_config

        func = getattr(tls_config, "promote_redis_url_to_tls", None)
        # Either the name must be absent, or — if re-exported — must point to task_queue
        if func is not None:
            assert func.__module__ == "synth_engine.shared.task_queue", (
                "promote_redis_url_to_tls found in shared/tls/config.py but defined "
                "outside shared.task_queue — duplication violation (ADV-P47-02)."
            )

    def test_bootstrapper_redis_dep_does_not_define_promote_function_locally(self) -> None:
        """bootstrapper/dependencies/redis.py must import from shared, not re-implement."""
        from synth_engine.bootstrapper.dependencies import redis as redis_dep

        module_file = redis_dep.__file__
        assert module_file is not None

        with open(module_file) as fh:
            source = fh.read()

        # The duplication signature: string replacement body defining rediss:// in-place
        assert 'return "rediss://" +' not in source, (
            "Duplicate promote_redis_url_to_tls implementation found in "
            "bootstrapper/dependencies/redis.py (ADV-P47-02)."
        )
