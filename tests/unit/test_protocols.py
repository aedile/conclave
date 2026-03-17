"""Unit tests for DPWrapperProtocol compliance and shared/protocols.py contract.

Verifies:
- DPWrapperProtocol exists in shared/protocols.py with all three required
  method signatures: wrap(), epsilon_spent(), check_budget().
- DPTrainingWrapper from modules/privacy/dp_engine satisfies DPWrapperProtocol
  at runtime (via isinstance with @runtime_checkable).
- Protocol can be imported without violating import-linter boundaries.
- _build_metadata() in synthesizer/engine.py is annotated with
  SingleTableMetadata return type (not Any).

Task: P26-T26.3 — Protocol Typing + DP-SGD Hardening
"""

from __future__ import annotations

import inspect
import typing


class TestDPWrapperProtocolStructure:
    """Verify DPWrapperProtocol has all required method signatures."""

    def test_protocol_exists_in_shared(self) -> None:
        """DPWrapperProtocol must be importable from synth_engine.shared.protocols."""
        from synth_engine.shared.protocols import DPWrapperProtocol  # noqa: F401

    def test_protocol_has_wrap_method(self) -> None:
        """DPWrapperProtocol must define a wrap() method."""
        from synth_engine.shared.protocols import DPWrapperProtocol

        assert hasattr(DPWrapperProtocol, "wrap"), (
            "DPWrapperProtocol must define wrap() method"
        )

    def test_protocol_has_epsilon_spent_method(self) -> None:
        """DPWrapperProtocol must define epsilon_spent() method."""
        from synth_engine.shared.protocols import DPWrapperProtocol

        assert hasattr(DPWrapperProtocol, "epsilon_spent"), (
            "DPWrapperProtocol must define epsilon_spent() method"
        )

    def test_protocol_has_check_budget_method(self) -> None:
        """DPWrapperProtocol must define check_budget() method."""
        from synth_engine.shared.protocols import DPWrapperProtocol

        assert hasattr(DPWrapperProtocol, "check_budget"), (
            "DPWrapperProtocol must define check_budget() method"
        )

    def test_wrap_signature_matches_dptw(self) -> None:
        """DPWrapperProtocol.wrap() must accept the same parameters as DPTrainingWrapper.wrap().

        Verifies parameter names (per Known Failure Pattern #1: parameter name
        mismatches survive mocks).
        """
        from synth_engine.shared.protocols import DPWrapperProtocol

        sig = inspect.signature(DPWrapperProtocol.wrap)
        params = list(sig.parameters.keys())
        # Must have: self, optimizer, model, dataloader (positional) plus
        # max_grad_norm, noise_multiplier (keyword-only)
        assert "optimizer" in params, "wrap() must have 'optimizer' parameter"
        assert "model" in params, "wrap() must have 'model' parameter"
        assert "dataloader" in params, "wrap() must have 'dataloader' parameter"
        assert "max_grad_norm" in params, "wrap() must have 'max_grad_norm' parameter"
        assert "noise_multiplier" in params, "wrap() must have 'noise_multiplier' parameter"

    def test_epsilon_spent_signature_matches_dptw(self) -> None:
        """DPWrapperProtocol.epsilon_spent() must accept delta as keyword-only."""
        from synth_engine.shared.protocols import DPWrapperProtocol

        sig = inspect.signature(DPWrapperProtocol.epsilon_spent)
        params = sig.parameters
        assert "delta" in params, "epsilon_spent() must have 'delta' parameter"
        # Must be keyword-only
        assert params["delta"].kind == inspect.Parameter.KEYWORD_ONLY, (
            "epsilon_spent() 'delta' must be keyword-only"
        )

    def test_check_budget_signature_matches_dptw(self) -> None:
        """DPWrapperProtocol.check_budget() must accept allocated_epsilon and delta as keyword-only."""
        from synth_engine.shared.protocols import DPWrapperProtocol

        sig = inspect.signature(DPWrapperProtocol.check_budget)
        params = sig.parameters
        assert "allocated_epsilon" in params, (
            "check_budget() must have 'allocated_epsilon' parameter"
        )
        assert "delta" in params, "check_budget() must have 'delta' parameter"
        assert params["allocated_epsilon"].kind == inspect.Parameter.KEYWORD_ONLY, (
            "check_budget() 'allocated_epsilon' must be keyword-only"
        )
        assert params["delta"].kind == inspect.Parameter.KEYWORD_ONLY, (
            "check_budget() 'delta' must be keyword-only"
        )

    def test_protocol_is_runtime_checkable(self) -> None:
        """DPWrapperProtocol must be decorated with @runtime_checkable."""
        from synth_engine.shared.protocols import DPWrapperProtocol

        # @runtime_checkable allows isinstance() checks without TypeError.
        # A non-implementing object must return False (not raise TypeError).
        class _NotAWrapper:
            pass

        result = isinstance(_NotAWrapper(), DPWrapperProtocol)
        assert result is False, (
            "isinstance() against DPWrapperProtocol must work (requires @runtime_checkable)"
        )


