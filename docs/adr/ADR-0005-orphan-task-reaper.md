# ADR-0005: Orphan Task Reaper — Background Cron for Stuck IN_PROGRESS Tasks

**Status:** Accepted
**Date:** 2026-03-13
**Deciders:** Project team

## Context

Background jobs in the Conclave Engine are dispatched to worker processes.
Worker processes can crash, lose database connectivity, or be OOM-killed
without gracefully releasing the tasks they hold.  Without a recovery
mechanism, tasks remain perpetually IN_PROGRESS, locks are never released,
and dependent workflows stall indefinitely.

Two remediation strategies were considered:

1. **Heartbeat + lease expiry:** Workers emit a heartbeat; the coordinator
   expires tasks whose heartbeat has lapsed.  Correct but requires a
   heartbeat infrastructure not yet present in Phase 2.
2. **Staleness-based reaper:** A periodic job scans for IN_PROGRESS tasks
   whose `started_at` timestamp is older than a configurable threshold and
   forcibly marks them FAILED.  Simple to implement and requires no changes
   to worker code.

## Decision

Implement an `OrphanTaskReaper` that:

- Accepts an abstract `TaskRepository` dependency (testable without a live
  database).
- Uses a configurable `stale_threshold_minutes` (default: 60 minutes) to
  determine which IN_PROGRESS tasks are orphaned.
- Calls `repository.fail_task(task_id)` for each stale task.
- Wraps each individual `fail_task` call in a try/except block so that a
  single persistence failure does not abort reaping of subsequent tasks.
  Errors are logged at ERROR level with the task ID included.
- Logs a summary INFO message with the count of tasks reaped per invocation.
- Is intended to be invoked by a periodic scheduler (e.g. Huey periodic task,
  APScheduler, or a Kubernetes CronJob) at a cadence shorter than the stale
  threshold (e.g. every 5 minutes with a 60-minute threshold).

The `TaskRepository` ABC pattern keeps the reaper decoupled from the concrete
database implementation introduced in Task 2.2, ensuring it can be unit-tested
without a live PostgreSQL instance.

## Consequences

- **Positive:** Simple, dependency-light implementation with no heartbeat
  infrastructure required.
- **Positive:** Per-task exception isolation means a single bad row in the
  database (e.g. a constraint violation) does not prevent recovery of other
  orphaned tasks.
- **Positive:** The `TaskRepository` ABC enables deterministic unit testing via
  mock injection.
- **Negative:** The staleness heuristic is a coarse approximation.  A task
  that genuinely takes longer than the threshold (e.g. due to a large dataset)
  will be incorrectly reaped.  Operators must tune `stale_threshold_minutes`
  to be safely above the P99 task completion time.
- **Negative:** Unlike a heartbeat mechanism, the reaper cannot distinguish
  between a crashed worker and a slow-but-healthy one.  This is an acceptable
  trade-off for Phase 2 given the absence of heartbeat infrastructure.
- **Future work:** Replace or supplement with a heartbeat-based lease mechanism
  in Phase 4+ when worker infrastructure matures.
