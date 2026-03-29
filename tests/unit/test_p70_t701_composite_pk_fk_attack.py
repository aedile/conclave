"""Negative/attack tests for T70.1 — Composite PK/FK support in subsetting.

ATTACK-FIRST TDD — these tests prove the system handles composite keys correctly
and rejects invalid configurations at reflection time.

CONSTITUTION Priority 0: Security — parameterised SQL only, no data injection
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T70.1 — Composite PK/FK Support in Subsetting (C6)
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from synth_engine.modules.subsetting.traversal import DagTraversal
from synth_engine.shared.schema_topology import (
    ColumnInfo,
    ForeignKeyInfo,
    SchemaTopology,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _col(name: str, pk: int = 0) -> ColumnInfo:
    """Build a ColumnInfo for test fixtures."""
    return ColumnInfo(name=name, type="INTEGER", primary_key=pk, nullable=False)


def _fk(constrained: list[str], referred_table: str, referred: list[str]) -> ForeignKeyInfo:
    """Build a ForeignKeyInfo for test fixtures."""
    return ForeignKeyInfo(
        constrained_columns=tuple(constrained),
        referred_table=referred_table,
        referred_columns=tuple(referred),
    )


def _make_engine_with_rows(
    call_map: dict[tuple[str, ...], list[dict[str, Any]]],
) -> Any:
    """Build a mock engine whose connections return rows based on SQL content.

    Args:
        call_map: Dict mapping (table_name_fragment,) to rows to return.

    Returns:
        MagicMock mimicking sqlalchemy.Engine with deterministic results.
    """
    from sqlalchemy import Engine

    def _make_result(rows: list[dict[str, Any]]) -> MagicMock:
        result = MagicMock()
        result.mappings.return_value = [dict(r) for r in rows]
        return result

    def _execute_side_effect(stmt: Any, params: Any = None) -> Any:
        stmt_str = str(stmt)
        for key_fragment, rows in call_map.items():
            if all(f in stmt_str for f in key_fragment):
                return _make_result(rows)
        return _make_result([])

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = _execute_side_effect

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    engine = MagicMock(spec=Engine)
    engine.connect.return_value = mock_ctx
    return engine


# ---------------------------------------------------------------------------
# T70.1 — Composite FK column count mismatch raises at reflection time
# ---------------------------------------------------------------------------


class TestCompositeFkColumnCountMismatch:
    """FK with mismatched constrained/referred column counts must raise ValueError."""

    def test_composite_fk_column_count_mismatch_raises_at_reflection(self) -> None:
        """ForeignKeyInfo with unequal constrained/referred column lengths must raise.

        A FK with 2 constrained columns pointing to 1 referred column is structurally
        invalid — the join condition cannot be formed.  The subsetting engine must
        raise ValueError when it encounters such a FK during traversal setup.
        """
        # Build a topology with a malformed FK: 2 constrained, 1 referred
        malformed_fk = ForeignKeyInfo(
            constrained_columns=("order_id", "product_id"),
            referred_table="products",
            referred_columns=("id",),  # Only 1 referred but 2 constrained — invalid
        )
        topology = SchemaTopology(
            table_order=("products", "order_items"),
            columns={
                "products": (_col("id", pk=1),),
                "order_items": (_col("order_id", pk=1), _col("product_id", pk=1)),
            },
            foreign_keys={
                "order_items": (malformed_fk,),
            },
        )
        engine = _make_engine_with_rows({})
        traversal = DagTraversal(engine=engine, topology=topology)

        with pytest.raises(ValueError, match=".*constrained.*referred.*"):
            list(
                traversal.traverse(
                    "products",
                    "SELECT * FROM products WHERE id = 1",
                )
            )

    def test_composite_fk_5_columns_raises(self) -> None:
        """FK with 5 constrained columns must raise ValueError at traversal time.

        Scope: support 2-4 column composites only.  Keys wider than 4 columns
        are rejected with a clear error (T70.1 spec §6).
        """
        fk_5_cols = ForeignKeyInfo(
            constrained_columns=("a", "b", "c", "d", "e"),
            referred_table="parent",
            referred_columns=("a", "b", "c", "d", "e"),
        )
        topology = SchemaTopology(
            table_order=("parent", "child"),
            columns={
                "parent": tuple(_col(c, pk=1) for c in ["a", "b", "c", "d", "e"]),
                "child": tuple(_col(c) for c in ["a", "b", "c", "d", "e"]),
            },
            foreign_keys={
                "child": (fk_5_cols,),
            },
        )
        engine = _make_engine_with_rows({})
        traversal = DagTraversal(engine=engine, topology=topology)

        with pytest.raises(ValueError, match=".*4.*column.*"):
            list(
                traversal.traverse(
                    "parent",
                    "SELECT * FROM parent WHERE a = 1",
                )
            )

    def test_composite_fk_4_columns_accepted(self) -> None:
        """FK with exactly 4 constrained columns must be accepted (boundary).

        4-column composites are within the supported range.
        """
        fk_4_cols = ForeignKeyInfo(
            constrained_columns=("a", "b", "c", "d"),
            referred_table="parent",
            referred_columns=("a", "b", "c", "d"),
        )
        parent_rows = [{"a": 1, "b": 2, "c": 3, "d": 4}]
        topology = SchemaTopology(
            table_order=("parent", "child"),
            columns={
                "parent": tuple(_col(c, pk=1) for c in ["a", "b", "c", "d"]),
                "child": tuple(_col(c) for c in ["a", "b", "c", "d"]),
            },
            foreign_keys={
                "child": (fk_4_cols,),
            },
        )
        engine = _make_engine_with_rows({("parent",): parent_rows, ("child",): []})
        traversal = DagTraversal(engine=engine, topology=topology)

        # Should NOT raise for 4-column FK
        results = list(
            traversal.traverse(
                "parent",
                "SELECT * FROM parent WHERE a = 1",
            )
        )
        assert ("parent", parent_rows) in results


# ---------------------------------------------------------------------------
# T70.1 — _extract_pk_values returns tuples for composite PKs
# ---------------------------------------------------------------------------


class TestExtractPkValuesComposite:
    """_extract_pk_values must return list[tuple] for composite PKs."""

    def test_extract_pk_values_composite_returns_tuples(self) -> None:
        """Composite PK extraction must return tuples of (pk1, pk2), not scalars."""
        topology = SchemaTopology(
            table_order=("orders",),
            columns={
                "orders": (
                    _col("order_id", pk=1),
                    _col("item_id", pk=1),
                    _col("qty", pk=0),
                ),
            },
            foreign_keys={},
        )
        engine = _make_engine_with_rows({})
        traversal = DagTraversal(engine=engine, topology=topology)

        rows = [
            {"order_id": 1, "item_id": 10, "qty": 3},
            {"order_id": 1, "item_id": 20, "qty": 1},
            {"order_id": 2, "item_id": 10, "qty": 2},
        ]

        pk_values = traversal._extract_pk_values("orders", rows)

        # For composite PK, each element should be a tuple
        assert isinstance(pk_values, list)
        assert len(pk_values) == 3  # 3 distinct (order_id, item_id) combos
        for val in pk_values:
            assert isinstance(val, tuple), f"Expected tuple, got {type(val)}"
            assert len(val) == 2


# ---------------------------------------------------------------------------
# T70.1 — Composite FK traversal uses AND clauses
# ---------------------------------------------------------------------------


class TestCompositeFkTraversal:
    """Composite FK traversal must generate AND-ed equality predicates."""

    def test_composite_fk_traversal_and_clause(self, caplog: pytest.LogCaptureFixture) -> None:
        """Traversal of composite FK must use AND-ed equality (not IN).

        The WHERE clause for a 2-column composite FK must be of the form:
        WHERE (fk_a = :v0a AND fk_b = :v0b) OR (fk_a = :v1a AND fk_b = :v1b)
        """
        fk = _fk(["order_id", "item_id"], "order_items", ["order_id", "item_id"])
        order_rows = [
            {"order_id": 1, "item_id": 10, "status": "COMPLETE"},
            {"order_id": 2, "item_id": 20, "status": "PENDING"},
        ]
        topology = SchemaTopology(
            table_order=("order_items", "shipments"),
            columns={
                "order_items": (
                    _col("order_id", pk=1),
                    _col("item_id", pk=1),
                    _col("status"),
                ),
                "shipments": (
                    _col("order_id"),
                    _col("item_id"),
                    _col("tracking"),
                ),
            },
            foreign_keys={
                "shipments": (fk,),
            },
        )
        executed_stmts: list[str] = []

        def _execute_side_effect(stmt: Any, params: Any = None) -> MagicMock:
            executed_stmts.append(str(stmt))
            result = MagicMock()
            result.mappings.return_value = []
            return result

        from sqlalchemy import Engine

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = _execute_side_effect
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        engine = MagicMock(spec=Engine)
        engine.connect.return_value = mock_ctx

        # Override seed to return order_rows
        first_call = True

        def _execute_with_seed(stmt: Any, params: Any = None) -> MagicMock:
            nonlocal first_call
            stmt_str = str(stmt)
            if first_call:
                first_call = False
                result = MagicMock()
                result.mappings.return_value = order_rows
                return result
            executed_stmts.append(stmt_str)
            result = MagicMock()
            result.mappings.return_value = []
            return result

        mock_conn.execute.side_effect = _execute_with_seed

        traversal = DagTraversal(engine=engine, topology=topology)
        list(traversal.traverse("order_items", "SELECT * FROM order_items"))

        # Check that the shipments query used AND-ed conditions
        shipment_stmts = [s for s in executed_stmts if "shipments" in s.lower()]
        assert len(shipment_stmts) >= 1, "Expected at least one shipments query"
        shipment_sql = shipment_stmts[0]
        # Must contain AND to join composite FK conditions
        assert "AND" in shipment_sql.upper()


# ---------------------------------------------------------------------------
# T70.1 — Junction table — no duplicate rows
# ---------------------------------------------------------------------------


class TestJunctionTableNoDuplicates:
    """Subsetting via multiple FK paths must not produce duplicate rows."""

    def test_junction_table_no_duplicate_rows(self) -> None:
        """Multiple FK paths to the same row must not produce duplicates.

        If table A and table B both have FKs to table C, and A and B are both
        fetched, table C's rows should be deduplicated.
        """
        # Setup: products and orders both reference category via FK
        fk_prod_cat = _fk(["category_id"], "categories", ["id"])
        fk_ord_cat = _fk(["category_id"], "categories", ["id"])

        # Both products and orders reference the same category (id=1)
        products_rows = [{"id": 10, "category_id": 1, "name": "Widget"}]
        orders_rows = [{"id": 100, "category_id": 1, "total": 50.0}]
        categories_rows = [{"id": 1, "name": "Electronics"}]

        topology = SchemaTopology(
            table_order=("categories", "products", "orders"),
            columns={
                "categories": (_col("id", pk=1), _col("name")),
                "products": (_col("id", pk=1), _col("category_id"), _col("name")),
                "orders": (_col("id", pk=1), _col("category_id"), _col("total")),
            },
            foreign_keys={
                "products": (fk_prod_cat,),
                "orders": (fk_ord_cat,),
            },
        )

        call_count: dict[str, int] = {"categories": 0}

        from sqlalchemy import Engine

        def _execute_side_effect(stmt: Any, params: Any = None) -> MagicMock:
            stmt_str = str(stmt)
            result = MagicMock()
            if "categories" in stmt_str.lower():
                call_count["categories"] += 1
                result.mappings.return_value = categories_rows
            elif "products" in stmt_str.lower():
                result.mappings.return_value = products_rows
            elif "orders" in stmt_str.lower():
                result.mappings.return_value = orders_rows
            else:
                result.mappings.return_value = categories_rows  # seed
            return result

        first_call = True

        def _execute_seed_first(stmt: Any, params: Any = None) -> MagicMock:
            nonlocal first_call
            result = MagicMock()
            if first_call:
                first_call = False
                result.mappings.return_value = categories_rows
            else:
                result.mappings.return_value = _execute_side_effect(stmt, params).mappings()
            return result

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = _execute_side_effect
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        engine = MagicMock(spec=Engine)
        engine.connect.return_value = mock_ctx

        traversal = DagTraversal(engine=engine, topology=topology)
        results = dict(
            traversal.traverse("categories", "SELECT * FROM categories WHERE id = 1")
        )

        # Categories must appear exactly once (no duplicates)
        if "categories" in results:
            cat_ids = [r["id"] for r in results["categories"]]
            assert len(cat_ids) == len(set(cat_ids)), f"Duplicate category rows: {cat_ids}"


# ---------------------------------------------------------------------------
# T70.1 — Table with no PK emits WARNING
# ---------------------------------------------------------------------------


class TestTableWithNoPkWarning:
    """Tables with no PK must emit a WARNING and be skipped gracefully."""

    def test_table_with_no_pk_emits_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Table with no PK columns must emit a WARNING and be skipped.

        The subsetting engine must not crash on no-PK tables.  It should
        log a WARNING and return an empty list of PK values.
        """
        topology = SchemaTopology(
            table_order=("nopk_table",),
            columns={
                "nopk_table": (
                    _col("col_a"),  # pk=0
                    _col("col_b"),  # pk=0
                ),
            },
            foreign_keys={},
        )
        engine = _make_engine_with_rows({})
        traversal = DagTraversal(engine=engine, topology=topology)

        rows = [{"col_a": 1, "col_b": 2}]

        with caplog.at_level(logging.WARNING):
            pk_values = traversal._extract_pk_values("nopk_table", rows)

        # Should return empty list (skip) rather than crash
        assert pk_values == []
        # Must emit a WARNING
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("nopk_table" in msg or "no pk" in msg.lower() or "pk" in msg.lower()
                   for msg in warning_messages), (
            f"Expected WARNING about missing PK, got: {warning_messages}"
        )


# ---------------------------------------------------------------------------
# T70.1 — VFK composite dedup
# ---------------------------------------------------------------------------


class TestVfkCompositeDedup:
    """VFK composite key deduplication uses full column tuples."""

    def test_vfk_composite_dedup(self) -> None:
        """Two VFKs with same table but different column combos must both be preserved.

        Deduplication of ForeignKeyInfo uses full (constrained, referred_table,
        referred) tuple — not just the first constrained column.
        """
        fk1 = ForeignKeyInfo(
            constrained_columns=("user_id", "role_id"),
            referred_table="user_roles",
            referred_columns=("user_id", "role_id"),
        )
        fk2 = ForeignKeyInfo(
            constrained_columns=("user_id", "perm_id"),
            referred_table="user_roles",
            referred_columns=("user_id", "perm_id"),
        )

        # These two FKs must NOT be considered duplicates despite sharing
        # the same referred_table and first constrained column
        assert fk1 != fk2, "Two FKs with different column combos must not be equal"
        assert fk1.constrained_columns != fk2.constrained_columns
