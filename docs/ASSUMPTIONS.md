# Domain Assumption Register

Claims the system makes about the problem domain that cannot be verified by the automated
development harness. These require external domain expert review.

See Rule 32 in CLAUDE.md for the mandate behind this register.

---

## Cryptographic Assumptions

| # | Claim | Source | Confidence | Harness-Verifiable | Status |
|---|-------|--------|------------|-------------------|--------|
| A-001 | PBKDF2-HMAC-SHA256 with 600,000 iterations meets NIST SP 800-63B for KEK derivation | Claude training data + NIST guidelines | High | No — requires cryptographic review | Unverified |
| A-002 | `ctypes.memset` prevents compiler optimization of memory zeroing in CPython | Claude training data + CPython implementation knowledge | Medium | No — requires CPython internals review | Unverified |
| A-003 | HMAC-SHA256 deterministic masking with a secret salt is computationally infeasible to reverse given a constrained input space (names, emails, SSNs) | Standard HMAC security properties | Medium | No — depends on salt entropy, dictionary size, and attacker resources | Unverified |
| A-004 | Fernet (AES-128-CBC + HMAC-SHA256) via the `cryptography` library provides adequate ALE for data at rest | `cryptography` library documentation | High | No — requires cryptographic review of key management lifecycle | Unverified |

## Differential Privacy Assumptions

| # | Claim | Source | Confidence | Harness-Verifiable | Status |
|---|-------|--------|------------|-------------------|--------|
| A-005 | Applying Opacus DP-SGD to the CTGAN discriminator provides a formal (epsilon, delta) privacy guarantee on the generator's synthetic output | Standard DP-GAN threat model: generator never sees real data directly; discriminator gradients are the privacy boundary | Medium | No — requires DP/ML researcher review | Unverified |
| A-006 | The Opacus RDP accountant correctly computes epsilon given the configured delta, noise multiplier, sample rate, and epoch count | Opacus library implementation | High | Partially — epsilon values are measured post-training, but correctness of the accountant itself is trusted from the library | Unverified |
| A-007 | Epsilon values below ~3.0 provide "strong" privacy; values below ~1.0 provide "excellent" privacy | DP literature conventions; no universal standard exists | Low | No — these thresholds are convention, not formally defined | Unverified |
| A-008 | The proxy-model fallback (ADR-0025) provides a "practical approximation" of DP | Project documentation; the proxy model trains on the same preprocessed data but is separate from the generator | Low | No — the proxy model's DP guarantee does not formally transfer to the generator output | Unverified |

## Compliance Assumptions

| # | Claim | Source | Confidence | Harness-Verifiable | Status |
|---|-------|--------|------------|-------------------|--------|
| A-009 | Differentially private synthetic data may qualify as non-personal data under GDPR Recital 26, depending on epsilon | Legal interpretation of Recital 26 "reasonably likely means" standard | Low | No — this is a legal determination, not a technical one | Unverified |
| A-010 | NIST SP 800-88 Rev 1 cryptographic erasure (key destruction) is sufficient for compliance with GDPR Article 17 right to erasure | NIST guidance + GDPR interpretation | Medium | No — requires legal counsel review per jurisdiction | Unverified |
| A-011 | The WORM audit log with HMAC signature chain provides adequate tamper evidence for SOC 2 / HIPAA audit requirements | General compliance patterns | Medium | No — requires compliance auditor review against specific control frameworks | Unverified |

## Architectural Assumptions

| # | Claim | Source | Confidence | Harness-Verifiable | Status |
|---|-------|--------|------------|-------------------|--------|
| A-012 | The modular monolith with import-linter boundary enforcement provides equivalent isolation to microservice network boundaries for this use case | ADR-0001 | High | Partially — import-linter enforces at commit time, but runtime `importlib` circumvention is possible (caught in P22) | Verified by convention |
| A-013 | Single-process + Huey worker is sufficient for the target deployment scale (single organization, <10 concurrent jobs) | ADR-0062 (single-operator model) | High | No — load testing was done on single-node only | Partially verified |
