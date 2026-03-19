# Phase 7 — Differential Privacy Integration

**Goal**: Wire DP-SGD end-to-end by implementing a custom CTGAN training loop that
exposes PyTorch internals for Opacus wrapping. This is the final phase before v1 release.

**Prerequisite**: Phase 6 must be complete (all tasks merged).

**ADV-048 drain**: This phase resolves the BLOCKER advisory that has been open since T4.3b.

---

## T7.1 — ADR-0025: Custom CTGAN Training Loop Architecture

**Priority**: Phase 7 entry gate — must be approved before T7.2 begins.

### Context & Constraints

- SDV's `CTGANSynthesizer.fit()` is a black box: it creates, trains, and destroys its own
  PyTorch optimizer, model, and DataLoader internally. Opacus `PrivacyEngine.make_private()`
  requires access to these objects *before* the training loop starts.
- ADR-0017 identified this as a risk ("Opacus compatibility with CTGAN internals") and
  deferred concrete wiring.
- The existing `DPTrainingWrapper` (modules/privacy/dp_engine.py) is fully implemented and
  tested against raw PyTorch — only the SDV integration point is missing.

### Acceptance Criteria

1. ADR-0025 document created in `docs/adr/` documenting:
   - Which SDV internals are reused (DataTransformer, Generator/Discriminator nn.Module architecture)
   - Which SDV internals are replaced (the training loop, optimizer management)
   - The Opacus integration point (where `PrivacyEngine.make_private()` hooks in)
   - A validated spike confirming that SDV's DataTransformer and model architecture are
     separable from `fit()` — include code snippets showing the extraction path
2. ADV-048 status updated from BLOCKER to IN PROGRESS in RETRO_LOG.

### Testing & Quality Gates

- No code changes — ADR only. Standard docs-gate applies.

---

## T7.2 — Custom CTGAN Training Loop

**Priority**: Core implementation task. Depends on T7.1.

### Context & Constraints

- Implement `DPCompatibleCTGAN` in `modules/synthesizer/dp_training.py`.
- Reuse SDV's `DataTransformer` for categorical encoding and mode-specific normalization.
- Reuse SDV's Generator and Discriminator `nn.Module` architecture (the network structure,
  not the training loop).
- Replace `CTGANSynthesizer.fit()` with a custom training loop that exposes `optimizer`,
  `model`, and `dataloader` as first-class objects.
- Must accept `DPTrainingWrapper` and call `wrapper.wrap(optimizer, model, dataloader)`
  before the training loop begins.
- Must produce statistically equivalent output to vanilla `CTGANSynthesizer.fit()` — validate
  via `ProfileDelta` comparison.
- import-linter: `modules/synthesizer/` must NOT import from `modules/privacy/`. The
  `dp_wrapper` parameter is typed as `Any` (same pattern as existing `SynthesisEngine.train()`).

### Acceptance Criteria

1. `DPCompatibleCTGAN` class in `modules/synthesizer/dp_training.py` with:
   - `__init__(metadata, epochs, dp_wrapper=None)` accepting optional DP wrapper
   - `fit(df)` method implementing the custom training loop
   - `sample(n_rows)` method delegating to the trained Generator
2. Custom training loop exposes optimizer/model/dataloader and calls `dp_wrapper.wrap()`
   when a wrapper is provided.
3. Unit tests: at least 90% coverage on the new module.
4. Integration test: train `DPCompatibleCTGAN` on Faker-generated data, verify output
   DataFrame has correct schema and row count.
5. Integration test: train with `dp_wrapper=None` (vanilla mode), verify output quality
   is statistically comparable to `CTGANSynthesizer.fit()` via `ProfileDelta.compare()`.

### Testing & Quality Gates

