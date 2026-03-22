"""Shared ASGI middleware components for the Conclave Engine.

Cross-cutting middleware that belongs in ``shared/`` because it has no
FastAPI/bootstrapper import dependency and expresses a general concern
reusable across the application.

Current contents:
    :mod:`~synth_engine.shared.middleware.idempotency` — Redis-backed
        request deduplication middleware (T45.1, TBD-07).

Boundary constraints
--------------------
Modules under ``shared/middleware/`` must NOT import from
``bootstrapper/`` or any ``modules/`` package.  Framework binding
(middleware registration, Redis client injection, exempt path injection)
is the responsibility of ``bootstrapper/middleware.py``.

CONSTITUTION Priority 0: Security
Task: T45.1 — Reintroduce Idempotency Middleware (TBD-07)
"""
