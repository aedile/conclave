# HISTORICAL — DO NOT USE: Pre-production spike. See docs/archive/spikes/findings_spike_*.md for conclusions.
"""Spike C — Topological Graphing & Memory-Safe Traversal.

Proves three capabilities for the Phase 3 subsetting engine:
  (a) FK graph inference from a live SQLite database via PRAGMA introspection.
  (b) Recursive CTE generation for seed-anchored relational subset traversal.
  (c) Row streaming with flat (non-spiking) memory consumption proven via tracemalloc.

Usage:
    python spikes/spike_topological_subset.py

All dependencies are stdlib-only: sqlite3, tracemalloc, collections, dataclasses, typing.
"""

import collections
import sqlite3
import tracemalloc
from collections.abc import Iterator
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants — volumes for the seeded Sakila-like schema
# ---------------------------------------------------------------------------
NUM_CUSTOMERS: int = 10_000
NUM_ADDRESSES: int = 10_000
NUM_FILMS: int = 1_000
NUM_INVENTORY: int = 5_000
NUM_RENTALS: int = 100_000
NUM_PAYMENTS: int = 150_000

SEED_CUSTOMER_IDS: list[int] = [1, 50, 100]

# Memory ceiling: peak during streaming must be < this multiple of post-seed peak.
STREAMING_MEMORY_MULTIPLIER: float = 2.0


