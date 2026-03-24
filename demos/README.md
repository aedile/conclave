# Conclave Engine — Demo & Benchmark Suite

Part of the [Conclave](../README.md) air-gapped synthetic data generation engine.

This directory contains demo notebooks and benchmark scripts. All notebooks run
**entirely offline** — no external API calls are made.

---

## Directory Layout

```
demos/
├── README.md                — This file
├── __init__.py              — Package marker (empty)
├── conclave_demo.py         — Reusable synthesis demo wrapper (T52.1)
├── quickstart.ipynb         — Quick-start: connect → synthesize → compare (T52.4)
├── epsilon_curves.ipynb     — Rigorous epsilon curve analysis across 45 parameter combinations (T52.3)
├── training_data.ipynb      — AI builder notebook: train-on-synthetic, test-on-real (T52.5)
├── generate_figures.py      — Figure regeneration script; run to rebuild all SVG outputs (T52.3)
├── figures/                 — Generated SVG/PNG figures (PNG/PDF gitignored, SVG committed)
│   ├── epsilon_vs_noise_multiplier.svg
│   ├── epsilon_vs_statistical_fidelity.svg
│   ├── epsilon_vs_schema_complexity.svg
│   ├── correlation_preservation.svg
│   └── fk_integrity.svg
└── results/                 — Generated JSON benchmark artifacts
    ├── grid_config.json              — Benchmark grid parameters
    ├── benchmark_customers_v1.json   — Results for customers table
    └── benchmark_orders_v1.json      — Results for orders table
```

---

## Prerequisites

### 1. Python and Poetry

```bash
# Install all required dependency groups
poetry install --with dev,synthesizer,demos
```

The `demos` group adds:

| Package       | Purpose                                      |
|---------------|----------------------------------------------|
| `matplotlib`  | 2-D plotting for distribution overlays       |
| `seaborn`     | Statistical visualisation built on matplotlib |
| `jupyter`     | Notebook server and kernel                   |
| `scikit-learn`| KS statistic, chi-squared, MAE metrics       |
| `nbstripout`  | Strips outputs before commit (pre-commit hook)|
| `scipy`       | Statistical distance calculations            |

### 2. Docker Compose Stack

The `quickstart.ipynb` notebook requires a running PostgreSQL instance.
The project ships a ready-to-use Docker Compose stack:

```bash
docker compose up -d
```

This starts PostgreSQL on `localhost:5432` with the default
`conclave:conclave@conclave` credentials.

### 3. Seed the Sample Database

```bash
poetry run python scripts/seed_sample_data.py \
    --conn postgresql://conclave:conclave@localhost:5432/conclave
```

### 4. Set Required Environment Variables

**Security requirement**: Never hardcode credentials in notebooks.
Set these in your shell before launching Jupyter:

```bash
# Signing key — must be at least 32 bytes
export ARTIFACT_SIGNING_KEY="change-me-to-a-32-byte-secret-key!"

# Database URL — defaults to Docker Compose stack if not set
export DATABASE_URL="postgresql://conclave:conclave@localhost:5432/conclave"
```

---

## Notebooks

### `quickstart.ipynb` — Quick-Start Workflow

**Audience**: Data architects exploring synthetic data generation for the
first time.

**Workflow**: connect → synthesize → compare

**Expected runtime**: 2–5 minutes (CPU-only, 200 rows, 5 epochs)

| Section    | What it does                                              |
|------------|-----------------------------------------------------------|
| **Connect**    | Discovers tables, row counts, and FK relationships    |
| **Synthesize** | Runs DP-CTGAN synthesis via `conclave_demo.run_demo()` |
| **Compare**    | Overlays real vs. synthetic distributions and correlation heatmaps |

**To run**:
```bash
jupyter lab demos/quickstart.ipynb
```

---

### `epsilon_curves.ipynb` — Epsilon Curve Analysis

**Audience**: Privacy engineers, compliance reviewers, researchers.

**Expected runtime**: 45–90 minutes (CPU-only, 45 parameter combinations)

Parameterized epsilon curve analysis across 45 noise-multiplier/epoch/sample-size
combinations. Each combination trains CTGAN with Opacus DP-SGD and records the
post-hoc epsilon measured by the Opacus RDP accountant.