- Unit tests with mocked SDV internals (RED → GREEN → REFACTOR).
- Integration tests with real SDV (synthesizer dependency group).
- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/test_dp_training_integration.py -v --no-cov`

---

## T7.3 — Opacus End-to-End Wiring

**Priority**: Wiring task. Depends on T7.2.

### Context & Constraints

- Wire `DPTrainingWrapper` through the bootstrapper (`build_dp_wrapper()` factory in
  `bootstrapper/main.py`) — this drains ADV-048.
- Wire `SynthesisEngine.train()` to use `DPCompatibleCTGAN` when `dp_wrapper` is provided
  (instead of vanilla `CTGANSynthesizer`).
- The existing `SynthesisEngine.train()` logs a warning when `dp_wrapper` is provided but
  can't be applied — replace this warning path with the actual wiring.

### Acceptance Criteria

1. `build_dp_wrapper()` factory function in `bootstrapper/main.py` that constructs a
   `DPTrainingWrapper` with configurable `max_grad_norm` and `noise_multiplier`.
2. `SynthesisEngine.train()` routes to `DPCompatibleCTGAN` when `dp_wrapper is not None`.
3. ADV-048 drained (removed from Open Advisory Items in RETRO_LOG).
4. Integration test: train with DP-SGD enabled, verify `wrapper.epsilon_spent(delta=1e-5) > 0`
   after training completes.
5. Integration test: budget exhaustion — set `allocated_epsilon` low enough that
   `check_budget()` raises `BudgetExhaustionError` mid-training.
6. Integration test: verify `PrivacyEngine.get_epsilon(delta)` returns a value consistent
   with the training configuration (noise_multiplier, epochs, batch_size).

### Testing & Quality Gates

- Unit tests for bootstrapper wiring (mock DPTrainingWrapper).
- Integration tests with real Opacus + SDV (synthesizer dependency group).
- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/test_dp_wiring_integration.py -v --no-cov`

---

## T7.4 — ProfileDelta Validation & Quality Benchmarks

**Priority**: Validation task. Depends on T7.3.

### Context & Constraints

- Run `StatisticalProfiler.compare()` between vanilla CTGAN output and DP-CTGAN output
  at various epsilon levels.
- Document expected quality degradation curves.
- This is a validation/documentation task, not a feature implementation.

### Acceptance Criteria

1. Benchmark script in `scripts/benchmark_dp_quality.py` that:
   - Trains vanilla CTGAN and DP-CTGAN (epsilon=1, 5, 10) on the same dataset
   - Runs `ProfileDelta.compare()` between source and each synthetic output
   - Outputs a summary table of distributional similarity metrics per epsilon level
2. Documentation in `docs/DP_QUALITY_REPORT.md` with benchmark results and
   recommended epsilon ranges for different use cases.
3. Acceptance: synthetic data passes basic distributional similarity at epsilon=10
   (column means within 2 standard deviations, categorical distributions within
   10% KL divergence).

### Testing & Quality Gates

- Benchmark script must be runnable via `poetry run python3 scripts/benchmark_dp_quality.py`.
- No new unit tests required (this is a validation/measurement task).
- Standard docs-gate applies for the quality report.

---

## T7.5 — Phase 7 E2E Test & Retrospective

**Priority**: Final phase task. Depends on T7.3 and T7.4.

### Context & Constraints

- Full pipeline E2E test: Parquet → DP-CTGAN training → synthetic output → FK
  post-processing → ProfileDelta validation.
- Verify privacy accountant ledger correctly reflects epsilon spend from real DP-SGD training.
- Phase 7 retrospective + advisory drain.
- Update README, OPERATOR_MANUAL with DP-SGD operational guidance.

### Acceptance Criteria

1. E2E integration test covering the full DP synthesis pipeline:
   - Load source Parquet → train with DP-CTGAN → generate synthetic rows →
     FK post-processing → ProfileDelta comparison
2. Privacy accountant ledger test: verify `spend_budget()` deducts the correct
   epsilon amount after a real DP training run.
3. README updated: Phase 7 marked complete, DP-SGD documented as operational.
4. OPERATOR_MANUAL updated: DP-SGD configuration section (epsilon, delta,
   noise_multiplier, max_grad_norm parameters and their effects).
5. Phase 7 end-of-phase retrospective in RETRO_LOG.
6. All open advisories audited per Rule 4.

### Testing & Quality Gates

- `poetry run pytest tests/unit/ --cov=src/synth_engine --cov-fail-under=90 -W error`
- `poetry run pytest tests/integration/ -v --no-cov -p pytest_postgresql`
- All review agents spawned (qa, ui-ux, devops, arch).
- Advisory count within Rule 11 ceiling after phase completion.

---

## Risks

1. **SDV DataTransformer separability**: SDV's `DataTransformer` may have undocumented
   coupling to `CTGANSynthesizer` internal state. T7.1 ADR must include a validated
   spike confirming separability before T7.2 commits to the approach.

2. **Opacus + CTGAN model compatibility**: Opacus requires per-sample gradient computation
   via `functorch` or `grad_sample` hooks. CTGAN's Discriminator may use layers that
   Opacus cannot instrument (e.g., custom activation functions). T7.2 must validate
   this empirically before full implementation.

3. **Training quality at low epsilon**: DP-SGD with tight privacy budgets (epsilon < 5)
   may produce synthetic data with significantly degraded utility. T7.4 benchmarks
   will quantify this and establish recommended operating ranges.
