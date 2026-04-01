# Phase 74 — Maintainability & Configuration Hardening

**Goal**: Decompose oversized files, externalize hardcoded values, and reduce
cognitive load for developers navigating the codebase. Addresses audit findings
C5, C6, C7, C8, and open advisory ADV-P70-01.

**Source**: Production Audit 2026-03-29, Findings C5-C8 + ADV-P70-01

---

## Tasks

### T74.1 — Externalize Database Pool Parameters to Settings

**File**: `shared/db.py:129-146`

Move `_POOL_SIZE`, `_MAX_OVERFLOW`, `_WORKER_POOL_SIZE`, `_WORKER_MAX_OVERFLOW`,
`_WORKER_POOL_RECYCLE`, `_WORKER_POOL_TIMEOUT` to `ConclaveSettings` with
environment variable backing.

**ACs**:
1. All 6 pool parameters configurable via environment variables.
2. Sensible defaults match current hardcoded values.
3. `db.py` reads from `get_settings()`, not module-level constants.
4. Existing tests pass. New test: override pool size via env var.

### T74.2 — Wire Rate Limit Window to Settings

**File**: `bootstrapper/dependencies/rate_limit_backend.py:33`

Replace `_WINDOW_SECONDS: int = 60` with a settings field.

**ACs**:
1. `rate_limit_window_seconds` added to `RateLimitSettings`.
2. `rate_limit_backend.py` reads from settings, not hardcoded constant.
3. Redis key format updated to use configurable window.
4. Existing rate limit tests pass. New test: custom window value.

### T74.3 — Decompose settings.py to ≤300 LOC

**File**: `shared/settings.py` (currently 1,025 LOC)

Continue the T71.4 extraction pattern: move remaining inline validator methods
and field groups into `settings_models.py` sub-models.

**ACs**:
1. `settings.py` ≤ 300 LOC (main class + imports + singleton).
2. All validators live in `settings_models.py` sub-model classes.
3. `get_settings()` API unchanged.
4. ADV-P70-01 closed in RETRO_LOG.

### T74.4 — Break Oversized Functions (≥100 LOC)

**Target functions** (descending by size):
- `deliver_webhook()` 205 lines → extract retry loop, signature computation, circuit breaker check
- `validate_config()` 173 lines → extract per-domain validation into private helpers
- `shred_job()` 149 lines → extract audit write, ownership check, status transition
- `train()` 142 lines → extract data loading, DP setup, training loop

**ACs**:
1. No function exceeds 80 lines after refactoring.
2. Extracted helpers are private (`_` prefix) and tested indirectly through
   the parent function's existing tests.
3. No behavioral changes — pure structural refactoring.
4. All existing tests pass unchanged.

### T74.5 — Break Oversized Functions (50-100 LOC)

**Target functions**: `register_webhook()` 138 lines, `set_legal_hold()` 136 lines,
`spend_budget()` 132 lines, `_validate_production_required_fields()` 129 lines,
remaining functions in the 50-100 LOC range.

**ACs**:
1. No function exceeds 50 lines.
2. Same constraints as T74.4.

### T74.6 — Documentation Cleanup (Batched Polish — Rule 16)

Archive completed phase docs, trim RETRO_LOG, strip task-ID listings from
module docstrings.

**ACs**:
1. `docs/backlog/phase-{01..71}.md` archived to `docs/retro_archive/`.
2. RETRO_LOG trimmed: open advisories + last 10 phases retained.
3. Module docstrings in `settings.py`, `vault.py`, `auth.py` reduced to
   behavioral description only (no task-ID archaeology).
4. `deferred-items.md` archived (all items delivered).
