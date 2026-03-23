# ADR-0048 — Audit Trail Anchoring Architecture

**Status**: Accepted
**Date**: 2026-03-22
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

## Decision

Introduce periodic **audit chain anchoring**: at configurable intervals, publish a snapshot of
the chain head (hash + entry count + timestamp) to an external store.  This snapshot is an
**anchor record**.  Even if AUDIT_KEY is compromised and the in-process chain is rewritten, an
attacker cannot silently alter anchors already published to an S3 Object Lock bucket.

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

### Design

```
AuditLogger.log_event()
    └─ advance chain (lock held)
    └─ emit JSON to logging (synchronous)
    └─ maybe_anchor(chain_head_hash, entry_count)  ← best-effort, non-blocking

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

**Negative / Trade-offs:**
- LocalFileAnchorBackend (the default) provides weaker guarantees than the threat model ideally
  requires; operators must explicitly configure S3 for production deployments.
- S3ObjectLockAnchorBackend requires explicit bootstrapper wiring (an S3 client is not
  auto-constructed from settings alone).
- AnchorManager adds a small per-event check (~2 μs) for the threshold comparison.

## Related Decisions

- ADR-0010 — AuditLogger singleton and hash-chain design.
- T48.5 — ALE Vault Dependency Enforcement (parallel task, same phase).
