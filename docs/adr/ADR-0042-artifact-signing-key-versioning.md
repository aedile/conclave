# ADR-0042: Artifact Signing Key Versioning

**Status**: Accepted
**Date**: 2026-03-21
**Task**: T42.1 — Implement Artifact Signing Key Versioning

## Context

The engine signs Parquet artifacts using HMAC-SHA256 to detect tampering.
Prior to T42.1, a single key (`ARTIFACT_SIGNING_KEY`) was used for all
artifacts.  A compromised key made all artifacts forgeable and all prior
artifacts unverifiable after rotation — there was no rotation mechanism.

## Decision

### Signature Format

Versioned signatures use the format:

```
KEY_ID (4 bytes) || HMAC-SHA256 (32 bytes) = 36 bytes total
```

The `KEY_ID` is a 4-byte opaque identifier stored as a big-endian hex string
in configuration.  It is embedded as a plain prefix (not encrypted) so that
verification code can select the correct key without an exhaustive search.

Legacy artifacts (pre-versioning) carry a bare 32-byte HMAC.  These are
auto-detected by length and treated as if signed with `LEGACY_KEY_ID =
0x00000000`.  The operator must map `LEGACY_KEY_ID` to the original key in
`ARTIFACT_SIGNING_KEYS` during the rotation window.

### Key Store

`ConclaveSettings` gains two new fields:

- `artifact_signing_keys: dict[str, str]` — JSON-encoded map of hex key ID
  strings to hex key strings.  All known keys (active + retired) go here to
  support the rotation window during which old artifacts remain verifiable.

- `artifact_signing_key_active: str | None` — The hex key ID string
  identifying which key in `artifact_signing_keys` is used to sign new
  artifacts.

The legacy `artifact_signing_key` field is retained for backward
compatibility.  If `artifact_signing_keys` is absent or empty, the system
falls back to legacy single-key mode.

### Key Resolution Priority (verification)

1. All entries in `artifact_signing_keys` (versioned mode).
2. `artifact_signing_key` mapped to `LEGACY_KEY_ID` (legacy backward compat).
3. If neither is set: signing is not enabled; verification is skipped.

### Signing Priority (production)

1. Versioned: when `artifact_signing_keys` + `artifact_signing_key_active`
   are both set.
2. Legacy: when only `artifact_signing_key` is set.
3. Unsigned: when neither is set (acceptable in development; WARNING logged).

### Audit Trail

Key rotation events are logged via `log_key_rotation_event()` to the WORM
audit trail.  The event type is `KEY_ROTATION` with `old_key_id` and
`new_key_id` in `details`.  This satisfies AC5.

## Alternatives Considered

### Asymmetric signing (RSA/Ed25519)

Would eliminate key distribution requirements between signing and
verification services.  Deferred because the system is a monolith — the
signer and verifier run in the same process.  Asymmetric overhead not
justified.  Can be revisited if the architecture splits.

### Envelope encryption with KEK

Too complex for the current phase.  The existing vault KEK could wrap
signing keys in a future iteration.

### Versioned signatures stored separately from data

Embedding the key ID in the `.sig` sidecar (not the Parquet file) keeps
the Parquet files untouched.  Avoids any dependency on Parquet library
internals for signature storage.  Accepted.

## Consequences

- **Positive**: Key rotation is now safe and non-disruptive.  Old artifacts
  remain verifiable during the rotation window.  Key rotation events are
  auditable.

- **Positive**: Legacy artifacts are backward-compatible; existing deployments
  do not need immediate migration.

- **Neutral**: Signature size increases by 4 bytes (36 vs 32) for new
  artifacts.  Negligible.

- **Negative**: Operators must set two env vars (`ARTIFACT_SIGNING_KEYS` and
  `ARTIFACT_SIGNING_KEY_ACTIVE`) instead of one.  The legacy path remains
  for simpler deployments.

## Enforcement

`verify_versioned()` enforces constant-time comparison via
`hmac.compare_digest` for both versioned and legacy formats.
The `KEY_ID_SIZE = 4` and `HMAC_DIGEST_SIZE = 32` constants are
tested directly in `test_key_versioning.py`.
