# ADR-0020 — Huey Task Queue Singleton Pattern

**Date:** 2026-03-15
**Status:** Accepted
**Deciders:** PM + Architect
**Task:** Advisory Drain Sprint (ADV-042)

---

## Context

The synthesizer module (T4.2c) requires a background task queue to run CTGAN
training jobs asynchronously. The queue instance (`huey`) must be:

1. Importable by both the Huey worker process (to discover registered tasks)
   and by `bootstrapper/main.py` (to enqueue tasks via `run_synthesis_job.call
   _local()`).
2. Configurable at runtime to use Redis in production and an in-memory backend
   for tests and local development without a running Redis instance.
3. Wired in a way that satisfies the modular monolith boundary constraints
   (modules must NOT import from bootstrapper; bootstrapper may import from
   modules and shared).

---

## Decision

**A single Huey instance is constructed in `shared/task_queue.py` and imported
by all code that needs it. Configuration is driven by environment variables.**

### Singleton location: `src/synth_engine/shared/task_queue.py`

The `huey` object is a module-level singleton. All task-decorated functions
and all call sites import from this one location:

```python
from synth_engine.shared.task_queue import huey
```

### Environment-variable backend selection

Three environment variables control the Huey instance:

| Variable | Values | Default | Purpose |
|---|---|---|---|
| `HUEY_BACKEND` | `redis`, `memory` | `redis` | Storage backend |
| `REDIS_URL` | Redis connection URL | `redis://redis:6379/0` | Redis endpoint (only used when `HUEY_BACKEND=redis`) |
| `HUEY_IMMEDIATE` | `true`, `false` | `false` | Execute tasks synchronously in the calling process |

**`HUEY_IMMEDIATE=true`** is intended for integration tests and local
debugging. It bypasses the worker process entirely — `task.call_local()` is
the preferred injection point for unit tests (no environment variable needed).

**`HUEY_BACKEND=memory`** selects `huey.MemoryHuey`, which is thread-safe but
process-local. Suitable for single-process local development.

### Import-side-effect task registration in `bootstrapper/main.py`

Huey discovers registered tasks by scanning the modules in which
`@huey.task()` decorators appear. These modules must be imported before the
worker starts (or before any task is enqueued from the API).

`bootstrapper/main.py` performs this import explicitly at module load time:

```python
from synth_engine.modules.synthesizer import tasks as _synthesizer_tasks  # noqa: F401
```

This import side-effect registers `run_synthesis_job` with the shared Huey
instance. The `# noqa: F401` suppresses the "imported but unused" linter
warning; the comment documents the intent.

This satisfies CLAUDE.md Rule 8: IoC hooks and task registrations are wired
in `bootstrapper/`, not inside the task module itself.

### Naming taxonomy: `shared/task_queue.py` vs `shared/tasks/`

Two similarly named locations exist in `shared/`:

| Path | Purpose |
|---|---|
| `shared/task_queue.py` | Huey instance construction (infrastructure) |
| `shared/tasks/` | Scheduled task definitions (business logic) |

`shared/task_queue.py` is the **queue infrastructure module** — it constructs
and configures the Huey instance. It contains no task definitions.

`shared/tasks/` contains **scheduled task definitions** — currently the orphan
task reaper (ADR-0005). These are tasks that run on a timer and are not
directly enqueued by the API. They import `huey` from `shared/task_queue.py`.

The naming difference (`task_queue` vs `tasks/`) is intentional: the former is
singular infrastructure, the latter is a package of definitions.

---

## Rationale

**Why a singleton in `shared/` rather than constructed in `bootstrapper/`?**

The Huey instance must be importable by task-decorated functions in
`modules/synthesizer/tasks.py`. Task modules may not import from
`bootstrapper/` (import-linter contract). Therefore the Huey instance must
live in `shared/`, which all modules are permitted to import from.

**Why environment-variable backend selection rather than dependency injection?**

Huey's architecture binds the `@huey.task()` decorator to the instance at
decoration time (module import). This is not a runtime injectable — changing
the backend after decoration would invalidate all registered tasks. Environment
variables evaluated once at import time are the correct mechanism here.

**Why `HUEY_IMMEDIATE` instead of always using `call_local()` in tests?**

`call_local()` is the preferred unit-test injection point and does not require
any environment variable. `HUEY_IMMEDIATE` exists as a fallback for integration
test scenarios where the full `run_synthesis_job` codepath (including the Huey
decorator wrapper) must be exercised without spawning a real worker process.

**Alternatives considered:**

- *Construct Huey in `bootstrapper/main.py` and pass it as a dependency:*
  Rejected — `modules/synthesizer/tasks.py` cannot import from `bootstrapper/`
  (import-linter contract violation).
- *One Huey instance per module:* Rejected — tasks would be registered on
  different instances, making cross-module coordination impossible.
- *Use Celery instead of Huey:* Rejected — Huey was selected in ADR-0003 and
  ADR-0005 for its minimal dependency footprint and SQLite/memory backend
  support (important for air-gapped dev environments).

---

## Consequences

- All background tasks in this codebase share a single Huey instance.
- Adding a new background task requires: (1) decorating with `@huey.task()` in
  the appropriate module, (2) importing the task module in `bootstrapper/main.py`
  to register it.
- The `HUEY_BACKEND`, `REDIS_URL`, and `HUEY_IMMEDIATE` variables must be
  documented in `.env.example` (done: ADV-043 drain).
- Future Huey configuration changes (e.g., adding a results backend, changing
  serialization) should be made in `shared/task_queue.py` and documented as
  an ADR amendment.
