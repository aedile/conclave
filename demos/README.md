# Conclave Engine — Demo & Benchmark Suite

This directory contains demo notebooks and benchmark scripts for the
Air-Gapped Synthetic Data Generation Engine.

## Overview

The demos directory provides interactive Jupyter notebooks and a reusable
Python demo module that together demonstrate the full
connect → synthesize → compare workflow.

All notebooks run **entirely offline** — no external API calls are made.

---

## Directory Layout

```
demos/
├── README.md            — This file
├── __init__.py          — Package marker (empty)
├── conclave_demo.py     — Reusable synthesis demo wrapper (T52.1)
├── quickstart.ipynb     — Quick-start: connect → synthesize → compare (T52.4)
├── figures/             — Generated SVG/PNG figures (PNG/PDF gitignored, SVG committed)
└── results/             — Generated CSV results and versioned JSON artifacts
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
poetry run nbstripout demos/quickstart.ipynb
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
