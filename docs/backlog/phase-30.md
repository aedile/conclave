# Phase 30 — True Discriminator-Level DP-SGD

**Goal**: Replace the proxy-model DP-SGD approximation with real Opacus DP-SGD wrapping of the
CTGAN Discriminator. After this phase, the epsilon value returned by
`DPTrainingWrapper.epsilon_spent()` reflects actual per-sample gradient clipping and Gaussian
noise applied to the model that sees real training data — making the differential privacy
guarantee end-to-end, not proxy-measured.

**Prerequisite**: Phase 29 must be complete. The README must already reflect the proxy-model
limitation before this phase changes the underlying behavior.

**ADR**: ADR-0025 (amended in T29.5 with forward reference to this phase). A new ADR-0036
will be created in T30.1 documenting the discriminator wrapping architecture.

**Risk register**:
- **R1: Opacus layer compatibility.** CTGAN's Discriminator uses `torch.nn.Linear`,
  `LeakyReLU`, `Dropout`, and `BatchNorm1d`. Opacus cannot instrument `BatchNorm1d` (it
  violates per-sample gradient isolation). Mitigation: replace `BatchNorm1d` with
  `GroupNorm` in the custom Discriminator — this is the standard Opacus-compatible
  substitution (documented in Opacus FAQ and `opacus.validators.ModuleValidator`).
- **R2: Training quality regression.** Changing `BatchNorm` to `GroupNorm` may degrade
  synthesis quality. Mitigation: T30.4 benchmarks vanilla vs DP quality and establishes
  acceptable degradation thresholds per epsilon level.
- **R3: SDV internal coupling.** Extracting the Discriminator from SDV's CTGAN internals
  requires accessing `ctgan.synthesizers.ctgan.Discriminator`. This is the same level of
  coupling already accepted in ADR-0025 for the Generator/DataProcessor. Version-pinned.

---

## T30.1 — ADR-0036: Discriminator-Level DP-SGD Architecture

**Priority**: P0 — Architectural decision must precede implementation.

### Context & Constraints

1. The current `DPCompatibleCTGAN._activate_opacus()` constructs a 1-layer `nn.Linear` proxy
   model, wraps it with Opacus, runs gradient steps on it, and reports the epsilon. The actual
   CTGAN Discriminator is trained separately by `CTGAN.fit()` without DP-SGD.

2. ADR-0025 §Decision actually describes the correct architecture (wrap the discriminator's
   optimizer), but the implementation took a shortcut because CTGAN's `fit()` method creates
   the Discriminator and optimizer as local variables that are inaccessible from outside.

3. The solution is to replace the call to `CTGAN.fit(processed_df, discrete_columns)` with a
   custom training loop that:
   (a) Constructs the Generator and Discriminator using CTGAN's architecture classes,
   (b) Creates the optimizers externally (accessible for Opacus wrapping),
   (c) Runs the GAN training loop (discriminator step → generator step) directly,
   (d) Wraps the Discriminator optimizer with `dp_wrapper.wrap()` before training begins.

4. Opacus requires all layers in the wrapped model to support per-sample gradients. CTGAN's
   Discriminator uses `BatchNorm1d`, which Opacus cannot instrument. The ADR must document
   the `BatchNorm1d` → `GroupNorm` substitution.

5. The Generator is NOT wrapped — it never sees real data directly. Only the Discriminator
   processes real records and needs DP-SGD protection. This is the standard approach from
   DP-GAN literature (Xie et al., 2018).

### Acceptance Criteria

1. `docs/adr/ADR-0036-discriminator-level-dp-sgd.md` created with:
   - Context: why the proxy model was insufficient
   - Decision: custom training loop with Discriminator-level Opacus wrapping
   - `BatchNorm1d` → `GroupNorm` substitution rationale
   - Import boundary preservation (synthesizer module still does not import from privacy)
   - Consequences: real epsilon accounting, potential quality regression, SDV coupling risk
2. ADR cross-references ADR-0025 and ADR-0017.
3. ADR reviewed by Architecture reviewer.

### Testing & Quality Gates

- `pre-commit run --all-files`
- Architecture reviewer spawned.

### Files to Create/Modify

- `docs/adr/ADR-0036-discriminator-level-dp-sgd.md`

