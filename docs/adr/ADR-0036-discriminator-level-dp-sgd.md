# ADR-0036 — Discriminator-Level DP-SGD Architecture

**Date:** 2026-03-18
**Status:** Accepted
**Deciders:** PM + Architecture Reviewer
**Tasks:** P30-T30.1 (ADR), P30-T30.2 (OpacusCompatibleDiscriminator), P30-T30.3 (custom training loop — IMPLEMENTED)
**Supersedes:** ADR-0025 §"Planned Resolution — Phase 30" (proxy model as primary path)
**References:** ADR-0025 (Custom CTGAN Training Loop), ADR-0017 (Synthesizer & DP Library Selection)

---

## Context

### The Proxy Model Shortcut (T7.3)

ADR-0025 §Decision describes the correct architecture for DP-SGD integration: the
CTGAN Discriminator's optimizer is wrapped with Opacus `PrivacyEngine.make_private()`
before the training loop begins. Only the Discriminator is wrapped because it is the
only component in the GAN that processes real training records.

The T7.3 implementation did not achieve this design. Instead,
`DPCompatibleCTGAN._activate_opacus()` constructs a **1-layer `nn.Linear` proxy model**,
wraps it with Opacus, executes gradient steps on it using the real training data, and
reads the resulting epsilon value. The actual CTGAN Discriminator is trained separately
inside `CTGAN.fit(processed_df, discrete_columns)` without any Opacus instrumentation.

This means the privacy accounting is valid — the proxy model processes the same real
records, so the (Epsilon, Delta) computation is correct — but the DP-SGD noise is
never injected into the Discriminator's actual weight updates. The model that learns
from real data is not DP-protected at the gradient level.

### Why the Proxy Was Necessary at T7.3

The root cause is a layer compatibility constraint between Opacus and CTGAN:

**Opacus per-sample gradient requirement.** Opacus instruments a PyTorch model by
registering gradient hooks that isolate each training sample's gradient contribution
before aggregation and clipping. This mechanism requires every layer in the wrapped
model to support per-sample gradient computation. Opacus enforces this at
`PrivacyEngine.make_private()` time via `ModuleValidator.validate()`, which raises
`IncompatibleModuleException` for any layer that violates the constraint.

**`BatchNorm1d` incompatibility.** CTGAN's Discriminator uses `torch.nn.BatchNorm1d`
for normalization between linear layers. `BatchNorm1d` accumulates statistics
(running mean, running variance) across the entire batch during both forward and
backward passes. This cross-sample coupling violates the per-sample gradient isolation
that Opacus requires: there is no way to attribute the gradient contribution of a
single sample to a layer whose forward pass depends on the mean and variance of all
samples in the batch. Opacus's `ModuleValidator` rejects `BatchNorm1d` with an
`IncompatibleModuleException`.

At T7.3, rather than block delivery of the DP-SGD pipeline, the proxy model was
accepted as a practical approximation documented in ADR-0025 with a forward reference
to Phase 30.

### What Phase 30 Must Resolve

Phase 30 must deliver the architecture ADR-0025 §Decision actually describes:

1. Replace `BatchNorm1d` with an Opacus-compatible normalization layer in the
   Discriminator.
2. Construct the Generator and Discriminator externally (outside `CTGAN.fit()`), so
   the Discriminator's optimizer is accessible before the training loop begins.
3. Wrap the Discriminator's optimizer via `dp_wrapper.wrap()` before training.
4. Run the GAN training loop directly, calling `dp_wrapper.check_budget()` per epoch
   for privacy budget enforcement.
5. Retain the proxy model as a fallback for environments where Opacus cannot
   instrument the Discriminator even after normalization substitution.

---

## Decision

### 1. `BatchNorm1d` → `GroupNorm` Substitution

The normalization layers in CTGAN's Discriminator are replaced with
`torch.nn.GroupNorm(1, num_features)`.

**Why `GroupNorm(1, num_features)` is correct:**

`GroupNorm` divides channels into groups and normalizes within each group
independently for each sample. With `num_groups=1`, all channels form one group, and
normalization is computed over all channels per sample — this is mathematically
equivalent to `LayerNorm` over the feature dimension. Crucially, `GroupNorm` has no
cross-sample coupling: the normalization statistics for sample `i` depend only on
sample `i`'s activations. This satisfies Opacus's per-sample gradient isolation
requirement.

`opacus.validators.ModuleValidator.validate()` accepts `GroupNorm` with zero errors.
`opacus.validators.ModuleValidator.fix()` performs this same substitution
automatically, but explicit construction is preferred here for auditability and test
determinism — the substitution is visible in code rather than hidden in a black-box
fix call.

**Why not `LayerNorm` directly?** `LayerNorm` is an equally valid substitute. Both
`LayerNorm` and `GroupNorm(1, C)` are per-sample and Opacus-compatible. `GroupNorm` is
chosen because it maps more directly onto the channel layout of the existing
Discriminator (which thinks in terms of feature channels from linear layers), and
because `GroupNorm(1, C)` is the substitution explicitly documented in the Opacus FAQ
and `ModuleValidator` source code.

