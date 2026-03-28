"""Attack tests for nonexistent ledger handling in the privacy accountant (T66.5).

Tests verify that accessing a nonexistent privacy ledger raises a typed
LedgerNotFoundError (not a raw SQLAlchemy NoResultFound), and that this
error maps to HTTP 404 without echoing the ledger_id in the response body.

CONSTITUTION Priority 0: Security — internal IDs must not leak in HTTP responses.
Task: T66.5 — Fix Accountant NoResultFound Propagation.

Negative/attack tests (committed before feature tests per Rule 22).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Attack tests — FAIL (RED) before T66.5 implementation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_budget_nonexistent_ledger_raises_ledger_not_found_error() -> None:
    """spend_budget() with a nonexistent ledger_id must raise LedgerNotFoundError.

    Previously, SQLAlchemy's scalar_one() raised a raw NoResultFound, which
    is an unhandled exception that falls through to a 500 response and may
    leak internal DB query context. The typed exception enables clean 404
    handling.
    """
    from sqlalchemy.exc import NoResultFound

    from synth_engine.modules.privacy.accountant import spend_budget
    from synth_engine.shared.exceptions import LedgerNotFoundError

    mock_session = MagicMock()
    mock_session.begin = MagicMock()

    # Simulate the async context manager for session.begin()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=None)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin.return_value = mock_cm

    # Simulate execute() returning a result whose scalar_one() raises NoResultFound
    mock_result = MagicMock()
    mock_result.scalar_one.side_effect = NoResultFound()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(LedgerNotFoundError) as exc_info:
        await spend_budget(
            amount=Decimal("0.5"),
            job_id=1,
            ledger_id=99999,
            session=mock_session,
        )

    assert "99999" in str(exc_info.value), (
        "LedgerNotFoundError message must include the ledger_id for log correlation"
    )


@pytest.mark.asyncio
async def test_reset_budget_nonexistent_ledger_raises_ledger_not_found_error() -> None:
    """reset_budget() with a nonexistent ledger_id must raise LedgerNotFoundError.

    Same as spend_budget — the raw NoResultFound must be wrapped.
    """
    from sqlalchemy.exc import NoResultFound

    from synth_engine.modules.privacy.accountant import reset_budget
    from synth_engine.shared.exceptions import LedgerNotFoundError

    mock_session = MagicMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=None)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin.return_value = mock_cm

    mock_result = MagicMock()
    mock_result.scalar_one.side_effect = NoResultFound()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(LedgerNotFoundError) as exc_info:
        await reset_budget(
            ledger_id=88888,
            session=mock_session,
        )

    assert "88888" in str(exc_info.value), (
        "LedgerNotFoundError message must include the ledger_id for log correlation"
    )


def test_ledger_not_found_error_message_includes_ledger_id() -> None:
    """LedgerNotFoundError message must include the ledger_id for log correlation.

    The internal exception message (for log ingestion) must contain the
    ledger_id so that SIEM systems can correlate the error with the specific
    ledger. The HTTP response body must NOT echo this value.
    """
    from synth_engine.shared.exceptions import LedgerNotFoundError

    exc = LedgerNotFoundError(ledger_id=12345)
    assert "12345" in str(exc), (
        f"LedgerNotFoundError message must contain ledger_id=12345, got: {exc!s}"
    )


def test_ledger_not_found_error_is_subclass_of_synth_engine_error() -> None:
    """LedgerNotFoundError must inherit from SynthEngineError.

    All domain exceptions must inherit from SynthEngineError so they are
    handled by the domain exception middleware (ADR-0037).
    """
    from synth_engine.shared.exceptions import LedgerNotFoundError, SynthEngineError

    assert issubclass(LedgerNotFoundError, SynthEngineError), (
        "LedgerNotFoundError must be a subclass of SynthEngineError"
    )


def test_ledger_not_found_error_mapped_to_404() -> None:
    """LedgerNotFoundError must map to HTTP 404 in OPERATOR_ERROR_MAP.

    The HTTP mapping prevents a raw 500 from leaking and signals to clients
    that the resource does not exist without revealing whether the ID space
    is sparse or dense (IDOR protection).
    """
    from synth_engine.bootstrapper.errors.mapping import OPERATOR_ERROR_MAP
    from synth_engine.shared.exceptions import LedgerNotFoundError

    assert LedgerNotFoundError in OPERATOR_ERROR_MAP, (
        "LedgerNotFoundError must be registered in OPERATOR_ERROR_MAP"
    )
    entry = OPERATOR_ERROR_MAP[LedgerNotFoundError]
    assert entry["status_code"] == 404, (
        f"Expected LedgerNotFoundError to map to HTTP 404, got {entry['status_code']}"
    )
    # HTTP response detail must NOT contain ledger_id (no internal ID leakage)
    assert "ledger_id" not in entry["detail"].lower(), (
        "HTTP detail must not echo internal ledger_id"
    )
    assert "99999" not in entry["detail"], (
        "HTTP detail must be a static string, not an f-string with the ledger_id"
    )
