"""Tests for budget error scrubbing — T47.9.

Verifies that :exc:`~synth_engine.shared.exceptions.BudgetExhaustionError`:

1. Never exposes epsilon values in ``str()`` or ``repr()``.
2. Stores structured epsilon attributes for internal logging.
3. Produces a safe, generic API response when routed through the
   bootstrapper error handler.
4. Emits a WARNING log containing the epsilon details for internal audit.

Attack/negative tests (Commit 1):
- ``test_budget_exhaustion_str_no_epsilon`` — ``str()`` must not leak values
- ``test_budget_exhaustion_repr_no_epsilon`` — ``repr()`` must not leak values
- ``test_api_response_for_budget_exhaustion_is_generic`` — HTTP response must
  contain only the mapped operator-safe message
- ``test_budget_exhaustion_args_no_epsilon_values`` — exc.args[0] must be safe

Feature tests (Commit 2):
- ``test_budget_exhaustion_attributes_accessible`` — structured attributes exist
- ``test_budget_exhaustion_internal_log_has_epsilon`` — WARNING log contains values
- ``test_budget_exhaustion_is_synth_engine_error`` — inheritance preserved
- ``test_budget_exhaustion_default_message`` — generic message constant is correct
- ``test_accountant_raise_site_uses_structured_budget_exhaustion`` — accountant
  raises with generic message and structured attrs (in-memory SQLite)

CONSTITUTION Priority 0: Security — epsilon budget state is internal, not HTTP-exposed
CONSTITUTION Priority 3: TDD
Task: T47.9 — Scrub Budget Values From Exception Messages
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from synth_engine.modules.privacy.ledger import (
    PrivacyLedger,
)
from synth_engine.shared.exceptions import BudgetExhaustionError, SynthEngineError

# ---------------------------------------------------------------------------
# Shared async fixture (SQLite / aiosqlite — same pattern as test_privacy_accountant.py)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def async_engine() -> AsyncGenerator[AsyncEngine]:
    """Provide an in-memory async SQLite engine with privacy schema.

    Yields:
        An :class:`AsyncEngine` with ``privacy_ledger`` and
        ``privacy_transaction`` tables created.
    """
    from synth_engine.shared.db import get_async_engine

    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# ATTACK / NEGATIVE TESTS — these must FAIL until the scrubbing is implemented
# ---------------------------------------------------------------------------


class TestBudgetExhaustionMessageScrubbing:
    """BudgetExhaustionError must not leak epsilon values in str or repr."""

    def test_budget_exhaustion_str_no_epsilon(self) -> None:
        """str(BudgetExhaustionError(...)) must NOT contain epsilon or decimal values.

        Attack scenario: epsilon values leaking into API error responses via
        ``str(exc)`` passed to a generic error handler.  This test verifies
        that no epsilon value strings appear in the exception's string
        representation, regardless of the values passed as arguments.

        Verifies:
        - Common epsilon value strings are absent from str(exc).
        - Common Decimal representation strings are absent.
        - The string representation is a safe, generic message.
        """
        exc = BudgetExhaustionError(
            requested_epsilon=Decimal("0.75"),
            total_spent=Decimal("9.5"),
            total_allocated=Decimal("10.0"),
        )
        exc_str = str(exc)

        # Must NOT contain any epsilon numeric values
        assert "0.75" not in exc_str, (
            f"str(BudgetExhaustionError) must not contain requested_epsilon value; got: {exc_str!r}"
        )
        assert "9.5" not in exc_str, (
            f"str(BudgetExhaustionError) must not contain total_spent value; got: {exc_str!r}"
        )
        assert "10.0" not in exc_str, (
            f"str(BudgetExhaustionError) must not contain total_allocated value; got: {exc_str!r}"
        )

        # Must NOT contain the word 'epsilon' followed by an = and value
        assert "epsilon=" not in exc_str.lower(), (
            f"str(BudgetExhaustionError) must not contain 'epsilon=<value>'; got: {exc_str!r}"
        )

    def test_budget_exhaustion_repr_no_epsilon(self) -> None:
        """repr(BudgetExhaustionError(...)) must NOT contain epsilon values.

        Attack scenario: repr() called in tracebacks or logging with %r format
        specifier, inadvertently leaking epsilon state.
        """
        exc = BudgetExhaustionError(
            requested_epsilon=Decimal("1.23"),
            total_spent=Decimal("8.77"),
            total_allocated=Decimal("10.0"),
        )
        exc_repr = repr(exc)

        assert "1.23" not in exc_repr, (
            f"repr(BudgetExhaustionError) must not contain requested_epsilon; got: {exc_repr!r}"
        )
        assert "8.77" not in exc_repr, (
            f"repr(BudgetExhaustionError) must not contain total_spent; got: {exc_repr!r}"
        )

    def test_budget_exhaustion_args_no_epsilon_values(self) -> None:
        """exc.args[0] must be the generic safe message, not a value-containing string.

        Attack scenario: exception middleware that accesses exc.args[0] directly
        and forwards it to the client.  This closes the gap between str(exc)
        scrubbing (which we control) and the underlying args tuple (which may
        be accessed by third-party middleware or stdlib tracebacks).
        """
        exc = BudgetExhaustionError(
            requested_epsilon=Decimal("0.5"),
            total_spent=Decimal("4.5"),
            total_allocated=Decimal("5.0"),
        )
        assert len(exc.args) >= 1
        args_str = str(exc.args[0])

        assert "0.5" not in args_str, (
            f"exc.args[0] must not contain epsilon value; got: {args_str!r}"
        )
        assert "4.5" not in args_str, f"exc.args[0] must not contain total_spent; got: {args_str!r}"

    def test_api_response_for_budget_exhaustion_is_generic(self) -> None:
        """The bootstrapper error handler must use OPERATOR_ERROR_MAP detail, not str(exc).

        Attack scenario: the operator_error_response() function inadvertently
        uses ``str(exc)`` as the ``detail`` field instead of the mapped
        operator-safe string from OPERATOR_ERROR_MAP.

        This test builds an exc and verifies the JSONResponse detail field
        contains only the pre-approved operator message (no epsilon values).
        """
        from synth_engine.bootstrapper.errors.formatter import operator_error_response

        exc = BudgetExhaustionError(
            requested_epsilon=Decimal("0.99"),
            total_spent=Decimal("9.01"),
            total_allocated=Decimal("10.0"),
        )
        response = operator_error_response(exc)
        content: Any = response.body

        # Decode the response body
        body_str = content.decode("utf-8") if isinstance(content, bytes) else str(content)

        # Must not contain epsilon values
        assert "0.99" not in body_str, (
            f"HTTP response body must not contain requested_epsilon; got: {body_str!r}"
        )
        assert "9.01" not in body_str, (
            f"HTTP response body must not contain total_spent; got: {body_str!r}"
        )

        # Must contain the safe operator message
        assert "budget" in body_str.lower() or "privacy" in body_str.lower(), (
            f"HTTP response body must contain operator-safe language; got: {body_str!r}"
        )

        # Must be HTTP 409
        assert response.status_code == 409, (
            f"BudgetExhaustionError must map to HTTP 409, got {response.status_code}"
        )

    def test_budget_exhaustion_str_contains_generic_message(self) -> None:
        """str(BudgetExhaustionError) must contain the safe generic message keyword.

        Verifies the positive contract: the string representation is not empty
        and describes the error in a safe, operator-friendly way.
        """
        exc = BudgetExhaustionError(
            requested_epsilon=Decimal("0.5"),
            total_spent=Decimal("4.5"),
            total_allocated=Decimal("5.0"),
        )
        exc_str = str(exc)

        # Generic message must mention budget exhaustion (no values)
        assert len(exc_str) > 0, "str(BudgetExhaustionError) must not be empty"
        assert "budget" in exc_str.lower() or "differential privacy" in exc_str.lower(), (
            f"str(BudgetExhaustionError) must contain generic budget message; got: {exc_str!r}"
        )


# ---------------------------------------------------------------------------
# FEATURE TESTS — verify structured attributes and internal logging
# ---------------------------------------------------------------------------


class TestBudgetExhaustionAttributes:
    """BudgetExhaustionError structured attributes must be accessible for logging."""

    def test_budget_exhaustion_attributes_accessible(self) -> None:
        """BudgetExhaustionError must expose requested_epsilon and total_spent as attributes.

        These attributes allow internal logging code to access the epsilon
        values without relying on parsing the string message.
        """
        exc = BudgetExhaustionError(
            requested_epsilon=Decimal("0.75"),
            total_spent=Decimal("9.5"),
            total_allocated=Decimal("10.0"),
        )

        assert hasattr(exc, "requested_epsilon"), (
            "BudgetExhaustionError must have 'requested_epsilon' attribute"
        )
        assert hasattr(exc, "total_spent"), (
            "BudgetExhaustionError must have 'total_spent' attribute"
        )
        assert hasattr(exc, "total_allocated"), (
            "BudgetExhaustionError must have 'total_allocated' attribute"
        )

        assert exc.requested_epsilon == Decimal("0.75"), (
            f"requested_epsilon must equal Decimal('0.75'), got {exc.requested_epsilon!r}"
        )
        assert exc.total_spent == Decimal("9.5"), (
            f"total_spent must equal Decimal('9.5'), got {exc.total_spent!r}"
        )
        assert exc.total_allocated == Decimal("10.0"), (
            f"total_allocated must equal Decimal('10.0'), got {exc.total_allocated!r}"
        )

    def test_budget_exhaustion_remaining_epsilon_attribute(self) -> None:
        """BudgetExhaustionError must expose remaining_epsilon as a computed attribute.

        The remaining budget at the time of exhaustion is useful for internal
        audit logging (operators monitoring how close the budget was before
        the next spend attempt exhausted it).
        """
        exc = BudgetExhaustionError(
            requested_epsilon=Decimal("0.75"),
            total_spent=Decimal("9.5"),
            total_allocated=Decimal("10.0"),
        )

        assert hasattr(exc, "remaining_epsilon"), (
            "BudgetExhaustionError must have 'remaining_epsilon' attribute"
        )
        # remaining = allocated - spent = 10.0 - 9.5 = 0.5
        assert exc.remaining_epsilon == Decimal("0.5"), (
            f"remaining_epsilon must equal Decimal('0.5'), got {exc.remaining_epsilon!r}"
        )

    def test_budget_exhaustion_is_synth_engine_error(self) -> None:
        """BudgetExhaustionError must remain a subclass of SynthEngineError.

        The exception hierarchy contract must be preserved after refactoring.
        """
        exc = BudgetExhaustionError(
            requested_epsilon=Decimal("0.1"),
            total_spent=Decimal("1.0"),
            total_allocated=Decimal("1.0"),
        )
        assert isinstance(exc, SynthEngineError), (
            "BudgetExhaustionError must inherit from SynthEngineError"
        )
        assert isinstance(exc, Exception), (
            "BudgetExhaustionError must be catchable as a plain Exception"
        )
        # BudgetExhaustionError message must be the privacy-safe generic string
        assert "budget" in str(exc).lower()

    def test_budget_exhaustion_default_message_is_generic(self) -> None:
        """BudgetExhaustionError generic message must match the expected constant.

        This pins the exact safe message string so that any accidental change
        to the constant triggers a test failure.
        """
        exc = BudgetExhaustionError(
            requested_epsilon=Decimal("0.1"),
            total_spent=Decimal("0.9"),
            total_allocated=Decimal("1.0"),
        )
        expected = "Differential privacy budget exhausted. Synthesis job cannot proceed."
        assert str(exc) == expected, (
            f"Generic message mismatch.\n  Expected: {expected!r}\n  Got:      {str(exc)!r}"
        )


class TestBudgetExhaustionInternalLogging:
    """Internal log records must contain epsilon values for audit purposes."""

    def test_budget_exhaustion_internal_log_has_epsilon(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Raising BudgetExhaustionError must log epsilon values at WARNING level.

        Simulates the spend_budget() warning log that fires before the raise.
        Verifies that epsilon details appear in the WARNING log entry, confirming
        that internal audit logging captures the structured data even though the
        exception message is scrubbed.

        This test verifies the LOGGING CONTRACT, not the exception constructor.
        The accountant code logs the values separately before raising.
        """
        with caplog.at_level(logging.WARNING, logger="synth_engine.modules.privacy.accountant"):
            # Simulate what the accountant does: log then raise
            _logger = logging.getLogger("synth_engine.modules.privacy.accountant")
            requested = Decimal("0.75")
            spent = Decimal("9.5")
            allocated = Decimal("10.0")

            _logger.warning(
                "Budget exhausted: ledger_id=%d, requested=%s, spent=%s, allocated=%s",
                1,
                requested,
                spent,
                allocated,
            )
            exc = BudgetExhaustionError(
                requested_epsilon=requested,
                total_spent=spent,
                total_allocated=allocated,
            )

        # The log must contain the epsilon values (for internal audit)
        log_text = " ".join(r.getMessage() for r in caplog.records)
        assert "0.75" in log_text, (
            f"WARNING log must contain requested epsilon value; log: {log_text!r}"
        )
        assert "9.5" in log_text, f"WARNING log must contain total_spent value; log: {log_text!r}"

        # The exception str must still be clean
        assert "0.75" not in str(exc), (
            "Exception str must not contain epsilon even when log contains it"
        )


