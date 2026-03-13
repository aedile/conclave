"""RBAC scope definitions for the Conclave Engine.

Scopes are defined as a StrEnum so they can be used both as enum members
(for exhaustive matching) and as plain strings in JWT payloads and HTTP
headers without an explicit `.value` call.

SCOPE_HIERARCHY maps each scope to the set of scopes it implies, enabling
callers to satisfy a required scope through an implied scope (e.g., ADMIN
implies every other scope).
"""

import enum
import logging

logger = logging.getLogger(__name__)


class Scope(str, enum.Enum):
    """Permission scopes issued inside access tokens.

    Each member serialises to its string value so that JWT payloads can
    store plain strings while application code uses typed enum members.
    """

    SYNTHESIZE = "synth:write"
    READ_RESULTS = "synth:read"
    ADMIN = "admin:*"
    AUDIT_READ = "audit:read"
    VAULT_UNSEAL = "vault:unseal"


# Maps a scope to the set of additional scopes it grants implicitly.
# ADMIN grants every other scope; all other scopes are self-contained.
SCOPE_HIERARCHY: dict[Scope, set[Scope]] = {
    Scope.ADMIN: {
        Scope.SYNTHESIZE,
        Scope.READ_RESULTS,
        Scope.AUDIT_READ,
        Scope.VAULT_UNSEAL,
    },
    Scope.SYNTHESIZE: set(),
    Scope.READ_RESULTS: set(),
    Scope.AUDIT_READ: set(),
    Scope.VAULT_UNSEAL: set(),
}


def has_required_scope(token_scopes: list[str], required: Scope) -> bool:
    """Return True if token_scopes satisfy the required scope.

    A scope is satisfied either by a direct string match or if any scope
    present in token_scopes implies the required scope via SCOPE_HIERARCHY.

    Args:
        token_scopes: List of raw scope strings carried in the JWT payload.
        required: The Scope enum member that must be satisfied.

    Returns:
        True when token_scopes contain or imply the required scope,
        False otherwise.
    """
    for raw in token_scopes:
        if raw == required:
            return True
        # Check whether this raw scope implies the required one via hierarchy
        try:
            candidate = Scope(raw)
        except ValueError:
            logger.warning(
                "Unrecognised scope string in token payload: %r — skipping",
                raw,
            )
            continue
        if required in SCOPE_HIERARCHY.get(candidate, set()):
            return True
    return False
