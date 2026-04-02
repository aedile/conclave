"""Negative/attack tests for Phase 76 — Advisory Drain & Polish.

Attack tests verifying that:
1. set_spend_budget_fn() double-set emits WARNING but succeeds (mirrors set_dp_wrapper_factory).
2. set_spend_budget_fn() single-set does NOT emit a warning (only double-set should warn).
3. After double-set, _spend_budget_fn holds the last supplied value (not None, not the first).

CONSTITUTION Priority 0: Security — no silent overwrite of safety-critical factory
CONSTITUTION Priority 3: TDD — Attack tests committed before feature implementation (Rule 22)
Task: P76 (set_spend_budget_fn double-set WARNING)
Phase: P76 — Advisory Drain & Polish
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


class TestSetSpendBudgetFnDoubleSetWarning:
    """Double-set of spend_budget_fn must emit WARNING and succeed."""

    def test_double_set_emits_warning(self) -> None:
        """Calling set_spend_budget_fn() twice must emit a WARNING log.

        Matches the existing pattern in set_dp_wrapper_factory() and
        set_webhook_delivery_fn() — a second registration signals a
        potential wiring configuration issue.
        """
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            set_spend_budget_fn,
        )

        fn_a = MagicMock(name="spend_budget_fn_a")
        fn_b = MagicMock(name="spend_budget_fn_b")

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration._logger"
        ) as mock_logger:
            set_spend_budget_fn(fn_a)
            set_spend_budget_fn(fn_b)  # double-set — must warn

            warning_calls = [c for c in mock_logger.method_calls if "warning" in str(c[0]).lower()]
            assert len(warning_calls) >= 1, (
                "set_spend_budget_fn() called twice must emit at least one WARNING. "
                f"Logger calls: {mock_logger.method_calls}"
            )

    def test_single_set_does_not_emit_warning(self) -> None:
        """A single call to set_spend_budget_fn() must NOT emit a WARNING.

        Only the second (and subsequent) call should warn — the first
        registration is the expected startup path.
        """
        import synth_engine.modules.synthesizer.jobs.job_orchestration as orch

        fn_a = MagicMock(name="spend_budget_fn_a")

        # Reset to None so we start clean for this test
        original = orch._spend_budget_fn
        orch._spend_budget_fn = None
        try:
            with patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration._logger"
            ) as mock_logger:
                from synth_engine.modules.synthesizer.jobs.job_orchestration import (
                    set_spend_budget_fn,
                )

                set_spend_budget_fn(fn_a)

                warning_calls = [
                    c for c in mock_logger.method_calls if "warning" in str(c[0]).lower()
                ]
                assert warning_calls == [], (
                    "set_spend_budget_fn() first call must NOT emit a WARNING. "
                    f"Unexpected warning calls: {warning_calls}"
                )
        finally:
            orch._spend_budget_fn = original

    def test_double_set_does_not_raise(self) -> None:
        """Double-set must succeed — not raise — to preserve backward compatibility.

        After two successful calls, _spend_budget_fn must be set (not None).
        """
        import synth_engine.modules.synthesizer.jobs.job_orchestration as orch
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            set_spend_budget_fn,
        )

        fn_a = MagicMock(name="spend_budget_fn_a")
        fn_b = MagicMock(name="spend_budget_fn_b")

        # Must not raise
        set_spend_budget_fn(fn_a)
        set_spend_budget_fn(fn_b)

        # Specific: callable must be set to the last supplied value (not None)
        assert orch._spend_budget_fn is fn_b, (
            "After double-set, _spend_budget_fn must equal fn_b (the last supplied callable). "
            f"Got: {orch._spend_budget_fn!r}"
        )

    def test_double_set_stores_last_value(self) -> None:
        """After double-set, _spend_budget_fn must hold the last supplied callable.

        Overwriting with the second value is the correct behavior — it matches
        set_dp_wrapper_factory() and set_webhook_delivery_fn() semantics.
        """
        import synth_engine.modules.synthesizer.jobs.job_orchestration as orch
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            set_spend_budget_fn,
        )

        fn_a = MagicMock(name="spend_budget_fn_a")
        fn_b = MagicMock(name="spend_budget_fn_b")

        set_spend_budget_fn(fn_a)
        set_spend_budget_fn(fn_b)

        assert orch._spend_budget_fn is fn_b, (
            "After double-set, _spend_budget_fn must hold the last supplied callable. "
            f"Got: {orch._spend_budget_fn!r}"
        )

    def test_warning_message_references_overwrite(self) -> None:
        """WARNING message must mention 'already' or 'overwriting' to be actionable.

        The warning is only useful if it tells the operator what happened.
        It must use language consistent with the other double-set warnings:
        'already registered' or 'overwriting'.
        """
        from synth_engine.modules.synthesizer.jobs.job_orchestration import (
            set_spend_budget_fn,
        )

        fn_a = MagicMock(name="spend_budget_fn_a")
        fn_b = MagicMock(name="spend_budget_fn_b")

        captured_messages: list[str] = []

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration._logger"
        ) as mock_logger:

            def _capture_warning(msg: str, *args: object, **kwargs: object) -> None:
                captured_messages.append(msg % args if args else msg)

            mock_logger.warning.side_effect = _capture_warning
            set_spend_budget_fn(fn_a)
            set_spend_budget_fn(fn_b)

        assert len(captured_messages) >= 1, (
            "set_spend_budget_fn() double-set must emit at least one warning message."
        )
        combined = " ".join(captured_messages).lower()
        assert "already" in combined or "overwriting" in combined, (
            "WARNING message must contain 'already' or 'overwriting' to be actionable. "
            f"Got: {captured_messages!r}"
        )
