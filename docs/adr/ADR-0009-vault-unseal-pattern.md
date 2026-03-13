# ADR-0009: Vault Unseal Pattern

**Date:** 2026-03-13
**Status:** Accepted
**Task:** P2-T2.4 — Vault Observability
**Deciders:** Engineering Team

---

## Context

The Conclave Engine handles sensitive operations (KEK derivation, ALE encryption,
JWT signing) that must not be accessible to an attacker who gains filesystem
access to the container.  A "sealed vault" pattern ensures the engine boots into
a locked state and only becomes operational after an authorised operator provides
a passphrase at runtime.

---

## Decision

### Boot State: SEALED

`VaultState` is a class with class-level state (`_is_sealed = True`,
`_kek = None`).  On startup the engine is sealed.  Any request to a non-exempt
route returns **423 Locked** via `SealGateMiddleware`.

### KEK Derivation: PBKDF2-HMAC-SHA256

```
KEK = PBKDF2_HMAC(
    hash  = SHA-256,
    pwd   = passphrase.encode(),
    salt  = VAULT_SEAL_SALT (base64url-decoded, >= 16 bytes),
    iters = 600_000,
    dklen = 32,
)
```

600,000 iterations is the 2024 OWASP recommendation for PBKDF2-SHA256 for
password-based key derivation.  `hashlib.pbkdf2_hmac` is used (stdlib only —
no new dependencies).

### Ephemeral-Only Storage: bytearray Zeroing

The KEK is stored in a `bytearray`, never `bytes`, so it can be deterministically
zeroed with `memoryview` when the vault is re-sealed:

```python
mv = memoryview(cls._kek)
for i in range(len(mv)):
    mv[i] = 0
```

This limits the window during which an attacker who reads `/proc/self/mem` or
a core dump could recover the key.  The passphrase is never stored.

### Salt: `VAULT_SEAL_SALT` Environment Variable

The salt is not secret — it merely prevents rainbow-table pre-computation of
the KEK for common passphrases.  It is provisioned as a base64url-encoded
environment variable so that it can be set once per deployment and is
consistent across process restarts (same passphrase → same KEK).

If `VAULT_SEAL_SALT` is missing at unseal time, `VaultState.unseal()` raises
`ValueError` — the engine refuses to derive a zero-salt KEK.

### HTTP Status: 423 Locked

RFC 4918 (WebDAV) defines 423 as "Locked".  This is the most semantically
accurate status code for "this resource is not accessible because the vault is
sealed".  Clients receive a clear `{"detail": "Service sealed. POST /unseal to activate."}` body.

### Exempt Routes

The following paths bypass the seal gate and are accessible without unsealing:

- `/unseal` — the unseal operation itself
- `/health` — liveness probe for orchestrators
- `/metrics` — Prometheus scrape (internal-only, see ADR-0011)
- `/docs`, `/redoc`, `/openapi.json` — API documentation

---

## Consequences

**Positive:**
- Zero key material persists to disk at any point.
- The KEK can be zeroed on demand (graceful re-seal).
- 423 gate is enforced at the middleware layer — no route handler can
  accidentally bypass it.
- `PBKDF2_HMAC` is stdlib; no new cryptographic dependencies introduced.

**Negative / Mitigations:**
- An operator must POST `/unseal` after every container restart.  This is
  intentional — it enforces human-in-the-loop for key material activation.
- The sealed state is class-level (singleton).  Tests must call
  `VaultState.reset()` in teardown to prevent state bleed.  This is enforced
  by a `pytest` autouse fixture.
