"""Unit tests for the DirectedAcyclicGraph and topological sort.

Tests cover:
- Empty graph, single-node, linear chain, complex hierarchy sorting
- Cycle detection (simple, complex 5-table, self-referential)
- CycleDetectionError structure
- Implicit node creation via add_edge
- has_cycle predicate
- add_edge idempotency (duplicate edge handling)

Task: P3-T3.2 — Relational Mapping & Topological Sort
"""

from __future__ import annotations

import pytest

from synth_engine.modules.ingestion.graph import CycleDetectionError, DirectedAcyclicGraph


class TestEmptyAndSingleNode:
    """Tests for empty and minimal graph states."""

    def test_empty_graph_topological_sort_returns_empty(self) -> None:
        """Topological sort on an empty graph returns an empty list."""
        dag: DirectedAcyclicGraph = DirectedAcyclicGraph()
        assert dag.topological_sort() == []

    def test_single_node_sort(self) -> None:
        """A graph with one node and no edges sorts to that single node."""
        dag = DirectedAcyclicGraph()
        dag.add_node("users")
        result = dag.topological_sort()
        assert result == ["users"]

    def test_nodes_returns_correct_set(self) -> None:
        """nodes() reflects all added nodes."""
        dag = DirectedAcyclicGraph()
        dag.add_node("a")
        dag.add_node("b")
        assert dag.nodes() == {"a", "b"}

    def test_edges_returns_correct_list(self) -> None:
        """edges() reflects all added edges as (parent, child) tuples."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("a", "b")
        assert ("a", "b") in dag.edges()


class TestLinearAndSimpleSort:
    """Tests for linear chains and simple topological orderings."""

    def test_linear_chain_sort(self) -> None:
        """A->B->C returns tables in dependency order: [A, B, C]."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("A", "B")
        dag.add_edge("B", "C")
        result = dag.topological_sort()
        # A before B, B before C
        assert result.index("A") < result.index("B")
        assert result.index("B") < result.index("C")

    def test_add_edge_creates_nodes_implicitly(self) -> None:
        """Adding an edge for nodes not yet added registers them as nodes."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("parent", "child")
        assert "parent" in dag.nodes()
        assert "child" in dag.nodes()

    def test_two_independent_nodes_sort(self) -> None:
        """Two nodes with no edges between them both appear in result."""
        dag = DirectedAcyclicGraph()
        dag.add_node("table_a")
        dag.add_node("table_b")
        result = dag.topological_sort()
        assert set(result) == {"table_a", "table_b"}
        assert len(result) == 2


class TestComplexHierarchySort:
    """Tests for multi-table hierarchy with diamond dependencies."""

    def test_complex_5_table_hierarchy_sort(self) -> None:
        """5-table diamond + extra node returns valid topological order.

        Schema:
            organizations (root)
            departments -> organizations
            users -> organizations
            projects -> departments
            projects -> users   (diamond via both departments and users)
            audit_log (independent)

        Valid orderings must satisfy:
            organizations before departments
            organizations before users
            departments before projects
            users before projects
        """
        dag = DirectedAcyclicGraph()
        dag.add_edge("organizations", "departments")
        dag.add_edge("organizations", "users")
        dag.add_edge("departments", "projects")
        dag.add_edge("users", "projects")
        dag.add_node("audit_log")

        result = dag.topological_sort()
        assert set(result) == {"organizations", "departments", "users", "projects", "audit_log"}
        assert result.index("organizations") < result.index("departments")
        assert result.index("organizations") < result.index("users")
        assert result.index("departments") < result.index("projects")
        assert result.index("users") < result.index("projects")

    def test_parent_always_precedes_child(self) -> None:
        """In a three-level hierarchy, all parents precede their children."""
        dag = DirectedAcyclicGraph()
        # root -> level1_a, root -> level1_b, level1_a -> leaf, level1_b -> leaf
        dag.add_edge("root", "level1_a")
        dag.add_edge("root", "level1_b")
        dag.add_edge("level1_a", "leaf")
        dag.add_edge("level1_b", "leaf")

        result = dag.topological_sort()
        assert result.index("root") < result.index("level1_a")
        assert result.index("root") < result.index("level1_b")
        assert result.index("level1_a") < result.index("leaf")
        assert result.index("level1_b") < result.index("leaf")


class TestCycleDetection:
    """Tests for cycle detection in various configurations."""

    def test_cycle_detection_simple(self) -> None:
        """A->B->A raises CycleDetectionError."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("A", "B")
        dag.add_edge("B", "A")
        with pytest.raises(CycleDetectionError):
            dag.topological_sort()

    def test_cycle_detection_self_referential(self) -> None:
        """A self-referential edge A->A raises CycleDetectionError."""
        dag = DirectedAcyclicGraph()
        dag.add_node("employees")
        dag.add_edge("employees", "employees")
        with pytest.raises(CycleDetectionError):
            dag.topological_sort()

    def test_cycle_detection_complex(self) -> None:
        """5-table hierarchy with injected cycle raises CycleDetectionError.

        Valid hierarchy:
            organizations -> departments -> employees -> salaries
            employees -> timesheets
        Injected cycle: timesheets -> organizations (circular)
        """
        dag = DirectedAcyclicGraph()
        dag.add_edge("organizations", "departments")
        dag.add_edge("departments", "employees")
        dag.add_edge("employees", "salaries")
        dag.add_edge("employees", "timesheets")
        # Inject cycle: timesheets points back to organizations
        dag.add_edge("timesheets", "organizations")

        with pytest.raises(CycleDetectionError):
            dag.topological_sort()

    def test_cycle_detection_error_contains_cycle_info(self) -> None:
        """CycleDetectionError exposes the cycle sequence via .cycle attribute."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("A", "B")
        dag.add_edge("B", "C")
        dag.add_edge("C", "A")

        with pytest.raises(CycleDetectionError) as exc_info:
            dag.topological_sort()

        error = exc_info.value
        assert hasattr(error, "cycle")
        assert isinstance(error.cycle, list)
        # The cycle must contain at least two nodes and mention known tables
        assert len(error.cycle) >= 2
        cycle_set = set(error.cycle)
        assert cycle_set.issubset({"A", "B", "C"})

    def test_cycle_detection_error_message_is_informative(self) -> None:
        """CycleDetectionError message names the cycle tables."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("table_x", "table_y")
        dag.add_edge("table_y", "table_x")

        with pytest.raises(CycleDetectionError) as exc_info:
            dag.topological_sort()

        assert "table_x" in str(exc_info.value) or "table_y" in str(exc_info.value)

    def test_has_cycle_returns_true_for_cycle(self) -> None:
        """has_cycle() returns True when a cycle is present."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("X", "Y")
        dag.add_edge("Y", "X")
        assert dag.has_cycle() is True

    def test_has_cycle_returns_false_for_dag(self) -> None:
        """has_cycle() returns False for a valid DAG."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("root", "child")
        dag.add_edge("child", "leaf")
        assert dag.has_cycle() is False

    def test_has_cycle_returns_false_for_empty_graph(self) -> None:
        """has_cycle() returns False for an empty graph."""
        dag = DirectedAcyclicGraph()
        assert dag.has_cycle() is False

    def test_cycle_detection_three_node_loop(self) -> None:
        """A->B->C->A raises CycleDetectionError with all three nodes in cycle."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("A", "B")
        dag.add_edge("B", "C")
        dag.add_edge("C", "A")

        with pytest.raises(CycleDetectionError) as exc_info:
            dag.topological_sort()

        # The cycle should reference the three participating nodes
        cycle_nodes = set(exc_info.value.cycle)
        assert len(cycle_nodes) >= 2


class TestAddEdgeIdempotency:
    """Tests for add_edge() idempotency contract.

    Verifies that calling add_edge() with the same (parent, child) pair more than
    once does not produce duplicate edges or corrupt the topological sort in-degree
    computation. This contract is critical for SchemaReflector.reflect() callers
    where composite or redundant FK constraints can produce the same logical
    relationship more than once (e.g., created_by and updated_by both referencing
    the users table).
    """

    def test_add_edge_is_idempotent_edges_list(self) -> None:
        """Adding the same edge twice yields exactly one edge in edges()."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("A", "B")
        dag.add_edge("A", "B")  # duplicate
        assert dag.edges() == [("A", "B")]

    def test_add_edge_is_idempotent_sort_correctness(self) -> None:
        """Duplicate add_edge calls do not corrupt topological sort ordering."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("A", "B")
        dag.add_edge("A", "B")  # duplicate must not double-count in-degree
        result = dag.topological_sort()
        assert result.index("A") < result.index("B")

    def test_add_edge_idempotent_multiple_fks_same_parent(self) -> None:
        """Simulate composite FKs where two columns both reference the same parent table.

        Real-world example: an orders table with created_by and updated_by columns
        that both hold a FK to users. SchemaReflector.reflect() will call
        add_edge("users", "orders") once per FK constraint — this must be deduplicated.
        """
        dag = DirectedAcyclicGraph()
        dag.add_edge("users", "orders")  # created_by FK
        dag.add_edge("users", "orders")  # updated_by FK (same parent/child — duplicate)
        assert dag.edges().count(("users", "orders")) == 1
        result = dag.topological_sort()
        assert result.index("users") < result.index("orders")

    def test_add_edge_idempotent_node_count_unchanged(self) -> None:
        """Duplicate add_edge does not create extra nodes."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("parent", "child")
        dag.add_edge("parent", "child")  # duplicate
        assert dag.nodes() == {"parent", "child"}

    def test_add_edge_distinct_edges_still_recorded(self) -> None:
        """Non-duplicate edges are all recorded; only exact duplicates are skipped."""
        dag = DirectedAcyclicGraph()
        dag.add_edge("A", "B")
        dag.add_edge("A", "C")  # different child — not a duplicate
        dag.add_edge("A", "B")  # duplicate of first
        assert len(dag.edges()) == 2
        assert ("A", "B") in dag.edges()
        assert ("A", "C") in dag.edges()