---

## T30.2 — Opacus-Compatible Discriminator Wrapper

**Priority**: P0 — Foundation for the DP training loop.

### Context & Constraints

1. CTGAN's `Discriminator` class (from `ctgan.synthesizers.ctgan`) uses:
   - `nn.Linear` layers (Opacus compatible)
   - `LeakyReLU` activation (Opacus compatible)
   - `nn.Dropout` (Opacus compatible)
   - `nn.BatchNorm1d` (Opacus **incompatible** — violates per-sample gradient isolation)
   - PacGAN packing (groups `pac` real samples into one discriminator input)

2. Opacus provides `opacus.validators.ModuleValidator.fix(model)` which auto-replaces
   incompatible layers. However, this is a black-box transformation. For auditability and
   test determinism, the substitution should be explicit.

3. The wrapper should:
   (a) Accept the same constructor parameters as CTGAN's Discriminator
       (`input_dim`, `discriminator_dim`, `pac`)
   (b) Replace `BatchNorm1d` with `GroupNorm(1, num_channels)` (equivalent to LayerNorm,
       the standard Opacus-compatible substitute)
   (c) Pass `opacus.validators.ModuleValidator.validate(model)` — zero errors
   (d) Produce identical output shapes to the original Discriminator

4. The wrapper lives in `modules/synthesizer/` (same module as `dp_training.py`) — it is a
   synthesis concern, not a privacy concern. It does not import from `modules/privacy/`.

### Acceptance Criteria

1. `modules/synthesizer/dp_discriminator.py` created with class
   `OpacusCompatibleDiscriminator(nn.Module)`.
2. Constructor mirrors CTGAN Discriminator: `(input_dim, discriminator_dim, pac)`.
3. All `BatchNorm1d` replaced with `GroupNorm(1, num_features)`.
4. `opacus.validators.ModuleValidator.validate(model)` returns zero errors.
5. Output shape matches CTGAN's original Discriminator for identical inputs.
6. Unit tests:
   - Forward pass produces correct output shape
   - `ModuleValidator.validate()` passes
   - Gradient computation works (backward pass + optimizer step)
   - Output differs from zero (non-degenerate initialization)
7. import-linter contracts pass (no cross-module violations).

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95 -W error`
- `poetry run mypy src/`
- `poetry run python -m importlinter`
- `pre-commit run --all-files`
- All review agents spawned (QA + Architecture).

### Files to Create/Modify

- `src/synth_engine/modules/synthesizer/dp_discriminator.py` (new)
- `tests/unit/test_dp_discriminator.py` (new)

---

## T30.3 — Custom GAN Training Loop with Discriminator DP-SGD

**Priority**: P0 — Core implementation.

### Context & Constraints

1. `DPCompatibleCTGAN._activate_opacus()` currently wraps a proxy model. This task replaces
   the entire `_activate_opacus()` method and the `CTGAN.fit()` delegation with a custom
   GAN training loop that:

   **Phase 1 (unchanged)**: Preprocess via SDV's DataProcessor.
   **Phase 2 (new)**: Construct Generator and OpacusCompatibleDiscriminator externally.
   **Phase 3 (new)**: Wrap the Discriminator's optimizer via `dp_wrapper.wrap()`.
   **Phase 4 (new)**: Run the GAN training loop:
     - For each epoch:
       - For each batch:
         - Discriminator step: forward real + fake, compute loss, backward, dp_optimizer.step()
         - Generator step: forward fake, compute loss, backward, optimizer_g.step()
       - Call `dp_wrapper.check_budget(allocated_epsilon, delta)` for early stopping

2. The Generator architecture must match CTGAN's Generator (from `ctgan.synthesizers.ctgan`).
   The Generator does NOT need Opacus compatibility — it is not wrapped.

3. The custom training loop must implement CTGAN's specific GAN training strategy:
   - Conditional vector generation for mode-specific normalization
   - PacGAN discriminator packing
   - Training-by-sampling strategy (oversample minority modes)
   These details are in `ctgan.synthesizers.ctgan.CTGAN.fit()` source code and must be
   faithfully reproduced.

4. **Fallback**: If Opacus cannot wrap the Discriminator even after `BatchNorm` replacement
   (e.g., PacGAN packing creates an incompatible reshape operation), the implementation must
   fall back to the proxy model approach and log a WARNING explaining why. This ensures the
   system degrades gracefully rather than failing.

5. `DPCompatibleCTGAN.sample()` is unchanged — it delegates to the trained Generator via
   CTGAN's sampling mechanism.

### Acceptance Criteria

1. `DPCompatibleCTGAN.fit()` with `dp_wrapper` provided:
   - Constructs `OpacusCompatibleDiscriminator` from T30.2
   - Constructs CTGAN Generator (reused from ctgan internals)
   - Wraps Discriminator optimizer via `dp_wrapper.wrap()`
   - Runs custom GAN training loop with conditional vectors + PacGAN
   - Calls `dp_wrapper.check_budget()` per epoch
   - `dp_wrapper.epsilon_spent(delta=1e-5)` returns positive value after training
2. `DPCompatibleCTGAN.fit()` with `dp_wrapper=None` (vanilla path) is unchanged.
3. The proxy model (`_activate_opacus`) method is removed or marked `@deprecated` with a
   flag that routes to it only as a fallback.
4. `DPCompatibleCTGAN.sample()` produces valid synthetic DataFrames after DP training.
5. Unit tests:
   - DP training loop runs to completion (mocked PyTorch/Opacus)
   - Epsilon is positive after training
   - Budget exhaustion raises `BudgetExhaustionError` mid-training
   - Vanilla path still works identically
   - Fallback to proxy model logs WARNING when discriminator wrapping fails
6. No import-linter violations.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=95 -W error`
- `poetry run mypy src/`
- `poetry run python -m importlinter`
- `poetry run bandit -c pyproject.toml -r src/`
- `pre-commit run --all-files`
- All review agents spawned (QA + DevOps + Architecture).

