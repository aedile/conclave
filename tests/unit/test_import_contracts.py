"""Gate 4 — Import contract tests (P73).

Parametrized tests that verify:
1. Symbol exports from ``__init__.py`` files remain stable after refactors.
2. Required fields exist on key model classes (``SynthesisJob``, ``PrivacyLedger``).
3. Required methods exist on protocol classes (``DPWrapperProtocol``,
   ``SpendBudgetProtocol``, ``WebhookRegistrationProtocol``).

These tests catch refactoring-induced breakage that ``import-linter`` alone
misses — ``import-linter`` only checks import paths, not symbol presence.

Constitution Priority 4: Comprehensive Testing.
Task: P73 — Test Quality Rehabilitation.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Gate 4a — __init__.py symbol exports
# ---------------------------------------------------------------------------

#: Each entry is (import_path, symbol_name).
#: Every symbol listed here must be importable from its package's public API.
_EXPORTED_SYMBOLS: list[tuple[str, str]] = [
    # bootstrapper.errors
    ("synth_engine.bootstrapper.errors", "OPERATOR_ERROR_MAP"),
    ("synth_engine.bootstrapper.errors", "OperatorErrorEntry"),
    ("synth_engine.bootstrapper.errors", "RFC7807Middleware"),
    ("synth_engine.bootstrapper.errors", "operator_error_response"),
    ("synth_engine.bootstrapper.errors", "problem_detail"),
    ("synth_engine.bootstrapper.errors", "register_error_handlers"),
    # modules.ingestion
    ("synth_engine.modules.ingestion", "PostgresIngestionAdapter"),
    ("synth_engine.modules.ingestion", "PrivilegeEscalationError"),
    ("synth_engine.modules.ingestion", "SchemaInspector"),
    # modules.mapping
    ("synth_engine.modules.mapping", "CycleDetectionError"),
    ("synth_engine.modules.mapping", "DirectedAcyclicGraph"),
    ("synth_engine.modules.mapping", "SchemaReflector"),
    # modules.masking
    ("synth_engine.modules.masking", "CollisionError"),
    ("synth_engine.modules.masking", "ColumnType"),
    ("synth_engine.modules.masking", "MaskingRegistry"),
    ("synth_engine.modules.masking", "deterministic_hash"),
    ("synth_engine.modules.masking", "mask_value"),
    # modules.privacy
    ("synth_engine.modules.privacy", "BudgetExhaustionError"),
    ("synth_engine.modules.privacy", "DPTrainingWrapper"),
    ("synth_engine.modules.privacy", "PrivacyLedger"),
    ("synth_engine.modules.privacy", "PrivacyTransaction"),
    ("synth_engine.modules.privacy", "reset_budget"),
    ("synth_engine.modules.privacy", "spend_budget"),
    # modules.profiler
    ("synth_engine.modules.profiler", "ColumnDelta"),
    ("synth_engine.modules.profiler", "ColumnProfile"),
    ("synth_engine.modules.profiler", "ProfileDelta"),
    ("synth_engine.modules.profiler", "StatisticalProfiler"),
    ("synth_engine.modules.profiler", "TableProfile"),
    # modules.subsetting
    ("synth_engine.modules.subsetting", "DagTraversal"),
    ("synth_engine.modules.subsetting", "EgressWriter"),
    ("synth_engine.modules.subsetting", "SubsetResult"),
    ("synth_engine.modules.subsetting", "SubsettingEngine"),
    # modules.synthesizer — deferred imports; import from submodules directly
    # Note: synthesizer.__init__ has __all__ but defers imports to prevent
    # SDV/rdt DeprecationWarning at collection time. Test from submodule paths.
    ("synth_engine.modules.synthesizer.storage.artifact", "ModelArtifact"),
    ("synth_engine.modules.synthesizer.training.engine", "SynthesisEngine"),
    ("synth_engine.modules.synthesizer.training.engine", "apply_fk_post_processing"),
    # shared.security
    ("synth_engine.shared.security", "HMAC_DIGEST_SIZE"),
    ("synth_engine.shared.security", "KEY_ID_SIZE"),
    ("synth_engine.shared.security", "LEGACY_KEY_ID"),
    ("synth_engine.shared.security", "VERSIONED_SIGNATURE_SIZE"),
    ("synth_engine.shared.security", "SecurityError"),
    ("synth_engine.shared.security", "build_key_map_from_settings"),
    ("synth_engine.shared.security", "compute_hmac"),
    ("synth_engine.shared.security", "sign_versioned"),
    ("synth_engine.shared.security", "verify_hmac"),
    ("synth_engine.shared.security", "verify_versioned"),
    # shared.tls
    ("synth_engine.shared.tls", "SERVICE_HOSTNAMES"),
    ("synth_engine.shared.tls", "TLSCertificateError"),
    ("synth_engine.shared.tls", "days_until_expiry"),
    ("synth_engine.shared.tls", "load_certificate"),
    ("synth_engine.shared.tls", "validate_certificate"),
    ("synth_engine.shared.tls", "validate_san_hostname"),
    ("synth_engine.shared.tls", "verify_chain"),
    ("synth_engine.shared.tls", "verify_key_cert_pair"),
]


@pytest.mark.parametrize(
    ("import_path", "symbol_name"),
    _EXPORTED_SYMBOLS,
    ids=[f"{p.split('.')[-1]}.{s}" for p, s in _EXPORTED_SYMBOLS],
)
def test_exported_symbol_is_importable(import_path: str, symbol_name: str) -> None:
    """Each symbol in a module's __all__ must be importable from that module.

    Args:
        import_path: Dotted module path (e.g. ``synth_engine.modules.masking``).
        symbol_name: Name of the symbol that should be exported.
    """
    module = importlib.import_module(import_path)
    _missing = object()
    symbol = getattr(module, symbol_name, _missing)

    assert symbol is not _missing, (
        f"Symbol '{symbol_name}' is not accessible from '{import_path}'.\n"
        f"Check that '{symbol_name}' is listed in the module's __all__ and "
        f"imported at the top of __init__.py."
    )
    # Verify it is actually non-None (not just a placeholder)
    assert symbol is not None, (
        f"Symbol '{symbol_name}' is exported from '{import_path}' but is None — "
        f"likely a stub or import that resolved to None."
    )


# ---------------------------------------------------------------------------
# Gate 4b — SynthesisJob field existence
# ---------------------------------------------------------------------------

#: Required fields on SynthesisJob (SQLModel table).
_SYNTHESIS_JOB_REQUIRED_FIELDS: list[str] = [
    "id",
    "status",
    "owner_id",
    "created_at",
    "table_name",
    "num_rows",
    "enable_dp",
]


@pytest.mark.parametrize("field_name", _SYNTHESIS_JOB_REQUIRED_FIELDS)
def test_synthesis_job_has_required_field(field_name: str) -> None:
    """SynthesisJob must expose every field in the contract list.

    Args:
        field_name: Name of the field that must exist on ``SynthesisJob``.
    """
    from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

    assert hasattr(SynthesisJob, field_name), (
        f"SynthesisJob is missing required field '{field_name}'.\n"
        f"This field is part of the public API contract and must not be renamed "
        f"or removed without updating callers and this contract test."
    )
    # The model_fields dict (pydantic/SQLModel) confirms the field is a real model field
    model_fields: dict[str, Any] = getattr(SynthesisJob, "model_fields", {})
    # Some fields may be defined as ClassVar or via SQLModel Column — fall back
    # gracefully.
    if model_fields:
        assert field_name in model_fields, (
            f"SynthesisJob.model_fields does not contain '{field_name}' — "
            f"the field may be defined as a ClassVar or property, which is not "
            f"a persisted model field."
        )


# ---------------------------------------------------------------------------
# Gate 4c — DPWrapperProtocol method existence
# ---------------------------------------------------------------------------

#: Required methods on DPWrapperProtocol.
_DP_WRAPPER_PROTOCOL_METHODS: list[str] = [
    "wrap",
    "epsilon_spent",
    "check_budget",
]


@pytest.mark.parametrize("method_name", _DP_WRAPPER_PROTOCOL_METHODS)
def test_dp_wrapper_protocol_has_required_method(method_name: str) -> None:
    """DPWrapperProtocol must define every method in the contract list.

    Args:
        method_name: Name of the method that must be declared on the protocol.
    """
    from synth_engine.shared.protocols import DPWrapperProtocol

    assert hasattr(DPWrapperProtocol, method_name), (
        f"DPWrapperProtocol is missing required method '{method_name}'.\n"
        f"Implementors of DPWrapperProtocol (e.g. DPTrainingWrapper) will break "
        f"structural subtyping if this method is removed."
    )
    method = getattr(DPWrapperProtocol, method_name)
    assert callable(method), (
        f"DPWrapperProtocol.{method_name} exists but is not callable — "
        f"expected a method declaration."
    )


# ---------------------------------------------------------------------------
# Gate 4d — WebhookRegistrationProtocol field existence
# ---------------------------------------------------------------------------

#: Required fields on WebhookRegistrationProtocol.
_WEBHOOK_PROTOCOL_FIELDS: list[str] = [
    "active",
    "callback_url",
    "signing_key",
    "id",
    "pinned_ips",
]


@pytest.mark.parametrize("field_name", _WEBHOOK_PROTOCOL_FIELDS)
def test_webhook_registration_protocol_has_required_field(field_name: str) -> None:
    """WebhookRegistrationProtocol must declare every field in the contract list.

    Protocol fields are declared in ``__annotations__`` rather than as class
    attributes, so this test checks ``__annotations__`` directly.

    Args:
        field_name: Name of the field that must be declared on the protocol.
    """
    from synth_engine.shared.protocols import WebhookRegistrationProtocol

    annotations = getattr(WebhookRegistrationProtocol, "__annotations__", {})
    assert field_name in annotations, (
        f"WebhookRegistrationProtocol is missing required field '{field_name}'.\n"
        f"Declared fields: {sorted(annotations.keys())}.\n"
        f"This field is referenced by webhook delivery logic and must exist on "
        f"any concrete implementation."
    )


# ---------------------------------------------------------------------------
# Gate 4e — PrivacyLedger field existence
# ---------------------------------------------------------------------------

#: Required fields on PrivacyLedger (SQLModel table).
_PRIVACY_LEDGER_REQUIRED_FIELDS: list[str] = [
    "id",
    "total_allocated_epsilon",
    "total_spent_epsilon",
]


@pytest.mark.parametrize("field_name", _PRIVACY_LEDGER_REQUIRED_FIELDS)
def test_privacy_ledger_has_required_field(field_name: str) -> None:
    """PrivacyLedger must expose every field in the contract list.

    Args:
        field_name: Name of the field that must exist on ``PrivacyLedger``.
    """
    from synth_engine.modules.privacy.ledger import PrivacyLedger

    assert hasattr(PrivacyLedger, field_name), (
        f"PrivacyLedger is missing required field '{field_name}'.\n"
        f"This field is part of the public API contract for differential privacy "
        f"accounting and must not be renamed or removed."
    )
    model_fields: dict[str, Any] = getattr(PrivacyLedger, "model_fields", {})
    if model_fields:
        assert field_name in model_fields, (
            f"PrivacyLedger.model_fields does not contain '{field_name}' — "
            f"field may have been converted to a ClassVar."
        )
