# ADR-0017a — Opacus `secure_mode` Decision (Amendment to ADR-0017)

**Date:** 2026-03-15
**Status:** Accepted
**Deciders:** PM + Architect
**Task:** P8-T8.2 — Security Hardening (ADV-067)
**Amends:** ADR-0017 (Synthesizer & Differential Privacy Library Selection)

---

## Context

ADV-067, raised during the P7-T7.3 DevOps review, called for evaluating whether
`PrivacyEngine(secure_mode=True)` should be enabled in production to strengthen the
cryptographic foundations of Opacus's RNG.

The advisory was filed because Opacus 1.5.x emits a `UserWarning: Secure RNG turned off`
at `PrivacyEngine()` instantiation time, which was suppressed via a `filterwarnings` entry
in `pyproject.toml` during T7.3.  The warning correctly signals that Opacus's default
`PrivacyEngine()` uses PyTorch's standard PRNG rather than a CSPRNG-backed one.

This ADR amendment documents the evaluation outcome and records the decision.

---

## Evaluation

### What `secure_mode=True` provides

`PrivacyEngine(secure_mode=True)` replaces PyTorch's standard Mersenne-Twister PRNG with
the `torchcsprng` package, which provides a cryptographically-secure random number generator
(CSPRNG) backed by AES-CTR.  This eliminates the theoretical possibility that an adversary
who can predict the PRNG state could bias the injected Gaussian noise, weakening the DP
guarantee.

In practice, this attack requires the adversary to:
1. Know the PRNG seed (which is not exposed and is randomized at process startup).
2. Observe enough PRNG outputs to reconstruct the state.
3. Use that knowledge to infer information about training data from the noise pattern.

For Opacus's use case — injecting noise into gradient aggregates during a training loop —
this attack path is academic: the gradient outputs are not observable by the adversary
in Conclave's air-gapped deployment model.

### Why `secure_mode=True` cannot be enabled in this environment

The Opacus `PrivacyEngine(secure_mode=True)` constructor raises `RuntimeError` at
instantiation time if `torchcsprng` is not installed:

```
RuntimeError: To use secure RNG, you must install the torchcsprng package!
Check out the instructions here: https://github.com/pytorch/csprng#installation
```

`torchcsprng` is a separate PyTorch extension package that:
1. Requires compilation against the installed PyTorch version's C++ ABI.
2. Has not published pre-built wheels for Python 3.14 (the project's required runtime).
3. Has not been updated since 2021 and is no longer actively maintained upstream.
4. Is not available in the air-gapped deployment environment without additional
   wheel-baking infrastructure that is out of scope for Phase 8.

Empirically verified: running `PrivacyEngine(secure_mode=True)` in the project's virtual
environment raises `RuntimeError` (tested against opacus 1.5.4, Python 3.14.1).

### Alternative mitigations evaluated

1. **PyTorch seeded PRNG**: The existing `DPTrainingWrapper` does not explicitly seed the
   Opacus PRNG.  PyTorch's default PRNG is initialized from OS entropy at process startup
   (`torch.seed()` is called implicitly).  This is not a CSPRNG but is not predictable
   by an external adversary in a deployed system.

2. **Reduce the warning log level**: The `filterwarnings` suppression in `pyproject.toml`
   (introduced in T7.3) prevents the `UserWarning` from polluting test output.  This
   suppression is retained.

3. **Document and defer**: The decision to defer `secure_mode=True` is explicitly documented
   here.  If `torchcsprng` publishes Python 3.14–compatible wheels in a future release, this
   advisory should be revisited as a P-next-phase hardening task.

---

## Decision

`PrivacyEngine(secure_mode=True)` is **not enabled** in the production `DPTrainingWrapper`.

The rationale:
- `torchcsprng` is unavailable for Python 3.14 and is unmaintained upstream.
- The practical attack vector it mitigates (PRNG state reconstruction leading to DP
  privacy guarantee weakening) requires adversarial access to the training process that
  is not present in Conclave's threat model.
- The existing `DPTrainingWrapper` uses PyTorch's default OS-entropy-seeded PRNG, which
  is sufficient for the deployment model.

The `filterwarnings` entry `"ignore:Secure RNG turned off:UserWarning:opacus"` in
`pyproject.toml` is retained and explicitly justified by this ADR amendment.

---

## Consequences

- ADV-067 is drained.  No code change is required in `dp_engine.py`.
- The `filterwarnings` suppression in `pyproject.toml` is now ADR-backed rather than
  advisory-only.
- If `torchcsprng` becomes available for Python 3.14+ in a future Opacus release, a new
  advisory task should be filed to re-evaluate.

---

## References

- ADR-0017: Synthesizer & Differential Privacy Library Selection
- ADR-0025: Custom CTGAN Training Loop Architecture
- Opacus documentation: https://opacus.ai/api/privacy_engine.html (air-gap note: consulted
  prior to deployment; not accessible from within the air-gapped environment at runtime)
- torchcsprng repository: https://github.com/pytorch/csprng (archived; last release 2021)
