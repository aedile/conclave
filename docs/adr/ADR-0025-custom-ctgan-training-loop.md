# ADR-0025 — Custom CTGAN Training Loop Architecture

**Date:** 2026-03-15
**Status:** Accepted
**Deciders:** PM + Architect
**Task:** P7-T7.1
**Supersedes:** ADR-0017 risk note ("Opacus compatibility with CTGAN internals")
**Resolves:** ADV-048 (BLOCKER — `build_dp_wrapper()` factory missing from bootstrapper)

---

## Context

ADR-0017 selected CTGAN + Opacus as the synthesis/DP pairing and identified a risk:
SDV's `CTGANSynthesizer.fit()` is a black box that internally creates, trains, and
destroys its own PyTorch optimizer, model, and DataLoader. Opacus's
`PrivacyEngine.make_private()` requires access to these objects *before* the training
loop starts. This mismatch was logged as ADV-048 (BLOCKER) and deferred to Phase 7.

The existing `DPTrainingWrapper` (`modules/privacy/dp_engine.py`) is fully implemented
and tested against raw PyTorch objects. The `SynthesisEngine.train()` method accepts a
`dp_wrapper: Any` parameter but currently logs a warning and trains WITHOUT DP-SGD when
a wrapper is provided. The entire DP-SGD pipeline is architecturally complete except for
the SDV integration point.

### The Problem (Concrete)

SDV's `CTGANSynthesizer` source code (SDV 1.x) reveals the following internal structure:

```python
# Inside CTGANSynthesizer.fit() — simplified
class CTGANSynthesizer(BaseSingleTableSynthesizer):
    def _fit(self, processed_data):
        transformer = self._data_processor        # DataTransformer (mode-specific normalization)
        train_data = transformer.transform(data)   # -> numpy array
        dataset = TensorDataset(torch.from_numpy(train_data))
        dataloader = DataLoader(dataset, batch_size=self._batch_size, ...)

        generator = Generator(...)        # nn.Module — generates synthetic rows
        discriminator = Discriminator(...) # nn.Module — classifies real vs synthetic

        optim_g = Adam(generator.parameters(), ...)
        optim_d = Adam(discriminator.parameters(), ...)

        for epoch in range(self._epochs):
            for real_batch in dataloader:
                # ... GAN training step (discriminator then generator) ...
```

Key observations:
1. **DataTransformer** (`_data_processor`): Handles categorical encoding and mode-specific
   normalization. This is reusable — it's constructed during `preprocess()` / `_fit()` but
   is a standalone pipeline that transforms DataFrames → numpy arrays.
2. **Generator and Discriminator**: Standard `nn.Module` subclasses. Their architecture
   (layer sizes, activation functions) is defined by SDV's CTGAN implementation. These are
   reusable — only the training loop around them needs to change.
3. **Optimizer and DataLoader**: Created fresh inside `_fit()`. These are the objects Opacus
   needs to wrap. They cannot be accessed from outside `_fit()`.
4. **Training loop**: A standard GAN loop (discriminator step → generator step per batch).
   This is the part that must be replaced.

### Validated Spike: SDV Internal Separability

The following extraction paths have been validated against SDV 1.x source code:

**DataTransformer extraction:**
```python
from sdv.single_table import CTGANSynthesizer
from sdv.metadata import SingleTableMetadata

metadata = SingleTableMetadata()
metadata.detect_from_dataframe(df)
synth = CTGANSynthesizer(metadata)

# Trigger data preprocessing without full fit()
synth.preprocess(df)
data_processor = synth._data_processor  # DataTransformer instance
transformed = data_processor.transform(df)  # -> pandas DataFrame of transformed features
```

**Generator/Discriminator architecture extraction:**
```python
# After preprocess(), the transformed data shape determines network dimensions
# SDV CTGAN uses these internal classes:
#   sdv.single_table.ctgan.Generator  (or ctgan.synthesizers.ctgan.Generator)
#   sdv.single_table.ctgan.Discriminator
# Both are nn.Module subclasses with standard forward() methods.

# The data_dim (input dimension) is derived from the transformed data:
data_dim = transformed.shape[1]

# Network construction follows SDV's pattern:
# Generator(embedding_dim, generator_dim, data_dim)
# Discriminator(data_dim + cond_dim, discriminator_dim, pac)
```

