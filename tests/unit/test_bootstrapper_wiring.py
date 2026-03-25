"""Unit tests for the explicit bootstrapper wiring module (T56.2).

Tests verify:
- wire_all() is idempotent (calling twice does not double-register in a harmful way).
- Importing main without calling create_app() fires wiring (Huey worker contract).
- Each wire_* function registers its respective IoC callback.
- _build_webhook_delivery_fn is defined in wiring.py with the correct return type.

Task: T56.2 — Extract Bootstrapper Wiring Module
CONSTITUTION Priority 3: TDD
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).parent.parent.parent
WIRING_PY = REPO_ROOT / "src" / "synth_engine" / "bootstrapper" / "wiring.py"


class TestWiringModuleStructure:
    """wiring.py must expose the expected public API."""

    def test_wiring_module_is_importable(self) -> None:
        """synth_engine.bootstrapper.wiring must be importable without error."""
        import synth_engine.bootstrapper.wiring as wiring  # noqa: F401 — importability check

    def test_wire_all_is_callable(self) -> None:
        """wire_all must be a callable exported from wiring."""
        from synth_engine.bootstrapper.wiring import wire_all

        assert callable(wire_all)

    def test_wire_dp_wrapper_factory_is_callable(self) -> None:
        """wire_dp_wrapper_factory must be a callable exported from wiring."""
        from synth_engine.bootstrapper.wiring import wire_dp_wrapper_factory

        assert callable(wire_dp_wrapper_factory)

    def test_wire_spend_budget_fn_is_callable(self) -> None:
        """wire_spend_budget_fn must be a callable exported from wiring."""
        from synth_engine.bootstrapper.wiring import wire_spend_budget_fn

        assert callable(wire_spend_budget_fn)

    def test_wire_webhook_delivery_fn_is_callable(self) -> None:
        """wire_webhook_delivery_fn must be a callable exported from wiring."""
        from synth_engine.bootstrapper.wiring import wire_webhook_delivery_fn

        assert callable(wire_webhook_delivery_fn)

    def test_build_webhook_delivery_fn_is_defined_in_wiring(self) -> None:
        """_build_webhook_delivery_fn must be defined in wiring.py, not main.py."""
        source = WIRING_PY.read_text()
        tree = ast.parse(source)
        found = any(
            isinstance(node, ast.FunctionDef) and node.name == "_build_webhook_delivery_fn"
            for node in ast.walk(tree)
        )
        assert found, (
            "_build_webhook_delivery_fn must be defined in bootstrapper/wiring.py (T56.2). "
            "It was moved there from main.py to eliminate the side-effect import block."
        )

    def test_build_webhook_delivery_fn_not_returns_any(self) -> None:
        """_build_webhook_delivery_fn in wiring.py must not have ``-> Any`` return type.

        The return type must be ``Callable[[int, str], None]`` or equivalent —
        not ``Any`` which defeats mypy's ability to verify call sites.
        """
        source = WIRING_PY.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name != "_build_webhook_delivery_fn":
                continue
            ret = node.returns
            if ret is None:
                continue
            if isinstance(ret, ast.Name) and ret.id == "Any":
                pytest.fail(
                    "_build_webhook_delivery_fn still has `-> Any` return type. "
                    "Replace with `Callable[[int, str], None]` (T56.2)."
                )


class TestWireAllIdempotency:
    """wire_all() must be safe to call multiple times."""

    def test_wire_all_twice_does_not_raise(self) -> None:
        """Calling wire_all() twice must not raise any exception.

        The underlying set_* functions overwrite the global unconditionally,
        so repeated calls are safe (idempotent in effect).
        """
        from synth_engine.bootstrapper.wiring import wire_all

        # Should not raise
        wire_all()
        wire_all()

    def test_wire_dp_wrapper_factory_twice_does_not_raise(self) -> None:
        """Calling wire_dp_wrapper_factory() twice must not raise."""
        from synth_engine.bootstrapper.wiring import wire_dp_wrapper_factory

        wire_dp_wrapper_factory()
        wire_dp_wrapper_factory()

    def test_wire_spend_budget_fn_twice_does_not_raise(self) -> None:
        """Calling wire_spend_budget_fn() twice must not raise."""
        from synth_engine.bootstrapper.wiring import wire_spend_budget_fn

        wire_spend_budget_fn()
        wire_spend_budget_fn()

    def test_wire_dp_wrapper_factory_registers_build_dp_wrapper(self) -> None:
        """wire_dp_wrapper_factory must call set_dp_wrapper_factory with build_dp_wrapper.

        After calling wire_dp_wrapper_factory(), the orchestration module's
        _dp_wrapper_factory must be non-None and must be the build_dp_wrapper
        function from factories.py.
        """
        from synth_engine.bootstrapper.factories import build_dp_wrapper
        from synth_engine.bootstrapper.wiring import wire_dp_wrapper_factory
        from synth_engine.modules.synthesizer import job_orchestration as orch

        wire_dp_wrapper_factory()

        assert orch._dp_wrapper_factory is build_dp_wrapper, (
            "wire_dp_wrapper_factory() must register build_dp_wrapper as the factory. "
            f"Got: {orch._dp_wrapper_factory!r}"
        )

    def test_wire_spend_budget_fn_registers_non_none_callable(self) -> None:
        """wire_spend_budget_fn must register a callable, not None.

        The exact callable object changes each call (closure), so we verify
        it is a non-None callable rather than checking object identity.
        """
        from synth_engine.bootstrapper.wiring import wire_spend_budget_fn
        from synth_engine.modules.synthesizer import job_orchestration as orch

        wire_spend_budget_fn()

        assert orch._spend_budget_fn is not None, (
            "wire_spend_budget_fn() must register a non-None spend_budget callable."
        )
        assert callable(orch._spend_budget_fn), "The registered _spend_budget_fn must be callable."

    def test_wire_webhook_delivery_fn_registers_non_none_callable(self) -> None:
        """wire_webhook_delivery_fn must register a callable, not None.

        We verify it is a non-None callable since the exact closure object
        changes per call.
        """
        from synth_engine.bootstrapper.wiring import wire_webhook_delivery_fn
        from synth_engine.modules.synthesizer import job_orchestration as orch

        # Reset first to ensure wire_webhook_delivery_fn does the registration
        orch._reset_webhook_delivery_fn()
        assert orch._webhook_delivery_fn is None, (
            "Pre-condition: callback should be None after reset."
        )

        wire_webhook_delivery_fn()

        assert orch._webhook_delivery_fn is not None, (
            "wire_webhook_delivery_fn() must register a non-None delivery callback."
        )
        assert callable(orch._webhook_delivery_fn), (
            "The registered _webhook_delivery_fn must be callable."
        )


class TestHueyWorkerContract:
    """Importing main without calling create_app() must fire wiring.

    This is the Huey worker contract: Huey workers import main for task
    discovery, never call create_app(), and still need the IoC callbacks live.
    """

    def test_importing_main_wires_dp_wrapper_factory(self) -> None:
        """Importing main must result in _dp_wrapper_factory being set.

        This verifies that wire_all() fires at module scope, not inside
        create_app(), so Huey workers get the factory without calling create_app().
        """
        from synth_engine.bootstrapper import main  # noqa: F401 — side-effect import
        from synth_engine.bootstrapper.factories import build_dp_wrapper
        from synth_engine.modules.synthesizer import job_orchestration as orch

        assert orch._dp_wrapper_factory is build_dp_wrapper, (
            f"_dp_wrapper_factory must be wired at import time Got: {orch._dp_wrapper_factory!r}"
        )

    def test_importing_main_wires_spend_budget_fn(self) -> None:
        """Importing main must result in _spend_budget_fn being set.

        Verifies Rule 8 compliance for the spend_budget IoC hook.
        """
        from synth_engine.bootstrapper import main  # noqa: F401 — side-effect import
        from synth_engine.modules.synthesizer import job_orchestration as orch

        assert orch._spend_budget_fn is not None, (
            "_spend_budget_fn must be wired by bootstrapper at import time (Rule 8, Huey contract)."
        )
        assert callable(orch._spend_budget_fn), "_spend_budget_fn must be a callable, not None."

    def test_importing_main_wires_webhook_delivery_fn(self) -> None:
        """Importing main must result in _webhook_delivery_fn being set.

        Verifies Rule 8 compliance for the webhook delivery IoC hook (T45.3).
        """
        from synth_engine.bootstrapper import main  # noqa: F401 — side-effect import
        from synth_engine.modules.synthesizer import job_orchestration as orch

        assert orch._webhook_delivery_fn is not None, (
            "_webhook_delivery_fn must be wired by bootstrapper at import time (Rule 8 / T45.3)."
        )
        assert callable(orch._webhook_delivery_fn), (
            "_webhook_delivery_fn must be a callable, not None."
        )

    def test_main_imports_wire_all_from_wiring(self) -> None:
        """main.py must import wire_all from bootstrapper.wiring, not define inline wiring.

        Verifies the T56.2 refactor is structurally complete: the old module-scope
        side-effect imports and manual set_* calls must be replaced by wire_all().
        """
        main_source = (REPO_ROOT / "src" / "synth_engine" / "bootstrapper" / "main.py").read_text()
        assert "from synth_engine.bootstrapper.wiring import" in main_source, (
            "main.py must import from bootstrapper.wiring (T56.2). "
            "The wiring logic must be delegated to wiring.py."
        )
        assert "wire_all()" in main_source, (
            "main.py must call wire_all() at module scope to fire Huey worker wiring (T56.2)."
        )

    def test_main_no_longer_has_inline_side_effect_imports(self) -> None:
        """main.py must not contain the old inline side-effect import block.

        The old block imported reaper_tasks, retention_tasks, tasks, and
        security.rotation at module scope with # noqa: E402 guards.  These
        must now live in wiring.py.
        """
        main_source = (REPO_ROOT / "src" / "synth_engine" / "bootstrapper" / "main.py").read_text()
        # The old side-effect import pattern used
        assert "noqa: E402" not in main_source, (
            "main.py must not contain # noqa: E402 imports (old inline wiring block). "
            "All side-effect imports must be in bootstrapper/wiring.py (T56.2)."
        )
