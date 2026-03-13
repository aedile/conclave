"""Unit tests for RBAC scope definitions.

CONSTITUTION Priority 3: TDD RED/GREEN Phase
Task: P2-T2.3 — Zero-Trust JWT Authentication & RBAC Scopes
"""

import pytest

from synth_engine.shared.auth.scopes import Scope, has_required_scope


def test_direct_scope_match() -> None:
    """has_required_scope returns True when the exact scope is present."""
    assert has_required_scope(["synth:write"], Scope.SYNTHESIZE) is True


def test_missing_scope() -> None:
    """has_required_scope returns False when the required scope is absent."""
    assert has_required_scope(["synth:read"], Scope.SYNTHESIZE) is False


def test_admin_implies_all_scopes() -> None:
    """admin:* in token_scopes satisfies any required scope via hierarchy."""
    assert has_required_scope(["admin:*"], Scope.SYNTHESIZE) is True
    assert has_required_scope(["admin:*"], Scope.READ_RESULTS) is True
    assert has_required_scope(["admin:*"], Scope.AUDIT_READ) is True
    assert has_required_scope(["admin:*"], Scope.VAULT_UNSEAL) is True


def test_scope_enum_values_are_strings() -> None:
    """Every Scope member must be a str instance (StrEnum contract)."""
    for member in Scope:
        assert isinstance(member, str), f"{member!r} is not a str"


def test_empty_token_scopes_returns_false() -> None:
    """Empty scope list never satisfies any required scope."""
    assert has_required_scope([], Scope.READ_RESULTS) is False


def test_audit_read_scope_direct() -> None:
    """audit:read in token satisfies Scope.AUDIT_READ directly."""
    assert has_required_scope(["audit:read"], Scope.AUDIT_READ) is True


def test_vault_unseal_scope_direct() -> None:
    """vault:unseal in token satisfies Scope.VAULT_UNSEAL directly."""
    assert has_required_scope(["vault:unseal"], Scope.VAULT_UNSEAL) is True


def test_read_results_does_not_imply_synthesize() -> None:
    """synth:read does not grant synth:write — scopes are not transitive."""
    assert has_required_scope(["synth:read"], Scope.SYNTHESIZE) is False


def test_unknown_scope_string_is_skipped() -> None:
    """An unrecognised scope string is silently skipped (ValueError branch)."""
    # "unknown:scope" is not a valid Scope enum value; it must not raise and
    # must not satisfy any required scope.
    assert has_required_scope(["unknown:scope"], Scope.SYNTHESIZE) is False
    assert has_required_scope(["unknown:scope", "synth:write"], Scope.SYNTHESIZE) is True


def test_unknown_scope_mixed_with_valid() -> None:
    """Unknown scopes are ignored; valid ones in the same list still match."""
    token_scopes = ["garbage:value", "admin:*"]
    assert has_required_scope(token_scopes, Scope.VAULT_UNSEAL) is True


@pytest.mark.parametrize(
    ("scope_value", "expected"),
    [
        (Scope.SYNTHESIZE, "synth:write"),
        (Scope.READ_RESULTS, "synth:read"),
        (Scope.ADMIN, "admin:*"),
        (Scope.AUDIT_READ, "audit:read"),
        (Scope.VAULT_UNSEAL, "vault:unseal"),
    ],
)
def test_scope_string_values(scope_value: Scope, expected: str) -> None:
    """Each Scope member has the correct string value."""
    assert scope_value == expected
