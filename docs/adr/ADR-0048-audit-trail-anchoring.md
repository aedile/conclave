# ADR-0048 — Audit Trail Anchoring Architecture

**Status**: Accepted
**Date**: 2026-03-22
**Amended**: 2026-03-24 (T53.2 — v2 HMAC signature format)
**Task**: T48.4 — Immutable Audit Trail Anchoring
**Deciders**: Development Team

---

## Context

The `AuditLogger` singleton in `shared/security/audit.py` maintains a HMAC-SHA256-signed,
hash-chained WORM audit trail.  The security properties of this chain depend on:

1. **AUDIT_KEY confidentiality** — an attacker who obtains AUDIT_KEY can forge new events and
   recompute the chain.
2. **Log infrastructure integrity** — an attacker with host-level access can rewrite or truncate
   the log file before the chain is checked.

If AUDIT_KEY is compromised, the in-process chain provides no protection: an attacker could
construct a parallel chain that appears valid.  The chain is tamper-*evident* within the
process, but provides no *external attestation* that the chain has not been replaced wholesale.

Additionally (identified in T53.2): the original HMAC computation excluded the `details` field
from the signed payload.  An attacker with write access to the log store could modify `details`
(e.g., changing a transaction amount from "100" to "9999999") without invalidating the
signature.

## Decision

### Original Decision (T48.4): Periodic Audit Chain Anchoring

Introduce periodic **audit chain anchoring**: at configurable intervals, publish a snapshot of
the chain head (hash + entry count + timestamp) to an external store.  This snapshot is an
**anchor record**.  Even if AUDIT_KEY is compromised and the in-process chain is rewritten, an
attacker cannot silently alter anchors already published to an S3 Object Lock bucket.

### Amendment (T53.2): Versioned HMAC Signature Format

Introduce a versioned signature scheme to close the `details`-exclusion gap while maintaining
backward compatibility with events written before the upgrade.

#### Signature Format

Signatures are stored as `<version>:<hex-digest>` strings in the `AuditEvent.signature` field:

| Version | HMAC Message Format | Notes |
|---------|---------------------|-------|
| `v1` | `timestamp\|event_type\|actor\|resource\|action\|prev_hash` | Legacy — `details` NOT signed |
| `v2` | `v2\|timestamp\|event_type\|actor\|resource\|action\|prev_hash\|<details_json>` | Current — `details` signed |

Where `<details_json>` is `json.dumps(details, sort_keys=True, separators=(",", ":"), allow_nan=False)`,
encoded as UTF-8.

**All new events use `v2`.**  `verify_event()` dispatches on the prefix and fails-closed on
unknown versions.

#### Design Constraints

1. **Version prefix inside HMAC**: The literal string `v2` is the first component of the v2
   HMAC message, not only a stored prefix.  This prevents a downgrade attack where an adversary
   strips the `details` field and relabels the signature as `v1` — the HMAC over the v2 message
   with `v2` prefix will never equal a v1 HMAC computed without it.

2. **Canonical details serialization**: `json.dumps` with `sort_keys=True` ensures the
   serialization is deterministic regardless of the insertion order of keys in the `details`
   dict.  Compact separators (`(",", ":")`) eliminate whitespace variation.

3. **Details size limit**: Canonical details JSON is limited to 64 KB (UTF-8 encoded).  Payloads
   exceeding this limit raise `ValueError` before the HMAC is computed, guarding against
   OOM-via-unbounded-payload attacks.

4. **Non-serializable value rejection**: `allow_nan=False` causes `json.dumps` to raise
   `ValueError` for `float('nan')` and `float('inf')`, preventing silent data corruption.

5. **Fail-closed on unknown versions**: `verify_event()` returns `False` immediately for any
   version prefix other than `v1:` or `v2:`.  It does not fall through to a default path.

6. **Constant-time comparison**: Both v1 and v2 verification paths use `hmac.compare_digest`
   to prevent timing-oracle attacks.

### Threat Model

