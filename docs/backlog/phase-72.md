# Phase 72 — Exception Specificity & Router Safety Hardening

**Goal**: Narrow all 50+ broad `except Exception` catches in production routers
to specific exception types, fixing the most widespread technical debt pattern
in the codebase. This phase addresses audit finding C1 (HIGH severity) and
partially addresses C7 (oversized functions) where the broad catches are a
contributing factor.

**Rationale**: Broad exception catches mask the distinction between audit
write failures, database errors, transient network errors, and programming
bugs. In production, all produce the same 500 response — operators cannot
distinguish between a recoverable transient failure and a systemic issue.
This violates the principle that error handling should be as specific as the
error taxonomy.

**Source**: Production Audit 2026-03-29, Finding C1

---

## Tasks

### T72.1 — Narrow Router Audit-Write Exception Catches

**Files**: `bootstrapper/routers/privacy.py`, `connections.py`, `security.py`,
`jobs.py`, `settings.py`, `webhooks.py`, `admin.py`, `compliance.py`, `health.py`

Replace each `except Exception:` in audit `log_event()` call sites with the
specific exceptions raised by the audit logger (e.g., `AuditWriteError`,
`IOError`, `OSError`). If `AuditWriteError` does not exist, introduce it in
`shared/exceptions.py`.

**ACs**:
1. Every `except Exception:` guarding an `audit.log_event()` call is replaced
   with `except (AuditWriteError, OSError):` (or the specific set).
2. Other exception types (e.g., `ValueError`, `KeyError`) propagate and become
   visible 500s with stack traces in logs.
3. No change to HTTP response status codes — 500 is still returned for audit
   failures.
4. All existing tests pass unchanged.
5. New test: an unexpected `RuntimeError` from `log_event()` propagates instead
   of being swallowed.

### T72.2 — Narrow Lifecycle Shutdown Exception Catches

**File**: `bootstrapper/lifecycle.py:58,62,66`

Replace the three broad catches in the shutdown sequence with specific
exception types for each resource (Redis disconnect, DB pool dispose, Huey
shutdown).

**ACs**:
1. Each shutdown handler catches only the exceptions its resource can raise.
2. Unexpected exceptions propagate and are logged as CRITICAL.
3. Shutdown sequence completes even if one handler fails (existing behavior
   preserved).

### T72.3 — Narrow TLS Config Exception Catches

**File**: `shared/tls/config.py:161,225,282`

Replace the three `except Exception as exc:` catches with specific SSL/crypto
exception types (`ssl.SSLError`, `ValueError`, `FileNotFoundError`).

**ACs**:
1. Each catch site names only the exceptions documented for the SSL/crypto
   operations being called.
2. `TypeError`, `AttributeError`, and other programming errors propagate.

### T72.4 — Narrow Synthesizer & Privacy Exception Catches

**Files**: `modules/synthesizer/training/dp_accounting.py:135,178,256`,
`modules/synthesizer/training/dp_training.py:431`,
`modules/synthesizer/storage/retention.py:128,159,222,249`,
`modules/privacy/sync_budget.py` (if applicable)

Replace broad catches with specific types for each domain:
- DP accounting: `opacus` exceptions, `ArithmeticError`, `ValueError`
- Retention: `OSError`, `sqlalchemy.exc.SQLAlchemyError`
- Training: `torch` exceptions, `RuntimeError`

**ACs**:
1. Each catch names specific exception types.
2. No silent swallowing of `KeyboardInterrupt` or `SystemExit` (these should
   never be caught by `except Exception` but verify explicitly).
3. New test for each narrowed catch verifying unexpected exceptions propagate.

### T72.5 — Fix httpx Connection Pooling in Webhook Delivery

**File**: `modules/synthesizer/jobs/webhook_delivery.py:529`

Replace `httpx.post()` per-call pattern with a module-level or function-scoped
`httpx.Client()` with connection pooling. The retry loop should reuse the same
TCP+TLS connection.

**ACs**:
1. `httpx.Client()` used with `with` context manager wrapping the retry loop.
2. Connection reuse verified: 3 retry attempts use 1 TCP connection (not 3).
3. Client is closed on function exit (context manager guarantees this).
4. Timeout still configurable per-request.
5. Existing webhook delivery tests pass.
6. New test: verify client is closed even on exception.

### T72.6 — Fix Sync/Async Session Race in Privacy Budget Reset

**File**: `bootstrapper/routers/privacy.py:299-332`

The sync session reads after an async mutation in a separate connection. Fix
by either: (a) using the async session for the post-mutation read, or
(b) performing the response construction inside `_run_reset_budget()` where
the async session is still active.

**ACs**:
1. Post-mutation read uses the same session/connection as the mutation.
2. No window for reading stale data after partial commit.
3. Compensating audit event still fires on mutation failure.
4. Existing privacy router tests pass.
5. New integration test: concurrent budget resets return consistent data.
