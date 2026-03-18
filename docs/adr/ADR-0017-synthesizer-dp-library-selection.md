# ADR-0017 — Synthesizer & Differential Privacy Library Selection

**Date:** 2026-03-14
**Status:** Accepted (v2 — see Version History)
**Deciders:** PM + Architect
**Task:** P4-T4.0

---

## Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| v1 | 2026-03-14 | PM + Architect | Original — CTGAN + Opacus selection, FK strategy, epsilon accounting |
| v1a | 2026-03-15 | PM + Architect | Amendment ADR-0017a — Opacus `secure_mode` decision (ADV-067) |
| v2 | 2026-03-17 | PM | Consolidated — ADR-0017a content incorporated inline; ADR-0017a marked Superseded |

---

## Context

The Conclave Engine's Phase 4 goal is to generate synthetic datasets with mathematically
provable Differential Privacy (DP) guarantees, such that individual records in the source
data cannot be reverse-engineered from the synthetic output.

Two candidate synthesis libraries exist in the SDV ecosystem, and two candidate DP libraries
address complementary threat models. The choice of synthesis library determines which DP
library is applicable — the two pairings are mutually exclusive. Selecting the wrong pairing
produces a system where DP cannot be applied correctly to the synthesis algorithm, or where
the privacy guarantee is mathematically weaker and harder to bound rigorously.

This ADR documents the evaluated options, the chosen combination, the FK-handling strategy,
and the Epsilon accounting approach, so that T4.1 through T4.4 can be planned and implemented
without contradictory architectural assumptions.

### Conclave's core privacy requirement

The engine must provide **training-level DP** — privacy is enforced during model training, not
only at query time. This means the model itself cannot memorize individual training records.
The specific requirement is: for any two adjacent datasets (differing by one record), the
probability of any output of the training algorithm differs by at most a factor of
`exp(Epsilon)`, with failure probability at most `Delta`. This is the (Epsilon, Delta)-DP
guarantee under DP-SGD.

Output-level perturbation (adding noise to query results after training on the original data)
does not satisfy this requirement because the unperturbed model is stored and reused across
queries.

### Multi-table relational context

The Conclave Engine operates on relational schemas with foreign-key (FK) relationships,
managed by the subsetting engine introduced in Phase 3.5. The subsetting engine exposes a
topological sort of tables (parents before children), which is a prerequisite for any
per-table training strategy.

---

## Options Evaluated

### Option A: HMA1 + OpenDP

**HMA1** (SDV Hierarchical Modeling Algorithm v2) is a statistical model purpose-built for
multi-table relational schemas. It handles FK relationships natively by learning a hierarchical
generative structure across tables. It does not require a separate FK post-processing step.

**OpenDP** is a library of query-based DP mechanisms — primarily Laplace and Gaussian noise
mechanisms applied to aggregate statistics. It is well-suited for releasing statistical
summaries (means, histograms) with provable DP.

**Pairing HMA1 + OpenDP:**

- HMA1 is not a neural network. It has no gradient descent training loop. DP-SGD (gradient
  perturbation) cannot be applied to it.
- To add DP to HMA1, noise would be injected at the output level: perturb the parameters of
  the learned generative model (e.g., add Laplace noise to conditional distribution tables).
  This is output perturbation, not training-level DP.
- Output perturbation provides weaker privacy guarantees: the unperturbed model is trained on
  the original data, then perturbed before release. An adversary with partial access to
  training (e.g., via membership inference) can exploit the unperturbed training artifacts.
- The Epsilon accounting for output perturbation across the full model parameter space of
  HMA1 is complex and non-standard. There is no established, peer-reviewed sensitivity
  analysis for HMA1's full parameter vector.
- OpenDP's Laplace/Gaussian mechanisms have well-understood sensitivity bounds for scalar
  and low-dimensional queries — but HMA1's parameter space is high-dimensional and
  hierarchical, making per-parameter sensitivity bounds difficult to derive without bespoke
  analysis.

**Verdict:** HMA1 + OpenDP does not satisfy the training-level DP requirement. It provides
output perturbation at best, with unclear and difficult-to-audit Epsilon accounting.

### Option B: CTGAN / TVAE + Opacus (Chosen)

**CTGAN** (Conditional Tabular GAN) and **TVAE** (Tabular Variational Autoencoder) are
single-table neural network models from the SDV library. They learn the distribution of one
table at a time. Both use PyTorch as their training backend.