| Threat | Mitigation |
|--------|-----------|
| AUDIT_KEY compromise → chain rewrite | Anchors in S3 COMPLIANCE mode cannot be deleted/modified, even by the bucket owner |
| Host compromise → local file rewrite | Local-file backend is WARNING-only; S3 Object Lock backend is recommended for production |
| Two workers anchoring simultaneously | AnchorManager uses a threading.Lock to serialize; only one anchor fires per threshold crossing |
| Clock skew / NTP drift | Time threshold uses `datetime.now(UTC)` monotonically; NTP drift of seconds is acceptable since anchor intervals are 24 h by default |
| Process crash between chain advance and anchor write | Anchoring is best-effort: backend failures are caught and logged at WARNING; audit chain is unaffected |
| Anchor tampering after publication | S3 COMPLIANCE Object Lock prevents modification or deletion during the retention period |
| First boot with no prior anchors | `AnchorManager` anchors immediately on the first call (no prior state) |
| **`details` field tampering** (T53.2) | **v2 HMAC includes details in signed payload; tampering invalidates signature** |
| **Version downgrade attack** (T53.2) | **Version prefix included in HMAC input; v2→v1 relabeling is cryptographically rejected** |
| **OOM via oversized details** (T53.2) | **64 KB limit on canonical details JSON raises ValueError before HMAC computation** |
| **Non-serializable details values** (T53.2) | **`allow_nan=False` rejects float('nan')/float('inf') at serialization time** |

### Design

```
AuditLogger.log_event()
    └─ _sign_v2(timestamp, ..., details)  ← HMAC over v2|...|details_json
    └─ advance chain (lock held)
    └─ emit JSON to logging (synchronous)
    └─ maybe_anchor(chain_head_hash, entry_count)  ← best-effort, non-blocking

AuditLogger.verify_event(event)
    ├─ sig starts with 'v2:' → _sign_v2(...) → hmac.compare_digest
    ├─ sig starts with 'v1:' → _sign_v1(...) → hmac.compare_digest
    └─ unknown prefix → return False (fail-closed)

AnchorManager.maybe_anchor(hash, count)
    ├─ lock acquired
    ├─ _should_anchor? (first-boot OR count threshold OR time threshold)
    ├─ construct AnchorRecord (frozen dataclass, validated)
    ├─ update _last_anchor_time and _last_anchored_entry_count
    ├─ lock released
    └─ backend.publish(anchor)  ← exceptions caught → WARNING log
```

### Backends

- **LocalFileAnchorBackend** — appends JSON lines to a local file.  Emits a WARNING on every
  `publish()` call to remind operators that local files provide no external attestation.  Use
  only for development or air-gapped deployments where S3 is unavailable.

- **S3ObjectLockAnchorBackend** — calls `s3.put_object` with `ObjectLockMode=COMPLIANCE` and a
  configurable retention period.  This is the production-grade backend.  Object Lock prevents
  modification or deletion of anchors, even by the AWS account root user, during the retention
  window.

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `ANCHOR_BACKEND` | `local_file` | Backend type: `local_file` or `s3_object_lock` |
| `ANCHOR_FILE_PATH` | `logs/audit_anchors.jsonl` | Path for local-file backend |
| `ANCHOR_EVERY_N_EVENTS` | `1000` | Publish anchor every N events |
| `ANCHOR_EVERY_SECONDS` | `86400` | Publish anchor at most once per N seconds (24 h) |

### Anchoring Trigger Logic

An anchor is published when **any** of the following is true:

1. First-ever call (no prior anchor).
2. `entry_count - last_anchored_entry_count >= anchor_every_n_events`.
3. `(now - last_anchor_time).seconds >= anchor_every_seconds`.

The count trigger uses a **delta** (events since last anchor), not an absolute threshold.

### Verification CLI

`scripts/verify-audit-chain.py` reads a local anchor file and compares the most recent anchor
against the provided chain head hash and entry count.  This is a standalone CLI tool, not a
test fixture.

## Consequences

**Positive:**
- Materially strengthens tamper-evidence: AUDIT_KEY compromise alone is insufficient to erase
  evidence of an attack if anchors are in S3 Object Lock COMPLIANCE mode.
- Pluggable backend design allows testing with in-memory mocks and production use with S3.
- Best-effort design never degrades audit throughput.
- (T53.2) `details` field is now cryptographically bound to each event's signature.
- (T53.2) Backward-compatible: existing v1 events remain verifiable indefinitely.
- (T53.2) Version downgrade attack is closed at the HMAC level, not just via prefix convention.

**Negative / Trade-offs:**
- LocalFileAnchorBackend (the default) provides weaker guarantees than the threat model ideally
  requires; operators must explicitly configure S3 for production deployments.
- S3ObjectLockAnchorBackend requires explicit bootstrapper wiring (an S3 client is not
  auto-constructed from settings alone).
- AnchorManager adds a small per-event check (~2 μs) for the threshold comparison.
- (T53.2) v2 signatures are 3 bytes longer than v1 (`v2:` prefix vs raw hex).
- (T53.2) Canonical details serialization adds ~5–50 μs per event depending on payload size.

## Related Decisions

- ADR-0010 — AuditLogger singleton and hash-chain design.
- T48.5 — ALE Vault Dependency Enforcement (parallel task, same phase).
- T53.2 — Audit HMAC: Include Details Field in Signature (this amendment).
