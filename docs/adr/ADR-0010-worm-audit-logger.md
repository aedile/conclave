# ADR-0010: WORM Audit Logger

**Date:** 2026-03-13
**Status:** Accepted
**Task:** P2-T2.4 — Vault Observability
**Amended:** 2026-03-14 — P2-D3: Singleton design, threading.Lock, restart caveat
**Deciders:** Engineering Team

---

## Context

Security-critical operations (vault unseal, future data generation jobs, auth
events) must produce a tamper-evident, non-repudiable audit trail.  In an
air-gapped deployment, this trail must be maintained without external services.

---

## Decision

### HMAC-SHA256 Per-Event Signatures

Each `AuditEvent` is signed with HMAC-SHA256 using a dedicated `AUDIT_KEY`
(separate from `ALE_KEY` and `JWT_SECRET_KEY`).  The signed message is:

```
{timestamp}|{event_type}|{actor}|{resource}|{action}|{prev_hash}
```

Signing these six fields binds the event's *identity* (what), *principal*
(who), *position in the chain* (prev_hash), and *timestamp* (when) to the
MAC.  Forging any field requires knowledge of `AUDIT_KEY`.

### Hash-Chain Tamper Evidence

Each event records the SHA-256 digest of the *previous event's JSON*:

```
event_n.prev_hash = SHA-256(event_{n-1}.model_dump_json())
```

The genesis event has `prev_hash = "0" * 64`.  This structure means:
- Deleting an event breaks every subsequent `prev_hash`.
- Reordering events breaks the chain.
- Inserting a fabricated event requires computing a valid HMAC signature,
  which requires knowledge of `AUDIT_KEY`.

### Dedicated AUDIT_KEY

Using a separate key limits blast radius: a `JWT_SECRET_KEY` compromise does
not allow audit log forgery, and vice versa.  `AUDIT_KEY` is hex-encoded 32
bytes, validated at `get_audit_logger()` factory time.

### WORM via Log Shipping

Events are emitted to `logging.getLogger("synth_engine.security.audit")` at
INFO level as JSON.  Making the logger output append-only (WORM) is an
*operational* concern: configure log shipping to an append-only store (S3 with
Object Lock, a syslog daemon, or a SIEM) at deploy time.  This module provides
the cryptographic guarantees; the storage layer provides the persistence
guarantees.

### Timing-Safe Verification

`AuditLogger.verify_event()` uses `hmac.compare_digest` to compare the
expected and actual signatures.  This prevents timing-oracle attacks that could
leak partial information about `AUDIT_KEY`.

### Module-Level Singleton (P2-D3)

`get_audit_logger()` now returns a **module-level singleton** rather than a
new `AuditLogger` instance on every call.  This fixes an architectural debt
item where the hash chain was silently reset on each HTTP request, allowing an
attacker to delete 99 audit events without any chain-integrity failure being
detectable.

**Design:**
- A module-level `_audit_logger_instance: AuditLogger | None` is guarded by
  `_audit_logger_lock: threading.Lock`.
- The singleton is created on first call to `get_audit_logger()` using the
  `AUDIT_KEY` read at that time.  All subsequent calls return the same object.
- `AuditLogger.log_event()` acquires an instance-level `threading.Lock` before
  reading or advancing `_prev_hash`.  This serialises concurrent callers (e.g.
  async FastAPI route handlers that call the logger directly or via
  `asyncio.to_thread`) and guarantees that the chain is gapless even under
  high concurrency.

**Chain scope:**
- The hash chain is continuous for the **lifetime of the process**.  All events
  emitted by any callsite during a process run form a single unbroken chain.
- On **process restart**, `_audit_logger_instance` is `None` and the chain
  begins at genesis again (`prev_hash = "0" * 64`).

**Test isolation:**
- `reset_audit_logger()` sets `_audit_logger_instance = None` under
  `_audit_logger_lock`.  It exists **solely for test isolation** and MUST NOT
  be called in production code.

**Phase 6 future work:**
On process restart, the previous chain tail (`prev_hash`) is lost from memory.
An auditor performing cross-restart integrity verification must stitch chains
manually.  Phase 6 should persist the latest `prev_hash` to the audit database
table so that new process instances can continue from where the previous one
left off, making the chain truly continuous across restarts.

### PII Constraint on `details` Field

The `details: dict[str, str]` field on `AuditEvent` is intentionally
unstructured to allow callers to attach contextual metadata.  However, callers
**MUST NOT** pass PII field values (e.g. names, email addresses, file contents)
in this dictionary.  The field's contents are written verbatim to the audit log
and shipped to the log store.

This constraint is currently enforced by convention only.  Before Phase 3 work
begins, a Pydantic validator or key allowlist should be added to `AuditEvent` to
reject keys outside a defined set, converting this from a documentation
constraint to a code constraint.

---

## Consequences

**Positive:**
- Stdlib only (`hmac`, `hashlib`, `logging`, `threading`) — no new dependencies.
- Hash chain + HMAC signatures give both integrity (no tampering) and
  authenticity (only the keyholder can produce valid events).
- Pydantic `BaseModel` ensures all events are schema-validated and trivially
  JSON-serialisable.
- The singleton `AuditLogger` maintains `_prev_hash` for the full process
  lifetime; `threading.Lock` makes `log_event()` safe under concurrency.
- Chain integrity now holds across all HTTP requests, not just within a single
  request — deleting any event is detectable.

**Negative / Mitigations:**
- The hash chain is only as strong as its first link.  If an attacker can
  truncate the log before the genesis event, they can start a new chain.
  Mitigation: replicate logs to an independent append-only store immediately.
- On process restart, the chain begins at genesis.  Cross-restart chain
  continuity is deferred to Phase 6 (persist `prev_hash` to the audit table).
- `AUDIT_KEY` must be rotated carefully: rotating the key breaks verification
  of old events unless the old key is retained.  Rotation procedure is out of
  scope for this ADR.
- `details` is an open-ended PII sink until a key allowlist is added (planned
  before Phase 3).
- `reset_audit_logger()` destroys chain continuity.  It is guarded by a
  docstring warning but not a runtime guard.  Callers in production code MUST
  NOT call it.
