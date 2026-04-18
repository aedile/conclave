"""Redis-backed session management for OIDC-authenticated users — Phase 81.

Provides session lifecycle primitives for OIDC sessions:
- :func:`create_session_key`: Generate a unique, random Redis key for a session.
- :func:`write_session`: Write a new session to Redis with the configured TTL.
- :func:`enforce_concurrent_session_limit`: Evict the oldest session when the
  per-user concurrent session limit is exceeded.

Session Architecture (ADR-0067 Decision 5):
--------------------------------------------
Sessions only exist when OIDC is enabled. Passphrase auth remains stateless JWT.

Session Redis key format: ``conclave:session:<random_token>``
Value: JSON with user_id, org_id, role, created_at, last_refreshed_at.
TTL: configurable via SESSION_TTL_SECONDS (default 28800 = 8h, minimum 60s).

Per-user session index: ``conclave:user_sessions:<user_id>:<org_id>``
A Redis SET whose members are the session keys for that user. Updated atomically
via a Lua script on create/evict to eliminate the O(N) SCAN and the TOCTOU race
in the concurrent-session limit check.

Security properties:
- Key uses a random token (not derived from user_id) — prevents session fixation.
- Concurrent session limit enforced by evicting oldest (earliest created_at).
  Uses atomic Lua script: SMEMBERS → count → evict → SADD (no TOCTOU window).
- Index key TTL bounded to ``ttl * limit`` seconds to prevent unbounded Redis
  memory growth from dead index keys (F18).
- Redis failure on write → 503 Service Unavailable (fail-closed).
- Redis failure on read/validate → 401 Unauthorized (fail-closed).

Module Boundary:
    Lives in ``bootstrapper/dependencies/`` — session management is an
    HTTP-layer authentication concern, not a domain module concern.

CONSTITUTION Priority 0: Security — session fixation prevention, fail-closed
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Phase: 81 — SSO/OIDC Integration
ADR: ADR-0067 — OIDC Integration
Review fix: F2 (O(N) SCAN replaced by per-user index), F3 (TOCTOU fixed with Lua script)
Review fix: F18 (index key TTL bounded), F19 (user_index_key made public)
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import redis as redis_lib

_logger = logging.getLogger(__name__)

#: Namespace prefix for all OIDC session keys in Redis.
#: Isolates session keys from OIDC state keys and other middleware namespaces.
SESSION_KEY_PREFIX: str = "conclave:session:"

#: Namespace prefix for the per-user session index SET.
#: Key format: ``conclave:user_sessions:<user_id>:<org_id>``
_USER_SESSIONS_INDEX_PREFIX: str = "conclave:user_sessions:"

#: Lua script for atomically creating a session and enforcing the concurrent limit.
#:
#: KEYS[1] = session key (e.g. ``conclave:session:<token>``)
#: KEYS[2] = user index key (e.g. ``conclave:user_sessions:<user_id>:<org_id>``)
#: ARGV[1] = session JSON value
#: ARGV[2] = session TTL in seconds
#: ARGV[3] = concurrent session limit (integer)
#:
#: Algorithm:
#:   1. SMEMBERS the user index to get all existing session keys.
#:   2. For each, GET the value, parse JSON, collect (key, created_at).
#:   3. If count >= limit, delete the oldest session key and SREM it from the index.
#:   4. SET the new session key with TTL.
#:   5. SADD the new session key to the user index.
#:   6. EXPIRE the index key to ``ttl * limit`` seconds (F18: bound index lifetime).
#:
#: This executes atomically — no other command can interleave.
_WRITE_SESSION_LUA = """
local session_key = KEYS[1]
local index_key   = KEYS[2]
local session_val = ARGV[1]
local ttl         = tonumber(ARGV[2])
local limit       = tonumber(ARGV[3])

-- Collect existing sessions for this user.
local members = redis.call('SMEMBERS', index_key)
local sessions = {}
for _, k in ipairs(members) do
    local raw = redis.call('GET', k)
    if raw then
        -- Very small JSON parse: find created_at value.
        local ts = string.match(raw, '"created_at"%s*:%s*"([^"]+)"')
        if ts then
            table.insert(sessions, {key=k, ts=ts})
        end
    else
        -- Key expired — clean up stale index entry.
        redis.call('SREM', index_key, k)
    end
end

-- Evict oldest if at or over limit.
if #sessions >= limit then
    -- Sort ascending by ts string (ISO-8601 compares lexicographically).
    table.sort(sessions, function(a, b) return a.ts < b.ts end)
    local oldest = sessions[1]
    redis.call('DEL', oldest.key)
    redis.call('SREM', index_key, oldest.key)
end

-- Write the new session.
redis.call('SETEX', session_key, ttl, session_val)
redis.call('SADD', index_key, session_key)

-- F18: Bound the index key lifetime to prevent unbounded Redis memory growth.
-- The index can hold at most ``limit`` members, each with TTL ``ttl``.
-- Setting the index TTL to ``ttl * limit`` ensures it is garbage-collected
-- even if all sessions expire without a revoke call removing the index.
redis.call('EXPIRE', index_key, ttl * limit)