class TestDPTrainingWrapperSatisfiesProtocol:
    """Verify DPTrainingWrapper structurally satisfies DPWrapperProtocol."""

    def test_dptw_passes_isinstance_check(self) -> None:
        """DPTrainingWrapper must satisfy DPWrapperProtocol at runtime.

        This is a structural type check: @runtime_checkable Protocol + isinstance()
        verifies method existence without needing to import DPTrainingWrapper into
        synthesizer-module code.
        """
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
        from synth_engine.shared.protocols import DPWrapperProtocol

        wrapper = DPTrainingWrapper()
        assert isinstance(wrapper, DPWrapperProtocol), (
            "DPTrainingWrapper must satisfy DPWrapperProtocol structurally"
        )

    def test_dptw_wrap_parameter_names_match_protocol(self) -> None:
        """DPTrainingWrapper.wrap() parameter names must match DPWrapperProtocol.wrap().

        Regression guard for Known Failure Pattern #1: parameter name mismatches
        survive mocks because mocks accept arbitrary kwargs.
        """
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
        from synth_engine.shared.protocols import DPWrapperProtocol

        proto_sig = inspect.signature(DPWrapperProtocol.wrap)
        impl_sig = inspect.signature(DPTrainingWrapper.wrap)

        proto_params = set(proto_sig.parameters.keys()) - {"self"}
        impl_params = set(impl_sig.parameters.keys()) - {"self"}

        assert proto_params == impl_params, (
            f"DPTrainingWrapper.wrap() parameter mismatch with protocol. "
            f"Protocol: {proto_params}, Implementation: {impl_params}"
        )


class TestSynthesisEngineDPWrapperType:
    """Verify SynthesisEngine.train() uses DPWrapperProtocol, not Any."""

    def test_train_dp_wrapper_annotation_is_not_bare_any(self) -> None:
        """SynthesisEngine.train() dp_wrapper parameter must not be typed as bare Any.

        Uses raw __annotations__ inspection to avoid NameError from TYPE_CHECKING
        forward references (ModelArtifact is behind TYPE_CHECKING in engine.py).
        """
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        # Access raw annotations without resolving forward refs.
        raw_annotations = SynthesisEngine.train.__annotations__
        dp_wrapper_annotation = raw_annotations.get("dp_wrapper")

        # Must be annotated (not absent)
        assert dp_wrapper_annotation is not None, (
            "SynthesisEngine.train() must have a 'dp_wrapper' annotation"
        )

        # Must not be bare typing.Any
        assert dp_wrapper_annotation is not typing.Any, (
            "dp_wrapper must not be typed as Any — use DPWrapperProtocol | None"
        )

    def test_train_dp_wrapper_annotation_references_protocol(self) -> None:
        """SynthesisEngine.train() dp_wrapper annotation must reference DPWrapperProtocol."""
        from synth_engine.modules.synthesizer.engine import SynthesisEngine

        raw_annotations = SynthesisEngine.train.__annotations__
        dp_wrapper_annotation = raw_annotations.get("dp_wrapper")

        # The annotation (possibly a string forward ref or a Union type) must
        # contain a reference to DPWrapperProtocol.
        annotation_repr = str(dp_wrapper_annotation)
        assert "DPWrapperProtocol" in annotation_repr, (
            f"dp_wrapper annotation '{annotation_repr}' must reference DPWrapperProtocol"
        )


class TestBuildMetadataReturnType:
    """Verify _build_metadata() has a typed return annotation, not Any."""

    def test_build_metadata_return_type_is_not_any(self) -> None:
        """_build_metadata() must not return Any — must return SingleTableMetadata."""
        from synth_engine.modules.synthesizer import engine as engine_mod

        raw_annotations = engine_mod._build_metadata.__annotations__
        return_annotation = raw_annotations.get("return")

        assert return_annotation is not None, "_build_metadata() must have a return type annotation"
        assert return_annotation is not typing.Any, (
            "_build_metadata() return type must not be Any"
        )

    def test_build_metadata_return_annotation_contains_single_table_metadata(self) -> None:
        """_build_metadata() return annotation must reference SingleTableMetadata."""
        from synth_engine.modules.synthesizer import engine as engine_mod

        raw_annotations = engine_mod._build_metadata.__annotations__
        return_annotation = raw_annotations.get("return")

        annotation_repr = str(return_annotation)
        assert "SingleTableMetadata" in annotation_repr, (
            f"_build_metadata() return annotation '{annotation_repr}' must reference "
            "SingleTableMetadata"
        )