**Opacus** (Meta AI) is the industry-standard library for applying DP-SGD to PyTorch models.
It wraps the optimizer in a `PrivacyEngine` that clips per-sample gradients to a maximum
L2 norm and adds calibrated Gaussian noise to the gradient aggregate before each optimizer
step. This is training-level DP applied at the gradient level.

**Pairing CTGAN + Opacus:**

- CTGAN's PyTorch training loop is directly wrappable by `opacus.PrivacyEngine`. The engine
  intercepts gradient computation, applies noise, and tracks cumulative privacy cost via a
  Rényi DP accountant. This is a standard, well-documented integration path.
- The (Epsilon, Delta)-DP guarantee is mathematically provable and tight. Opacus's RDP
  accountant provides closed-form bounds on Epsilon for given noise multiplier, max gradient
  norm, batch size, and number of training steps.
- The FK limitation (CTGAN is single-table only) is manageable because the Conclave subsetting
  engine already exposes a topological sort of tables. Per-table training in topological order,
  combined with FK post-processing (see below), restores referential integrity without
  requiring multi-table awareness in the synthesis library.
- TVAE is an alternative to CTGAN within the same pairing — both support Opacus integration.
  CTGAN is preferred for its mode coverage properties on tabular data with mixed types.

**Verdict:** CTGAN + Opacus satisfies the training-level DP requirement with peer-reviewed,
accountable Epsilon bounds.

---

## Decision

**CTGAN with Opacus DP-SGD.**

The FK handling cost introduced by CTGAN's single-table architecture is bounded and manageable
via the topological training strategy described below. This cost is preferable to the
architectural risk of HMA1's unauditable output perturbation DP guarantees, which would
undermine Conclave's core value proposition.

TVAE is held as a drop-in alternative to CTGAN within the same pipeline, selectable by
configuration, and is architecturally treated identically for the purposes of this ADR.

---

## FK Handling Strategy

Because CTGAN operates on a single table at a time, multi-table FK relationships must be
preserved by the training orchestration layer rather than by the synthesis model itself.
The following three-step strategy achieves zero orphan FKs in synthetic output:

### Step 1: Topological training order

Tables are trained in topological order derived from the subsetting engine
(`modules/subsetting/`). Parent tables are trained before child tables. This ensures that
when a child table is trained, the synthetic parent table already exists and its primary
key (PK) set is known.

### Step 2: FK column conditioning during training

For each child table, the parent FK column is included as a feature in the training data
presented to CTGAN. This allows the model to learn the empirical distribution of FK values
(e.g., that `order.customer_id` tends to cluster around certain ranges), preserving
statistical FK patterns in the synthetic output. The raw FK values used during training are
drawn from the real source data; the model is not told they are FKs — it treats them as a
numeric or categorical feature column.

### Step 3: FK post-processing (orphan elimination)

After generation, any FK value in the synthetic child table that does not appear in the
synthetic parent table's PK column is replaced by a value sampled uniformly from the actual
synthetic parent PK set. This step is deterministic and bounded: every generated row is
inspected exactly once, and the resampling pool is the synthetic parent PK set (known size).
This guarantees zero orphan FKs regardless of how well the model learned the FK distribution.

**Why not filter out orphan rows instead of resampling?**
Filtering would reduce the requested row count, potentially below the caller's target. Uniform
resampling preserves row count while maintaining a valid FK distribution. The statistical
distortion introduced by resampling is bounded by the fraction of orphan FKs, which is
expected to be small when FK conditioning (Step 2) is applied correctly.

---

## Epsilon / Delta Accounting

### Per-run tracking (T4.3b)

Opacus tracks per-run Epsilon via its built-in **Rényi Differential Privacy (RDP) accountant**,
the default since Opacus 1.0. The RDP accountant provides tighter Epsilon bounds than the
Moments Accountant for DP-SGD and is more computationally efficient than the exact Gaussian
mechanism.

After each training epoch, the synthesis engine calls `PrivacyEngine.get_epsilon(delta)` to
obtain the cumulative Epsilon for the current training run. Training halts if the per-run
Epsilon budget is exceeded before all epochs complete.

**Fixed Delta convention:** Delta is fixed at `1e-5` (the probability of a privacy "failure"
event). This value is configurable per job but `1e-5` is the recommended default for
datasets up to 10^6 records.

### Global budget tracking (T4.4)

