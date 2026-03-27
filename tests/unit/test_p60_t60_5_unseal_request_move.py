"""Tests for T60.5 — UnsealRequest moved to schemas/vault.py.

Verifies that:
- UnsealRequest is importable from the canonical new location (schemas/vault.py)
- The re-export chain through lifecycle.py and main.py still works
- The model validates a passphrase string correctly
- The model rejects missing passphrase (negative test)

CONSTITUTION Priority 3: TDD
Task: T60.5 — Move UnsealRequest to schemas/vault.py
"""

from __future__ import annotations

import pytest


class TestUnsealRequestCanonicalLocation:
    """UnsealRequest must live in schemas/vault.py (canonical source)."""

    def test_unseal_request_importable_from_schemas_vault(self) -> None:
        """UnsealRequest must be importable from schemas/vault.py."""
        from synth_engine.bootstrapper.schemas.vault import UnsealRequest

        assert UnsealRequest is not None

    def test_unseal_request_is_pydantic_model(self) -> None:
        """UnsealRequest must be a Pydantic BaseModel subclass."""
        from pydantic import BaseModel

        from synth_engine.bootstrapper.schemas.vault import UnsealRequest

        assert issubclass(UnsealRequest, BaseModel)

    def test_unseal_request_has_passphrase_field(self) -> None:
        """UnsealRequest must have a passphrase field of type str."""
        from synth_engine.bootstrapper.schemas.vault import UnsealRequest

        model_fields = UnsealRequest.model_fields
        assert "passphrase" in model_fields

    def test_unseal_request_accepts_valid_passphrase(self) -> None:
        """UnsealRequest must instantiate with a non-empty passphrase."""
        from synth_engine.bootstrapper.schemas.vault import UnsealRequest

        req = UnsealRequest(passphrase="my-secret-passphrase")
        assert req.passphrase == "my-secret-passphrase"

    def test_unseal_request_rejects_missing_passphrase(self) -> None:
        """UnsealRequest must raise ValidationError when passphrase is absent."""
        from pydantic import ValidationError

        from synth_engine.bootstrapper.schemas.vault import UnsealRequest

        with pytest.raises(ValidationError) as exc_info:
            UnsealRequest()  # type: ignore[call-arg]

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("passphrase",) for e in errors)


class TestUnsealRequestReExports:
    """Re-export chain through lifecycle.py and main.py must be preserved."""

    def test_unseal_request_re_exported_from_lifecycle(self) -> None:
        """lifecycle.py must still export UnsealRequest for backward compatibility."""
        from synth_engine.bootstrapper.lifecycle import UnsealRequest

        assert UnsealRequest is not None

    def test_unseal_request_re_exported_from_main(self) -> None:
        """main.py must still export UnsealRequest for backward compatibility."""
        from synth_engine.bootstrapper.main import UnsealRequest

        assert UnsealRequest is not None

    def test_all_three_are_same_class(self) -> None:
        """schemas/vault, lifecycle, and main must all point to the same class."""
        from synth_engine.bootstrapper.lifecycle import UnsealRequest as UnsealFromLifecycle
        from synth_engine.bootstrapper.main import UnsealRequest as UnsealFromMain
        from synth_engine.bootstrapper.schemas.vault import UnsealRequest as UnsealFromSchemas

        assert UnsealFromSchemas is UnsealFromLifecycle
        assert UnsealFromLifecycle is UnsealFromMain
