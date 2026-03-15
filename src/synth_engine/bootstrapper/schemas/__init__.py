"""Pydantic request/response schemas for the Conclave Engine API.

Schemas in this subpackage are the API boundary types — they define what
the REST API accepts and returns.  They are deliberately separate from the
SQLModel database models in ``modules/`` to avoid creating reverse
dependencies (bootstrapper importing from modules is allowed; the reverse
is forbidden by import-linter).

Task: P5-T5.1 — Task Orchestration API Core
"""
