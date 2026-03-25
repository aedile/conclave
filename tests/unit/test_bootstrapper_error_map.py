"""Unit tests for unseal route RFC 7807 format and complete operator error map.

Tests for /unseal error format and verification that all 11 SynthEngineError
subclasses are mapped in OPERATOR_ERROR_MAP.
Split from test_bootstrapper_errors.py (T56.3).

Task: T34.3 — Complete OPERATOR_ERROR_MAP for All Domain Exceptions
CONSTITUTION Priority 0: Security — sanitized messages for security-sensitive exceptions.
CONSTITUTION Priority 3: TDD
"""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.unit


class TestUnsealRouteRFC7807Format:
    """T29.3: Tests verifying /unseal route uses RFC 7807 format for errors.

    The /unseal route previously returned ad-hoc ``{"error_code": ..., "detail": ...}``
    responses. These must be upgraded to RFC 7807 format with operator-friendly
    messages, matching the pattern used by other domain exception handlers.

    Task: P29-T29.3 — Error Message Audience Differentiation
    """

    @pytest.mark.asyncio
    async def test_empty_passphrase_returns_rfc7807_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /unseal with empty passphrase must return RFC 7807 body.

        The response must have ``type``, ``title``, ``status``, and ``detail``
        keys per RFC 7807, not the legacy ``error_code``/``detail`` format.

        T38.2 note: VAULT_SEAL_SALT must be set so the empty-passphrase check
        is reached after the timing fix (check moved after derive_kek).
        """
        import base64
        import os

        from synth_engine.bootstrapper.main import create_app

        salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
        monkeypatch.setenv("VAULT_SEAL_SALT", salt)

        app = create_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/unseal", json={"passphrase": ""})

        assert response.status_code == 400
        body = response.json()
        # Must be RFC 7807 format
        assert "type" in body, "Response must contain RFC 7807 'type' field"
        assert "title" in body, "Response must contain RFC 7807 'title' field"
        assert "status" in body, "Response must contain RFC 7807 'status' field"
        assert "detail" in body, "Response must contain RFC 7807 'detail' field"
        # Must NOT be legacy format
        assert "error_code" not in body, (
            "Response must not use legacy 'error_code' field — use RFC 7807 format"
        )
        assert body["title"] == "Empty Passphrase"

    @pytest.mark.asyncio
    async def test_vault_config_error_returns_rfc7807_format(self) -> None:
        """POST /unseal when VAULT_SEAL_SALT missing must return RFC 7807 body."""
        from unittest.mock import patch as _patch

        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.security.vault import VaultConfigError

        app = create_app()

        with _patch(
            "synth_engine.bootstrapper.lifecycle.VaultState.unseal",
            side_effect=VaultConfigError("VAULT_SEAL_SALT not set"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/unseal", json={"passphrase": "somepass"})

        assert response.status_code == 400
        body = response.json()
        assert "type" in body
        assert "title" in body
        assert "status" in body
        assert "detail" in body
        assert "error_code" not in body, "Response must not use legacy 'error_code' field"
        assert body["title"] == "Vault Configuration Error"


class TestT343CompleteOperatorErrorMap:
    """T34.3: Tests for the 6 newly-mapped domain exceptions in OPERATOR_ERROR_MAP.

    Verifies that all 11 SynthEngineError subclasses have RFC 7807 mappings
    with correct HTTP status codes and type URIs.

    Task: T34.3 — Complete OPERATOR_ERROR_MAP for All Domain Exceptions
    CONSTITUTION Priority 0: Security — sanitized messages for security-sensitive exceptions.
    """

    def test_vault_already_unsealed_error_maps_to_400_bad_request(self) -> None:
        """VaultAlreadyUnsealedError must map to HTTP 400 Bad Request.

        Attempting to unseal an already-unsealed vault is a bad request — the
        operator's desired state (vault unsealed) is already achieved. HTTP 400
        Bad Request is more appropriate than 409 Conflict and is consistent with
        the bespoke inline handler in bootstrapper/lifecycle.py.

        Review finding P34: status code reconciliation between OPERATOR_ERROR_MAP
        and the bespoke handler in lifecycle.py — both must agree on 400.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import VaultAlreadyUnsealedError

        assert VaultAlreadyUnsealedError in OPERATOR_ERROR_MAP, (
            "VaultAlreadyUnsealedError must be in OPERATOR_ERROR_MAP (T34.3)"
        )
        entry = OPERATOR_ERROR_MAP[VaultAlreadyUnsealedError]
        assert entry["status_code"] == 400
        assert entry["type_uri"] == "about:blank"
        assert len(entry["title"]) > 0
        assert len(entry["detail"]) > 0

    def test_license_error_maps_to_403_forbidden(self) -> None:
        """LicenseError must map to HTTP 403 Forbidden.

        A license validation failure means the operator is not authorized to use
        the engine. HTTP 403 Forbidden communicates this clearly.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import LicenseError

        assert LicenseError in OPERATOR_ERROR_MAP, (
            "LicenseError must be in OPERATOR_ERROR_MAP (T34.3)"
        )
        entry = OPERATOR_ERROR_MAP[LicenseError]
        assert entry["status_code"] == 403
        assert entry["type_uri"] == "about:blank"
        assert len(entry["title"]) > 0
        assert len(entry["detail"]) > 0

    def test_collision_error_maps_to_409_conflict(self) -> None:
        """CollisionError must map to HTTP 409 Conflict.

        A masking collision is a data-state conflict — two distinct source values
        would collide to the same masked output. HTTP 409 Conflict is correct.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.modules.masking.registry import CollisionError

        assert CollisionError in OPERATOR_ERROR_MAP, (
            "CollisionError must be in OPERATOR_ERROR_MAP (T34.3). "
            "Import from modules/masking/registry.py per task spec."
        )
        entry = OPERATOR_ERROR_MAP[CollisionError]
        assert entry["status_code"] == 409
        assert entry["type_uri"] == "about:blank"
        assert len(entry["title"]) > 0
        assert len(entry["detail"]) > 0

    def test_cycle_detection_error_maps_to_422_unprocessable(self) -> None:
        """CycleDetectionError must map to HTTP 422 Unprocessable Entity.

        A cycle in the schema FK graph is a structural data problem — the input
        schema is malformed. HTTP 422 Unprocessable Entity is correct.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.modules.mapping.graph import CycleDetectionError

        assert CycleDetectionError in OPERATOR_ERROR_MAP, (
            "CycleDetectionError must be in OPERATOR_ERROR_MAP (T34.3). "
            "Import from modules/mapping/graph.py per task spec."
        )
        entry = OPERATOR_ERROR_MAP[CycleDetectionError]
        assert entry["status_code"] == 422
        assert entry["type_uri"] == "about:blank"
        assert len(entry["title"]) > 0
        assert len(entry["detail"]) > 0

    def test_epsilon_measurement_error_maps_to_500_with_problem_type(self) -> None:
        """EpsilonMeasurementError must map to HTTP 500 with the epsilon-measurement problem type.

        When the DP engine cannot measure the privacy cost of a training run, the job
        is marked FAILED.  The operator-facing response must clearly identify the
        problem type so operators know to retry or investigate DP accounting.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import EpsilonMeasurementError

        assert EpsilonMeasurementError in OPERATOR_ERROR_MAP, (
            "EpsilonMeasurementError must be in OPERATOR_ERROR_MAP (T37.1). "
            "Import from shared/exceptions.py."
        )
        entry = OPERATOR_ERROR_MAP[EpsilonMeasurementError]
        assert entry["status_code"] == 500
        assert entry["type_uri"] == "/problems/epsilon-measurement-failure"
        assert len(entry["title"]) > 0
        assert len(entry["detail"]) > 0

    def test_privilege_escalation_error_maps_to_403_with_sanitized_detail(self) -> None:
        """PrivilegeEscalationError must map to HTTP 403 with a fixed sanitized detail.

        The detail must be a static string that does NOT contain database role names,
        privilege descriptions, or any security-sensitive context from str(exc).
        Security: detail text must not leak credential hints to the HTTP caller.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import PrivilegeEscalationError

        assert PrivilegeEscalationError in OPERATOR_ERROR_MAP, (
            "PrivilegeEscalationError must be in OPERATOR_ERROR_MAP (T34.3)"
        )
        entry = OPERATOR_ERROR_MAP[PrivilegeEscalationError]
        assert entry["status_code"] == 403
        assert entry["type_uri"] == "about:blank"
        # The detail must be a non-empty static safe string
        assert len(entry["detail"]) > 0
        # The detail must NOT be dynamic exception text — it must be a fixed string
        # that does not reveal database role names or privilege details
        assert "INSERT" not in entry["detail"]
        assert "UPDATE" not in entry["detail"]
        assert "DELETE" not in entry["detail"]
        assert "superuser" not in entry["detail"].lower()

    def test_artifact_tampering_error_maps_to_422_with_sanitized_detail(self) -> None:
        """ArtifactTamperingError must map to HTTP 422 with a fixed sanitized detail.

        The detail must be a static string that does NOT contain artifact paths,
        HMAC signing key hints, or any security-sensitive context from str(exc).
        Security: detail text must not confirm artifact locations to the HTTP caller.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.shared.exceptions import ArtifactTamperingError

        assert ArtifactTamperingError in OPERATOR_ERROR_MAP, (
            "ArtifactTamperingError must be in OPERATOR_ERROR_MAP (T34.3)"
        )
        entry = OPERATOR_ERROR_MAP[ArtifactTamperingError]
        assert entry["status_code"] == 422
        assert entry["type_uri"] == "about:blank"
        # The detail must be a non-empty static safe string
        assert len(entry["detail"]) > 0

    def test_all_11_synth_engine_error_subclasses_are_mapped(self) -> None:
        """OPERATOR_ERROR_MAP must contain entries for all 11 SynthEngineError subclasses.

        This is the primary acceptance criterion for T34.3: no domain exception
        should fall through to the generic 500 handler. Every SynthEngineError
        subclass must have an explicit RFC 7807 mapping.
        """
        from synth_engine.bootstrapper.errors import OPERATOR_ERROR_MAP
        from synth_engine.modules.mapping.graph import CycleDetectionError
        from synth_engine.modules.masking.registry import CollisionError
        from synth_engine.shared.exceptions import (
            ArtifactTamperingError,
            BudgetExhaustionError,
            EpsilonMeasurementError,
            LicenseError,
            OOMGuardrailError,
            PrivilegeEscalationError,
            VaultAlreadyUnsealedError,
            VaultConfigError,
            VaultEmptyPassphraseError,
            VaultSealedError,
        )

        expected = {
            BudgetExhaustionError,
            EpsilonMeasurementError,
            OOMGuardrailError,
            PrivilegeEscalationError,
            ArtifactTamperingError,
            VaultSealedError,
            VaultEmptyPassphraseError,
            VaultAlreadyUnsealedError,
            VaultConfigError,
            LicenseError,
            CollisionError,
            CycleDetectionError,
        }
        missing = expected - set(OPERATOR_ERROR_MAP.keys())
        assert not missing, (
            f"OPERATOR_ERROR_MAP is missing entries for: {', '.join(c.__name__ for c in missing)}"
        )

    @pytest.mark.asyncio
    async def test_vault_already_unsealed_raises_400_through_middleware(self) -> None:
        """VaultAlreadyUnsealedError raised in a route must produce RFC 7807 400 response.

        Review finding P34: OPERATOR_ERROR_MAP maps VaultAlreadyUnsealedError to 400
        (consistent with the bespoke inline handler in bootstrapper/lifecycle.py).
        """
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import VaultAlreadyUnsealedError

        app = create_app()

        @app.get("/test-vault-already-unsealed")
        async def _raise_already_unsealed() -> None:
            raise VaultAlreadyUnsealedError("Vault is already unsealed")

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
                response = await client.get("/test-vault-already-unsealed")

        assert response.status_code == 400
        body = response.json()
        assert body["type"] == "about:blank"
        assert body["status"] == 400
        assert "title" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_post_unseal_when_already_unsealed_returns_400(self) -> None:
        """POST /unseal when vault is already unsealed must return HTTP 400.

        Review finding P34: the lifecycle.py bespoke handler for VaultAlreadyUnsealedError
        on POST /unseal returns 400. This test exercises that path directly to confirm
        the concrete endpoint agrees with the OPERATOR_ERROR_MAP entry (both 400).
        """
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.security.vault import VaultAlreadyUnsealedError, VaultState

        app = create_app()
        with patch.object(
            VaultState,
            "unseal",
            side_effect=VaultAlreadyUnsealedError(
                "Vault is already unsealed. Call seal() before unsealing again."
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/unseal", json={"passphrase": "any-passphrase"})

        assert response.status_code == 400
        body = response.json()
        assert body["type"] == "about:blank"
        assert body["title"] == "Vault Already Unsealed"
        assert body["status"] == 400
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_license_error_raises_403_through_middleware(self) -> None:
        """LicenseError raised in a route must produce RFC 7807 403 response."""
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import LicenseError

        app = create_app()

        @app.get("/test-license-error")
        async def _raise_license() -> None:
            raise LicenseError("License token has expired.")

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
                response = await client.get("/test-license-error")

        assert response.status_code == 403
        body = response.json()
        assert body["type"] == "about:blank"
        assert body["status"] == 403
        assert "title" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_collision_error_raises_409_through_middleware(self) -> None:
        """CollisionError raised in a route must produce RFC 7807 409 response."""
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.modules.masking.registry import CollisionError

        app = create_app()

        @app.get("/test-collision-error")
        async def _raise_collision() -> None:
            raise CollisionError("Masking collision detected")

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
                response = await client.get("/test-collision-error")

        assert response.status_code == 409
        body = response.json()
        assert body["type"] == "about:blank"
        assert body["status"] == 409
        assert "title" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_cycle_detection_error_raises_422_through_middleware(self) -> None:
        """CycleDetectionError raised in a route must produce RFC 7807 422 response.

        CycleDetectionError already had a bespoke handler in router_registry.
        T34.3 migrates it to use OPERATOR_ERROR_MAP via operator_error_response()
        for consistency with all other domain exceptions.
        """
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.modules.mapping.graph import CycleDetectionError

        app = create_app()

        @app.get("/test-cycle-error")
        async def _raise_cycle() -> None:
            raise CycleDetectionError(["orders", "customers", "orders"])

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
                response = await client.get("/test-cycle-error")

        assert response.status_code == 422
        body = response.json()
        assert body["type"] == "about:blank"
        assert body["status"] == 422
        assert "title" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_privilege_escalation_does_not_leak_internals_via_http(self) -> None:
        """PrivilegeEscalationError HTTP response must not contain the raw exception message.

        Security: the exception message may contain database role names or privilege
        details. The HTTP response must use the static sanitized detail from
        OPERATOR_ERROR_MAP — never str(exc).
        """
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import PrivilegeEscalationError

        app = create_app()

        @app.get("/test-privilege-escalation")
        async def _raise_priv() -> None:
            raise PrivilegeEscalationError(
                "User 'admin_role' has INSERT, UPDATE, DELETE on table 'users'"
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
                response = await client.get("/test-privilege-escalation")

        assert response.status_code == 403
        body = response.json()
        # Must not expose the raw exception message with role name and privilege details
        assert "admin_role" not in str(body)
        assert "INSERT" not in str(body)
        assert body["type"] == "about:blank"

    @pytest.mark.asyncio
    async def test_artifact_tampering_does_not_leak_internals_via_http(self) -> None:
        """ArtifactTamperingError HTTP response must not contain the raw exception message.

        Security: the exception message may contain artifact paths or HMAC details.
        The HTTP response must use the static sanitized detail from
        OPERATOR_ERROR_MAP — never str(exc).
        """
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.shared.exceptions import ArtifactTamperingError

        app = create_app()

        @app.get("/test-artifact-tampering")
        async def _raise_tamper() -> None:
            raise ArtifactTamperingError(
                "HMAC mismatch on /data/models/secret_model.pkl key=0xdeadbeef"
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
                response = await client.get("/test-artifact-tampering")

        assert response.status_code == 422
        body = response.json()
        # Must not expose the raw exception message with artifact path or HMAC key hint
        assert "secret_model.pkl" not in str(body)
        assert "0xdeadbeef" not in str(body)
        assert body["type"] == "about:blank"