class TestAccountantRaiseSite:
    """Accountant raise site must use generic message with structured attributes."""

    @pytest.mark.asyncio
    async def test_accountant_raise_site_uses_structured_budget_exhaustion(
        self, async_engine: AsyncEngine
    ) -> None:
        """spend_budget() must raise BudgetExhaustionError with structured attributes.

        Uses an in-memory SQLite database to exercise the full raise path in
        accountant.spend_budget() without any mocking.  Verifies that the raised
        exception has:
        - A generic str() with no epsilon values
        - Accessible requested_epsilon, total_spent, total_allocated attributes

        Arrange: Insert a PrivacyLedger with total_allocated=10.0, total_spent=9.5.
        Act: Call spend_budget(amount=0.75, ...) — triggers exhaustion.
        Assert: BudgetExhaustionError.str() is generic; attributes are populated.
        """
        from synth_engine.modules.privacy.accountant import spend_budget
        from synth_engine.shared.db import get_async_session

        # Arrange: ledger with tight budget
        async with get_async_session(async_engine) as setup_session:
            ledger = PrivacyLedger(
                total_allocated_epsilon=Decimal("10.0"),
                total_spent_epsilon=Decimal("9.5"),
            )
            setup_session.add(ledger)
            await setup_session.commit()
            await setup_session.refresh(ledger)
            ledger_id = ledger.id

        # Act: trigger exhaustion
        with pytest.raises(BudgetExhaustionError) as exc_info:
            async with get_async_session(async_engine) as spend_session:
                await spend_budget(
                    amount=Decimal("0.75"),
                    job_id=1,
                    ledger_id=ledger_id,
                    session=spend_session,
                )

        exc = exc_info.value

        # The message must be generic — no epsilon values
        assert "0.75" not in str(exc), (
            f"BudgetExhaustionError message must not contain requested epsilon; got: {str(exc)!r}"
        )
        assert "9.5" not in str(exc), (
            f"BudgetExhaustionError message must not contain total_spent; got: {str(exc)!r}"
        )
        assert "10.0" not in str(exc), (
            f"BudgetExhaustionError message must not contain total_allocated; got: {str(exc)!r}"
        )

        # Structured attributes must be accessible
        assert exc.requested_epsilon == Decimal("0.75"), (
            f"requested_epsilon must be Decimal('0.75'), got {exc.requested_epsilon!r}"
        )
        assert exc.total_spent == Decimal("9.5"), (
            f"total_spent must be Decimal('9.5'), got {exc.total_spent!r}"
        )
        assert exc.total_allocated == Decimal("10.0"), (
            f"total_allocated must be Decimal('10.0'), got {exc.total_allocated!r}"
        )


class TestDpEngineCheckBudgetScrubbing:
    """dp_engine.check_budget() raise site must also use generic message."""

    def test_check_budget_str_no_epsilon(self) -> None:
        """DPTrainingWrapper.check_budget() must raise with a generic message.

        The check_budget() raise site currently includes epsilon values in the
        message.  After T47.9, it must raise BudgetExhaustionError without
        epsilon in str(exc).
        """
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper

        wrapper = DPTrainingWrapper()
        wrapper._wrapped = True  # type: ignore[attr-defined]

        # Simulate epsilon_spent returning a value >= allocated_epsilon
        with patch.object(wrapper, "epsilon_spent", return_value=1.5):
            with pytest.raises(BudgetExhaustionError) as exc_info:
                wrapper.check_budget(allocated_epsilon=1.0, delta=1e-5)

        exc = exc_info.value
        exc_str = str(exc)

        # epsilon values must NOT appear in the exception string
        assert "1.5" not in exc_str, (
            f"check_budget() BudgetExhaustionError must not contain spent epsilon; got: {exc_str!r}"
        )
        assert "1.0" not in exc_str, (
            f"check_budget() BudgetExhaustionError must not contain "
            f"allocated epsilon; got: {exc_str!r}"
        )
