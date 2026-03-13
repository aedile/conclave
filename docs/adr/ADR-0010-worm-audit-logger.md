# ADR-0010: WORM Audit Logger

**Date:** 2026-03-13
**Status:** Accepted
**Task:** P2-T2.4 — Vault Observability
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

### Per-Call Chain Isolation

`get_audit_logger()` is a factory: each call returns a **new** `AuditLogger`
instance whose hash chain begins at genesis (`prev_hash = "0" * 64`).  Callers
that require a single continuous chain across multiple operations must hold the
returned instance for the lifetime of that chain and must not call the factory
again on each operation.

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
- Stdlib only (`hmac`, `hashlib`, `logging`) — no new dependencies.
- Hash chain + HMAC signatures give both integrity (no tampering) and
  authenticity (only the keyholder can produce valid events).
- Pydantic `BaseModel` ensures all events are schema-validated and trivially
  JSON-serialisable.
- The `AuditLogger` instance is stateful (tracks `_prev_hash`); the factory
  `get_audit_logger()` returns a fresh instance, so callers own their chain.

**Negative / Mitigations:**
- The hash chain is only as strong as its first link.  If an attacker can
  truncate the log before the genesis event, they can start a new chain.
  Mitigation: replicate logs to an independent append-only store immediately.
- `AUDIT_KEY` must be rotated carefully: rotating the key breaks verification
  of old events unless the old key is retained.  Rotation procedure is out of
  scope for this ADR.
- `details` is an open-ended PII sink until a key allowlist is added (planned
  before Phase 3).
