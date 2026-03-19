"""Directed Acyclic Graph (DAG) for database schema foreign-key relationships.

Provides:
- :class:`CycleDetectionError`: raised when a circular dependency is found.
- :class:`DirectedAcyclicGraph`: FK-relationship graph with Kahn's Algorithm
  topological sort and DFS-based cycle reporting.

Architecture note
-----------------
This module imports only from the Python standard library and from
``synth_engine.shared``.  Cross-module imports (between modules/) are
forbidden by import-linter contracts in pyproject.toml.

ADR-0013: Relational Mapping DAG and Topological Sort Design.
CONSTITUTION Priority 0: Security -- no external calls, no PII exposure.
Task: P3-T3.2 -- Relational Mapping & Topological Sort
Task: P34-T34.2 -- Consolidate module-local exceptions into shared hierarchy
"""

from __future__ import annotations

from collections import deque

from synth_engine.shared.exceptions import SynthEngineError


class CycleDetectionError(SynthEngineError):
    """Raised when a circular dependency is detected in the schema graph.

    The ``cycle`` attribute holds the sequence of table names forming the
    detected cycle, ordered so that ``cycle[i]`` has an edge to ``cycle[i+1]``
    and the last node has an edge back to a node earlier in the sequence.

    Inherits :exc:`synth_engine.shared.exceptions.SynthEngineError` so that
    the middleware layer can catch all engine errors uniformly.

    Args:
        cycle: Ordered list of table names that form the cycle.
    """

    def __init__(self, cycle: list[str]) -> None:
        self.cycle: list[str] = cycle
        cycle_repr = " -> ".join(cycle)
        super().__init__(
            f"Circular dependency detected in schema graph: {cycle_repr}. "
            "Provide explicit cycle-breaking rules before ingestion can proceed."
        )


class DirectedAcyclicGraph:
    """DAG representation of a database schema's foreign-key relationships.

    Each node is a table name.  A directed edge ``parent -> child`` means
    the child table holds a foreign key referencing the parent table.
    Topological sort (Kahn's Algorithm) returns tables in dependency order
    so that parent tables are always processed before their children.

    Example::

        dag = DirectedAcyclicGraph()
        dag.add_edge("organizations", "departments")
        dag.add_edge("departments", "employees")
        order = dag.topological_sort()
        # -> ["organizations", "departments", "employees"]

    """

    def __init__(self) -> None:
        self._nodes: set[str] = set()
        # Adjacency list: parent -> list[child]
        self._adjacency: dict[str, list[str]] = {}
        # Edge set for O(1) duplicate detection; list for insertion-order introspection
        self._edge_set: set[tuple[str, str]] = set()
        self._edges: list[tuple[str, str]] = []

    def add_node(self, table: str) -> None:
        """Register a table as a node in the graph.

        Idempotent -- calling with an existing name is a no-op.

        Args:
            table: Table name to register.
        """
        self._nodes.add(table)
        if table not in self._adjacency:
            self._adjacency[table] = []

    def add_edge(self, parent: str, child: str) -> None:
        """Add a directed edge: parent -> child (child holds FK to parent).

        Idempotent -- calling with an already-present (parent, child) pair is a
        no-op.  This matches :meth:`add_node`'s idempotency contract and prevents
        duplicate edges when :class:`SchemaReflector` reflects schemas with composite
        or redundant FK constraints (e.g., ``created_by`` and ``updated_by`` columns
        that both reference the same parent table).

        Implicitly creates both nodes if they do not already exist.

        Args:
            parent: The referenced (parent) table name.
            child: The referencing (child) table name.
        """
        self.add_node(parent)
        self.add_node(child)
        edge = (parent, child)
        if edge in self._edge_set:
            return
        self._edge_set.add(edge)
        self._adjacency[parent].append(child)
        self._edges.append(edge)

    def nodes(self) -> set[str]:
        """Return the set of all registered table names.

        Returns:
            A set of table name strings.
        """
        return set(self._nodes)

    def edges(self) -> list[tuple[str, str]]:
        """Return all edges as (parent, child) tuples.

        Returns:
            A list of ``(parent, child)`` tuples in insertion order.
            Each unique edge appears exactly once; duplicate :meth:`add_edge`
            calls for the same pair are silently deduplicated.
        """
        return list(self._edges)

    def topological_sort(self) -> list[str]:
        """Return tables in dependency order using Kahn's Algorithm.

        Parent tables always appear before their children.  If the graph is
        empty, an empty list is returned.

        Returns:
            Ordered list of table names; parents before children.

        Raises:
            CycleDetectionError: If a cycle is detected. The exception's
                ``cycle`` attribute contains the sequence of nodes forming
                the cycle.
        """
        # Build in-degree count for each node.
        in_degree: dict[str, int] = dict.fromkeys(self._nodes, 0)
        for _parent, child in self._edges:
            in_degree[child] += 1

        # Initialise queue with all zero-in-degree nodes, sorted for
        # deterministic output across Python runs.
        queue: deque[str] = deque(sorted(n for n in self._nodes if in_degree[n] == 0))
        result: list[str] = []

        while queue:
            node = queue.popleft()
            result.append(node)
            for child in sorted(self._adjacency.get(node, [])):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(result) != len(self._nodes):
            cycle = self._find_cycle()
            raise CycleDetectionError(cycle)

        return result

    def has_cycle(self) -> bool:
        """Return True if the graph contains at least one cycle.

        Calls :meth:`topological_sort` and returns ``False`` if no cycle is
        detected, or ``True`` if :exc:`CycleDetectionError` is raised.  This
        method does NOT use DFS directly; it relies on Kahn's Algorithm via
        :meth:`topological_sort`.

        Returns:
            ``True`` if a cycle exists; ``False`` if the graph is a valid DAG.
        """
        try:
            self.topological_sort()
            return False
        except CycleDetectionError:
            return True

    def _find_cycle(self) -> list[str]:
        """Identify a cycle in the graph via DFS with a recursion stack.

        Performs a depth-first search tracking both visited nodes and the
        current recursion stack.  When a back-edge is found (a node on the
        stack is encountered again), the cycle is extracted from the stack.

        This method is only called when Kahn's Algorithm has confirmed that
        a cycle exists (i.e., not all nodes were processed).  The DFS is
        therefore guaranteed to find a back-edge.

        Returns:
            Ordered list of node names forming the cycle. The sequence
            starts at the re-entry point and ends at the node that closes
            the cycle.

        Raises:
            AssertionError: If called when no cycle exists -- indicates an
                internal invariant violation in the caller (Kahn's residual
                check produced a wrong result).
        """
        visited: set[str] = set()
        rec_stack: set[str] = set()
        # Ordered stack for path reconstruction
        path: list[str] = []

        def dfs(node: str) -> list[str] | None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbour in self._adjacency.get(node, []):
                if neighbour not in visited:
                    result = dfs(neighbour)
                    if result is not None:
                        return result
                elif neighbour in rec_stack:
                    # Found back-edge: extract cycle from path
                    cycle_start = path.index(neighbour)
                    return path[cycle_start:]

            path.pop()
            rec_stack.discard(node)
            return None

        for start in sorted(self._nodes):
            if start not in visited:
                found = dfs(start)
                if found is not None:
                    return found

        # Unreachable when called correctly: _find_cycle() is only invoked after
        # Kahn's Algorithm confirms a cycle exists, so DFS must always find a
        # back-edge above.  If reached, the caller's invariant is broken.
        raise AssertionError(
            "_find_cycle called when no cycle exists -- "
            "Kahn's residual check produced a wrong result"
        )
