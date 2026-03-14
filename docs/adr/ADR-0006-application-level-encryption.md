# ADR-0006: Application-Level Encryption for PII Fields

**Status:** Accepted — amended 2026-03-13 (vault-KEK wiring)
**Date:** 2026-03-13
**Amended:** 2026-03-13 (fix/P2-debt-D1 — ALE-Vault KEK wiring)
**Deciders:** Project team
**Task:** P2-T2.2 — Secure Database Layer
**Fix:** P2-debt-D1 — ALE-Vault KEK wiring

## Context

The Conclave Engine stores personally identifiable information (PII) in a
PostgreSQL database.  Relying solely on database-level encryption (e.g.
PostgreSQL Transparent Data Encryption or filesystem-level LUKS) is
insufficient because:

- A compromised DBA account or `SUPERUSER` credential grants full plaintext
  read access to all rows, bypassing TDE entirely.
- Database backup files (WAL archives, `pg_dump` snapshots) contain the
  same data in the same encrypted-at-rest form — if the backup storage key
  and the database key are the same (common default), a stolen backup yields
  plaintext PII.
- In air-gapped BYOC deployments the host organisation controls the
  infrastructure layer, reducing the reliability of database-engine security
  guarantees as a sole control.

The principle of defence-in-depth requires that PII be unintelligible to any
party that does not hold the application-layer encryption key, regardless of
their access to the database engine or its backups.

### Architectural debt resolved (2026-03-13)

Task P2-T2.2 originally specified that ALE must "tie into the Vault Unseal
state," but the initial implementation sourced the ALE key exclusively from
the `ALE_KEY` environment variable, leaving the vault KEK and ALE completely
decoupled.  fix/P2-debt-D1 resolves this by wiring `get_fernet()` to derive
the ALE key from the vault KEK via HKDF-SHA256 whenever the vault is unsealed.

## Decision

Implement **Application-Level Encryption (ALE)** for all PII columns using
`cryptography.Fernet` (AES-128-CBC + HMAC-SHA256) surfaced as a SQLAlchemy
`TypeDecorator` named `EncryptedString`.

### Key design choices

| Concern | Decision |
|---------|----------|
| Algorithm | `cryptography.Fernet` — AES-128-CBC + HMAC-SHA256 (AEAD semantics) |
| Primary key source | Vault KEK, derived via HKDF-SHA256 when vault is unsealed |
| Fallback key source | `ALE_KEY` environment variable — sealed vault / development only |
| Key derivation | HKDF-SHA256; see parameters below |
| Performance | `get_fernet()` is not cached; HKDF is fast and caching across seal/unseal is incorrect |
| Test isolation | `_reset_fernet_cache()` retained as documented no-op for backward compatibility |
| Integration point | `EncryptedString` TypeDecorator — annotate any SQLModel column for transparent encrypt/decrypt |
| Nullability | `None` values pass through unchanged to support nullable columns |
| Integrity | Fernet tokens carry an HMAC; tampered ciphertext raises `InvalidToken` |

### Vault-KEK wiring via HKDF-SHA256

When the vault is **unsealed**, `get_fernet()` derives the 32-byte ALE key
from the vault Key Encryption Key (KEK) using HKDF with the following
parameters:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Algorithm | HMAC-SHA256 | Standard HKDF recommendation; FIPS-140-2 approved |
| Length | 32 bytes | AES-256 key material; Fernet requires 32 URL-safe base64 bytes |
| Salt | `b"conclave-ale-v1"` | Fixed public label — not secret; provides context versioning and cross-context domain separation |
| Info | `b"application-level-encryption"` | Sub-context label that distinguishes ALE derivation from other future KEK consumers |

The salt and info values are public (not secret).  Their purpose is to prevent
key reuse across contexts and to version the derivation function so that a
future rotation of either label constitutes a distinct key stream.

### ALE_KEY env-var fallback (sealed vault / development only)