### Files to Create/Modify

- `src/synth_engine/modules/synthesizer/dp_training.py` (major rewrite of fit() DP path)
- `tests/unit/test_dp_training_init.py` (updated)
- `tests/unit/test_dp_training_privacy.py` (updated)
- `tests/unit/test_dp_training_sample.py` (updated)

---

## T30.4 — DP Quality Benchmark: Proxy vs Discriminator

**Priority**: P1 — Validation that the new implementation produces acceptable quality.

### Context & Constraints

1. The existing `scripts/benchmark_dp_quality.py` (if present) or a new benchmark script
   must compare:
   - Vanilla CTGANSynthesizer (no DP) as the quality baseline
   - DPCompatibleCTGAN with proxy model (Phase 7 implementation — kept as fallback)
   - DPCompatibleCTGAN with Discriminator-level DP-SGD (Phase 30 implementation)

2. Quality is measured via `ProfileDelta.compare()` from `modules/profiler/` — this already
   exists and produces statistical distance metrics (KS statistic, Jensen-Shannon divergence,
   covariance matrix Frobenius norm).

3. The benchmark should test at epsilon levels: 0.1, 0.5, 1.0, 5.0, 10.0 — the standard
   range from DP literature. Expected: lower epsilon → more noise → worse quality, but
   discriminator-level DP should produce *better* quality than proxy-model DP at the same
   epsilon because the noise is applied where it matters (training) rather than wasted on a
   proxy.

4. Results should be documented in `docs/DP_QUALITY_REPORT.md` with clear labeling of which
   implementation produced each datapoint.

### Acceptance Criteria

1. Benchmark script exists and is runnable via `poetry run python scripts/benchmark_dp_quality.py`.
2. Report compares all three configurations (vanilla, proxy DP, discriminator DP) at 5 epsilon
   levels.
3. `docs/DP_QUALITY_REPORT.md` updated with discriminator-level DP results, clearly labeled.
4. README DP section updated to reference discriminator-level DP as the current implementation
   (removing the Phase 29 interim language).
5. At minimum, discriminator-level DP at epsilon=10.0 produces quality within 20% of vanilla
   (measured by mean ProfileDelta KS statistic across columns). If not, document the gap and
   tuning recommendations.

### Testing & Quality Gates

- Benchmark script runs without error on sample data.
- `docs/DP_QUALITY_REPORT.md` contains the comparison table.
- All review agents spawned (QA + Architecture).

