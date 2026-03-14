# ADR-0014 — Deterministic Masking Engine Design

**Date:** 2026-03-13
**Status:** Accepted
**Deciders:** Conclave Engine Team
**Task:** P3-T3.3

---

## Context

The Conclave Engine must replace real PII values (names, emails, SSNs, credit
card numbers, phone numbers) with fake but realistic-looking equivalents before
synthetic data is exported.  Two hard requirements drive this decision:

1. **Referential integrity**: the same real value appearing in multiple rows or
   tables must always mask to the *same* fake value.  A non-deterministic
   approach (e.g., random Faker seeding per call) would break foreign-key
   relationships and make the synthetic dataset unusable for testing.

2. **No collisions**: two *different* real values must not mask to the *same*
   fake value within the same column domain (table + column salt).  Collisions
   would corrupt uniqueness constraints and JOIN results.

---

## Decision

### 1. Deterministic approach: HMAC-SHA256 seeding Faker

A per-record seed is derived using `hmac.new(salt, value, hashlib.sha256)`.
The first 8 bytes of the digest are converted to an integer and passed to
`Faker.seed_instance()`.

**Why HMAC over plain SHA-256?**
- HMAC provides domain separation via the salt key, preventing cross-column
  hash collisions even for identical values.
- HMAC-SHA256 is a PRF (pseudorandom function): outputs are computationally
  indistinguishable from random, giving excellent distribution across Faker's
  name/email pools.
- It uses only stdlib (`hmac`, `hashlib`) — no additional dependency.

**Why Faker over hand-crafted generation?**
- Faker produces realistic-looking data (real names, valid email formats, proper
  SSN structure) that passes downstream schema validation.
- Faker is already a common project dependency; no new transitive risk.
- Faker's `credit_card_number()` generates LUHN-valid numbers by construction,
  satisfying the LUHN compliance requirement without a custom implementation.

**Performance optimization**: a single module-level `Faker` instance is reused
across all calls.  `seed_instance()` fully resets internal state before each
call, preserving determinism.  This avoids per-call construction overhead and
delivers a ~7x throughput improvement (benchmarked: 420ms → 58ms per 1,000
calls).  The module-level instance is not thread-safe; concurrent callers must
use separate instances.

### 2. Collision prevention: two-phase retry + suffix strategy

The registry tracks `(salt → set[masked_value])` across a single
table-processing run.  When a collision is detected:

**Phase 1 — Retry (max 10 attempts):** Re-derive the hash using
`f"{value}_{attempt}"` as the input.  Changing the input changes the HMAC
output, yielding a different Faker seed and (almost certainly) a different name.
This covers the common case of small datasets with sparse collisions.

**Phase 2 — Suffix:** If all 10 retry hashes still collide (which occurs for
large datasets where Faker's name pool is exhausted relative to the dataset
size), a deterministic numeric suffix is appended to the masked output
(`"John Smith_1"`, `"John Smith_2"`, ...).  This guarantees uniqueness for
arbitrarily large datasets, including the mandatory 100,000-record benchmark.

**Why not just increase `_MAX_RETRIES`?**
A higher retry cap would help but cannot guarantee uniqueness for datasets
larger than Faker's output pool.  The suffix phase provides a provable
O(1)-per-record uniqueness guarantee regardless of pool size.

**Why `_MAX_RETRIES = 10`?**
Ten attempts is sufficient for datasets well under Faker's pool size (~5,000+
distinct names).  Beyond that, Phase 2 takes over cleanly.

### 3. LUHN compliance: Faker credit_card_number() rationale

`Faker.credit_card_number(card_type=None)` generates a random card number
that passes the LUHN check by construction.  The implementation generates the
first N-1 digits randomly and computes the check digit algorithmically.  No
post-processing or verification step is needed in the masking layer — the
property is guaranteed by Faker's implementation.

A standalone `luhn_check()` function is provided in `masking/luhn.py` and
re-exported from `algorithms.py` to:
(a) verify the guarantee in unit tests, and
(b) provide a reusable primitive for any future downstream validation.