When the vault is **sealed**, `get_fernet()` falls back to the `ALE_KEY`
environment variable.  This path is intended for:

- Local development without a running vault unseal flow
- Unit and integration tests that do not test vault integration
- Emergency recovery scenarios where the vault cannot be unsealed

**This fallback must not be used in production.**  In production, the vault
must be unsealed before any ALE encrypt/decrypt operations are performed.
A sealed vault producing ALE ciphertext (via `ALE_KEY`) is inconsistent with
a subsequently unsealed vault: the two key streams are distinct and ciphertext
encrypted under one cannot be decrypted by the other.

### Key rotation implications

Vault passphrase rotation (which produces a new KEK via PBKDF2) directly
rotates the ALE key, because the ALE key is derived from the KEK.  **All
existing ciphertext encrypted under the previous KEK becomes unreadable
after passphrase rotation** unless a re-encryption migration is performed:

1. Unseal with the old passphrase.
2. Decrypt all ALE-encrypted rows (reads use the old-KEK-derived ALE key).
3. Seal and re-unseal with the new passphrase.
4. Re-encrypt all rows (writes use the new-KEK-derived ALE key).
5. Retire the old passphrase.

This migration must be scripted, tested in a staging environment, and
executed within a maintenance window with the database in read-only mode.

### Placement

`src/synth_engine/shared/security/ale.py` — inside the `shared/` cross-cutting
utilities package, consistent with the File Placement Rules in `CLAUDE.md`.

## Consequences

### Positive

- PII columns are encrypted before they reach the PostgreSQL wire protocol.
  A DBA or a stolen backup file yields only Fernet tokens, not plaintext.
- Encryption and decryption are transparent to callers: SQLModel models
  annotated with `EncryptedString` require no additional application logic.
- Fernet's HMAC provides integrity protection: corrupted or tampered rows
  surface as `cryptography.fernet.InvalidToken` rather than silent data
  corruption.
- The ALE key lifecycle is now bound to the vault: sealing the vault
  eliminates the in-memory ALE key (derived on demand from the KEK), reducing
  the attack surface after an administrative seal.
- HKDF derivation is deterministic: the same passphrase always produces the
  same KEK (via PBKDF2) which always produces the same ALE key (via HKDF),
  ensuring consistent encryption across application restarts.

### Negative / Constraints

- **Vault passphrase rotation requires row re-encryption.**  Rotating the
  operator passphrase rotates the KEK and therefore the ALE key.  All existing
  ciphertext must be re-encrypted before the old passphrase is retired.  A
  migration script must be written and tested before any passphrase rotation.
- **Loss of the vault passphrase means permanent data loss** for all
  encrypted columns.  The passphrase (or its KEK derivation) must be backed
  up to a hardware security module (HSM) or equivalent.  For air-gapped
  deployments, offline cold-storage backup of the passphrase is mandatory.
- **`ALE_KEY` env-var ciphertext is incompatible with vault-KEK ciphertext.**
  Data encrypted via the env-var fallback cannot be decrypted after unsealing,
  and vice versa.  Mixing key paths within a single database is a data loss
  risk and must be prevented by operational procedure.
- **Database-level search on encrypted columns is not possible.**  Queries
  that filter or sort on PII fields (e.g. `WHERE ssn = ?`) are not supported
  without application-side decryption and comparison.  Index design must
  account for this constraint.
- **Callers must handle `InvalidToken`.**  Any code that calls
  `process_result_value` on a corrupted or tampered row must be prepared to
  catch `cryptography.fernet.InvalidToken` and handle it as a data integrity
  failure.

## Compliance

This decision directly supports compliance with:

- **GDPR Article 32** — appropriate technical measures including encryption
- **HIPAA § 164.312(a)(2)(iv)** — encryption and decryption of ePHI
- **CCPA** — reasonable security measures for personal information

CONSTITUTION Priority 0: Security — encryption of PII at the application
layer is a non-negotiable security control for this project.