# ---------------------------------------------------------------------------
# Schema — DDL for the Sakila-like in-memory database
# ---------------------------------------------------------------------------
SCHEMA_DDL: str = """
CREATE TABLE customer (
    customer_id  INTEGER PRIMARY KEY,
    first_name   TEXT NOT NULL,
    last_name    TEXT NOT NULL,
    email        TEXT NOT NULL
);
CREATE TABLE address (
    address_id   INTEGER PRIMARY KEY,
    address      TEXT NOT NULL,
    city         TEXT NOT NULL,
    customer_id  INTEGER REFERENCES customer(customer_id)
);
CREATE TABLE film (
    film_id       INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,
    release_year  INTEGER NOT NULL
);
CREATE TABLE inventory (
    inventory_id  INTEGER PRIMARY KEY,
    film_id       INTEGER REFERENCES film(film_id)
);
CREATE TABLE rental (
    rental_id     INTEGER PRIMARY KEY,
    customer_id   INTEGER REFERENCES customer(customer_id),
    inventory_id  INTEGER REFERENCES inventory(inventory_id),
    rental_date   TEXT NOT NULL
);
CREATE TABLE payment (
    payment_id    INTEGER PRIMARY KEY,
    customer_id   INTEGER REFERENCES customer(customer_id),
    rental_id     INTEGER REFERENCES rental(rental_id),
    amount        REAL NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ForeignKey:
    """Represents a single FK relationship between two tables.

    Attributes:
        from_table: The child table that holds the FK column.
        from_column: The column in the child table.
        to_table: The parent table being referenced.
        to_column: The column in the parent table (usually PK).
    """

    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class MemoryCheckpoint:
    """A single tracemalloc snapshot with a human-readable label.

    Attributes:
        label: Short description of the phase (e.g. "after_seed").
        current_bytes: Bytes allocated at snapshot time.
        peak_bytes: Peak bytes since tracemalloc.start() was called.
    """

    label: str
    current_bytes: int
    peak_bytes: int


# ---------------------------------------------------------------------------
# Step 1: Database builder
# ---------------------------------------------------------------------------


def _build_schema(conn: sqlite3.Connection) -> None:
    """Execute the DDL statements that create all six tables.

    Args:
        conn: Open SQLite connection (in-memory).
    """
    conn.executescript(SCHEMA_DDL)


def _seed_customers(conn: sqlite3.Connection) -> None:
    """Insert NUM_CUSTOMERS fictional customer rows.

    Names are deterministically generated from the row index so that
    the data is reproducible and contains no real PII.

    Args:
        conn: Open SQLite connection.
    """
    rows = [
        (i, f"First{i}", f"Last{i}", f"user{i}@fictional.invalid")
        for i in range(1, NUM_CUSTOMERS + 1)
    ]
    conn.executemany(
        "INSERT INTO customer (customer_id, first_name, last_name, email) VALUES (?,?,?,?)",
        rows,
    )


def _seed_addresses(conn: sqlite3.Connection) -> None:
    """Insert NUM_ADDRESSES fictional address rows, one per customer.

    Args:
        conn: Open SQLite connection.
    """
    rows = [(i, f"{i} Fictional St", f"City{i % 100}", i) for i in range(1, NUM_ADDRESSES + 1)]
    conn.executemany(
        "INSERT INTO address (address_id, address, city, customer_id) VALUES (?,?,?,?)",
        rows,
    )


def _seed_films(conn: sqlite3.Connection) -> None:
    """Insert NUM_FILMS fictional film rows.

    Args:
        conn: Open SQLite connection.
    """
    rows = [(i, f"Film Title {i}", 2000 + (i % 25)) for i in range(1, NUM_FILMS + 1)]
    conn.executemany(
        "INSERT INTO film (film_id, title, release_year) VALUES (?,?,?)",
        rows,
    )


def _seed_inventory(conn: sqlite3.Connection) -> None:
    """Insert NUM_INVENTORY fictional inventory rows referencing films.

    Args:
        conn: Open SQLite connection.
    """
    rows = [(i, 1 + (i % NUM_FILMS)) for i in range(1, NUM_INVENTORY + 1)]
    conn.executemany(
        "INSERT INTO inventory (inventory_id, film_id) VALUES (?,?)",
        rows,
    )


def _seed_rentals(conn: sqlite3.Connection) -> None:
    """Insert NUM_RENTALS fictional rental rows.

    Each rental belongs to customer ``1 + (i % NUM_CUSTOMERS)`` so that
    customers are evenly distributed across rentals.

    Args:
        conn: Open SQLite connection.
    """
    rows = [
        (
            i,
            1 + (i % NUM_CUSTOMERS),
            1 + (i % NUM_INVENTORY),
            f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
        )
        for i in range(1, NUM_RENTALS + 1)
    ]
    conn.executemany(
        "INSERT INTO rental (rental_id, customer_id, inventory_id, rental_date) VALUES (?,?,?,?)",
        rows,
    )


def _seed_payments(conn: sqlite3.Connection) -> None:
    """Insert NUM_PAYMENTS fictional payment rows.

    Each payment's ``customer_id`` is derived from the rental it references
    (``rental_id = 1 + (i % NUM_RENTALS)``), mirroring the denormalised
    customer link that exists in a realistic Sakila-style schema.  This
    ensures the seed data is internally consistent so that FK-chain subset
    queries return the expected rows.

    Args:
        conn: Open SQLite connection.
    """
    rows = []
    for i in range(1, NUM_PAYMENTS + 1):
        rental_id = 1 + (i % NUM_RENTALS)
        # Derive customer_id from the rental's customer assignment formula:
        # rental r has customer_id = 1 + (r % NUM_CUSTOMERS)
        customer_id = 1 + (rental_id % NUM_CUSTOMERS)
        amount = round(1.99 + (i % 20) * 0.50, 2)
        rows.append((i, customer_id, rental_id, amount))
    conn.executemany(
        "INSERT INTO payment (payment_id, customer_id, rental_id, amount) VALUES (?,?,?,?)",
        rows,
    )


def build_and_seed_database() -> sqlite3.Connection:
    """Create an in-memory SQLite database, define schema, and seed all tables.

    Returns:
        An open sqlite3.Connection with the row factory set to sqlite3.Row.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    _build_schema(conn)
    _seed_customers(conn)
    _seed_addresses(conn)
    _seed_films(conn)
    _seed_inventory(conn)
    _seed_rentals(conn)
    _seed_payments(conn)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Step 2: FK graph inference
# ---------------------------------------------------------------------------


