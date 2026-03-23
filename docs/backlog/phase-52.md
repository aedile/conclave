# Phase 52 — Demo & Benchmark Suite

**Goal**: Produce runnable Jupyter notebook demos with real, reproducible benchmark
results. Parameterized epsilon curve generation with rigorous statistical methodology.
Two audience-specific notebooks (data architects, AI/ML builders). All results committed
as versioned artifacts with honest analysis — results that look bad stay in.

**Prerequisite**: Phase 50 merged (security fixes resolve expired advisories). Phase 51
recommended (release engineering).

**ADR**: None required — no architectural changes. Demo dependency group is additive and
isolated.

**Source**: Portfolio review and go-to-market planning, 2026-03-23.

---

## T52.1 — Benchmark Infrastructure

**Priority**: P1 — Foundation for all subsequent tasks.

### Context & Constraints

1. No plotting libraries exist in `pyproject.toml`. No Jupyter notebooks exist in the repo.
2. `scripts/benchmark_dp_quality.py` exists but runs at 500 rows / 10 epochs — acknowledged
   in `docs/archive/DP_QUALITY_REPORT.md` as insufficient for quality assessment (GAN
   hasn't converged).
3. `scripts/e2e_load_test.py` exists as a load test harness but produces no visualizations.
4. Demo dependencies (matplotlib, seaborn, jupyter, scikit-learn) MUST NOT appear in the
   production dependency tree or Docker image. They belong in a
   `[tool.poetry.group.demos]` optional group only.
5. Production modules (`src/synth_engine/`) MUST NOT import from demo dependencies.
6. The benchmark harness must be idempotent — skip already-completed parameter combinations
   to allow resume after crash.
7. All benchmark runs must use a fixed random seed strategy (torch manual seed, numpy seed,
   Python random seed) for reproducibility. Limitations (cuDNN non-determinism on GPU) must
   be documented in results metadata.
8. `nbstripout` must be added as a pre-commit hook to prevent credential/PII leaks from
   executed notebook cell outputs.

### Acceptance Criteria

1. `[tool.poetry.group.demos]` dependency group added to `pyproject.toml` with:
   `matplotlib`, `seaborn`, `jupyter`, `scikit-learn`, `nbstripout`.
2. `nbstripout` added to `.pre-commit-config.yaml` to strip all notebook cell outputs
   before commit.
3. `demos/` top-level directory created with `demos/results/` and `demos/figures/` in
   `.gitignore` for generated output, but `demos/*.ipynb`, `demos/*.py`,
   `demos/README.md` committed.
4. `scripts/benchmark_epsilon_curves.py` created — parameterized harness that:
   - Accepts: PostgreSQL connection string, table name, parameter grid config (JSON/YAML).
   - Trains CTGAN at configurable noise multipliers x epoch counts x sample sizes.
   - Records per-run: actual epsilon (from Opacus RDP accountant), wall time (start = first
     training epoch, stop = final sample generation), KS statistic per numeric column,
     chi-squared p-value per categorical column, mean absolute error per column, correlation
     matrix delta, FK orphan rate.
   - Uses delta value matching production constant `_DP_EPSILON_DELTA` (currently `1e-5`).
   - Sets fixed random seeds (torch, numpy, Python) per run for reproducibility.
   - Outputs structured JSON + CSV to configurable output directory.
   - Is idempotent — skips completed parameter combinations on resume.
   - Records failure rows (with error type and message) for any grid cell that errors —
     never silently omits.
   - Includes `schema_version` field in all output artifacts.
   - Includes hardware metadata (CPU model, RAM, core count, OS, GPU if available) in
     results.
   - Sanitizes all artifact filenames from parameter grid config, not from dataset schema
     column names.
   - Has a configurable per-run timeout (default: 30 minutes) — writes TIMEOUT result row
     and continues.
5. All artifact filenames derived from parameter grid configuration, not from dataset column
   names (path traversal prevention).
6. `demos/conclave_demo.py` convenience wrapper created for notebook use — orchestrates
   synthesis via direct Python imports (not API calls), using an isolated SQLite or fresh
   PostgreSQL instance for privacy budget (never touches production ledger).

### Files to Create/Modify

- Modify: `pyproject.toml` (add demos dependency group)
- Modify: `.pre-commit-config.yaml` (add nbstripout hook)
- Create: `demos/` directory structure
- Create: `scripts/benchmark_epsilon_curves.py`
- Create: `demos/conclave_demo.py`

### Negative Test Requirements (from spec-challenger)

- `test_demo_dependencies_not_imported_in_production_modules` — import every module in
  `src/synth_engine/` without demos group; assert no ImportError.
- `test_benchmark_harness_rejects_run_without_dataset_fixture` — run harness with no
  dataset; verify clear error.
- `test_benchmark_epsilon_delta_matches_production_constant` — assert benchmark delta
  equals production `_DP_EPSILON_DELTA`.
- `test_benchmark_run_produces_identical_metrics_given_fixed_seed` — run twice with same
  seed; assert metrics match within tolerance.
- `test_benchmark_harness_records_failure_row_on_run_error` — inject failure; verify
  failure row recorded, not omitted.
- `test_results_artifact_contains_schema_version_field` — parse results; assert
  schema_version present.
- `test_committed_results_contain_no_real_column_names` — assert column identifiers match
  fixture schema only.
- `test_parameter_grid_is_committed_alongside_results` — grid config must be an artifact.

---

## T52.2 — Execute Benchmarks (Real Results)

**Priority**: P1 — Produces the raw data for all notebooks.

### Context & Constraints

1. Benchmarks MUST run against `sample_data/` fixtures (publicly committable, all fictional
   Faker-generated data) — never against production data.
2. Seed PostgreSQL with scaled-up sample data: 1K, 10K, 50K rows per table using
   `scripts/seed_sample_data.py` with configurable row counts. 100K deferred to
   GPU-available hardware.
3. Parameter grid for `customers` table (most PII-dense): noise multipliers
   (0.5, 1.0, 2.0, 5.0, 10.0) x epoch counts (50, 100, 200) x sample sizes
   (1K, 10K, 50K) = 45 cells.
4. Reduced grid for `orders` table: noise multipliers (1.0, 5.0, 10.0) x epochs
   (100, 200) x sample sizes (10K, 50K) = 12 cells.
5. All runs on documented hardware. Estimated total wall time: 8-16 hours on CPU
   (16 GB RAM, no GPU). Grid designed to complete within this envelope.
6. Results committed to `demos/results/` as versioned JSON/CSV artifacts with parameter
   grid config alongside.
7. Benchmarks run against an isolated database instance — fresh PostgreSQL with its own
   privacy ledger. The production ledger is never touched.
8. CPU-only is the supported and documented path. GPU acceleration detected at runtime and
   recorded in metadata but not required.

### Acceptance Criteria

1. Scaled sample data generation script supports configurable row counts (1K, 10K, 50K).
2. Full parameter grid executed for `customers` (45 cells) and `orders` (12 cells).
3. Every grid cell has a result row — no omissions. Failed cells have failure rows with
   error details.
4. Results committed as `demos/results/benchmark_customers_v1.json`,
   `demos/results/benchmark_orders_v1.json` with `schema_version: "1.0"`.
5. Parameter grid config committed alongside results as `demos/results/grid_config.json`.
6. Hardware metadata present and non-empty in all result files.
7. FK orphan rate is 0 for all successful synthesis runs.
8. Wall time field present and positive for all result rows.
9. All committed artifacts reference only `sample_data/` fixture column names — no
   production schema names.

### Files to Create/Modify

- Modify: `scripts/seed_sample_data.py` (add configurable row counts)
- Create: `demos/results/benchmark_customers_v1.json`
- Create: `demos/results/benchmark_orders_v1.json`
- Create: `demos/results/grid_config.json`

### Negative Test Requirements (from spec-challenger)

- `test_results_manifest_contains_all_parameter_grid_cells` — parse results; assert every
  grid tuple has a result row.
- `test_fk_orphan_rate_is_zero_for_well_formed_fixture` — assert FK metric is 0 for
  successful runs.
- `test_wall_time_field_present_and_positive_in_all_result_rows` — measurement
  completeness.
- `test_results_hardware_metadata_present_and_non_empty` — hardware documentation gate.

---

## T52.3 — Epsilon Curve Notebook

**Priority**: P1 — The rigorous benchmark notebook.

### Context & Constraints

1. This notebook is for people who care about the math. Methodology must be defensible in
   a peer review.
2. All charts generated from committed raw results in `demos/results/` — no live training
   in the notebook itself.
3. Epsilon values are post-hoc measured by Opacus RDP accountant, not configured targets.
4. Results that look bad stay in. The committed results artifact MUST contain a result row
   for every cell in the parameter grid — the notebook MUST NOT filter out unfavorable
   results.
5. Notebook must execute cleanly with `Run All` from a fresh kernel — no hidden state
   dependencies.
6. Figures saved as SVG (publication quality) to `demos/figures/` via a documented
   regeneration script.

### Acceptance Criteria

1. `demos/epsilon_curves.ipynb` created with sections:
   - **Methodology**: Hardware, software versions, seed strategy, Opacus RDP accountant,
     delta value, dataset description, parameter grid, wall-time measurement scope (first
     epoch to final sample), limitations.
   - **Epsilon vs. Noise Multiplier**: For each sample size, sigma on x-axis, measured
     epsilon on y-axis. Expected inverse relationship annotated.
   - **Epsilon vs. Statistical Fidelity**: Epsilon on x-axis, mean KS statistic on y-axis.
     Annotate sweet spot IF one exists — do not manufacture one.
   - **Epsilon vs. Dataset Size**: Fixed sigma, varying row counts. Demonstrates
     subsampling amplification.
   - **Correlation Preservation Heatmaps**: Side-by-side source vs. synthetic correlation
     matrices at three epsilon levels (strong/moderate/weak).
   - **FK Integrity Verification**: Table showing orphan count = 0 for all runs.
   - **Honest Limitations**: CTGAN architecture constraints, epoch count vs. convergence,
     what these numbers mean and don't mean.
2. Every chart has: axis labels with units, legend, one-sentence interpretation, figure
   title.
3. Notebook executes cleanly via `jupyter nbconvert --execute` from fresh kernel with no
   errors.
4. Pre-rendered SVG figures committed to `demos/figures/` and referenced in
   `demos/README.md`.
5. `demos/generate_figures.py` script regenerates all figures from committed raw results.

### Files to Create/Modify

- Create: `demos/epsilon_curves.ipynb`
- Create: `demos/generate_figures.py`
- Create: `demos/figures/` (pre-rendered SVGs)

### Negative Test Requirements (from spec-challenger)

- `test_notebooks_execute_cleanly_from_fresh_kernel` — run notebook via nbconvert; assert
  zero cell errors.
- `test_figures_are_regenerable_from_committed_results` — run generate_figures.py; assert
  output matches committed figures.
- `test_notebook_epsilon_curve_runs_without_network_access` — notebook must not pull data
  at runtime.

---

## T52.4 — Quick-Start Notebook

**Priority**: P1 — The data architect demo.

### Context & Constraints

1. Target audience: data architects who need to see results fast. Three cells: connect,
   synthesize, compare.
2. Uses `demos/conclave_demo.py` wrapper from T52.1 for clean interface.
3. The notebook MUST NOT contain hardcoded database credentials. Connection strings use
   environment variables or localhost defaults for the Docker Compose stack.
4. `nbstripout` (from T52.1) prevents accidental credential commits from executed cell
   output.
5. Notebooks load model artifacts ONLY through the verified production code path
   (`ModelArtifact.load()`) — never via raw `pickle.load()`.
6. The output of Cell 3 (comparison plots) is the screenshot you send people.

### Acceptance Criteria

1. `demos/quickstart.ipynb` created with three primary cells:
   - **Cell 1 (Connect)**: Connect to PostgreSQL via env var or localhost default. Print
     discovered tables, row counts, FK relationships.
   - **Cell 2 (Synthesize)**: Generate synthetic data for 2-3 tables with DP enabled.
     Print summary: table, rows generated, epsilon, duration.
   - **Cell 3 (Compare)**: Side-by-side distribution overlays (real vs synthetic),
     correlation heatmaps, FK integrity check ("FK orphans: 0").
2. No hardcoded credentials in notebook source cells.
3. Notebook executes cleanly from fresh kernel against the Docker Compose PostgreSQL
   instance seeded with sample data.
4. All model artifact loading uses `ModelArtifact.load()` (HMAC-verified), never raw
   `pickle.load()`.
5. `demos/README.md` includes setup instructions (docker compose, seed data, install demos
   group, run notebook).

### Files to Create/Modify

- Create: `demos/quickstart.ipynb`
- Modify: `demos/README.md`

### Negative Test Requirements (from spec-challenger)

- `test_notebooks_execute_cleanly_from_fresh_kernel` — (shared with T52.3, covers all
  notebooks).
- `test_demo_readme_links_resolve_to_existing_files` — all links in demos/README.md point
  to existing files.

---

## T52.5 — AI Builder Notebook

**Priority**: P2 — The ML training data demo.

### Context & Constraints

1. Target audience: AI developers/founders who want to train models on synthetic data.
2. Demonstrates "train on synthetic, test on real" methodology — the key value proposition
   for ML use cases.
3. Downstream task: binary classification (payment method prediction from order amount +
   customer features) using scikit-learn LogisticRegression (simple, reproducible, no GPU
   needed).
4. Evaluation metric: ROC-AUC (handles class imbalance better than accuracy).
5. Train/test split: 80/20 stratified on the target variable. Holdout set is ALWAYS real
   data.
6. Comparison protocol:
   - Baseline: Train on real, test on real (upper bound).
   - Synthetic: Train on synthetic (various epsilon levels), test on real.
   - Augmented: Train on real + synthetic combined, test on real.
7. scikit-learn is in the `demos` dependency group — not in production.
8. All model artifact loading through verified production path only.

### Acceptance Criteria

1. `demos/training_data.ipynb` created with sections:
   - **Model Selection**: Documents model class (LogisticRegression), metric (ROC-AUC),
     train/test split (80/20 stratified), dataset, and rationale.
   - **Generate Synthetic Data**: Using conclave_demo wrapper, generate synthetic datasets
     at 3 epsilon levels.
   - **Train on Real (Baseline)**: Train LogisticRegression, report ROC-AUC on holdout.
   - **Train on Synthetic**: Train on synthetic data at each epsilon level, report ROC-AUC
     on same holdout.
   - **Utility Curve**: Plot epsilon on x-axis, downstream ROC-AUC on y-axis. Shows
     practical privacy-utility tradeoff.
   - **Augmentation**: Train on real + synthetic combined, report ROC-AUC.
   - **Privacy Guarantee Explanation**: What epsilon=0.17, epsilon=2.0, epsilon=10.0 mean
     in plain language.
   - **Honest Limitations**: Synthetic data typically underperforms real data on downstream
     tasks — state this explicitly. Simple model may not capture all effects.
2. Every chart has axis labels, units, legend, interpretation.
3. Notebook executes cleanly from fresh kernel.
4. Fixed random seed for train/test split and model training (reproducible results).

### Files to Create/Modify

- Create: `demos/training_data.ipynb`

### Negative Test Requirements (from spec-challenger)

- `test_notebooks_execute_cleanly_from_fresh_kernel` — (shared, covers all notebooks).
- `test_ai_builder_notebook_documents_model_selection_rationale` — assert notebook
  contains Model Selection markdown cell with model name, metric, split, dataset.

---

## T52.6 — Published Results & README Updates

**Priority**: P1 — Makes the demos discoverable.

### Context & Constraints

1. Pre-rendered figures from T52.3 committed as SVGs for people who won't run notebooks.
2. `demos/README.md` is the entry point — how to run, hardware requirements, expected
   runtimes.
3. Top-level `README.md` updated with a "Demos & Benchmarks" section linking to notebooks
   and key figures.
4. The README section should include 1-2 inline figures (epsilon curve, correlation
   heatmap) as compelling visual evidence.
5. All links in both READMEs must resolve to existing files.

### Acceptance Criteria

1. `demos/README.md` created with: overview, prerequisites (Docker Compose, demos
   dependency group), setup instructions, per-notebook descriptions with expected runtimes,
   hardware requirements, methodology summary.
2. Top-level `README.md` updated with "Demos & Benchmarks" section between "Validated
   Scale" and "Quality and Development Process" sections. Includes:
   - 1-2 key figures inline (epsilon vs fidelity curve, correlation heatmap).
   - Links to all three notebooks.
   - Link to `demos/README.md` for full setup.
   - Brief methodology note ("all epsilon values post-hoc measured by Opacus RDP
     accountant, not configured targets").
3. All links in `demos/README.md` resolve to existing committed files.
4. All links in updated `README.md` resolve to existing committed files.
5. "How This Was Built" section metrics updated to current values (commits, PRs, ADRs,
   LOC).

### Files to Create/Modify

- Create: `demos/README.md`
- Modify: `README.md` (add Demos & Benchmarks section, update metrics)

### Negative Test Requirements (from spec-challenger)

- `test_demo_readme_links_resolve_to_existing_files` — all README links point to existing
  files.

---

## Task Execution Order

```
T52.1 (benchmark infrastructure + deps) ──────> foundation
                                                    |
                                                    v
T52.2 (execute benchmarks, commit results) ────> raw data
                                                    |
                                                    v
T52.3 (epsilon curves notebook) ───┐
T52.4 (quick-start notebook) ─────┼──> parallel (notebooks)
T52.5 (AI builder notebook) ──────┘
                                      |
                                      v notebooks complete
T52.6 (published results + READMEs) ──> documentation
```

T52.1 must complete first (infrastructure). T52.2 depends on T52.1 (needs harness).
T52.3/T52.4/T52.5 can run in parallel after T52.2 produces results. T52.6 depends on all
notebooks being complete.

---

## Phase 52 Exit Criteria

1. Benchmark harness produces reproducible results with fixed seeds.
2. Full parameter grid executed — every cell has a result row (no omissions).
3. All three notebooks execute cleanly from fresh kernel via `nbconvert --execute`.
4. Pre-rendered SVG figures committed and regenerable from raw results.
5. `nbstripout` pre-commit hook active — no cell outputs in committed notebooks.
6. Demo dependency group isolated — production modules do not import from it.
7. Benchmark runs use isolated database — production privacy ledger untouched.
8. All committed artifacts reference only `sample_data/` fixture column names.
9. README updated with Demos & Benchmarks section, current metrics, inline figures.
10. All quality gates pass.
11. Review agents pass for all tasks.
