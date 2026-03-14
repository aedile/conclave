"""Unit tests for DagTraversal — topological DAG traversal engine.

All tests mock database connections; no live PostgreSQL required.

Task: P3-T3.4 -- Subsetting & Materialization Core
Security: All SQL uses parameterised text() queries — no f-string interpolation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from sqlalchemy import Engine

from synth_engine.modules.subsetting.traversal import DagTraversal
from synth_engine.shared.schema_topology import (
    ColumnInfo,
    ForeignKeyInfo,
    SchemaTopology,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _col(name: str, pk: int = 0) -> ColumnInfo:
    """Build a ColumnInfo for test fixtures.

    Args:
        name: Column name.
        pk: Primary key position (0 = not PK).

    Returns:
        A frozen ColumnInfo.
    """
    return ColumnInfo(name=name, type="INTEGER", primary_key=pk, nullable=False)


def _fk(constrained: list[str], referred_table: str, referred: list[str]) -> ForeignKeyInfo:
    """Build a ForeignKeyInfo for test fixtures.

    Args:
        constrained: Column names on the child (constrained) side.
        referred_table: The parent table name.
        referred: Column names on the parent (referred) side.

    Returns:
        A frozen ForeignKeyInfo.
    """
    return ForeignKeyInfo(
        constrained_columns=tuple(constrained),
        referred_table=referred_table,
        referred_columns=tuple(referred),
    )


def _make_engine() -> MagicMock:
    return MagicMock(spec=Engine)


def _make_conn_ctx(rows: list[dict[str, Any]]) -> MagicMock:
    """Build a mock connection context manager that returns a row result.

    Args:
        rows: Rows to return from execute().

    Returns:
        A MagicMock that behaves as ``engine.connect().__enter__()``.
    """
    mock_result = MagicMock()
    mock_result.mappings.return_value = [dict(r) for r in rows]

    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_result

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    return mock_ctx, mock_conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDagTraversalSingleTable:
    """DagTraversal with a single table and no FK relationships."""

    def test_traverse_single_table_no_fks(self) -> None:
        """traverse() yields only seed rows when no FK relationships exist."""
        topology = SchemaTopology(
            table_order=("departments",),
            columns={"departments": (_col("id", 1), _col("name"))},
            foreign_keys={"departments": ()},
        )
        engine = _make_engine()
        seed_rows = [{"id": 1, "name": "Engineering"}, {"id": 2, "name": "Sales"}]

        ctx, conn = _make_conn_ctx(seed_rows)
        engine.connect.return_value = ctx

        traversal = DagTraversal(engine=engine, topology=topology)
        results = list(traversal.traverse("departments", "SELECT * FROM departments LIMIT 2"))

        assert results == [("departments", seed_rows)]

    def test_traverse_empty_seed_result_yields_nothing(self) -> None:
        """traverse() yields nothing when seed query returns 0 rows."""
        topology = SchemaTopology(
            table_order=("departments",),
            columns={"departments": (_col("id", 1), _col("name"))},
            foreign_keys={"departments": ()},
        )
        engine = _make_engine()

        ctx, conn = _make_conn_ctx([])
        engine.connect.return_value = ctx

        traversal = DagTraversal(engine=engine, topology=topology)
        results = list(traversal.traverse("departments", "SELECT * FROM departments LIMIT 1"))

        assert results == []


class TestDagTraversalWithForeignKeys:
    """DagTraversal FK following — parent and child directions."""

    def test_traverse_follows_parent_fks(self) -> None:
        """traverse() fetches parent rows referenced by the seed table's FK columns.

        departments (parent) <- employees (seed, has FK dept_id -> departments.id)
        SubsettingEngine targets employees; traversal fetches departments too.
        Topology order: departments, employees (parents before children).
        """
        topology = SchemaTopology(
            table_order=("departments", "employees"),
            columns={
                "departments": (_col("id", 1), _col("name")),
                "employees": (_col("id", 1), _col("dept_id"), _col("name")),
            },
            foreign_keys={
                "departments": (),
                "employees": (_fk(["dept_id"], "departments", ["id"]),),
            },
        )
        engine = _make_engine()

        emp_rows = [{"id": 10, "dept_id": 5, "name": "Alice"}]
        dept_rows = [{"id": 5, "name": "Engineering"}]

        # Two connect() calls: seed (employees), then parent (departments)
        ctx_emp, conn_emp = _make_conn_ctx(emp_rows)
        ctx_dept, conn_dept = _make_conn_ctx(dept_rows)

        call_count = 0

        def connect_side_effect() -> MagicMock:
            nonlocal call_count
            call_count += 1
            # First call: seed query (employees)
            if call_count == 1:
                return ctx_emp
            # Subsequent: department lookup
            return ctx_dept

        engine.connect.side_effect = connect_side_effect

        traversal = DagTraversal(engine=engine, topology=topology)
        results = list(traversal.traverse("employees", "SELECT * FROM employees LIMIT 1"))

        # departments fetched (parent), employees is seed
        table_names = [t for t, _ in results]
        assert "departments" in table_names
        assert "employees" in table_names

    def test_traverse_follows_child_fks(self) -> None:
        """traverse() fetches child rows whose FK references seed table PKs.

        departments (seed) -> employees (child, FK dept_id -> departments.id)
        """
        topology = SchemaTopology(
            table_order=("departments", "employees"),
            columns={
                "departments": (_col("id", 1), _col("name")),
                "employees": (_col("id", 1), _col("dept_id"), _col("name")),
            },
            foreign_keys={
                "departments": (),
                "employees": (_fk(["dept_id"], "departments", ["id"]),),
            },
        )
        engine = _make_engine()

        dept_rows = [{"id": 1, "name": "Engineering"}]
        emp_rows = [
            {"id": 10, "dept_id": 1, "name": "Alice"},
            {"id": 11, "dept_id": 1, "name": "Bob"},
        ]

        call_count = 0

        def connect_side_effect() -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                ctx, _ = _make_conn_ctx(dept_rows)
                return ctx
            ctx, _ = _make_conn_ctx(emp_rows)
            return ctx

        engine.connect.side_effect = connect_side_effect

        traversal = DagTraversal(engine=engine, topology=topology)
        results = list(traversal.traverse("departments", "SELECT * FROM departments LIMIT 1"))

        table_names = [t for t, _ in results]
        assert "departments" in table_names
        assert "employees" in table_names

        # employees rows should be the child rows
        emp_result = next(rows for t, rows in results if t == "employees")
        assert len(emp_result) == 2

    def test_traverse_respects_topological_order(self) -> None:
        """traverse() yields results in topology order (parents before children)."""
        topology = SchemaTopology(
            table_order=("departments", "employees", "salaries"),
            columns={
                "departments": (_col("id", 1),),
                "employees": (_col("id", 1), _col("dept_id")),
                "salaries": (_col("id", 1), _col("employee_id")),
            },
            foreign_keys={
                "departments": (),
                "employees": (_fk(["dept_id"], "departments", ["id"]),),
                "salaries": (_fk(["employee_id"], "employees", ["id"]),),
            },
        )
        engine = _make_engine()

        dept_rows = [{"id": 1}]
        emp_rows = [{"id": 10, "dept_id": 1}]
        salary_rows = [{"id": 100, "employee_id": 10}]

        row_map = {
            0: dept_rows,
            1: emp_rows,
            2: salary_rows,
        }
        call_count = 0

        def connect_side_effect() -> MagicMock:
            nonlocal call_count
            rows = row_map.get(call_count, [])
            call_count += 1
            ctx, _ = _make_conn_ctx(rows)
            return ctx

        engine.connect.side_effect = connect_side_effect

        traversal = DagTraversal(engine=engine, topology=topology)
        results = list(traversal.traverse("departments", "SELECT * FROM departments LIMIT 1"))

        table_names = [t for t, _ in results]
        # Verify topological order: each parent appears before its child
        assert table_names.index("departments") < table_names.index("employees")
        assert table_names.index("employees") < table_names.index("salaries")


class TestDagTraversalEdgeCases:
    """DagTraversal edge cases — branch guards and NULL FK filtering."""

    def test_traverse_skips_table_when_no_pk_in_topology(self) -> None:
        """Table with no PK defined in topology yields its rows but FK linking is skipped.

        When ``_extract_pk_values`` finds no PK columns for a table, it returns
        an empty list and the child-direction FK fetch is bypassed (the
        ``if not parent_pk_values: continue`` branch on line ~179).

        Scenario: departments has rows but no PK column declared; employees has
        an FK to departments.  departments' rows are yielded from seed, but the
        FK-following query for employees is never issued because parent PK
        extraction returns empty.
        """
        topology = SchemaTopology(
            table_order=("departments", "employees"),
            columns={
                # departments has NO primary_key column (pk=0 for all)
                "departments": (_col("id"), _col("name")),
                "employees": (_col("id", 1), _col("dept_id"), _col("name")),
            },
            foreign_keys={
                "departments": (),
                "employees": (_fk(["dept_id"], "departments", ["id"]),),
            },
        )
        engine = _make_engine()

        dept_rows = [{"id": 1, "name": "Engineering"}]

        # Only the seed query is executed; no FK follow-up should occur.
        ctx, conn = _make_conn_ctx(dept_rows)
        engine.connect.return_value = ctx

        traversal = DagTraversal(engine=engine, topology=topology)
        results = list(traversal.traverse("departments", "SELECT * FROM departments LIMIT 1"))

        # departments rows are yielded (seed table always yields)
        table_names = [t for t, _ in results]
        assert "departments" in table_names

        # employees must NOT be in results — FK linking was skipped (no PK values)
        assert "employees" not in table_names

        # connect() was only called once (seed query only — no FK follow-up)
        assert engine.connect.call_count == 1

    def test_traverse_filters_null_fk_values(self) -> None:
        """Child rows where the FK column value is NULL are excluded from FK linking.

        The parent-direction branch filters with ``row.get(child_fk_col) is not None``
        (line ~201).  Rows where dept_id IS NULL must not contribute to the set
        of parent PK values used to fetch departments rows.

        Scenario: employees (seed) has two rows; one has dept_id=5, the other
        has dept_id=None.  Only dept_id=5 should be used to fetch departments.
        """
        topology = SchemaTopology(
            table_order=("departments", "employees"),
            columns={
                "departments": (_col("id", 1), _col("name")),
                "employees": (_col("id", 1), _col("dept_id"), _col("name")),
            },
            foreign_keys={
                "departments": (),
                "employees": (_fk(["dept_id"], "departments", ["id"]),),
            },
        )
        engine = _make_engine()

        # One employee has dept_id=None (unassigned), one has dept_id=5
        emp_rows = [
            {"id": 10, "dept_id": None, "name": "Contractor"},
            {"id": 11, "dept_id": 5, "name": "Alice"},
        ]
        dept_rows = [{"id": 5, "name": "Engineering"}]

        ctx_emp, _ = _make_conn_ctx(emp_rows)
        ctx_dept, _ = _make_conn_ctx(dept_rows)

        call_count = 0

        def connect_side_effect() -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ctx_emp
            return ctx_dept

        engine.connect.side_effect = connect_side_effect

        traversal = DagTraversal(engine=engine, topology=topology)
        results = list(traversal.traverse("employees", "SELECT * FROM employees LIMIT 2"))

        table_names = [t for t, _ in results]

        # employees seed rows are always yielded
        assert "employees" in table_names

        # departments should still be fetched (dept_id=5 is non-null and valid)
        assert "departments" in table_names

        # The departments result must only contain the row for id=5
        dept_result = next(rows for t, rows in results if t == "departments")
        assert len(dept_result) == 1
        assert dept_result[0]["id"] == 5

    def test_traverse_handles_parent_not_yet_fetched(self) -> None:
        """Child table processed before its parent in iteration hits the ``continue`` branch.

        In the child-direction FK check (line ~173), if a table's FK references
        a parent that is not yet in ``fetched``, the traversal continues to the
        next FK without fetching anything.

        Scenario: topology order is (projects, employees) — projects comes first
        but employees is the seed table.  When traversal processes 'projects' it
        has an FK pointing to employees, but employees is not yet in ``fetched``
        at that point.  The continue branch is taken; no DB call is made for
        projects during the initial pass through the FK list.  The parent-direction
        check then correctly picks up employees -> projects relationship and fetches
        projects rows.
        """
        topology = SchemaTopology(
            table_order=("projects", "employees"),
            columns={
                "projects": (_col("id", 1), _col("lead_id"), _col("name")),
                "employees": (_col("id", 1), _col("name")),
            },
            foreign_keys={
                "projects": (_fk(["lead_id"], "employees", ["id"]),),
                "employees": (),
            },
        )
        engine = _make_engine()

        emp_rows = [{"id": 10, "name": "Alice"}]
        project_rows = [{"id": 100, "lead_id": 10, "name": "Alpha"}]

        ctx_emp, _ = _make_conn_ctx(emp_rows)
        ctx_projects, _ = _make_conn_ctx(project_rows)

        call_count = 0

        def connect_side_effect() -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ctx_emp
            return ctx_projects

        engine.connect.side_effect = connect_side_effect

        traversal = DagTraversal(engine=engine, topology=topology)
        # employees is seed but comes second in table_order
        results = list(traversal.traverse("employees", "SELECT * FROM employees LIMIT 1"))

        table_names = [t for t, _ in results]

        # Both tables should appear — no exception from the continue branch
        assert "employees" in table_names
        assert "projects" in table_names

        # projects rows were fetched via the parent-direction branch
        proj_result = next(rows for t, rows in results if t == "projects")
        assert len(proj_result) == 1
        assert proj_result[0]["id"] == 100