class SchemaInspector:
    """Infers FK relationships from SQLite PRAGMA foreign_key_list.

    Reads the live database metadata without touching application data,
    so it is safe to use against production read-replicas.

    Args:
        conn: Open sqlite3.Connection with any row factory.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Initialise the inspector with a live database connection.

        Args:
            conn: The SQLite connection to inspect.
        """
        self._conn = conn

    def get_tables(self) -> list[str]:
        """Return all user-defined table names in the database.

        Returns:
            Sorted list of table name strings.
        """
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [row[0] for row in cursor]

    def get_foreign_keys(self) -> list[ForeignKey]:
        """Enumerate every FK relationship in the schema.

        Uses ``PRAGMA foreign_key_list(<table>)`` for each discovered table.
        Table name is sourced from sqlite_master metadata, not user input.

        Returns:
            List of ForeignKey dataclass instances, one per FK column.
        """
        fks: list[ForeignKey] = []
        for table in self.get_tables():
            cursor = self._conn.execute(f"PRAGMA foreign_key_list({table})")  # nosec B608
            for row in cursor:
                fks.append(
                    ForeignKey(
                        from_table=table,
                        from_column=row[3],
                        to_table=row[2],
                        to_column=row[4],
                    )
                )
        return fks

    def build_dependency_graph(self) -> dict[str, list[ForeignKey]]:
        """Build an adjacency list keyed by the child (from) table.

        Returns:
            Mapping of ``{child_table: [ForeignKey, ...]}`` for all FK edges.
        """
        graph: dict[str, list[ForeignKey]] = collections.defaultdict(list)
        for fk in self.get_foreign_keys():
            graph[fk.from_table].append(fk)
        return dict(graph)

    def topological_sort(self) -> list[str]:
        """Return tables in dependency order using Kahn's algorithm.

        Parent tables appear before their child tables, making the ordering
        safe for INSERT operations. Tables with no FK relationships are
        included at the front of the result.

        Returns:
            List of all table names in topological (parent-first) order.

        Raises:
            ValueError: If the FK graph contains a cycle.
        """
        tables = self.get_tables()
        fks = self.get_foreign_keys()

        # in_degree: number of distinct parent tables each table depends on.
        # Deduplicate edges so multiple FK columns to the same parent only
        # contribute one unit of in-degree.
        in_degree: dict[str, int] = dict.fromkeys(tables, 0)
        # children: parent -> list of tables that reference it
        children: dict[str, list[str]] = {t: [] for t in tables}

        seen_edges: set[tuple[str, str]] = set()
        for fk in fks:
            edge = (fk.from_table, fk.to_table)
            if fk.from_table != fk.to_table and edge not in seen_edges:
                seen_edges.add(edge)
                in_degree[fk.from_table] += 1
                children[fk.to_table].append(fk.from_table)

        queue: collections.deque[str] = collections.deque(t for t in tables if in_degree[t] == 0)
        result: list[str] = []

        while queue:
            node = queue.popleft()
            result.append(node)
            for child in children[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(result) != len(tables):
            cycle_tables = [t for t in tables if t not in result]
            raise ValueError(f"FK cycle detected among tables: {cycle_tables}")

        return result


# ---------------------------------------------------------------------------
# Step 3: Recursive SQL CTE generator
# ---------------------------------------------------------------------------


class SubsetQueryGenerator:
    """Generates CTEs for seed-anchored relational subset traversal.

    Given a seed table and a list of seed primary-key IDs, generates a
    WITH clause that walks the FK graph and collects all dependent rows
    without loading the full graph into memory.

    All SQL identifiers (table and column names) are sourced from PRAGMA
    schema metadata; the WHERE clause contains only integer literals.
    This design prevents SQL injection.

    Args:
        inspector: An initialised SchemaInspector for the same connection.
    """

    def __init__(self, inspector: SchemaInspector) -> None:
        """Initialise the generator with a schema inspector.

        Args:
            inspector: SchemaInspector providing FK graph access.
        """
        self._inspector = inspector

    def _resolve_reachable(
        self,
        seed_table: str,
        graph: dict[str, list[ForeignKey]],
        topo: list[str],
    ) -> set[str]:
        """Determine which tables are reachable from the seed via FK edges.

        Walks the topological order so parents are resolved before children.

        Args:
            seed_table: The anchor table name.
            graph: FK adjacency list from build_dependency_graph().
            topo: Tables in topological (parent-first) order.

        Returns:
            Set of table names reachable from seed_table.
        """
        reachable: set[str] = {seed_table}
        for table in topo:
            if table in graph:
                for fk in graph[table]:
                    if fk.to_table in reachable:
                        reachable.add(table)
        return reachable

    def _infer_pk_column(self, table: str) -> str:
        """Return the primary key column name for a table.

        Uses ``PRAGMA table_info`` to find the first column marked as PK.
        Falls back to ``<table>_id`` if the PRAGMA returns no PK.
        Table name is sourced from schema metadata, not user input.

        Args:
            table: Table name to inspect.

        Returns:
            Column name string.
        """
        # Private access: inspector is a tightly-coupled collaborator in this spike.
        cursor = self._inspector._conn.execute(f"PRAGMA table_info({table})")  # nosec B608
        for row in cursor:
            if row[5] == 1:  # pk flag in PRAGMA table_info
                return str(row[1])
        return f"{table}_id"

    def _build_cte_body(
        self,
        table: str,
        graph: dict[str, list[ForeignKey]],
        reachable: set[str],
    ) -> str | None:
        """Build the SELECT body for a single dependent CTE.

        Uses EXISTS subqueries (one per resolved FK parent, deduplicated by
        parent table) to filter rows that belong to the subset.  EXISTS avoids
        both the ambiguous-column-alias problem that arises with multiple JOIN
        clauses and the row duplication from cross-joining multiple parent sets.

        All identifiers (table and column names) are sourced from PRAGMA
        schema metadata and cannot carry SQL injection payloads.

        Args:
            table: The dependent table being added to the CTE chain.
            graph: FK adjacency list from SchemaInspector.build_dependency_graph().
            reachable: Set of already-resolved table names.

        Returns:
            A SQL string for the body of ``seed_<table> AS (...)`` or
            ``None`` if no resolved parent FK edges exist for this table.
        """
        fks_for_table = graph.get(table, [])
        seen_parents: set[str] = set()
        exists_clauses: list[str] = []

        for fk in fks_for_table:
            if fk.to_table in reachable and fk.to_table not in seen_parents:
                # Identifiers from PRAGMA metadata — not user input.
                # nosec B608: table/column identifiers sourced from sqlite_master.
                inner = (
                    f"SELECT 1 FROM seed_{fk.to_table} p"  # nosec B608
                    f"\n      WHERE p.{fk.to_column} = t.{fk.from_column}"
                )
                exists_clauses.append(f"EXISTS (\n      {inner}\n    )")
                seen_parents.add(fk.to_table)

        if not exists_clauses:
            return None

        where_clause = "\n    AND ".join(exists_clauses)
        return f"    SELECT t.* FROM {table} t\n    WHERE {where_clause}"  # nosec B608

    def generate_cte_block(self, seed_table: str, seed_ids: list[int]) -> str:
        """Build only the WITH … CTE definitions (no trailing SELECT).

        This is the building block used by both generate_subset_ctes (for
        the summary count query) and StreamingSubsetExtractor.stream_table
        (which appends its own per-table SELECT).

        Args:
            seed_table: Name of the anchor table (e.g. ``"customer"``).
            seed_ids: List of integer primary-key values to seed from.

        Returns:
            A ``WITH`` block string ending after the last CTE definition,
            suitable for appending a SELECT clause.
        """
        graph = self._inspector.build_dependency_graph()
        topo = self._inspector.topological_sort()
        reachable = self._resolve_reachable(seed_table, graph, topo)

        # id_list contains only str(int) values — safe from SQL injection.
        id_list = ", ".join(str(i) for i in seed_ids)
        cte_lines: list[str] = []

        # Seed CTE: seed_table and pk_col are schema identifiers; id_list is
        # integers only.  nosec B608: no user-supplied strings in the SQL.
        pk_col = self._infer_pk_column(seed_table)
        seed_inner = (
            f"SELECT * FROM {seed_table}"  # nosec B608
            f"\n    WHERE {pk_col} IN ({id_list})"
        )
        cte_lines.append(f"  seed_{seed_table} AS (\n    {seed_inner}\n  )")

        # Dependent CTEs — one per reachable non-seed table, in topo order
        for table in topo:
            if table == seed_table or table not in reachable:
                continue
            cte_body = self._build_cte_body(table, graph, reachable)
            if cte_body is None:
                continue
            cte_lines.append(f"  seed_{table} AS (\n{cte_body}\n  )")

        return "WITH\n" + ",\n".join(cte_lines)

    def generate_subset_ctes(self, seed_table: str, seed_ids: list[int]) -> str:
        """Build a complete WITH…SELECT SQL string for the given seed set.

        Combines the CTE block with a UNION ALL COUNT(*) summary SELECT so
        callers can execute the full query in one shot.

        Args:
            seed_table: Name of the anchor table (e.g. ``"customer"``).
            seed_ids: List of integer primary-key values to seed from.

        Returns:
            A complete SQL string: WITH block + UNION ALL COUNT(*) SELECT.
        """
        graph = self._inspector.build_dependency_graph()
        topo = self._inspector.topological_sort()
        reachable = self._resolve_reachable(seed_table, graph, topo)

        cte_block = self.generate_cte_block(seed_table, seed_ids)

        ordered = [seed_table] + [t for t in topo if t != seed_table and t in reachable]
        # nosec B608: table name identifiers sourced from schema metadata only.
        union_parts = [
            f"  SELECT '{t}' AS tbl, COUNT(*) AS rows FROM seed_{t}"  # nosec B608
            for t in ordered
        ]
        select_block = "\nUNION ALL\n".join(union_parts)

        return f"{cte_block}\n{select_block}"


# ---------------------------------------------------------------------------
# Step 4: Streaming subset extractor
# ---------------------------------------------------------------------------


class StreamingSubsetExtractor:
    """Streams subset rows table-by-table using cursor iteration.

    Memory stays flat because rows are yielded one at a time and are
    never accumulated into an in-process list.  The SQLite engine
    evaluates the CTE lazily via the cursor's iterator protocol.

    Args:
        conn: Open sqlite3.Connection (row_factory should be sqlite3.Row).
        generator: An initialised SubsetQueryGenerator.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        generator: SubsetQueryGenerator,
    ) -> None:
        """Initialise the extractor.

        Args:
            conn: Active SQLite connection.
            generator: SubsetQueryGenerator for CTE construction.
        """
        self._conn = conn
        self._generator = generator

    def stream_table(self, cte_block: str, table_name: str) -> Iterator[sqlite3.Row]:
        """Stream rows from a single table using a pre-built CTE block.

        Appends a ``SELECT * FROM seed_<table_name>`` to the CTE block and
        yields individual rows without buffering the full result set.
        table_name is sourced from topological_sort() (schema metadata).

        Args:
            cte_block: The WITH…CTE-definitions-only string from
                SubsetQueryGenerator.generate_cte_block() (no trailing SELECT).
            table_name: Which CTE alias to stream (e.g. ``"customer"``).

        Yields:
            sqlite3.Row objects, one at a time.
        """
        sql = f"{cte_block}\nSELECT * FROM seed_{table_name}"  # nosec B608
        cursor = self._conn.execute(sql)
        yield from cursor

    def extract_subset(
        self, seed_table: str, seed_ids: list[int]
    ) -> Iterator[tuple[str, sqlite3.Row]]:
        """Stream all rows in the subset, table by table.

        Walks every reachable table in topological order, yielding
        ``(table_name, row)`` pairs.  Memory consumption is bounded by
        the size of a single row, not the full subset.

        Args:
            seed_table: Anchor table name.
            seed_ids: Seed primary-key IDs.

        Yields:
            Tuples of ``(table_name, sqlite3.Row)``.
        """
        # Private access to collaborator objects is intentional in this spike:
        # these objects are tightly coupled by design to avoid a public API
        # that would be premature at proof-of-concept stage.
        inspector = self._generator._inspector
        graph = inspector.build_dependency_graph()
        topo = inspector.topological_sort()
        reachable = self._generator._resolve_reachable(seed_table, graph, topo)

        ordered_tables = [seed_table] + [t for t in topo if t != seed_table and t in reachable]

        # Build the CTE block once; reuse it for every per-table stream query.
        cte_block = self._generator.generate_cte_block(seed_table, seed_ids)

        for table in ordered_tables:
            for row in self.stream_table(cte_block, table):
                yield table, row


# ---------------------------------------------------------------------------
# Step 5: Main demo
# ---------------------------------------------------------------------------


def _checkpoint(label: str) -> MemoryCheckpoint:
    """Take a tracemalloc snapshot and return a labelled MemoryCheckpoint.

    Args:
        label: Human-readable phase name for reporting.

    Returns:
        MemoryCheckpoint with current and peak byte counts.
    """
    current, peak = tracemalloc.get_traced_memory()
    return MemoryCheckpoint(label=label, current_bytes=current, peak_bytes=peak)


def _print_section(title: str) -> None:
    """Print a formatted section header to stdout.

    Args:
        title: Section heading text.
    """
    separator = "=" * 70
    print(f"\n{separator}")
    print(f"  {title}")
    print(separator)


def _bytes_to_mb(b: int) -> float:
    """Convert bytes to megabytes.

    Args:
        b: Byte count.

    Returns:
        Float megabyte value rounded to two decimal places.
    """
    return round(b / (1024 * 1024), 2)


def main() -> None:
    """Run the full Spike C demonstration.

    Phases:
        1. Build and seed the Sakila-like in-memory database.
        2. Inspect the FK graph and print discovered edges.
        3. Generate CTE SQL for the seed customer set.
        4. Stream the subset, counting rows per table.
        5. Assert flat memory consumption.
        6. Print a findings summary table.
    """
    tracemalloc.start()

    # ------------------------------------------------------------------
    # Phase 1 — Seed the database
    # ------------------------------------------------------------------
    _print_section("Phase 1: Building & Seeding Database")
    cp_before_seed = _checkpoint("before_seed")
    print(f"  Memory before seed : {_bytes_to_mb(cp_before_seed.current_bytes)} MB current")

    conn = build_and_seed_database()
    cp_after_seed = _checkpoint("after_seed")
    print(f"  Customers  : {NUM_CUSTOMERS:>8,}")
    print(f"  Addresses  : {NUM_ADDRESSES:>8,}")
    print(f"  Films      : {NUM_FILMS:>8,}")
    print(f"  Inventory  : {NUM_INVENTORY:>8,}")
    print(f"  Rentals    : {NUM_RENTALS:>8,}")
    print(f"  Payments   : {NUM_PAYMENTS:>8,}")
    print(
        f"  Memory after seed  : {_bytes_to_mb(cp_after_seed.current_bytes)} MB current"
        f" / {_bytes_to_mb(cp_after_seed.peak_bytes)} MB peak"
    )

    # ------------------------------------------------------------------
    # Phase 2 — FK graph inference
    # ------------------------------------------------------------------
    _print_section("Phase 2: FK Graph Inference")
    inspector = SchemaInspector(conn)

    tables = inspector.get_tables()
    print(f"  Tables discovered  : {tables}")

    fks = inspector.get_foreign_keys()
    print(f"\n  FK edges ({len(fks)} total):")
    for fk in fks:
        print(f"    {fk.from_table}.{fk.from_column}  -->  {fk.to_table}.{fk.to_column}")

    topo = inspector.topological_sort()
    print(f"\n  Topological order  : {topo}")

    # ------------------------------------------------------------------
    # Phase 3 — CTE generation
    # ------------------------------------------------------------------
    _print_section("Phase 3: CTE SQL Generation")
    generator = SubsetQueryGenerator(inspector)
    cte_sql = generator.generate_subset_ctes("customer", SEED_CUSTOMER_IDS)
    print(f"\n  Seed IDs : {SEED_CUSTOMER_IDS}")
    print("\n  Generated SQL:\n")
    for line in cte_sql.splitlines():
        print(f"    {line}")

    # ------------------------------------------------------------------
    # Phase 4 — Streaming extraction with memory tracking
    # ------------------------------------------------------------------
    _print_section("Phase 4: Streaming Extraction")
    extractor = StreamingSubsetExtractor(conn, generator)

    cp_before_extract = _checkpoint("before_extract")
    print(f"  Memory before extract: {_bytes_to_mb(cp_before_extract.current_bytes)} MB")

    row_counts: dict[str, int] = collections.defaultdict(int)
    tracemalloc.reset_peak()  # reset so we measure only the streaming phase

    for table_name, _row in extractor.extract_subset("customer", SEED_CUSTOMER_IDS):
        row_counts[table_name] += 1

    cp_after_extract = _checkpoint("after_extract")
    print(
        f"  Memory after extract : {_bytes_to_mb(cp_after_extract.current_bytes)} MB current"
        f" / {_bytes_to_mb(cp_after_extract.peak_bytes)} MB peak (streaming phase only)"
    )

    # Compute reachable set for display purposes — private access is intentional.
    graph = inspector.build_dependency_graph()
    reachable_tables = generator._resolve_reachable("customer", graph, topo)
    print("\n  Rows extracted per table:")
    for table_name in topo:
        if table_name in reachable_tables:
            count = row_counts.get(table_name, 0)
            print(f"    {table_name:<20} : {count:>6,} rows")

    # ------------------------------------------------------------------
    # Phase 5 — Memory proof
    # ------------------------------------------------------------------
    _print_section("Phase 5: Flat Memory Assertion")
    peak_after_seed_mb = _bytes_to_mb(cp_after_seed.peak_bytes)
    peak_streaming_mb = _bytes_to_mb(cp_after_extract.peak_bytes)
    ceiling_mb = round(peak_after_seed_mb * STREAMING_MEMORY_MULTIPLIER, 2)

    print(f"  Peak after seed         : {peak_after_seed_mb} MB")
    print(f"  Peak during streaming   : {peak_streaming_mb} MB")
    print(f"  Ceiling (2x post-seed)  : {ceiling_mb} MB")

    if peak_streaming_mb >= ceiling_mb:
        raise RuntimeError(
            f"Memory spike detected: streaming peak {peak_streaming_mb} MB "
            f">= ceiling {ceiling_mb} MB (2x post-seed {peak_after_seed_mb} MB)"
        )
    print("  PASS — streaming memory is flat (< 2x post-seed peak)")

    # ------------------------------------------------------------------
    # Phase 6 — Findings summary table
    # ------------------------------------------------------------------
    _print_section("Phase 6: Findings Summary")
    checkpoints = [
        cp_before_seed,
        cp_after_seed,
        cp_before_extract,
        cp_after_extract,
    ]
    print(f"\n  {'Phase':<30} {'Current (MB)':>14} {'Peak (MB)':>12}")
    print(f"  {'-' * 30} {'-' * 14} {'-' * 12}")
    for cp in checkpoints:
        print(
            f"  {cp.label:<30} "
            f"{_bytes_to_mb(cp.current_bytes):>14.2f} "
            f"{_bytes_to_mb(cp.peak_bytes):>12.2f}"
        )

    print(f"\n  FK graph edges     : {len(fks)}")
    print(f"  Topological order  : {' -> '.join(topo)}")
    total_rows = sum(row_counts.values())
    print(f"  Total rows extracted (3 seeds): {total_rows:,}")
    print("\n  Spike C COMPLETE — all assertions passed.\n")

    tracemalloc.stop()
    conn.close()


if __name__ == "__main__":
    main()