### 4. Length constraint: truncation strategy

Every masking function accepts an optional `max_length: int | None` parameter.
When set, the masked output is truncated to `max_length` characters via
Python slice notation (`result[:max_length]`).

**Trade-offs of truncation:**
- Simplest approach with zero risk of infinite loops.
- May break email format if truncated before the `@` sign — callers should set
  `max_length` conservatively for email columns (at least 15 characters
  recommended).
- Does not violate determinism: the same (value, salt, max_length) triple
  always produces the same truncated result.

An alternative (regenerating until output fits) was rejected: it is not
guaranteed to terminate and adds significant complexity.

---

## Security Trade-offs (ADV-027)

### HMAC key is a column identity, not a secret

The `salt` parameter passed to `deterministic_hash()` (and through it to HMAC)
is a **column-identity string** such as `"users.email"` — **not** a secret
value.  This is intentional, and the reasoning is as follows:

**What the masking layer provides:**
- **Determinism**: the same real value in the same column always maps to the
  same masked value, preserving referential integrity across tables regardless
  of the order in which rows or tables are processed.
- **Domain separation**: two columns with the same value but different
  `"table.column"` salts produce different masked outputs, preventing
  cross-column leakage.

**What the masking layer does NOT provide:**
- **Confidentiality of the mapping**: because the salt is public (it is the
  column name), an attacker who knows the code and the masked value could
  brute-force the original value for low-entropy columns (e.g., a boolean
  stored as a name).

**Why this is acceptable for this project:**
Confidentiality of the real-to-masked mapping is provided by a
deployment-level `MASKING_SALT` environment variable, which is combined with
the column-identity salt at the CLI / bootstrapper call site (Phase 4,
ADV-035).  The masking module itself has no access to deployment secrets —
this is intentional to keep the module stateless and testable without
secret-management infrastructure.

**Summary of layered security:**
```
[Real PII value]
    |
    v
[HMAC(key="MASKING_SALT + users.email", msg=value)]  ← deployment secret injected here
    |
    v
[Faker seed → deterministic fake name]
```

The `MASKING_SALT` layer is out of scope for ADR-0014 and will be documented
in the Phase 4 bootstrapper ADR.

---

## Module boundaries

`synth_engine.modules.masking` imports only:
- stdlib: `hashlib`, `hmac`, `collections.abc`, `enum`, `typing`
- third-party: `faker` (production dependency)

It does **not** import from `ingestion`, `profiler`, `synthesizer`, `privacy`,
or `bootstrapper`.  This satisfies the import-linter independence contract
defined in `pyproject.toml`.

---

## Consequences

### Positive
- Referential integrity is preserved across multi-table synthetic datasets.
- The 100,000-record no-collision requirement is satisfied in ~9 seconds.
- LUHN-valid credit card masking is guaranteed without custom digit arithmetic.
- All constraints are enforced with stdlib + Faker only; no cryptographic
  library is added.

### Negative / Risks
- **Thread safety**: the module-level `_FAKER` instance is not thread-safe.
  Concurrent masking pipelines must instantiate separate registries and ensure
  each thread has its own Faker instance.  This is documented and acceptable for
  the current single-threaded ingestion pipeline.
- **Truncated emails**: aggressive `max_length` values on email columns can
  produce syntactically invalid (truncated) email strings.  The caller is
  responsible for setting reasonable constraints.
- **Faker pool exhaustion**: for datasets exceeding ~5,000 unique values per
  salt domain, Phase 2 suffix disambiguation activates, producing values like
  `"John Smith_42"` that are less realistic.  For most enterprise datasets this
  is acceptable.
- **Low-entropy column risk**: for boolean-like columns masked as names, an
  attacker with code access could enumerate the ~2-value space in O(1).  The
  deployment-level `MASKING_SALT` (Phase 4) mitigates this for production
  deployments.