**Conclusion:** SDV's DataTransformer and Generator/Discriminator nn.Module architectures
are separable from `fit()`. The optimizer, DataLoader, and training loop are the only
components that must be reimplemented.

---

## Decision

Implement `DPCompatibleCTGAN` in `modules/synthesizer/dp_training.py` — a custom CTGAN
training loop that:

1. **Reuses** SDV's `DataTransformer` (via `CTGANSynthesizer.preprocess()` +
   `_data_processor`) for categorical encoding and mode-specific normalization.
2. **Reuses** SDV's Generator and Discriminator `nn.Module` architecture definitions
   (the network structure, not the training code).
3. **Replaces** SDV's internal training loop with a custom loop that exposes the
   optimizer, model, and DataLoader as first-class objects.
4. **Integrates** with `DPTrainingWrapper` by calling `wrapper.wrap(optimizer, model,
   dataloader)` before the training loop begins, when a wrapper is provided.

### What is reused from SDV

| Component | Source | Why reuse |
|-----------|--------|-----------|
| DataTransformer | `synth._data_processor` after `preprocess()` | Mode-specific normalization (VGM) and categorical encoding are complex and well-tested in SDV. Reimplementing would introduce bugs. |
| Generator nn.Module | SDV's CTGAN Generator class | The network architecture (residual blocks, batch norm) is standard and proven for tabular data. |
| Discriminator nn.Module | SDV's CTGAN Discriminator class | PacGAN discriminator with gradient penalty. Standard architecture. |
| SingleTableMetadata | `sdv.metadata.SingleTableMetadata` | Schema detection for column sdtypes. Already used by `SynthesisEngine._build_metadata()`. |

### What is replaced

| Component | Why replace |
|-----------|-------------|
| `CTGANSynthesizer._fit()` training loop | Must expose optimizer/model/dataloader for Opacus wrapping |
| Optimizer construction | Must be constructed *outside* the training loop so `DPTrainingWrapper.wrap()` can intercept it |
| DataLoader construction | Must be constructed before the training loop starts so Opacus can wrap it |
| Epoch iteration | Must call `DPTrainingWrapper.check_budget()` after each epoch for budget enforcement |

### The Opacus Integration Point

```python
# Pseudocode for the custom training loop

class DPCompatibleCTGAN:
    def fit(self, df, dp_wrapper=None):
        # 1. Preprocess using SDV's DataTransformer
        transformed_data = self._preprocess(df)

        # 2. Create PyTorch objects (exposed, not hidden inside fit())
        dataset = TensorDataset(torch.from_numpy(transformed_data))
        dataloader = DataLoader(dataset, batch_size=self._batch_size)
        discriminator = Discriminator(...)  # SDV's nn.Module architecture
        generator = Generator(...)          # SDV's nn.Module architecture
        optimizer_d = Adam(discriminator.parameters())
        optimizer_g = Adam(generator.parameters())

        # 3. ** THE OPACUS INTEGRATION POINT **
        if dp_wrapper is not None:
            # Wrap the discriminator's optimizer with Opacus DP-SGD
            # Only the discriminator is wrapped — it sees real data.
            # The generator never sees real data directly; it learns from
            # the discriminator's gradient signal.
            optimizer_d = dp_wrapper.wrap(
                optimizer=optimizer_d,
                model=discriminator,
                dataloader=dataloader,
                max_grad_norm=...,
                noise_multiplier=...,
            )

        # 4. Standard GAN training loop
        for epoch in range(self._epochs):
            for real_batch in dataloader:
                # ... discriminator step with optimizer_d ...
                # ... generator step with optimizer_g ...

            # 5. Budget enforcement per epoch
            if dp_wrapper is not None:
                dp_wrapper.check_budget(
                    allocated_epsilon=self._allocated_epsilon,
                    delta=self._delta,
                )
```

### Why only the Discriminator is DP-wrapped

In a GAN, only the **discriminator** processes real training data. The generator
receives gradient signal from the discriminator but never sees real records directly.
 DP-SGD protects the discriminator's gradient computation — clipping per-sample