**Impact on PacGAN packing.** CTGAN's Discriminator uses PacGAN packing: `pac` real
samples are concatenated into a single discriminator input of size `input_dim * pac`.
This affects the `num_features` argument to `GroupNorm` — the norm must be applied
to the packed feature dimension. The `OpacusCompatibleDiscriminator` (T30.2) must
account for this when constructing `GroupNorm` layers within each PacGAN-sized linear
block.

### 2. `OpacusCompatibleDiscriminator` (T30.2)

A new class `OpacusCompatibleDiscriminator(nn.Module)` is created in
`modules/synthesizer/dp_discriminator.py`. It accepts the same constructor parameters
as CTGAN's internal Discriminator (`input_dim`, `discriminator_dim`, `pac`) and
replicates the same layer structure (linear → normalization → leaky relu → dropout)
except that every `BatchNorm1d` is replaced by `GroupNorm(1, num_features)`.

`opacus.validators.ModuleValidator.validate(model)` must return zero errors on any
instance of this class.

### 3. Custom GAN Training Loop (T30.3)

`DPCompatibleCTGAN.fit()` is rewritten to replace the `CTGAN.fit()` black-box call
with a custom training loop that exposes all PyTorch objects externally:

```
Phase 1 (unchanged): Preprocess via SDV DataProcessor.
Phase 2 (new): Construct Generator and OpacusCompatibleDiscriminator externally.
Phase 3 (new): Construct optimizers externally (accessible for Opacus wrapping).
Phase 4 (new): Wrap the Discriminator's optimizer via dp_wrapper.wrap().
Phase 5 (new): Run the GAN training loop with conditional vectors and PacGAN packing.
               Per epoch: call dp_wrapper.check_budget() for budget enforcement.
```

The training loop must faithfully reproduce CTGAN's training strategy:
- Conditional vector generation for mode-specific normalization (VGM).
- PacGAN discriminator packing (group `pac` real samples per input).
- Training-by-sampling strategy (oversample minority modes).

These details are taken from `ctgan.synthesizers.ctgan.CTGAN.fit()` source code.

### 4. Only the Discriminator is DP-Wrapped

The Generator is not wrapped with Opacus. This is the standard approach in DP-GAN
literature (Xie et al., 2018; Jordon et al., 2018):

- The Discriminator is the only GAN component that processes real training records. Its
  gradient computation is the channel through which information about individual training
  samples flows into the model. DP-SGD protects this channel by clipping per-sample
  gradients and injecting calibrated Gaussian noise before each optimizer step.
- The Generator never receives real records as input. It receives only a latent noise
  vector and generates synthetic samples, learning from the Discriminator's gradient
  signal. The information the Generator receives about real data is already noise-masked
  by the DP-SGD applied to the Discriminator. Wrapping the Generator would add
  unnecessary noise without a corresponding privacy benefit.

This design is consistent with Opacus's single-model wrapping API and the privacy
analysis in Xie et al. (2018), which proves that the (Epsilon, Delta)-DP guarantee on
the Discriminator's gradient is sufficient to bound the information leakage about any
single training record through the full GAN.

### 5. Proxy Model Fallback

The proxy model approach from T7.3 is retained as a fallback. If
`OpacusCompatibleDiscriminator` fails `ModuleValidator.validate()` at runtime (for
example, due to an incompatible future SDV/Opacus version), `DPCompatibleCTGAN.fit()`
logs a `WARNING` explaining the fallback and routes to the proxy model path. This
ensures the system degrades gracefully rather than raising an unhandled exception.

A runtime flag (or configuration parameter) controls which path is active, so
operators can explicitly request the fallback for environments with known incompatibilities.

### 6. Epsilon Accounting After Phase 30

After T30.3, `dp_wrapper.epsilon_spent(delta)` returns the epsilon computed from
DP-SGD steps applied to the real Discriminator. The per-sample gradient noise is
injected into the actual weight updates of the model that processes real training data.
The epsilon value is no longer a proxy measurement — it reflects the genuine
differential privacy cost of training this Discriminator on this dataset.

---

## Import Boundary Constraints

Per ADR-0001 and the import-linter contracts:

- `modules/synthesizer/dp_training.py` and `modules/synthesizer/dp_discriminator.py`
  MUST NOT import from `modules/privacy/`.
- The `dp_wrapper` parameter in `DPCompatibleCTGAN.fit()` is typed as `Any`, consistent
  with the existing pattern in `SynthesisEngine.train()`.
- `DPTrainingWrapper` (in `modules/privacy/dp_engine.py`) is injected by the
  bootstrapper at runtime. The synthesizer module never depends on the concrete type.
- `OpacusCompatibleDiscriminator` is a synthesis concern — it is a model architecture
  class that belongs in `modules/synthesizer/`. It does not contain privacy accounting
  logic and has no dependency on `modules/privacy/`.

