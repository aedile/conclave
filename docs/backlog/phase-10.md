# Phase 10 — Test Infrastructure Repair & Final Polish

**Goal**: Fix the broken test infrastructure caused by Python 3.14.1 deprecation
changes in pytest-asyncio, drain the last stale TODO, and bring README to final
currency. No new features.

**Prerequisite**: Phase 9 must be complete (all tasks merged, retrospective signed off).

---

## T10.1 — Fix pytest-asyncio Python 3.14.1 Compatibility (BLOCKER)

**Priority**: P0 — Constitutional quality gate (Priority 1) is currently failing.

### Context & Constraints

All 809 unit tests error during setup with `-W error` due to
`asyncio.get_event_loop_policy()` deprecation in pytest-asyncio 0.26.0.
The deprecation warning is raised during pytest-asyncio's collection phase,
before pyproject.toml `filterwarnings` take effect. This means the
Constitutional mandate (`pytest -W error`) produces 809 errors, 0 passes.

pytest-asyncio 1.3.0 is available (major version bump). Evaluate whether
upgrading to 1.x resolves the issue. If 1.x introduces breaking API changes,
document them and adapt test code. If 1.x is not compatible, find an
alternative fix (e.g., conftest.py-level warning suppression that fires
before collection).

### Acceptance Criteria

1. `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
   passes with 809+ tests and 90%+ coverage.
2. `poetry run pytest tests/integration/ -v --no-cov` passes (pre-existing failures
   from infrastructure not available locally are acceptable).
3. pyproject.toml `filterwarnings` updated if needed.
4. If pytest-asyncio major version changes, document breaking changes and
   any test adaptations required.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/ -v --no-cov`
- All review agents spawned.

---

## T10.2 — Drain Stale TODO and Update README Status

**Priority**: Cleanup — low risk, high confidence.

### Context & Constraints

1. `src/synth_engine/bootstrapper/main.py:262` has `TODO(T4.4)` for
   `build_privacy_accountant()` factory wiring. Task 4.4 is complete.
   Either implement the wiring or remove the TODO with a justification.

2. `README.md` says "Phase 9 — Documentation, Observability, and Advisory Drain
   is in progress." Phase 9 is complete. Update to reflect completion and add
   Phase 9 row to the status table.

### Acceptance Criteria

1. No stale `TODO(T4.4)` in source code — either wired or removed with justification.
2. README.md phase status updated to "Phase 9 complete".
3. README.md phase table includes Phase 9 row.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
  (depends on T10.1 being complete)
- All review agents spawned.

---

## Phase 10 Exit Criteria

- All unit tests pass with `-W error` on Python 3.14.1.
- No stale TODOs referencing completed tasks.
- README current with Phase 9 completion.
- All quality gates passing.
- Phase 10 end-of-phase retrospective completed.