return 1
"""


def user_index_key(user_id: str, org_id: str) -> str:
    """Construct the Redis SET key for a user's session index.

    Args:
        user_id: UUID string of the user.
        org_id: UUID string of the user's organization.

    Returns:
        Redis key string for the per-user session index SET.
    """
    return f"{_USER_SESSIONS_INDEX_PREFIX}{user_id}:{org_id}"


def create_session_key() -> str:
    """Generate a unique Redis key for a new session.

    The key format is ``conclave:session:<random_token>`` where the token
    is generated with :func:`secrets.token_urlsafe` (32 bytes → 43 chars).

    The token is NOT derived from user_id — this prevents session fixation
    attacks where an attacker knowing the user_id could predict session keys.

    Returns:
        A unique Redis key string suitable for storing a session value.
    """
    token = secrets.token_urlsafe(32)
    return f"{SESSION_KEY_PREFIX}{token}"


def write_session(
    *,
    redis_client: redis_lib.Redis,
    user_id: str,
    org_id: str,
    role: str,
    ttl_seconds: int,
    concurrent_limit: int = 10,
) -> str:
    """Write a new session to Redis and return the session key.

    Creates a new session record under a randomly generated key. The concurrent
    session limit is enforced atomically via a Lua script that also maintains
    the per-user session index (``conclave:user_sessions:<user_id>:<org_id>``).

    Args:
        redis_client: Redis client instance.
        user_id: UUID string of the authenticated user.
        org_id: UUID string of the user's organization.
        role: RBAC role string for this session (e.g. ``"operator"``).
        ttl_seconds: Session TTL in seconds. Must match SESSION_TTL_SECONDS.
        concurrent_limit: Maximum concurrent sessions per user before eviction.

    Returns:
        The Redis key under which the session was stored.

    Raises:
        redis.RedisError: If the Redis write fails. Callers should catch
            this and return 503 Service Unavailable (fail-closed).
    """  # noqa: DOC502
    now = datetime.now(UTC).isoformat()
    session_data = json.dumps(
        {
            "user_id": user_id,
            "org_id": org_id,
            "role": role,
            "created_at": now,
            "last_refreshed_at": now,
        }
    )

    session_key = create_session_key()
    index_key = user_index_key(user_id, org_id)

    redis_client.eval(
        _WRITE_SESSION_LUA,
        2,
        session_key,
        index_key,
        session_data,
        str(ttl_seconds),
        str(concurrent_limit),
    )

    _logger.debug(
        "Session created: key_prefix=%s user_id=%s org_id=%s",
        session_key[:30],  # Log prefix only — not the full token
        user_id,
        org_id,
    )

    return session_key


def enforce_concurrent_session_limit(
    *,
    redis_client: redis_lib.Redis,
    user_id: str,
    org_id: str,
    limit: int,
) -> None:
    """Evict the oldest session if the concurrent session limit is reached.

    Uses the per-user session index SET (``conclave:user_sessions:<user_id>:<org_id>``)
    to find sessions for this user in O(1) per user (not O(N) SCAN).

    This function is called before :func:`write_session` when the session write
    is done outside of the Lua path (e.g. the older call site). Prefer
    :func:`write_session` with ``concurrent_limit`` for atomic enforcement.

    Args:
        redis_client: Redis client instance.
        user_id: UUID string of the user about to create a new session.
        org_id: UUID string of the user's organization.
        limit: Maximum number of concurrent sessions allowed per user.

    Raises:
        redis.RedisError: If the Redis scan or delete fails.
    """  # noqa: DOC502
    index_key = user_index_key(user_id, org_id)
    session_keys: set[bytes] = cast(set[bytes], redis_client.smembers(index_key))

    if not session_keys:
        return

    # Load session values for this user's keys.
    key_list = list(session_keys)
    raw_values: list[bytes | None] = cast(list[bytes | None], redis_client.mget(key_list))

    user_sessions: list[tuple[bytes, str]] = []  # (key, created_at)
    stale_keys: list[bytes] = []
    for key, raw in zip(key_list, raw_values, strict=False):
        if raw is None:
            # Key expired — record for cleanup.
            stale_keys.append(key)
            continue
        try:
            data: dict[str, str] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        created_at = data.get("created_at", "")
        user_sessions.append((key, created_at))

    # Clean up stale index entries.
    if stale_keys:
        redis_client.srem(index_key, *stale_keys)

    if len(user_sessions) < limit:
        return

    # Sort ascending by created_at (earliest first).
    user_sessions.sort(key=lambda item: item[1])

    # Evict the oldest session.
    oldest_key, oldest_created = user_sessions[0]
    redis_client.delete(oldest_key)
    redis_client.srem(index_key, oldest_key)

    _logger.info(
        "Concurrent session limit reached for user_id=%s org_id=%s "
        "(limit=%d, existing=%d). Evicted oldest session (created_at=%s).",
        user_id,
        org_id,
        limit,
        len(user_sessions),
        oldest_created,
    )


def remove_session_from_index(
    *,
    redis_client: redis_lib.Redis,
    user_id: str,
    org_id: str,
    session_key: str | bytes,
) -> None:
    """Remove a session key from the per-user session index.

    Called when a session is revoked to keep the per-user index consistent.

    Args:
        redis_client: Redis client instance.
        user_id: UUID string of the session owner.
        org_id: UUID string of the session owner's organization.
        session_key: The session Redis key to remove from the index.
    """
    index_key = user_index_key(user_id, org_id)
    redis_client.srem(index_key, session_key)
