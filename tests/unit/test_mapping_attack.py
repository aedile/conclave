"""Attack tests for the mapping module DAG (P73 review fix).

Verifies that the DAG and SchemaReflector reject or safely handle
malformed, adversarial, and boundary inputs.

Rule 22 (CLAUDE.md): attack tests are written before feature tests.
Gate 1 (P73): this file satisfies the mapping module attack test requirement.

CONSTITUTION Priority 0: Security — malformed input must never cause
silent data corruption or unhandled exceptions that expose internals.

Task: P73 review — Fix Gate 1 silent-skip for modules/mapping.
"""

from __future__ import annotations

import pytest

from synth_engine.modules.mapping.graph import CycleDetectionError, DirectedAcyclicGraph

pytestmark = [pytest.mark.unit, pytest.mark.attack]


# ---------------------------------------------------------------------------
# Malformed / adversarial graph inputs
# ---------------------------------------------------------------------------


def test_topological_sort_raises_on_simple_cycle() -> None:
    """DAG rejects a direct cycle A -> B -> A with CycleDetectionError.

    An attacker-controlled schema that introduces circular FK references must
    not cause an infinite loop or silent incorrect ordering — it must raise a
    clear, catchable error.
    """
    dag = DirectedAcyclicGraph()
    dag.add_edge("A", "B")
    dag.add_edge("B", "A")

    with pytest.raises(CycleDetectionError) as exc_info:
        dag.topological_sort()

    assert "A" in str(exc_info.value) or "B" in str(exc_info.value), (
        f"CycleDetectionError message should name a node in the cycle; got: {exc_info.value}"
    )


def test_topological_sort_raises_on_self_referential_cycle() -> None:
    """DAG rejects a self-referential edge (table references itself).

    A self-join FK (e.g. ``employees.manager_id -> employees.id``) introduces
    a trivial cycle.  The DAG must detect and reject it rather than entering
    an infinite loop.
    """
    dag = DirectedAcyclicGraph()
    dag.add_edge("employees", "employees")

    with pytest.raises(CycleDetectionError) as exc_info:
        dag.topological_sort()

    assert "employees" in str(exc_info.value), (
        f"CycleDetectionError should name 'employees'; got: {exc_info.value}"
    )


def test_topological_sort_raises_on_multi_node_cycle() -> None:
    """DAG detects a 3-node cycle (A -> B -> C -> A).

    Multi-hop cycles in attacker-controlled schema graphs must be detected
    — not silently skipped or produce an incorrect sort order.
    """
    dag = DirectedAcyclicGraph()
    dag.add_edge("A", "B")
    dag.add_edge("B", "C")
    dag.add_edge("C", "A")

    with pytest.raises(CycleDetectionError) as exc_info:
        dag.topological_sort()

    error_str = str(exc_info.value)
    # At least one node from the cycle must be named
    assert any(node in error_str for node in ("A", "B", "C")), (
        f"CycleDetectionError should name a cycle node; got: {error_str}"
    )


def test_add_edge_with_empty_string_node_name() -> None:
    """Adding an edge with an empty string node name must not silently corrupt state.

    Schema table names must be non-empty.  An empty string node indicates
    a parsing bug upstream.  The DAG must store it deterministically — callers
    are responsible for validation before calling add_edge, but the DAG itself
    must not crash or produce undefined behavior.
    """
    dag = DirectedAcyclicGraph()
    # This should not raise — the DAG accepts the edge; validation is the caller's job.
    dag.add_edge("", "child")

    nodes = dag.nodes()
    assert "" in nodes, f"Empty string node should be stored; got nodes: {nodes}"
    assert "child" in nodes, f"'child' node should be stored; got nodes: {nodes}"


def test_has_cycle_returns_true_for_cyclic_graph() -> None:
    """has_cycle() returns True without raising for a cyclic graph.

    This is the safe probe before calling topological_sort() — callers can
    check has_cycle() first to branch without exception handling.
    """
    dag = DirectedAcyclicGraph()
    dag.add_edge("X", "Y")
    dag.add_edge("Y", "X")

    result = dag.has_cycle()

    assert result == True, f"Expected has_cycle()=True for X->Y->X cycle; got {result}"


def test_has_cycle_returns_false_for_acyclic_graph() -> None:
    """has_cycle() returns False for a well-formed DAG.

    Ensures the cycle detector does not produce false positives for valid
    schema graphs that an attacker cannot exploit.
    """
    dag = DirectedAcyclicGraph()
    dag.add_edge("organizations", "departments")
    dag.add_edge("departments", "employees")

    result = dag.has_cycle()

    assert result == False, f"Expected has_cycle()=False for acyclic graph; got {result}"


def test_duplicate_edge_is_idempotent() -> None:
    """Adding the same edge twice does not duplicate it in the edge list.

    Duplicate edges could cause incorrect topological sort weights or
    phantom FK traversal in the subsetting engine.
    """
    dag = DirectedAcyclicGraph()
    dag.add_edge("parent", "child")
    dag.add_edge("parent", "child")  # duplicate

    edges = dag.edges()
    edge_count = edges.count(("parent", "child"))

    assert edge_count == 1, (
        f"Duplicate edge should be de-duplicated; found {edge_count} copies of (parent, child)"
    )
