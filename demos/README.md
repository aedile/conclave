# Conclave Engine — Demo & Benchmark Suite

This directory contains demo notebooks and benchmark scripts for the
Air-Gapped Synthetic Data Generation Engine.

> This README is a placeholder, to be filled in during T52.6.

## Directory Layout

```
demos/
├── README.md            — This file
├── __init__.py          — Package marker (empty)
├── conclave_demo.py     — Convenience wrapper for interactive synthesis demos
├── figures/             — Generated SVG/PNG figures (PNG/PDF gitignored, SVG committed)
└── results/             — Generated CSV results and versioned JSON artifacts
```

## Installation

The demos group is an optional Poetry dependency group:

```bash
poetry install --with dev,demos
```

## Usage

See `conclave_demo.py` for a programmatic synthesis walkthrough.
The benchmark harness lives at `scripts/benchmark_epsilon_curves.py`.
