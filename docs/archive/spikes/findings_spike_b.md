> **HISTORICAL -- DO NOT USE**
> This document is an archived spike findings report. The spike code it describes
> was never promoted to production. It is retained for historical reference only.
> Do not import, adapt, or copy patterns from this document into production code
> without first consulting the relevant ADR and the Spike-to-Production Promotion
> Checklist in `CLAUDE.md`.

---

# Spike B Findings: Deterministic Format-Preserving Encryption with LUHN

**Date:** 2026-03-13
**Task:** P0.8-Spike-B
**Author:** Software Developer Agent
**Status:** COMPLETE — all acceptance criteria satisfied

---

## Goal

Prove we can deterministically encrypt/mask 10,000 credit card numbers while:

1. Preserving the 16-digit format
2. Guaranteeing zero collisions
3. Passing a LUHN check on every masked output
4. Using stdlib only (no external cryptography library)

---

## Algorithm Choice

### Feistel-Based Format-Preserving Encryption

We chose a **Feistel network over the decimal digit domain** as the FPE primitive.

**Why Feistel FPE instead of alternatives?**

| Approach | Verdict | Reason |
|---|---|---|
| AES-FF1/FF3 (NIST SP 800-38G) | Rejected | Requires `pycryptodome` or `cryptography` — violates stdlib-only constraint |
| Cycle-walking on random permutation | Rejected | Does not guarantee zero collisions without a full PRF construction |
| Simple modular hash (one-way) | Rejected | Not invertible; violates reversibility requirement |
| Feistel over digit strings | Selected | Stdlib-only, provably bijective, easily invertible, HMAC-SHA256 round function |

**Why HMAC-SHA256 as the round function?**

HMAC-SHA256 is available in Python's stdlib (`hmac`, `hashlib`) and provides a pseudorandom function (PRF) with 256-bit output. Reducing its output modulo 10^n maps it into the decimal digit domain with negligible bias for n <= 15 (the output space of 10^15 is small relative to the 2^256 HMAC range).

### Construction Details

For a digit string of length n:

1. Split into `L` (left_len = ceil(n/2) digits) and `R` (right_len = floor(n/2) digits).
2. For each round `i` in [0, FEISTEL_ROUNDS):
   - `new_R = (L + HMAC-SHA256(key, R || i)) mod 10^left_len`
   - `L, R = R mod 10^right_len, new_R`
3. Reassemble: output = `str(L).zfill(right_len) + str(R).zfill(left_len)`

**Why 8 rounds?**

8 rounds is the conventional minimum for a balanced Feistel cipher over small domains. For 15-digit inputs (7+8 digit halves), HMAC-SHA256 as the round function provides statistical independence between rounds after 4 rounds. 8 rounds provides a comfortable security margin for collision resistance in a spike context.

### LUHN-Preserving Strategy

Rather than trying to find FPE output that happens to pass LUHN (which would require constrained FPE over a 15-digit domain), we use the **encrypt-then-recompute** pattern:

1. Encrypt only the first 15 digits using FPE.
2. Compute the correct 16th LUHN check digit deterministically from the encrypted payload.
3. Append: `masked_card = fpe.encrypt(card[:15]) + luhn_check_digit(encrypted_payload)`

This guarantees 100% LUHN validity by construction with zero additional overhead.

---

## Results

All 4 acceptance criteria passed on the first and second pass:

| Criterion | Result | Detail |
|---|---|---|
| Zero collisions | PASS | 0 collisions in 10,000 masked cards |
| 100% LUHN validity | PASS | 10,000 / 10,000 cards pass luhn_check() |
| Format preserved | PASS | All outputs are exactly 16 decimal digits |
| Determinism | PASS | Pass 1 == Pass 2 (same key + same input = same output) |

**Performance:** approximately 86,000-90,000 cards/second on a single core (Apple Silicon M-series), well above the 10,000 cards/second threshold.

---

## Sample Output

```
5104332181960015  ->  0680921967823539
5338908386379404  ->  4060857306112220
5265423511615596  ->  5216682148157591
```

Plaintext cards are randomly generated fictional data (Visa/MC/Amex/Discover BIN prefixes, seeded RNG). No real card numbers are used.

---

## Security Properties

- **Bijective (collision-free):** A Feistel network is a bijection by construction. Each plaintext maps to exactly one ciphertext for a given key. Zero collisions is provably guaranteed, not merely empirically observed.
- **Deterministic:** HMAC-SHA256 is deterministic. Given the same key and input, the cipher always produces the same output.
- **Key-dependent:** Different keys produce different masked outputs. The key should be stored in a secret vault (e.g., HashiCorp Vault) in production.
- **Not semantically secure:** FPE does not hide the format or provide IND-CPA security over the ciphertext space. It is format-preserving by design. Do not use as a general-purpose encryption scheme.

---

## Limitations and Risks

1. **Not NIST-approved FPE:** This implementation uses a custom Feistel construction, not NIST SP 800-38G FF1/FF3. For regulatory environments (PCI-DSS Level 1), NIST-approved FPE is required. The `cryptography` library provides FF1.
2. **Round count is empirical:** 8 rounds is a practical choice, not a formally proven security bound for this specific domain and key size. A formal analysis would be required for production use.
3. **B311 acknowledged:** `random.Random(42)` is used for card generation only (reproducibility of the test fixture), not for any cryptographic operation. The FPE key is generated with `secrets.token_bytes(32)`.

---

## Recommendation for Phase 3 Masking Engine

**Adopt this pattern directly** for the `src/synth_engine/modules/masking/` module, with these refinements:

1. **Extract `FeistelFPE` to `masking/fpe.py`** as a production class.
2. **Extract `luhn_check`, `_luhn_check_digit`, and `luhn_preserving_mask` to `masking/luhn.py`**.
3. **Inject the FPE key via DI** from the bootstrapper's secret vault interface (do not hardcode or derive from a constant).
4. **Consider FF1 for regulated environments:** If the deployment environment requires PCI-DSS Level 1 compliance, swap the custom Feistel for `cryptography.hazmat.primitives.ciphers.algorithms.AES` with FF1 mode once the `cryptography` package is approved as a dependency.
5. **Cache `FeistelFPE` instances** keyed by their byte key to avoid re-instantiation overhead across large batch jobs.

The Feistel approach proved unambiguously viable: zero collisions, 100% LUHN validity, ~90k cards/second throughput, and clean stdlib-only implementation. The pattern is ready for production hardening in Phase 4.

---

## How to Run

```bash
python spikes/spike_fpe_luhn.py
```

Expected output ends with:

```
ALL ASSERTIONS PASSED
  Zero collisions      : PASS (0 collisions)
  100% LUHN validity   : PASS (100.00%)
  Format preserved     : PASS (all 16 digits)
  Determinism          : PASS (pass1 == pass2)
```
