# ADR-0004: Application-Level Encryption for PII Fields

**Status:** Accepted
**Date:** 2026-03-13
**Deciders:** Project team
**Task:** P2-T2.2 — Secure Database Layer

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

## Decision

Implement **Application-Level Encryption (ALE)** for all PII columns using
`cryptography.Fernet` (AES-128-CBC + HMAC-SHA256) surfaced as a SQLAlchemy
`TypeDecorator` named `EncryptedString`.

### Key design choices

| Concern | Decision |
|---------|----------|
| Algorithm | `cryptography.Fernet` — AES-128-CBC + HMAC-SHA256 (AEAD semantics) |
| Key source | `ALE_KEY` environment variable; never baked into the image or committed to VCS |
| Performance | `functools.lru_cache(maxsize=1)` on `get_fernet()` eliminates repeated env-var lookups |
| Test isolation | `_reset_fernet_cache()` clears the cache so monkeypatched keys take effect |
| Integration point | `EncryptedString` TypeDecorator — annotate any SQLModel column for transparent encrypt/decrypt |
| Nullability | `None` values pass through unchanged to support nullable columns |
| Integrity | Fernet tokens carry an HMAC; tampered ciphertext raises `InvalidToken` |

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
- The `lru_cache` design means the key is parsed once at first use;
  subsequent operations are pure in-process calls with no I/O overhead.

### Negative / Constraints

- **Key rotation requires re-encryption of all rows.**  Rotating `ALE_KEY`
  means all existing ciphertext must be decrypted with the old key and
  re-encrypted with the new key before the old key is retired.  A migration
  script must be written and tested before any key rotation.
- **Loss of `ALE_KEY` means permanent data loss** for all encrypted columns.
  The key must be backed up to a hardware security module (HSM) or secrets
  manager.  For air-gapped deployments, offline cold-storage backup of the
  key material is mandatory.
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
