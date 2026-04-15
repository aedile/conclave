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

Security properties:
- Key uses a random token (not derived from user_id) — prevents session fixation.
- Concurrent session limit enforced by evicting oldest (earliest created_at).
- Redis failure on write → 503 Service Unavailable (fail-closed).
- Redis failure on read/validate → 401 Unauthorized (fail-closed).

Module Boundary:
    Lives in ``bootstrapper/dependencies/`` — session management is an
    HTTP-layer authentication concern, not a domain module concern.

CONSTITUTION Priority 0: Security — session fixation prevention, fail-closed
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Phase: 81 — SSO/OIDC Integration
ADR: ADR-0067 — OIDC Integration
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
) -> str:
    """Write a new session to Redis and return the session key.

    Creates a new session record under a randomly generated key with the
    specified TTL. The session data contains user identity and timestamps
    required for session management and auditing.

    Args:
        redis_client: Redis client instance.
        user_id: UUID string of the authenticated user.
        org_id: UUID string of the user's organization.
        role: RBAC role string for this session (e.g. ``"operator"``).
        ttl_seconds: Session TTL in seconds. Must match SESSION_TTL_SECONDS.

    Returns:
        The Redis key under which the session was stored (for audit logging
        and returning to the caller to include in the JWT or response).

    Raises:
        redis.RedisError: If the Redis write fails. Callers should catch
            this and return 503 Service Unavailable (fail-closed).
    """
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
    redis_client.setex(session_key, ttl_seconds, session_data)

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

    Scans all ``conclave:session:*`` keys, loads the session data for each,
    and filters for sessions belonging to the given user_id + org_id. If the
    count of matching sessions is >= limit, the session with the earliest
    ``created_at`` timestamp is deleted to make room for the new one.

    Eviction policy: one session evicted per call, not all excess sessions.
    The caller writes the new session after this function returns.

    Args:
        redis_client: Redis client instance.
        user_id: UUID string of the user about to create a new session.
        org_id: UUID string of the user's organization.
        limit: Maximum number of concurrent sessions allowed per user.

    Raises:
        redis.RedisError: If the Redis scan or delete fails.
    """
    session_keys: list[bytes] = list(redis_client.scan_iter(f"{SESSION_KEY_PREFIX}*"))

    if not session_keys:
        return

    # Batch-load all session values.
    raw_values: list[bytes | None] = cast(list[bytes | None], redis_client.mget(session_keys))

    # Filter sessions belonging to this user.
    user_sessions: list[tuple[bytes, str]] = []  # (key, created_at)
    for key, raw in zip(session_keys, raw_values, strict=False):
        if raw is None:
            continue
        try:
            data: dict[str, str] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if data.get("user_id") == user_id and data.get("org_id") == org_id:
            created_at = data.get("created_at", "")
            user_sessions.append((key, created_at))

    if len(user_sessions) < limit:
        # Under the limit — no eviction needed.
        return

    # Sort ascending by created_at (earliest first).
    user_sessions.sort(key=lambda item: item[1])

    # Evict the oldest session.
    oldest_key, oldest_created = user_sessions[0]
    redis_client.delete(oldest_key)

    _logger.info(
        "Concurrent session limit reached for user_id=%s org_id=%s "
        "(limit=%d, existing=%d). Evicted oldest session (created_at=%s).",
        user_id,
        org_id,
        limit,
        len(user_sessions),
        oldest_created,
    )
