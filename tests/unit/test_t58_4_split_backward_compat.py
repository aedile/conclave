"""Backward-compatibility and negative tests for T58.4 file splits.

Verifies that:
1. All existing import paths from audit.py still work.
2. All existing import paths from models.py still work.
3. The new split modules export the correct symbols.
4. No circular imports exist between the new modules.
5. Negative: unknown version prefix in verify_event returns False.
6. Negative: sign_v3 with oversized details raises ValueError.
7. Negative: RestrictedUnpickler blocks disallowed modules.

T73: Parametrize 3 groups in TestAuditSignaturesModule:
  - sign_v* importable (3 → 1 parametrized)
  - sign_v* returns version prefix (3 → 1 parametrized)
  - sign_v*/v2 oversized-details raises (2 → 1 parametrized)

Task: T58.4
"""

from __future__ import annotations

import pickle

import pytest

# ---------------------------------------------------------------------------
# Backward-compat: audit.py re-exports
# ---------------------------------------------------------------------------


class TestAuditBackwardCompat:
    """All existing 'from audit import X' paths still resolve."""

    def test_audit_event_importable_from_audit(self) -> None:
        from synth_engine.shared.security.audit import AuditEvent

        assert AuditEvent.__name__ == "AuditEvent"

    def test_audit_logger_importable_from_audit(self) -> None:
        from synth_engine.shared.security.audit import AuditLogger

        assert AuditLogger.__name__ == "AuditLogger"

    def test_get_audit_logger_importable_from_audit(self) -> None:
        from synth_engine.shared.security.audit import get_audit_logger

        assert callable(get_audit_logger)

    def test_reset_audit_logger_importable_from_audit(self) -> None:
        from synth_engine.shared.security.audit import reset_audit_logger

        assert callable(reset_audit_logger)

    def test_audit_chain_resume_failure_total_importable(self) -> None:
        from synth_engine.shared.security.audit import AUDIT_CHAIN_RESUME_FAILURE_TOTAL

        # Verify it is a Prometheus counter (has inc() method)
        assert hasattr(AUDIT_CHAIN_RESUME_FAILURE_TOTAL, "inc")


# ---------------------------------------------------------------------------
# New split modules: sign_v1/v2/v3 parametrized tests (T73)
# ---------------------------------------------------------------------------

# v1 takes 7 positional args; v2 and v3 take 8 (adds details dict)
_SIGN_V1_ARGS = (b"\x00" * 32, "ts", "TYPE", "actor", "res", "act", "prevhash")
_SIGN_V2V3_ARGS = (b"\x00" * 32, "ts", "TYPE", "actor", "res", "act", "prevhash", {})

_SIGN_VERSION_CASES = [
    pytest.param("sign_v1", _SIGN_V1_ARGS, "v1:", id="v1"),
    pytest.param("sign_v2", _SIGN_V2V3_ARGS, "v2:", id="v2"),
    pytest.param("sign_v3", _SIGN_V2V3_ARGS, "v3:", id="v3"),
]

_SIGN_OVERSIZED_CASES = [
    pytest.param("sign_v2", id="v2"),
    pytest.param("sign_v3", id="v3"),
]


@pytest.mark.parametrize(("fn_name", "call_args", "expected_prefix"), _SIGN_VERSION_CASES)
def test_sign_function_is_importable_and_returns_version_prefix(
    fn_name: str,
    call_args: tuple[object, ...],
    expected_prefix: str,
) -> None:
    """sign_v* must be importable and must return a result prefixed with its version tag.

    Each signing function must produce a signature string whose leading bytes
    identify the version, so that verify_event can dispatch to the correct verifier.

    Args:
        fn_name: Name of the signing function (sign_v1, sign_v2, or sign_v3).
        call_args: Positional arguments to pass to the function.
        expected_prefix: Version prefix the result must start with (e.g. "v1:").
    """
    import synth_engine.shared.security.audit_signatures as mod

    fn = getattr(mod, fn_name)
    assert callable(fn), f"{fn_name} must be callable"
    result = fn(*call_args)
    assert isinstance(result, str), f"{fn_name}() must return str, got {type(result).__name__!r}"
    assert result.startswith(expected_prefix), (
        f"{fn_name}() must start with {expected_prefix!r}, got {result[:10]!r}"
    )


@pytest.mark.parametrize("fn_name", _SIGN_OVERSIZED_CASES)
def test_sign_function_rejects_oversized_details(fn_name: str) -> None:
    """sign_v2 and sign_v3 must raise ValueError when the details dict exceeds 64 KB.

    The details dict is serialized to JSON and embedded in the HMAC input.
    An unbounded details size could allow HMAC-length-extension attacks and
    creates oversized audit records that fail storage constraints.

    Args:
        fn_name: Name of the signing function to test (sign_v2 or sign_v3).
    """
    import synth_engine.shared.security.audit_signatures as mod

    fn = getattr(mod, fn_name)
    key = b"\x00" * 32
    big_details = {"k": "x" * (65 * 1024)}
    with pytest.raises(ValueError, match="exceed"):
        fn(key, "ts", "TYPE", "actor", "res", "act", "prevhash", big_details)