gradients and adding Gaussian noise — which bounds the information about any single
training record that leaks through the discriminator to the generator.

This is the standard approach in DP-GAN literature (Xie et al., 2018; Jordon et al.,
2018) and is consistent with Opacus's single-model wrapping API.

---

## Import Boundary Constraints

Per ADR-0001 and import-linter contracts:

- `modules/synthesizer/dp_training.py` must NOT import from `modules/privacy/`.
- The `dp_wrapper` parameter is typed as `Any` (same pattern as existing
  `SynthesisEngine.train()`).
- `DPTrainingWrapper` is injected by the bootstrapper — the synthesizer module never
  knows the concrete type.

---

## Consequences

### For T7.2 (Custom CTGAN Training Loop)

`DPCompatibleCTGAN` is implemented in `modules/synthesizer/dp_training.py` with:
- `__init__(metadata, epochs, dp_wrapper=None)` accepting optional DP wrapper
- `fit(df)` implementing the custom training loop with exposed PyTorch objects
- `sample(num_rows)` delegating to the trained Generator

The class accesses SDV private attributes (`_data_processor`, internal Generator/
Discriminator classes). This coupling is documented and accepted — SDV does not provide
a public API for component-level access. Version pinning in `pyproject.toml` mitigates
breakage risk.

### For T7.3 (Opacus End-to-End Wiring)

- `build_dp_wrapper()` factory function in `bootstrapper/main.py` constructs a
  `DPTrainingWrapper` with configurable `max_grad_norm` and `noise_multiplier`.
- `SynthesisEngine.train()` routes to `DPCompatibleCTGAN` when `dp_wrapper is not None`
  (instead of vanilla `CTGANSynthesizer`).
- The warning log in `SynthesisEngine.train()` is replaced with actual DP wiring.
- ADV-048 is drained (removed from Open Advisory Items).

### For T7.4 (Quality Benchmarks)

`ProfileDelta.compare()` quantifies distributional similarity between vanilla CTGAN
output and DP-CTGAN output at various epsilon levels. Expected: quality degrades
gracefully as epsilon decreases (more noise → more privacy → less utility).

### Negative consequences / risks

1. **SDV private attribute coupling**: Accessing `_data_processor` and internal
   Generator/Discriminator classes creates a dependency on SDV's internal structure.
   SDV version upgrades may break these access paths. Mitigation: pin SDV version in
   `pyproject.toml`; wrap access in a compatibility layer that can be updated when SDV
   changes.

2. **Opacus + CTGAN Discriminator compatibility**: Opacus requires models to support
   per-sample gradient computation. CTGAN's Discriminator may use layers that Opacus
   cannot instrument (e.g., custom activations, non-standard batch operations). T7.2
   must validate this empirically before committing to the full implementation. If
   incompatible, the Discriminator can be reimplemented as a standard MLP while
   preserving the same architecture (input dim, hidden dims, output dim).

3. **Training quality parity**: The custom training loop must produce statistically
   equivalent output to vanilla `CTGANSynthesizer.fit()` when `dp_wrapper=None`.
   T7.2 validates this via `ProfileDelta.compare()`.

---

## References

- ADR-0017: Synthesizer & Differential Privacy Library Selection — the foundational
  decision that selected CTGAN + Opacus and identified the training loop risk.
- ADR-0001: Modular Monolith Topology — import-linter boundary constraints.
- ADV-048: BLOCKER advisory tracking the missing `build_dp_wrapper()` wiring.
- `src/synth_engine/modules/privacy/dp_engine.py`: Existing `DPTrainingWrapper`
  implementation (fully tested, awaiting SDV integration).
- `src/synth_engine/modules/synthesizer/engine.py`: Existing `SynthesisEngine` with
  `dp_wrapper` parameter that currently logs a warning.
- Xie, L. et al. (2018). "Differentially Private Generative Adversarial Network."
  arXiv:1802.06739. Established the approach of applying DP-SGD only to the
  discriminator in GAN training.

---

## Amendments

- **P24-T24.1** (2026-03-17): `sample()` parameter renamed from `n_rows` to `num_rows` to match the polymorphic SDV `CTGANSynthesizer.sample(num_rows=...)` interface that `SynthesisEngine.generate()` calls.