The `opacus.validators.ModuleValidator` import inside `dp_discriminator.py` is
permitted — Opacus is a direct dependency of the synthesizer module, not of the privacy
module.

---

## Consequences

### Positive

- **End-to-end DP guarantee.** The (Epsilon, Delta) value reported by
  `epsilon_spent()` after Phase 30 reflects DP-SGD applied to the actual model that
  trains on real data. The privacy guarantee is no longer a proxy measurement.

- **Correctness vs. ADR-0025 §Decision.** The Phase 30 implementation matches the
  architecture described in ADR-0025's Decision section, resolving the documented
  deviation introduced by T7.3.

- **Graceful degradation.** The proxy model fallback prevents hard failures in
  environments where Opacus cannot instrument the Discriminator, ensuring operational
  continuity if SDV or Opacus versions change unexpectedly.

- **Auditability.** Explicit `BatchNorm1d` → `GroupNorm` substitution in
  `OpacusCompatibleDiscriminator` is visible in source code and covered by unit tests,
  rather than hidden in `ModuleValidator.fix()` black-box transformation.

### Negative / Risks

- **SDV internal coupling (R3 from phase-30.md).** Constructing the Generator and
  Discriminator externally requires importing from `ctgan.synthesizers.ctgan` — the
  same level of internal access already accepted in ADR-0025 for the DataProcessor.
  SDV version upgrades may change the internal class names or constructor signatures.
  Mitigation: SDV version is pinned in `pyproject.toml`. A compatibility shim can be
  added if the internal API changes in a future pin update.

- **Training quality regression risk (R2 from phase-30.md).** Replacing `BatchNorm1d`
  with `GroupNorm` changes the normalization behavior. Batch normalization accumulates
  statistics over the entire batch (beneficial for stable training with large batches),
  while `GroupNorm(1, C)` normalizes per-sample (less sensitive to batch size but
  potentially less stable early in training). T30.4 benchmarks vanilla CTGAN vs proxy
  DP vs discriminator-level DP at five epsilon levels to quantify any quality
  regression and establish acceptable degradation thresholds.

- **Opacus + PacGAN packing risk (R1 from phase-30.md).** CTGAN's PacGAN packing
  concatenates `pac` samples before feeding them to the Discriminator. If Opacus
  cannot correctly attribute per-sample gradients through the packing reshape operation,
  `ModuleValidator.validate()` will raise an error and the fallback path will activate.
  T30.3 must test this case explicitly (Fallback acceptance criterion 5 in phase-30.md).

- **Conditional vector complexity.** CTGAN's training-by-sampling strategy and
  conditional vector generation are non-trivial to reproduce faithfully. The custom
  training loop in T30.3 must be validated for output statistical equivalence (within
  ProfileDelta tolerance) against vanilla `CTGANSynthesizer` when `dp_wrapper=None`.

- **`_activate_opacus()` removal.** Removing the proxy model as the primary path
  (while retaining it as a fallback) is a breaking change in the `DPCompatibleCTGAN`
  internal API. Existing tests that mock `_activate_opacus()` must be updated in T30.3.

---

## References

- ADR-0025 — Custom CTGAN Training Loop Architecture. The foundational decision that
  established the `DPCompatibleCTGAN` class, accepted the proxy model as a temporary
  compromise in T7.3, and cross-referenced Phase 30 as the planned resolution.
- ADR-0017 — Synthesizer & Differential Privacy Library Selection. Selected CTGAN +
  Opacus as the synthesis/DP pairing and identified the training loop compatibility
  risk (`"Opacus compatibility with CTGAN internals"` in Negative consequences).
- ADR-0001 — Modular Monolith Topology. Defines the import-linter contracts governing
  the `modules/synthesizer` ↔ `modules/privacy` boundary.
- `docs/backlog/phase-30.md` — Phase 30 task breakdown, risk register (R1–R3), and
  acceptance criteria that this ADR's Decision section maps to.
- `src/synth_engine/modules/synthesizer/dp_training.py` — `DPCompatibleCTGAN`
  implementation (proxy model path, to be replaced in T30.3).
- `src/synth_engine/modules/privacy/dp_engine.py` — `DPTrainingWrapper` implementation
  (fully tested; injection target for `dp_wrapper` parameter).
- Xie, L. et al. (2018). "Differentially Private Generative Adversarial Network."
  arXiv:1802.06739. Establishes the standard approach of applying DP-SGD to the
  Discriminator only, with proof that this bounds information leakage about individual
  training records.
- Jordon, J. et al. (2018). "PATE-GAN: Generating Synthetic Data with Differential
  Privacy Guarantees." ICLR 2019. Alternative DP-GAN approach; cited for context on
  the theoretical framing that DP on the discriminator is the correct protection point.
- Opacus `ModuleValidator` documentation — lists supported and unsupported layer types.
  `BatchNorm1d` is in the unsupported list; `GroupNorm` is in the supported list.
  `ModuleValidator.fix()` implements the `BatchNorm1d` → `GroupNorm` substitution
  automatically.