| Section    | What it does                                              |
|------------|-----------------------------------------------------------|
| **Grid setup**     | Defines the 45-point benchmark grid (noise, epochs, sample size) |
| **Training loop**  | Runs DP-CTGAN for each grid point; measures post-hoc epsilon |
| **Curve plots**    | Epsilon vs noise multiplier, statistical fidelity, schema complexity |
| **KS statistics**  | Kolmogorov-Smirnov test per column across all grid points |
| **FK integrity**   | Verifies zero orphan rows at every synthesis run |

Epsilon values are post-hoc measured by the Opacus RDP accountant — not
configured targets. All runs use fixed random seeds for reproducibility.
See [docs/archive/DP_QUALITY_REPORT.md](../docs/archive/DP_QUALITY_REPORT.md) for
prior micro-benchmark results and recommended epsilon ranges by use case.

**To run**:
```bash
jupyter lab demos/epsilon_curves.ipynb
```

Pre-rendered figures are in `figures/`. To regenerate them from the committed
benchmark results:

```bash
poetry run python demos/generate_figures.py
```

---

### `training_data.ipynb` — AI Builder: Train-on-Synthetic, Test-on-Real

**Audience**: AI/ML builders who need privacy-safe training datasets.

**Expected runtime**: 10–20 minutes (CPU-only, 1,000 rows, 10 epochs)

Demonstrates the privacy-utility tradeoff: train a downstream classifier on
synthetic data at varying epsilon levels, then evaluate against real held-out
data. Shows that strong privacy (low epsilon) comes with a measurable utility
cost, and quantifies that cost.

| Section    | What it does                                              |
|------------|-----------------------------------------------------------|
| **Synthesis**     | Generates synthetic data at five epsilon levels          |
| **Downstream ML** | Trains a classifier (LogisticRegression) on each synthetic set |
| **Utility curve** | Plots test accuracy vs. epsilon; highlights the privacy-utility frontier |
| **Takeaway**      | Recommends epsilon ranges by use case                    |

**To run**:
```bash
jupyter lab demos/training_data.ipynb
```

---

### `generate_figures.py` — Figure Regeneration Script

Reads the committed benchmark result artifacts from `results/` and regenerates
all five SVG figures in `figures/`. Run this after adding new benchmark results
or when updating figure styling.

```bash
poetry run python demos/generate_figures.py
```

Produces:

| Figure | Description |
|--------|-------------|
| `figures/epsilon_vs_noise_multiplier.svg` | Epsilon as a function of noise multiplier |
| `figures/epsilon_vs_statistical_fidelity.svg` | Privacy-utility frontier (epsilon vs. KS statistic) |
| `figures/epsilon_vs_schema_complexity.svg` | Epsilon behaviour across table sizes |
| `figures/correlation_preservation.svg` | Pearson correlation preservation by noise level |
| `figures/fk_integrity.svg` | FK orphan row count across all runs (should be zero) |

---

## Hardware Requirements

| Config        | RAM   | Storage | Notes                       |
|---------------|-------|---------|-----------------------------|
| CPU (minimum) | 4 GB  | 1 GB    | Slower training (~5 min/run)|
| CPU (recommended) | 8 GB | 2 GB | Comfortable for benchmarks  |
| GPU (optional) | 8 GB VRAM | 2 GB | 5–10x faster training |

GPU acceleration requires the synthesizer dependency group and a
CUDA-compatible PyTorch build.

---

## Committing Notebooks

Cell outputs **must** be stripped before committing. The project pre-commit
hook runs `nbstripout` automatically.

To strip manually:

```bash
poetry run nbstripout demos/quickstart.ipynb demos/epsilon_curves.ipynb demos/training_data.ipynb
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ImportError: Missing required dependency` | `synthesizer` or `demos` group not installed | `poetry install --with dev,synthesizer,demos` |
| `EnvironmentError: ARTIFACT_SIGNING_KEY is not set` | Key env var missing | `export ARTIFACT_SIGNING_KEY="..."` (>= 32 bytes) |
| `OperationalError: could not connect to server` | PostgreSQL not running | `docker compose up -d` |
| `ValueError: signing_key must be at least 32 bytes` | Key is too short | Use a key of at least 32 characters |
| Slow synthesis | CPU-only mode | Set `FORCE_CPU=true` or add a GPU; reduce `epochs` |
| `generate_figures.py` fails | Missing results JSON | Run `epsilon_curves.ipynb` first to populate `results/` |
