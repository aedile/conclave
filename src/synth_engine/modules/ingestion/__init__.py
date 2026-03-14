"""Ingestion — PostgreSQL connection adapter and schema validators.

Public API:

- :class:`~synth_engine.modules.ingestion.postgres_adapter.PostgresIngestionAdapter`
- :class:`~synth_engine.modules.ingestion.postgres_adapter.PrivilegeEscalationError`

Architecture note
-----------------
``modules/ingestion`` is responsible for a single coherent domain: connecting
to a PostgreSQL source database, verifying runtime privileges, and streaming
raw table data.

Import-linter contracts (``pyproject.toml``) enforce:
- ``ingestion`` may NOT import from ``mapping``, ``subsetting``, ``masking``,
  ``profiler``, ``privacy``, or ``bootstrapper``.

The relational mapping logic (DAG, topological sort, schema reflection) lives
in ``modules/mapping``.  The subsetting and egress logic lives in
``modules/subsetting``.  These were extracted in T3.5.2 to give each module
a single coherent responsibility.

Task: P3.5-T3.5.2 — Module Cohesion Refactor
"""