Per-run Epsilon consumption is reported to the Privacy Accountant (`modules/privacy/`), which
maintains a global ledger of cumulative Epsilon spend across all synthesis jobs. The global
budget uses **sequential composition**: the total Epsilon across all training runs on the same
dataset is the sum of per-run Epsilons. This is conservative but correct and easy to audit.

The split between T4.3b and T4.4 is:
- **T4.3b** (`modules/privacy/dp_engine.py`): wraps the optimizer, calls `get_epsilon()` per
  epoch, raises `BudgetExhaustionError` if the per-run allocation is exceeded.
- **T4.4** (`modules/privacy/accountant.py`): deducts the per-run Epsilon from the global
  ledger using a `SELECT ... FOR UPDATE` lock (PostgreSQL pessimistic locking) to prevent
  concurrent jobs from overdrawing the budget.

### Why RDP over Moments Accountant?

The Moments Accountant (Abadi et al., 2016) was the original DP-SGD accounting method. The
RDP accountant (Mironov, 2017) provides tighter bounds on Epsilon for the same noise
multiplier and number of steps, meaning that for a given privacy budget, the RDP accountant
permits more training steps before exhaustion. Opacus 1.0 ships the RDP accountant as its
default; using it requires no additional configuration beyond accepting the default.

### Why not PRV (Privacy Random Variables) accountant?

The PRV accountant (Koskela et al., 2021) provides even tighter bounds than RDP for many
practical settings, but requires numerical integration over the privacy loss distribution
and adds significant computational overhead per accounting query. For Conclave's expected
training run durations (minutes to hours), the per-epoch accounting overhead of RDP is
negligible and the tighter PRV bound is not worth the implementation complexity. This
decision may be revisited in a future phase if very tight per-run budgets become a product
requirement.

---

## Opacus `secure_mode` Decision (from ADR-0017a)

*Incorporated from ADR-0017a (2026-03-15, ADV-067). ADR-0017a is now Superseded by this v2.*

### What `secure_mode=True` provides

`PrivacyEngine(secure_mode=True)` replaces PyTorch's standard Mersenne-Twister PRNG with
the `torchcsprng` package, which provides a cryptographically-secure random number generator
(CSPRNG) backed by AES-CTR. This eliminates the theoretical possibility that an adversary
who can predict the PRNG state could bias the injected Gaussian noise, weakening the DP
guarantee.

In practice, this attack requires the adversary to:
1. Know the PRNG seed (which is not exposed and is randomized at process startup).
2. Observe enough PRNG outputs to reconstruct the state.
3. Use that knowledge to infer information about training data from the noise pattern.

For Opacus's use case — injecting noise into gradient aggregates during a training loop —
this attack path is academic: the gradient outputs are not observable by the adversary in
Conclave's air-gapped deployment model.

### Why `secure_mode=True` is not enabled

