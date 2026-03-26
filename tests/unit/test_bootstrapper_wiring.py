"""Unit tests for the explicit bootstrapper wiring module (T56.2).

Tests verify:
- wire_all() is idempotent (calling twice does not double-register in a harmful way).
- Importing main without calling create_app() fires wiring (Huey worker contract).
- Each wire_* function registers its respective IoC callback.
- _build_webhook_delivery_fn is defined in wiring.py with the correct return type.
- _build_webhook_delivery_fn closure skips delivery when DATABASE_URL is empty (no-op path).
- _build_webhook_delivery_fn closure logs exception and does not propagate on DB error.

Task: T56.2 — Extract Bootstrapper Wiring Module
CONSTITUTION Priority 3: TDD
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        """wire_all must be a callable with the expected name."""
        from synth_engine.bootstrapper.wiring import wire_all

        assert callable(wire_all)
        assert wire_all.__name__ == "wire_all"

    def test_wire_dp_wrapper_factory_is_callable(self) -> None:
        """wire_dp_wrapper_factory must be a callable with the expected name."""
        from synth_engine.bootstrapper.wiring import wire_dp_wrapper_factory

        assert callable(wire_dp_wrapper_factory)
        assert wire_dp_wrapper_factory.__name__ == "wire_dp_wrapper_factory"

    def test_wire_spend_budget_fn_is_callable(self) -> None:
        """wire_spend_budget_fn must be a callable with the expected name."""
        from synth_engine.bootstrapper.wiring import wire_spend_budget_fn

        assert callable(wire_spend_budget_fn)
        assert wire_spend_budget_fn.__name__ == "wire_spend_budget_fn"

    def test_wire_webhook_delivery_fn_is_callable(self) -> None:
        """wire_webhook_delivery_fn must be a callable with the expected name."""
        from synth_engine.bootstrapper.wiring import wire_webhook_delivery_fn

        assert callable(wire_webhook_delivery_fn)
        assert wire_webhook_delivery_fn.__name__ == "wire_webhook_delivery_fn"

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
        from synth_engine.modules.synthesizer.jobs import job_orchestration as orch

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
        from synth_engine.modules.synthesizer.jobs import job_orchestration as orch

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
        from synth_engine.modules.synthesizer.jobs import job_orchestration as orch

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


class TestBuildWebhookDeliveryFnBehavior:
    """Behavioral tests for the closure returned by _build_webhook_delivery_fn."""

    def test_no_database_url_logs_warning_and_skips_delivery(self) -> None:
        """Closure must log a warning and return early when database_url is empty.

        This covers the ``if not database_url`` guard inside ``_deliver``.
        No DB call should be attempted; ``_logger.warning`` must fire exactly
        once with the job_id embedded in the message.
        """
        from synth_engine.bootstrapper.wiring import _build_webhook_delivery_fn

        mock_settings = MagicMock()
        mock_settings.database_url = ""
        mock_settings.webhook_delivery_timeout_seconds = 5

        with (
            patch(
                "synth_engine.bootstrapper.wiring.get_settings",
                return_value=mock_settings,
            ),
            patch("synth_engine.bootstrapper.wiring._logger") as mock_logger,
            patch("synth_engine.bootstrapper.wiring.get_engine") as mock_get_engine,
        ):
            deliver_fn = _build_webhook_delivery_fn()
            # Must not raise
            deliver_fn(42, "COMPLETE")

        mock_logger.warning.assert_called_once()
        warning_call_args = mock_logger.warning.call_args
        # The warning format string references the job_id; verify 42 appears in args
        assert 42 in warning_call_args.args, (
            f"Expected job_id 42 in warning args, got: {warning_call_args.args!r}"
        )
        # Engine must not be touched — delivery was skipped
        mock_get_engine.assert_not_called()

    def test_db_exception_logs_exception_and_does_not_propagate(self) -> None:
        """Closure must catch DB errors, log with _logger.exception, and not re-raise.

        This verifies the ``except Exception`` block inside ``_deliver``.
        The job lifecycle must not be disrupted by webhook delivery failures.
        """
        from synth_engine.bootstrapper.wiring import _build_webhook_delivery_fn

        mock_settings = MagicMock()
        mock_settings.database_url = (
            "postgresql://test:test@localhost/testdb"  # pragma: allowlist secret
        )
        mock_settings.webhook_delivery_timeout_seconds = 5

        with (
            patch(
                "synth_engine.bootstrapper.wiring.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "synth_engine.bootstrapper.wiring.get_engine",
                side_effect=RuntimeError("DB connection refused"),
            ),
            patch("synth_engine.bootstrapper.wiring._logger") as mock_logger,
        ):
            deliver_fn = _build_webhook_delivery_fn()
            # Must NOT raise — exception must be absorbed
            deliver_fn(99, "FAILED")

        mock_logger.exception.assert_called_once()
        exception_call_args = mock_logger.exception.call_args
        # The exception format string references the job_id and status
        assert 99 in exception_call_args.args, (
            f"Expected job_id 99 in exception args, got: {exception_call_args.args!r}"
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
        from synth_engine.modules.synthesizer.jobs import job_orchestration as orch

        assert orch._dp_wrapper_factory is build_dp_wrapper, (
            f"_dp_wrapper_factory must be wired at import time Got: {orch._dp_wrapper_factory!r}"
        )

    def test_importing_main_wires_spend_budget_fn(self) -> None:
        """Importing main must result in _spend_budget_fn being set.

        Verifies Rule 8 compliance for the spend_budget IoC hook.
        """
        from synth_engine.bootstrapper import main  # noqa: F401 — side-effect import
        from synth_engine.modules.synthesizer.jobs import job_orchestration as orch

        assert orch._spend_budget_fn is not None, (
            "_spend_budget_fn must be wired by bootstrapper at import time (Rule 8, Huey contract)."
        )
        assert callable(orch._spend_budget_fn), "_spend_budget_fn must be a callable, not None."

    def test_importing_main_wires_webhook_delivery_fn(self) -> None:
        """Importing main must result in _webhook_delivery_fn being set.

        Verifies Rule 8 compliance for the webhook delivery IoC hook (T45.3).
        """
        from synth_engine.bootstrapper import main  # noqa: F401 — side-effect import
        from synth_engine.modules.synthesizer.jobs import job_orchestration as orch

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


class TestWebhookDeliveryExceptionHandling:
    """P58: Webhook delivery exception handling must distinguish expected vs unexpected errors.

    The broad `except Exception` in _build_webhook_delivery_fn should be split:
    - Known DB/network exceptions: logged with _logger.exception()
    - Programming errors: logged at CRITICAL with type(exc).__name__

    This preserves the "never crash the job" contract while making programming
    errors visible at CRITICAL level.

    Task: P58 — Split wiring.py webhook delivery exception handling
    """

    def test_sqlalchemy_error_logged_at_exception_level(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SQLAlchemyError during webhook delivery must be logged via _logger.exception.

        The delivery function must not propagate the exception — it must catch it,
        log it, and return silently to preserve the "never crash the job" contract.
        """
        from unittest.mock import MagicMock, patch

        from sqlalchemy.exc import SQLAlchemyError

        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")  # pragma: allowlist secret
        monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.bootstrapper.wiring import _build_webhook_delivery_fn

        deliver_fn = _build_webhook_delivery_fn()

        with patch("synth_engine.bootstrapper.wiring.get_engine") as mock_engine:
            mock_engine.side_effect = SQLAlchemyError("DB connection failed")
            with patch("synth_engine.bootstrapper.wiring._logger") as mock_logger:
                # Must not raise
                deliver_fn(job_id=99, status="COMPLETE")

                # Must log at exception (not just warning)
                assert mock_logger.exception.called or mock_logger.critical.called, (
                    "SQLAlchemyError must be logged via exception() or critical()"
                )

    def test_unexpected_programming_error_logged_at_critical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A programming error (unexpected Exception) must be logged at CRITICAL level.

        This makes programming errors visible while still preserving the
        "never crash the job" delivery contract.

        Task: P58 — Split wiring.py webhook delivery exception handling
        """
        from unittest.mock import patch

        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")  # pragma: allowlist secret
        monkeypatch.setenv("AUDIT_KEY", "aa" * 32)
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.bootstrapper.wiring import _build_webhook_delivery_fn

        deliver_fn = _build_webhook_delivery_fn()

        # Inject a totally unexpected programming error (not a DB/network error)
        class _ProgrammingBug(RuntimeError):
            pass

        with patch("synth_engine.bootstrapper.wiring.get_engine") as mock_engine:
            mock_engine.side_effect = _ProgrammingBug("unexpected internal error")
            with patch("synth_engine.bootstrapper.wiring._logger") as mock_logger:
                # Must not raise
                deliver_fn(job_id=99, status="COMPLETE")

                # Programming errors must be logged at CRITICAL
                assert mock_logger.critical.called, (
                    "Unexpected (non-DB/network) exceptions must be logged at CRITICAL level"
                )
