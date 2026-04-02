# Phase 77 — Security Fixes & Polish (Roast Findings)

**Goal**: Fix 2 critical security findings from the post-P76 production audit,
plus batch 4 medium-severity code quality items. Polish items (docs, test
improvements) deferred or batched inline per Rule 16.

**Source**: Post-P76 Production Roast, 2026-04-02

---

## Critical Fixes (Security — standalone phase justified)

### T77.1 — Add UnicodeError to Router Audit-Write Exception Catches

**Files**: 8 router files under `bootstrapper/routers/`

`audit.log_event()` calls `.encode("utf-8")` internally which can raise
`UnicodeError` (not a subclass of `ValueError` or `OSError`). The current
`except (ValueError, OSError):` misses this path.

**ACs**:
1. All 8 router audit-write catches updated to `except (ValueError, OSError, UnicodeError):`
2. New test: inject a `UnicodeError` side effect on `log_event()` and verify
   it's caught (returns 500 + increments counter), not propagated.
3. No behavioral change — same 500 response, same counter increment.

### T77.2 — Use Full SHA-256 Hash for Redis Circuit Breaker Keys

**File**: `modules/synthesizer/jobs/webhook_delivery.py:175`

`_url_hash()` truncates SHA-256 to 16 hex chars (64 bits). While 2^64
collision space is practically safe, it's unnecessarily narrow for a
security primitive. Use the full 64-char hex digest.

**ACs**:
1. `_url_hash()` returns full `hexdigest()` (64 chars), not `[:16]`.
2. All Redis key references updated for the longer hash.
3. Existing CB tests pass unchanged (they don't depend on hash length).
4. New test: verify `_url_hash()` returns 64 characters.

## Medium-Severity Fixes (batched inline)

### T77.3 — Narrow CB Initialization Broad Catch

**File**: `webhook_delivery.py:628`

Change `except Exception` to `except (redis_lib.RedisError, TypeError, ValueError)`
with justification comment.

### T77.4 — Fix artifact.py stderr Usage

**File**: `modules/synthesizer/storage/artifact.py:164-167`

Replace `sys.stderr.write()` with `_logger.error()` to maintain observability.

### T77.5 — Polish Items (batched)

- Add `UnicodeError` justification comments to all catch sites
- Update RETRO_LOG with P77 retrospective
- docs: commit for Rule 9
