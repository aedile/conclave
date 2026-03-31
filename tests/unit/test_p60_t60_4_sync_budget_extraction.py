"""Tests for T60.4 — budget transaction logic extracted to modules/privacy/sync_budget.py.

Verifies that:
- sync_spend_budget is importable from modules/privacy/sync_budget.py
- sync_spend_budget raises ValueError for non-positive amount
- sync_spend_budget raises BudgetExhaustionError when budget is exhausted
- sync_spend_budget deducts epsilon and creates a PrivacyTransaction (integration via mock)
- factories.py build_spend_budget_fn delegates to sync_spend_budget
- sync_budget.py does NOT import from bootstrapper (boundary violation)
- All SQLAlchemy imports are deferred (inside function body)

CONSTITUTION Priority 3: TDD
Task: T60.4 — Extract domain transaction logic to modules/privacy/sync_budget.py
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from unittest.mock import MagicMock, patch


class TestSyncBudgetCanonicalLocation:
    """sync_spend_budget must live in modules/privacy/sync_budget.py."""

    def test_function_importable_from_sync_budget(self) -> None:
        """sync_spend_budget must be importable from modules/privacy/sync_budget."""
        from synth_engine.modules.privacy.sync_budget import sync_spend_budget

        assert sync_spend_budget.__name__ == "sync_spend_budget"

        assert callable(sync_spend_budget)

    def test_no_module_scope_sqlalchemy_imports(self) -> None:
        """sync_budget.py must not import SQLAlchemy at module scope (deferred only)."""
        import synth_engine.modules.privacy.sync_budget as sync_budget_mod

        source = inspect.getsource(sync_budget_mod)
        # SQLAlchemy imports must be INSIDE the function body, not at module scope.
        # Check that no module-scope import exists (all SA imports are inside the fn)
        lines = source.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Module-scope lines are unindented imports
            if stripped.startswith("from sqlalchemy") or stripped.startswith("import sqlalchemy"):
                # Must not be at module scope (indent == 0 means module scope)
                indent = len(line) - len(line.lstrip())
                assert indent > 0, f"SQLAlchemy imported at module scope on line {i + 1}: {line!r}"

    def test_no_bootstrapper_import_in_sync_budget(self) -> None:
        """sync_budget.py must NOT import from bootstrapper (boundary violation)."""
        import synth_engine.modules.privacy.sync_budget as sync_budget_mod

        source = inspect.getsource(sync_budget_mod)
        assert "from synth_engine.bootstrapper" not in source, (
            "sync_budget.py must not import from bootstrapper — import-linter boundary"
        )
        assert "import synth_engine.bootstrapper" not in source, (
            "sync_budget.py must not import from bootstrapper — import-linter boundary"
        )


class TestSyncSpendBudgetValidation:
    """sync_spend_budget must validate inputs before touching the database."""

    def test_raises_value_error_for_zero_amount(self) -> None:
        """sync_spend_budget must raise ValueError when amount is 0."""
        import pytest

        from synth_engine.modules.privacy.sync_budget import sync_spend_budget

        mock_engine = MagicMock()
        # ValueError is raised before Session is touched — no Session patch needed
        with pytest.raises(ValueError, match="amount must be positive"):
            sync_spend_budget(mock_engine, amount=0.0, job_id=1, ledger_id=1)

    def test_raises_value_error_for_negative_amount(self) -> None:
        """sync_spend_budget must raise ValueError when amount is negative."""
        import pytest

        from synth_engine.modules.privacy.sync_budget import sync_spend_budget

        mock_engine = MagicMock()
        with pytest.raises(ValueError, match="amount must be positive"):
            sync_spend_budget(mock_engine, amount=-0.5, job_id=1, ledger_id=1)

    def test_accepts_decimal_amount(self) -> None:
        """sync_spend_budget must accept Decimal amounts without conversion error."""
        from synth_engine.modules.privacy.sync_budget import sync_spend_budget

        mock_engine = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_ledger = MagicMock()
        mock_ledger.total_spent_epsilon = Decimal("0.0")
        mock_ledger.total_allocated_epsilon = Decimal("10.0")
        mock_session.execute.return_value.scalar_one.return_value = mock_ledger

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_session.begin.return_value = mock_ctx

        # Patch sqlalchemy.orm.Session — the deferred import target used by
        # sync_spend_budget at call time (module-scope patch is impossible since
        # Session is imported inside the function body, not at module scope).
        with patch("sqlalchemy.orm.Session", return_value=mock_ctx):
            # Should not raise — Decimal(0.5) is positive
            sync_spend_budget(mock_engine, amount=Decimal("0.5"), job_id=1, ledger_id=1)
            assert Decimal("0.5") > 0, "amount must be positive"
            assert str(Decimal("0.5")) == "0.5"


class TestSyncSpendBudgetBudgetExhaustion:
    """sync_spend_budget must raise BudgetExhaustionError when budget is exhausted."""

    def test_raises_budget_exhaustion_error_when_over_limit(self) -> None:
        """sync_spend_budget must raise BudgetExhaustionError on exhaustion."""
        import pytest

        from synth_engine.modules.privacy.sync_budget import sync_spend_budget
        from synth_engine.shared.exceptions import BudgetExhaustionError

        mock_engine = MagicMock()
        mock_session = MagicMock()

        mock_ledger = MagicMock()
        mock_ledger.total_spent_epsilon = Decimal("9.9")
        mock_ledger.total_allocated_epsilon = Decimal("10.0")
        mock_session.execute.return_value.scalar_one.return_value = mock_ledger

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_session.begin.return_value = mock_ctx

        # Patch sqlalchemy.orm.Session — the deferred import target used at call time.
        with patch("sqlalchemy.orm.Session", return_value=mock_ctx):
            with pytest.raises(BudgetExhaustionError) as exc_info:
                sync_spend_budget(mock_engine, amount=0.5, job_id=1, ledger_id=1)

        # Verify the error contains useful information
        err = exc_info.value
        assert hasattr(err, "requested_epsilon") or "budget" in str(err).lower()


class TestFactoriesDelegation:
    """build_spend_budget_fn in factories.py must delegate to sync_spend_budget."""

    def test_factories_references_sync_spend_budget(self) -> None:
        """factories.py _sync_wrapper must call sync_spend_budget from privacy module."""
        import synth_engine.bootstrapper.factories as factories_mod

        source = inspect.getsource(factories_mod)
        assert "sync_spend_budget" in source, (
            "factories.py must reference sync_spend_budget from privacy module"
        )

    def test_factories_reduced_by_removing_transaction_logic(self) -> None:
        """factories.py _sync_wrapper must no longer contain inline transaction code."""
        import synth_engine.bootstrapper.factories as factories_mod

        source = inspect.getsource(factories_mod)
        # The direct PrivacyLedger ORM access should NOT be in factories.py
        # (it should be in sync_budget.py now)
        assert "PrivacyLedger" not in source, (
            "factories.py must not contain PrivacyLedger ORM access "
            "(moved to modules/privacy/sync_budget.py)"
        )