### Files to Create/Modify

- `scripts/benchmark_dp_quality.py` (new or updated)
- `docs/DP_QUALITY_REPORT.md`
- `README.md` (final DP section update)

---

## T30.5 — Integration Test: Real Opacus on Real Discriminator

**Priority**: P0 — Constitution Priority 4. The discriminator-level DP-SGD path must be
validated with real Opacus, not mocks.

### Context & Constraints

1. The existing `tests/integration/test_dp_integration.py` tests the `DPTrainingWrapper`
   against a raw PyTorch model. This task adds a test that exercises the full pipeline:
   `DPCompatibleCTGAN.fit(df, dp_wrapper=real_wrapper)` → Opacus wraps the real
   `OpacusCompatibleDiscriminator` → training loop runs → `epsilon_spent()` is positive.

2. This test requires the synthesizer dependency group (torch, sdv, opacus). Mark with
   `@pytest.mark.synthesizer`.

3. Use a small DataFrame (10-20 rows, 3-4 columns) and 2 epochs for speed.

4. Verify:
   - `epsilon_spent(delta=1e-5)` > 0 after training
   - `sample(num_rows=5)` returns a valid DataFrame with correct schema
   - The Opacus `PrivacyEngine` was attached to the Discriminator (not a proxy model)

### Acceptance Criteria

1. `tests/integration/test_dp_discriminator_e2e.py` created with at least 3 tests:
   - Full DP training pipeline produces positive epsilon
   - Sampling after DP training produces valid output
   - Budget exhaustion (tiny allocated_epsilon) raises BudgetExhaustionError during training
2. Tests pass with `poetry run pytest tests/integration/test_dp_discriminator_e2e.py -v --no-cov`.
3. Tests are marked `@pytest.mark.synthesizer`.

### Testing & Quality Gates

- `poetry run pytest tests/integration/test_dp_discriminator_e2e.py -v --no-cov`
- `pre-commit run --all-files`
- All review agents spawned (QA + DevOps).

### Files to Create/Modify

- `tests/integration/test_dp_discriminator_e2e.py` (new)

---

## T30.6 — ADR-0025 Final Amendment: Proxy Model Superseded

**Priority**: P1 — Documentation (Constitution Priority 6).

### Context & Constraints

1. After T30.3 merges, ADR-0025's status should be updated to reflect that the proxy model
   approach is superseded by discriminator-level DP-SGD.

2. The proxy model path should be documented as a fallback (activated when Opacus cannot
   instrument the Discriminator), not the primary path.

3. `dp_training.py` module docstring must be updated to reflect the new architecture.

### Acceptance Criteria

1. ADR-0025 status: "Accepted → Superseded by Phase 30 (discriminator-level DP-SGD is the
   primary path; proxy model retained as fallback)."
2. ADR-0036 status: "Accepted."
3. `dp_training.py` module docstring updated to describe the Discriminator wrapping as primary.
4. DP-SGD security assumptions docstring section updated (proxy model assumptions no longer
   primary).

### Testing & Quality Gates

- `pre-commit run --all-files`
- All review agents spawned (Architecture).

### Files to Create/Modify

- `docs/adr/ADR-0025-custom-ctgan-training-loop.md`
- `docs/adr/ADR-0036-discriminator-level-dp-sgd.md`
- `src/synth_engine/modules/synthesizer/dp_training.py` (docstring only)

---

## Phase 30 Exit Criteria

1. `DPCompatibleCTGAN.fit()` with `dp_wrapper` wraps the **real CTGAN Discriminator** (or
   Opacus-compatible replacement), not a proxy model.
2. `epsilon_spent()` reflects DP-SGD accounting on the Discriminator that processes real data.
3. Integration test confirms real Opacus on real Discriminator with positive epsilon.
4. Quality benchmark documents the vanilla vs proxy vs discriminator comparison.
5. README and DP Quality Report updated to reflect discriminator-level DP as the production
   implementation.
6. ADR-0025 superseded; ADR-0036 accepted.
7. Proxy model fallback path exists and is documented for environments where Opacus cannot
   instrument the Discriminator.
8. All quality gates pass at 95% coverage threshold.