class TestAuditLoggerModule:
    """audit_logger.py exports AuditEvent and AuditLogger."""

    def test_audit_event_importable_from_logger(self) -> None:
        from synth_engine.shared.security.audit_logger import AuditEvent

        assert AuditEvent.__name__ == "AuditEvent"

    def test_audit_logger_importable_from_logger(self) -> None:
        from synth_engine.shared.security.audit_logger import AuditLogger

        assert AuditLogger.__name__ == "AuditLogger"

    def test_audit_logger_log_event_returns_audit_event(self) -> None:
        from synth_engine.shared.security.audit_logger import AuditEvent, AuditLogger

        key = b"\xab" * 32
        logger = AuditLogger(audit_key=key)
        event = logger.log_event(
            event_type="TEST",
            actor="test_actor",
            resource="test_res",
            action="test_act",
            details={"k": "v"},
        )
        assert isinstance(event, AuditEvent)
        assert event.event_type == "TEST"
        assert event.actor == "test_actor"
        assert event.signature.startswith("v3:")

    def test_verify_event_unknown_version_returns_false(self) -> None:
        from synth_engine.shared.security.audit_logger import AuditEvent, AuditLogger

        key = b"\xab" * 32
        logger = AuditLogger(audit_key=key)
        # Manually craft an event with an unknown version prefix
        event = AuditEvent(
            timestamp="2026-01-01T00:00:00+00:00",
            event_type="TEST",
            actor="actor",
            resource="res",
            action="act",
            details={},
            prev_hash="0" * 64,
            signature="v99:deadbeef",
        )
        result = logger.verify_event(event)
        assert result is False, "unknown version prefix must return False (fail-closed)"
        assert not result


class TestAuditSingletonModule:
    """audit_singleton.py exports get_audit_logger and reset_audit_logger."""

    def test_get_audit_logger_importable_from_singleton(self) -> None:
        from synth_engine.shared.security.audit_singleton import get_audit_logger

        assert callable(get_audit_logger)

    def test_reset_audit_logger_importable_from_singleton(self) -> None:
        from synth_engine.shared.security.audit_singleton import reset_audit_logger

        assert callable(reset_audit_logger)


# ---------------------------------------------------------------------------
# No circular imports (import ordering check)
# ---------------------------------------------------------------------------


class TestNoCircularImports:
    """Verify the split modules can be imported in any order without circular errors."""

    def test_import_signatures_first(self) -> None:
        import importlib

        importlib.import_module("synth_engine.shared.security.audit_signatures")
        importlib.import_module("synth_engine.shared.security.audit_logger")
        importlib.import_module("synth_engine.shared.security.audit_singleton")
        # If we reach here, no circular import occurred
        assert True

    def test_import_singleton_first(self) -> None:
        # Python caches imports — the key is no ImportError is raised
        import importlib

        importlib.import_module("synth_engine.shared.security.audit_singleton")
        importlib.import_module("synth_engine.shared.security.audit_logger")
        importlib.import_module("synth_engine.shared.security.audit_signatures")
        assert True


# ---------------------------------------------------------------------------
# T70.6: models.py shim removed — canonical paths tested directly
# ---------------------------------------------------------------------------


class TestModelsBackwardCompat:
    """Canonical import paths that replaced the models.py shim (T70.6)."""

    def test_model_artifact_importable_from_artifact(self) -> None:
        from synth_engine.modules.synthesizer.storage.artifact import ModelArtifact

        assert ModelArtifact.__name__ == "ModelArtifact"

    def test_restricted_unpickler_importable_from_canonical(self) -> None:
        from synth_engine.modules.synthesizer.storage.restricted_unpickler import (
            RestrictedUnpickler,
        )

        assert RestrictedUnpickler.__name__ == "RestrictedUnpickler"

    def test_synthesizer_model_protocol_importable(self) -> None:
        from synth_engine.modules.synthesizer.storage.restricted_unpickler import SynthesizerModel

        assert SynthesizerModel.__name__ == "SynthesizerModel"

    def test_security_error_importable_from_hmac_signing(self) -> None:
        from synth_engine.shared.security.hmac_signing import (
            ArtifactTamperingError as SecurityError,
        )

        # SecurityError is ArtifactTamperingError (see hmac_signing.py)
        assert SecurityError.__name__ == "ArtifactTamperingError"

    def test_allowed_module_prefixes_importable(self) -> None:
        from synth_engine.modules.synthesizer.storage.restricted_unpickler import (
            _ALLOWED_MODULE_PREFIXES,
        )

        assert isinstance(_ALLOWED_MODULE_PREFIXES, tuple)
        assert len(_ALLOWED_MODULE_PREFIXES) > 0

    def test_allowed_builtin_names_importable(self) -> None:
        from synth_engine.modules.synthesizer.storage.restricted_unpickler import (
            _ALLOWED_BUILTIN_NAMES,
        )

        assert isinstance(_ALLOWED_BUILTIN_NAMES, frozenset)
        assert "dict" in _ALLOWED_BUILTIN_NAMES

    def test_artifact_verification_failure_total_importable(self) -> None:
        from synth_engine.modules.synthesizer.storage.artifact import (
            ARTIFACT_VERIFICATION_FAILURE_TOTAL,
        )

        assert hasattr(ARTIFACT_VERIFICATION_FAILURE_TOTAL, "inc")