The Opacus `PrivacyEngine(secure_mode=True)` constructor raises `RuntimeError` at
instantiation time if `torchcsprng` is not installed. `torchcsprng` is a separate PyTorch
extension package that:
1. Requires compilation against the installed PyTorch version's C++ ABI.
2. Has not published pre-built wheels for Python 3.14 (the project's required runtime).
3. Has not been updated since 2021 and is no longer actively maintained upstream.
4. Is not available in the air-gapped deployment environment.

Empirically verified: running `PrivacyEngine(secure_mode=True)` raises `RuntimeError`
(tested against opacus 1.5.4, Python 3.14.1).

### Decision

`PrivacyEngine(secure_mode=True)` is **not enabled** in the production `DPTrainingWrapper`.

The `filterwarnings` entry `"ignore:Secure RNG turned off:UserWarning:opacus"` in
`pyproject.toml` is retained and explicitly justified by this section. If `torchcsprng`
publishes Python 3.14–compatible wheels in a future Opacus release, a new advisory task
should be filed to re-evaluate this decision.

---

## Consequences

### For T4.1 (GPU Passthrough & Ephemeral Storage)

GPU passthrough is required for CTGAN training to be performant. The `FORCE_CPU=true`
fallback path remains mandatory for CI and development environments without NVIDIA hardware.
Ephemeral storage (MinIO `tmpfs`-backed bucket) holds per-table Parquet files between the
subsetting and synthesis steps, and checkpoint artifacts between epochs.

### For T4.2a (Statistical Profiler)

No dependency on the library choice. The profiler operates on DataFrames/Parquet files and
is library-agnostic.

### For T4.2b (Synthesizer Core)

`SynthesisEngine` must:
1. Accept table data as Parquet (not directly from PostgreSQL).
2. Instantiate a `CTGANSynthesizer` (SDV) per table.
3. Train tables in topological order (using the sort from the subsetting engine).
4. Apply FK post-processing after each child table is generated.
5. Accept an optional `DPTrainingWrapper` parameter (injected by T4.3b) that wraps the
   optimizer before the training loop begins.

### For T4.3b (DP Engine Wiring)

`DPTrainingWrapper` in `modules/privacy/dp_engine.py` must:
1. Accept a PyTorch optimizer, model, and DataLoader and return a DP-wrapped optimizer via
   `opacus.PrivacyEngine.make_private()`.
2. Expose `epsilon_spent(delta)` and `check_budget(allocated_epsilon, delta)`.
3. NOT import from `modules/synthesizer/` — the boundary is enforced by import-linter.
   The wrapper accepts a training callback interface, not a synthesizer class.

`opacus` must be added to `pyproject.toml` as a production dependency.

### For T4.4 (Privacy Accountant)

The global ledger uses sequential composition to sum per-run Epsilons. The accountant must
use PostgreSQL `SELECT ... FOR UPDATE` to prevent budget overdraw under concurrent synthesis
jobs. SQLite is not an acceptable substitute for the concurrency integration test.

### Negative consequences / risks

- **Single-table training overhead**: training N tables requires N separate model training
  runs. For schemas with many tables, total training time is higher than a single HMA1 fit.
  Mitigation: tables can be parallelized where the dependency graph allows (sibling tables
  with no FK relationship between them).

- **FK distribution distortion**: the uniform resampling in Step 3 of the FK strategy may
  alter the statistical distribution of FK values if the model generates many orphan FKs.
  Mitigation: FK conditioning (Step 2) reduces orphan rate; the profiler (T4.2a) can
  quantify distribution drift in the FK column between source and synthetic output.

- **Opacus compatibility with CTGAN internals**: Opacus requires per-sample gradient
  computation (using `functorch` or `grad_sample` hooks), which may conflict with CTGAN's
  internal training loop if CTGAN does not expose a standard PyTorch `nn.Module` training
  interface. This risk will be validated empirically in T4.3b. If CTGAN's training loop
  is not directly wrappable, the fallback is to implement a thin PyTorch wrapper that
  exposes CTGAN's discriminator as a standard `nn.Module`.

---

## References

- Phase 0.8 Spike A: `spikes/spike_ml_memory.py`
  Proved that a tabular synthesizer can train on a 500 MB dataset and generate 1000 synthetic
  records within a 2 GB memory ceiling using chunked/batched processing. The spike used a
  `ChunkedGaussianSynthesizer` as a memory physics proof — the same chunked approach (reading
  source data in batches) applies to CTGAN training via DataLoader batch size configuration.
  Peak allocation during fit was well within the 2 GB ceiling on a chunked basis.

- Phase 3.5 subsetting engine: `src/synth_engine/modules/subsetting/`
  Provides the topological sort of FK-linked tables that drives the per-table training order
  defined in this ADR's FK handling strategy.

- ADR-0001: Modular Monolith Topology (`docs/adr/ADR-0001-modular-monolith-topology.md`)
  Defines the import-linter constraints that govern the `modules/privacy` ↔ `modules/synthesizer`
  boundary described in the T4.3b consequences section.

- ADR-0017a: Opacus `secure_mode` Decision (now Superseded — content incorporated in v2)

- ADR-0025: Custom CTGAN Training Loop Architecture

- Opacus library (Meta AI / PyTorch ecosystem)
  Implements DP-SGD via per-sample gradient clipping and Gaussian noise injection. Ships the
  RDP accountant as its default accounting method since version 1.0. No external URL is
  referenced per the air-gapped deployment mandate; the library is pinned in `pyproject.toml`.

- Mironov, I. (2017). "Rényi Differential Privacy of the Gaussian Mechanism."
  IEEE Computer Security Foundations Symposium. The theoretical foundation for the RDP
  accountant used by Opacus.

- Abadi, M. et al. (2016). "Deep Learning with Differential Privacy."
  ACM CCS. The original DP-SGD paper introducing the Moments Accountant (superseded by RDP
  for tighter bounds in Opacus's default configuration).

- torchcsprng repository: https://github.com/pytorch/csprng (archived; last release 2021)
