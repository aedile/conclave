"""Shared token-generation helpers for OIDC unit tests — Phase 81.

These helpers are used by both ``test_oidc_attack.py`` and ``test_oidc_feature.py``
to avoid duplicating the JWT creation logic (F15 review finding).

CONSTITUTION Priority 4: Comprehensive Testing
CONSTITUTION Priority 5: Code Quality
Phase: 81 — SSO/OIDC Integration
Review fix: F15 (_make_token duplicated)
"""

from __future__ import annotations

import time
from typing import Any

import jwt as pyjwt

#: Shared JWT signing secret for OIDC unit tests.
#: Must match JWT_SECRET_KEY in the test fixture environments.
OIDC_TEST_JWT_SECRET: str = (  # pragma: allowlist secret
    "unit-test-jwt-secret-key-long-enough-for-hs256-32chars+"
)

_ORG_A_UUID = "11111111-1111-1111-1111-111111111111"
_USER_A_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def make_oidc_token(
    sub: str = "user@example.com",
    org_id: str = _ORG_A_UUID,
    role: str = "operator",
    user_id: str = _USER_A_UUID,
    expired: bool = False,
) -> str:
    """Create a signed JWT for OIDC unit tests.

    Args:
        sub: JWT subject claim (typically user email or UUID).
        org_id: Organization UUID string.
        role: RBAC role string.
        user_id: User UUID string for the ``user_id`` claim.
        expired: If True, set ``exp`` 10 seconds in the past.

    Returns:
        Compact JWT string signed with :data:`OIDC_TEST_JWT_SECRET`.
    """
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
    return pyjwt.encode(payload, OIDC_TEST_JWT_SECRET, algorithm="HS256")


def make_oidc_auth_header(token: str) -> dict[str, str]:
    """Build an Authorization Bearer header for the given token.

    Args:
        token: Compact JWT string.

    Returns:
        Dictionary with a single ``Authorization`` key.
    """
    return {"Authorization": f"Bearer {token}"}