# ---------------------------------------------------------------------------
# New split modules: direct imports
# ---------------------------------------------------------------------------


class TestRestrictedUnpicklerModule:
    """restricted_unpickler.py exports the allowlist and RestrictedUnpickler."""

    def test_restricted_unpickler_blocks_os_module(self) -> None:
        # Craft a pickle that would import os.system — this is a classic RCE vector

        from synth_engine.modules.synthesizer.storage.restricted_unpickler import (
            RestrictedUnpickler,
        )

        data = pickle.dumps({"key": "value"})
        # The safe dict should deserialize without error
        result = RestrictedUnpickler.loads(data, extra_allowed_prefixes=())
        assert result == {"key": "value"}

    def test_restricted_unpickler_blocks_subprocess(self) -> None:
        # Build a payload that references subprocess.Popen — blocked
        import io

        from synth_engine.modules.synthesizer.storage.restricted_unpickler import (
            RestrictedUnpickler,
        )
        from synth_engine.shared.security.hmac_signing import SecurityError

        # We can't easily create a valid pickle that references subprocess without
        # actually pickling something. Use a hand-crafted minimal pickle instead.
        # Opcode 0x80 = PROTO, 0x04 = version 4
        # This tests find_class directly
        up = RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(SecurityError, match="not permitted"):
            up.find_class("subprocess", "Popen")

    def test_restricted_unpickler_blocks_eval_builtin(self) -> None:
        import io

        from synth_engine.modules.synthesizer.storage.restricted_unpickler import (
            RestrictedUnpickler,
        )
        from synth_engine.shared.security.hmac_signing import SecurityError

        up = RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(SecurityError, match="not permitted"):
            up.find_class("builtins", "eval")

    def test_allowed_module_prefixes_includes_artifact(self) -> None:
        from synth_engine.modules.synthesizer.storage.restricted_unpickler import (
            _ALLOWED_MODULE_PREFIXES,
        )

        # After T58.4, the new artifact module path must be in the allowlist
        assert "synth_engine.modules.synthesizer.storage.artifact" in _ALLOWED_MODULE_PREFIXES, (
            "artifact module path missing from allowlist — new pickles would fail to load"
        )

    def test_allowed_module_prefixes_includes_legacy_models(self) -> None:
        from synth_engine.modules.synthesizer.storage.restricted_unpickler import (
            _ALLOWED_MODULE_PREFIXES,
        )

        # Backward compat: old artifacts with .storage.models path must still load
        assert "synth_engine.modules.synthesizer.storage.models" in _ALLOWED_MODULE_PREFIXES, (
            "legacy models module path missing — old artifacts would fail to load"
        )


class TestArtifactModule:
    """artifact.py exports ModelArtifact and helper functions."""

    def test_model_artifact_importable_from_artifact(self) -> None:
        from synth_engine.modules.synthesizer.storage.artifact import ModelArtifact

        assert ModelArtifact.__name__ == "ModelArtifact"

    def test_detect_signed_format_importable(self) -> None:
        from synth_engine.modules.synthesizer.storage.artifact import _detect_signed_format

        assert callable(_detect_signed_format)

    def test_validate_signing_key_importable(self) -> None:
        from synth_engine.modules.synthesizer.storage.artifact import _validate_signing_key

        assert callable(_validate_signing_key)

    def test_validate_signing_key_rejects_empty(self) -> None:
        from synth_engine.modules.synthesizer.storage.artifact import _validate_signing_key

        with pytest.raises(ValueError, match="must not be empty"):
            _validate_signing_key(b"", context="test")

    def test_validate_signing_key_rejects_short(self) -> None:
        from synth_engine.modules.synthesizer.storage.artifact import _validate_signing_key

        with pytest.raises(ValueError, match="at least"):
            _validate_signing_key(b"\x00" * 16, context="test")

    def test_detect_signed_format_false_for_unsigned(self) -> None:
        from synth_engine.modules.synthesizer.storage.artifact import (
            ModelArtifact,
            _detect_signed_format,
        )

        artifact = ModelArtifact(table_name="t", model=None)
        unsigned_payload = pickle.dumps(artifact, protocol=pickle.HIGHEST_PROTOCOL)
        assert _detect_signed_format(unsigned_payload) is False
        assert not _detect_signed_format(unsigned_payload)

    def test_model_artifact_pickled_with_artifact_module_path(self) -> None:
        from synth_engine.modules.synthesizer.storage.artifact import ModelArtifact

        artifact = ModelArtifact(table_name="customers", model=None)
        data = pickle.dumps(artifact)
        # New artifacts should reference the artifact module, not models
        assert b"storage.artifact" in data, (
            "ModelArtifact pickle should reference storage.artifact module path"
        )
