"""RED-phase tests for P8-T8.3 — Data Model & Architecture Cleanup.

Covers three advisory items:

ADV-050: ``PrivacyLedger`` epsilon columns must use SQLAlchemy ``Numeric``
    type (``Numeric(precision=20, scale=10)``), not Python ``float`` /
    SQLAlchemy ``Float``.  Prevents floating-point accumulation drift in
    long-running epsilon accounting.

ADV-054: ``LicenseError`` must NOT carry an HTTP ``status_code`` attribute.
    HTTP status mapping belongs in the bootstrapper middleware/exception
    handler layer (per ADR-0008), not in the framework-agnostic
    ``shared/security/`` layer.

ADV-071: ``BudgetExhaustionError`` must be importable from the public API
    surface of ``synth_engine.modules.privacy`` (i.e. from the module's
    ``__init__.py`` re-exports).

CONSTITUTION Priority 3: TDD — these tests MUST fail before implementation.
CONSTITUTION Priority 4: 90%+ coverage.
Task: P8-T8.3 — Data Model & Architecture Cleanup (ADV-050, ADV-054, ADV-071)
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

# ---------------------------------------------------------------------------
# ADV-050: PrivacyLedger epsilon columns — Numeric not Float
# ---------------------------------------------------------------------------


def test_privacy_ledger_epsilon_columns_use_numeric_sa_type() -> None:
    """PrivacyLedger epsilon columns must declare Numeric SQLAlchemy column type.

    Walks the SQLModel table's SA column metadata and asserts both
    ``total_allocated_epsilon`` and ``total_spent_epsilon`` columns carry
    a ``sqlalchemy.Numeric`` (or subclass) type, NOT ``sqlalchemy.Float``.

    This guards against ADV-050: floating-point drift in epsilon accounting.
    """
    import sqlalchemy

    from synth_engine.modules.privacy.ledger import PrivacyLedger

    table = PrivacyLedger.__table__  # type: ignore[attr-defined]

    for col_name in ("total_allocated_epsilon", "total_spent_epsilon"):
        col = table.c[col_name]
        assert isinstance(col.type, sqlalchemy.Numeric), (
            f"Column '{col_name}' must be Numeric, got {type(col.type).__name__}"
        )
        assert not isinstance(col.type, sqlalchemy.Float), (
            f"Column '{col_name}' must not be Float (Float is a subclass of "
            f"Numeric but loses precision). Got {type(col.type).__name__}"
        )


def test_privacy_ledger_epsilon_numeric_precision() -> None:
    """PrivacyLedger epsilon columns must have precision=20 and scale=10.

    Verifies the exact precision/scale tuple requested in ADV-050 to ensure
    enough fractional digits for epsilon values used in privacy accounting.
    """
    import sqlalchemy

    from synth_engine.modules.privacy.ledger import PrivacyLedger

    table = PrivacyLedger.__table__  # type: ignore[attr-defined]

    for col_name in ("total_allocated_epsilon", "total_spent_epsilon"):
        col = table.c[col_name]
        col_type = col.type
        assert isinstance(col_type, sqlalchemy.Numeric)
        assert col_type.precision == 20, (
            f"Column '{col_name}' must have precision=20, got {col_type.precision}"
        )
        assert col_type.scale == 10, f"Column '{col_name}' must have scale=10, got {col_type.scale}"


def test_privacy_transaction_epsilon_spent_uses_numeric_sa_type() -> None:
    """PrivacyTransaction.epsilon_spent column must also use Numeric type.

    ADV-050 applies to all epsilon storage, including the per-transaction
    audit record, not only the ledger totals.
    """
    import sqlalchemy

    from synth_engine.modules.privacy.ledger import PrivacyTransaction

    table = PrivacyTransaction.__table__  # type: ignore[attr-defined]
    col = table.c["epsilon_spent"]
    assert isinstance(col.type, sqlalchemy.Numeric), (
        f"PrivacyTransaction.epsilon_spent must be Numeric, got {type(col.type).__name__}"
    )
    assert not isinstance(col.type, sqlalchemy.Float), (
        "PrivacyTransaction.epsilon_spent must not be Float"
    )


def test_privacy_ledger_accepts_decimal_values() -> None:
    """PrivacyLedger accepts Decimal values for epsilon fields without error.

    A Numeric column must accept Python Decimal instances, preserving
    full decimal precision in application-layer computations.
    """
    from synth_engine.modules.privacy.ledger import PrivacyLedger

    ledger = PrivacyLedger(
        total_allocated_epsilon=Decimal("10.0000000001"),
        total_spent_epsilon=Decimal("0.0000000001"),
    )
    # Values are stored as-is (no float coercion) at the Python model layer
    assert ledger.total_allocated_epsilon == Decimal("10.0000000001")
    assert ledger.total_spent_epsilon == Decimal("0.0000000001")


# ---------------------------------------------------------------------------
# ADV-054: LicenseError must not carry status_code
# ---------------------------------------------------------------------------


def test_license_error_has_no_status_code_attribute() -> None:
    """LicenseError must NOT have a status_code attribute.

    ADV-054: HTTP status semantics do not belong in shared/security/licensing.py.
    The bootstrapper middleware/exception handler is responsible for mapping
    LicenseError to an HTTP 403, per ADR-0008 architectural separation of
    concerns.
    """
    from synth_engine.shared.security.licensing import LicenseError

    err = LicenseError("test error")
    assert not hasattr(err, "status_code"), (
        "LicenseError must not carry a status_code attribute. "
        "HTTP status mapping belongs in bootstrapper/."
    )


def test_license_error_constructor_rejects_status_code_kwarg() -> None:
    """LicenseError constructor must not accept a status_code keyword argument.

    ADV-054: The constructor signature must not include status_code at all,
    enforcing that callers cannot accidentally embed HTTP semantics in the
    shared layer.
    """
    from synth_engine.shared.security.licensing import LicenseError

    sig = inspect.signature(LicenseError.__init__)
    assert "status_code" not in sig.parameters, (
        "LicenseError.__init__ must not accept status_code parameter. "
        "HTTP status code mapping belongs in bootstrapper/."
    )


def test_license_error_carries_detail_attribute() -> None:
    """LicenseError must still carry a detail attribute after removing status_code.

    The ``detail`` attribute is used by the bootstrapper router to build RFC 7807
    Problem Details responses — it is a shared layer concern (plain string),
    not an HTTP concern.
    """
    from synth_engine.shared.security.licensing import LicenseError

    err = LicenseError("meaningful error message")
    assert err.detail == "meaningful error message"
    assert str(err) == "meaningful error message"


def test_bootstrapper_licensing_router_maps_403_without_exc_status_code() -> None:
    """The licensing router exception handler must NOT read exc.status_code.

    After ADV-054, the router hardcodes 403 (the only valid response for
    LicenseError) rather than delegating to exc.status_code.
    """
    import ast
    import pathlib

    router_path = pathlib.Path("src/synth_engine/bootstrapper/routers/licensing.py")
    source = router_path.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "exc"
                and node.attr == "status_code"
            ):
                pytest.fail(
                    "bootstrapper/routers/licensing.py still reads exc.status_code. "
                    "After ADV-054, the router must hardcode 403 for LicenseError."
                )
    # No exc.status_code found — verify the source was read
    assert len(source) > 0, "licensing router source must be non-empty"


# ---------------------------------------------------------------------------
# ADV-071: BudgetExhaustionError re-exported from modules/privacy/__init__.py
# ---------------------------------------------------------------------------


def test_budget_exhaustion_error_importable_from_modules_privacy() -> None:
    """BudgetExhaustionError must be importable from synth_engine.modules.privacy.

    ADV-071: The public API surface of the privacy module must re-export
    BudgetExhaustionError so callers can use the stable import path:

        from synth_engine.modules.privacy import BudgetExhaustionError

    rather than reaching into the internal dp_engine submodule.
    """
    from synth_engine.modules.privacy import BudgetExhaustionError

    # Must be the real class, not a stub
    assert issubclass(BudgetExhaustionError, Exception)


def test_budget_exhaustion_error_in_privacy_all() -> None:
    """BudgetExhaustionError must appear in synth_engine.modules.privacy.__all__.

    A stable public API surface requires explicit listing in __all__ so that
    ``from synth_engine.modules.privacy import *`` works correctly for
    downstream consumers.
    """
    import synth_engine.modules.privacy as privacy_module

    assert "BudgetExhaustionError" in privacy_module.__all__, (
        "BudgetExhaustionError must be listed in synth_engine.modules.privacy.__all__"
    )


# ---------------------------------------------------------------------------
# spend_budget() — Decimal input covers no-conversion branch (Fix 4)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def async_engine_t83() -> AsyncGenerator[AsyncEngine]:
    """In-memory async SQLite engine for T8.3 Decimal-input spend_budget test.

    Yields:
        An AsyncEngine with all SQLModel tables created.
    """
    from synth_engine.shared.db import get_async_engine

    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_spend_budget_with_decimal_amount_no_conversion(
    async_engine_t83: AsyncEngine,
) -> None:
    """spend_budget() accepts a Decimal amount and skips float-to-Decimal conversion.

    When amount is already a Decimal, spend_budget() must use it directly
    (the isinstance(amount, Decimal) branch) rather than converting via
    Decimal(str(amount)).  This test exercises that no-conversion path end-to-end.

    Arrange: Insert a PrivacyLedger with total_allocated=Decimal("1.0").
    Act: Call spend_budget(amount=Decimal("0.5"), ...).
    Assert:
    - No exception raised (budget not exhausted).
    - Ledger total_spent_epsilon == Decimal("0.5").
    """
    from sqlalchemy import select

    from synth_engine.modules.privacy.accountant import spend_budget
    from synth_engine.modules.privacy.ledger import PrivacyLedger
    from synth_engine.shared.db import get_async_session

    async with get_async_session(async_engine_t83) as s:
        ledger = PrivacyLedger(
            total_allocated_epsilon=Decimal("1.0"),
            total_spent_epsilon=Decimal("0.0"),
        )
        s.add(ledger)
        await s.commit()
        await s.refresh(ledger)
        ledger_id = ledger.id

    # Act: pass a Decimal directly — exercises the no-conversion branch
    async with get_async_session(async_engine_t83) as s:
        await spend_budget(
            amount=Decimal("0.5"),
            job_id=1,
            ledger_id=ledger_id,
            session=s,
        )

    # Assert: ledger updated with exact Decimal value
    async with get_async_session(async_engine_t83) as s:
        result = await s.execute(select(PrivacyLedger).where(PrivacyLedger.id == ledger_id))
        updated = result.scalar_one()
        assert updated.total_spent_epsilon == Decimal("0.5"), (
            f"Expected Decimal('0.5'), got {updated.total_spent_epsilon!r}"
        )
        assert isinstance(updated.total_spent_epsilon, Decimal)
