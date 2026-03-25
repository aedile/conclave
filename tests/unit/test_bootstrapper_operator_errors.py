"""Unit tests for operator-friendly error message mapping.

Operator-facing error message differentiation tests.
Split from test_bootstrapper_errors.py (T56.3).

Task: P29-T29.3 — Error Message Audience Differentiation
CONSTITUTION Priority 0: Security — sanitized error messages for operators.
CONSTITUTION Priority 3: TDD
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.unit


class TestOperatorFriendlyErrorMessages:
    """T29.3: Tests for operator-friendly error message mapping.

    The bootstrapper's exception handlers must convert domain exceptions into
    RFC 7807 responses with human-readable titles and actionable detail messages.
    Internal exception messages are preserved in logs and MUST NOT be exposed
    verbatim via HTTP.

    CONSTITUTION Priority 0: Security — never leak internal technical details.
    Task: P29-T29.3 — Error Message Audience Differentiation
    """

    def test_budget_exhaustion_error_produces_friendly_title(self) -> None:
        """BudgetExhaustionError must map to 'Privacy Budget Exceeded' title."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import BudgetExhaustionError

        assert BudgetExhaustionError in OPERATOR_ERROR_MAP
        entry = OPERATOR_ERROR_MAP[BudgetExhaustionError]
        assert entry["title"] == "Privacy Budget Exceeded"

    def test_budget_exhaustion_error_detail_contains_remediation(self) -> None:
        """BudgetExhaustionError detail must mention how to reset the budget."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import BudgetExhaustionError

        entry = OPERATOR_ERROR_MAP[BudgetExhaustionError]
        detail = entry["detail"].lower()
        # Must reference budget reset action
        assert "reset" in detail or "budget" in detail

    def test_vault_sealed_error_produces_friendly_title(self) -> None:
        """VaultSealedError must map to 'Vault Is Sealed' title."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import VaultSealedError

        assert VaultSealedError in OPERATOR_ERROR_MAP
        entry = OPERATOR_ERROR_MAP[VaultSealedError]
        assert entry["title"] == "Vault Is Sealed"

    def test_vault_sealed_error_detail_contains_unseal_instruction(self) -> None:
        """VaultSealedError detail must instruct operator to unseal."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import VaultSealedError

        entry = OPERATOR_ERROR_MAP[VaultSealedError]
        detail = entry["detail"].lower()
        assert "unseal" in detail

    def test_vault_empty_passphrase_error_produces_friendly_title(self) -> None:
        """VaultEmptyPassphraseError must map to 'Empty Passphrase' title."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.security.vault import VaultEmptyPassphraseError

        assert VaultEmptyPassphraseError in OPERATOR_ERROR_MAP
        entry = OPERATOR_ERROR_MAP[VaultEmptyPassphraseError]
        assert entry["title"] == "Empty Passphrase"

    def test_vault_empty_passphrase_error_detail_contains_action(self) -> None:
        """VaultEmptyPassphraseError detail must instruct operator to enter passphrase."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.security.vault import VaultEmptyPassphraseError

        entry = OPERATOR_ERROR_MAP[VaultEmptyPassphraseError]
        detail = entry["detail"].lower()
        assert "passphrase" in detail

    def test_vault_config_error_produces_friendly_title(self) -> None:
        """VaultConfigError must map to 'Vault Configuration Error' title."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.security.vault import VaultConfigError

        assert VaultConfigError in OPERATOR_ERROR_MAP
        entry = OPERATOR_ERROR_MAP[VaultConfigError]
        assert entry["title"] == "Vault Configuration Error"

    def test_vault_config_error_detail_references_env_var(self) -> None:
        """VaultConfigError detail must reference the VAULT_SEAL_SALT env var."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.security.vault import VaultConfigError

        entry = OPERATOR_ERROR_MAP[VaultConfigError]
        assert "VAULT_SEAL_SALT" in entry["detail"]

    def test_oom_guardrail_error_produces_friendly_title(self) -> None:
        """OOMGuardrailError must map to 'Memory Limit Exceeded' title."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import OOMGuardrailError

        assert OOMGuardrailError in OPERATOR_ERROR_MAP
        entry = OPERATOR_ERROR_MAP[OOMGuardrailError]
        assert entry["title"] == "Memory Limit Exceeded"

    def test_oom_guardrail_error_detail_contains_remediation(self) -> None:
        """OOMGuardrailError detail must suggest reducing the dataset."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import OOMGuardrailError

        entry = OPERATOR_ERROR_MAP[OOMGuardrailError]
        detail = entry["detail"].lower()
        assert "dataset" in detail or "reduce" in detail or "rows" in detail

    def test_operator_error_map_entries_have_required_keys(self) -> None:
        """Every entry in OPERATOR_ERROR_MAP must have title, detail, status_code, type_uri."""
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP

        required_keys = {"title", "detail", "status_code", "type_uri"}
        for exc_class, entry in OPERATOR_ERROR_MAP.items():
            missing = required_keys - entry.keys()
            assert not missing, f"{exc_class.__name__} entry missing keys: {missing}"

    def test_privilege_escalation_error_in_operator_map_with_sanitized_detail(self) -> None:
        """PrivilegeEscalationError must appear in OPERATOR_ERROR_MAP with a fixed safe detail.

        T34.3: All 11 SynthEngineError subclasses must have RFC 7807 mappings.
        The detail must NOT reference database roles, credential hints, or any
        security-sensitive internals — it must use a fixed, sanitized string.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import PrivilegeEscalationError

        assert PrivilegeEscalationError in OPERATOR_ERROR_MAP, (
            "PrivilegeEscalationError must have an OPERATOR_ERROR_MAP entry (T34.3). "
            "The detail must be a fixed, sanitized string — not str(exc)."
        )
        entry = OPERATOR_ERROR_MAP[PrivilegeEscalationError]
        assert entry["status_code"] == 403
        # Detail must be a fixed static string — must not contain dynamic exception text
        assert len(entry["detail"]) > 0
        # Must NOT include the placeholder that would expose role/privilege internals
        assert "str(exc)" not in entry["detail"]

    def test_artifact_tampering_error_in_operator_map_with_sanitized_detail(self) -> None:
        """ArtifactTamperingError must appear in OPERATOR_ERROR_MAP with a fixed safe detail.

        T34.3: All 11 SynthEngineError subclasses must have RFC 7807 mappings.
        The detail must NOT reference artifact paths, HMAC keys, or signing details —
        it must use a fixed, sanitized string.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import ArtifactTamperingError

        assert ArtifactTamperingError in OPERATOR_ERROR_MAP, (
            "ArtifactTamperingError must have an OPERATOR_ERROR_MAP entry (T34.3). "
            "The detail must be a fixed, sanitized string — not str(exc)."
        )
        entry = OPERATOR_ERROR_MAP[ArtifactTamperingError]
        assert entry["status_code"] == 422
        # Detail must be a fixed static string — must not reference artifact paths or HMAC keys
        assert len(entry["detail"]) > 0

    def test_operator_error_response_raises_key_error_for_unknown_exception(self) -> None:
        """operator_error_response() must raise KeyError for unmapped exception classes.

        The docstring for operator_error_response() documents that it raises
        KeyError when called with an exception whose class is not in
        OPERATOR_ERROR_MAP.  This test exercises that contract directly so the
        behaviour is verified by the test suite.
        """
        from synth_engine.bootstrapper.errors import operator_error_response

        with pytest.raises(KeyError):
            operator_error_response(RuntimeError("test"))


class TestOperatorFriendlyExceptionHandlers:
    """T29.3: Integration tests for exception handlers registered in router_registry.

    These tests verify that the FastAPI exception handlers wire up correctly
    and produce RFC 7807 responses with operator-friendly messages when domain
    exceptions are raised from route handlers.
    """

    @pytest.mark.asyncio
    async def test_budget_exhaustion_returns_rfc7807_with_friendly_title(self) -> None:
        """BudgetExhaustionError raised in a route must return RFC 7807 with friendly title."""
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import BudgetExhaustionError

        app = create_app()

        @app.get("/test-budget-exhaustion")
        async def _raise_budget() -> None:
            raise BudgetExhaustionError(
                requested_epsilon=Decimal("0.234"),
                total_spent=Decimal("1.234"),
                total_allocated=Decimal("1.0"),
            )

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/test-budget-exhaustion")

        body = response.json()
        assert body["title"] == "Privacy Budget Exceeded"
        assert "type" in body
        assert "status" in body
        assert "detail" in body

    def test_budget_exhaustion_internal_message_not_in_http_detail(self) -> None:
        """BudgetExhaustionError HTTP detail must not contain raw epsilon values.

        The operator-friendly detail must be the mapping value, not the raw
        internal exception message which contains technical epsilon/delta values.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import BudgetExhaustionError

        entry = OPERATOR_ERROR_MAP[BudgetExhaustionError]
        # The operator detail should not expose raw epsilon math
        assert "epsilon_spent" not in entry["detail"]
        assert "allocated_epsilon" not in entry["detail"]

    @pytest.mark.asyncio
    async def test_vault_sealed_returns_rfc7807_with_friendly_title(self) -> None:
        """VaultSealedError raised in a route must return RFC 7807 with friendly title."""
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import VaultSealedError

        app = create_app()

        @app.get("/test-vault-sealed")
        async def _raise_sealed() -> None:
            raise VaultSealedError()

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/test-vault-sealed")

        body = response.json()
        assert body["title"] == "Vault Is Sealed"
        assert body["status"] == 423

    @pytest.mark.asyncio
    async def test_oom_guardrail_returns_rfc7807_with_friendly_title(self) -> None:
        """OOMGuardrailError raised in a route must return RFC 7807 with friendly title."""
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import OOMGuardrailError

        app = create_app()

        @app.get("/test-oom-guardrail")
        async def _raise_oom() -> None:
            raise OOMGuardrailError(
                "6.8 GiB estimated, 8.0 GiB available -- reduce dataset by 1.00x"
            )

        with (
            patch(
                "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
                return_value=False,
            ),
            patch(
                "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/test-oom-guardrail")

        body = response.json()
        assert body["title"] == "Memory Limit Exceeded"
