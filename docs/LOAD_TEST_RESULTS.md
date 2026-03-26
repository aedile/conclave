# Load Test Results

**Status**: Template — load test not yet executed (requires live PostgreSQL with pagila schema).

Run the load test with:

```bash
DATABASE_URL=postgresql://user:pass@localhost:5432/pagila \
  poetry run python scripts/load_test.py \
  --row-count 5000 --epochs 50 --epsilon 10.0
```

See `scripts/load_test.py` for full usage documentation.

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--row-count` | 5000 | Rows to synthesize per table |
| `--epochs` | 50 | CTGAN training epochs per table |
| `--epsilon` | 10.0 | Differential privacy epsilon budget (range: (0, 10.0]) |
| `--delta` | 1e-5 | Differential privacy delta |

## Target Tables

The load test exercises the 5-table pagila subset:

1. `customer` — root table (5,000+ rows seed point)
2. `address` — FK parent of customer
3. `rental` — FK child of customer
4. `inventory` — FK parent of rental
5. `film` — FK parent of inventory

## Expected Metrics (Apple M4, 24 GB RAM)

These are target baselines. Actual results depend on hardware and DB load.

| Stage | Expected Time | Notes |
|-------|--------------|-------|
| Schema reflection | < 1s | |
| Subsetting (5000 rows) | 2-5s | FK-aware traversal |
| Masking | < 1s | HMAC-SHA256 FPE |
| DP-SGD training per table | 30-120s | Depends on table size and epoch count |
| Total pipeline | 3-10 min | 5 tables, 50 epochs each |

## Convergence Notes

Based on Phase 54 validation experience:
- `customer`, `rental`, `inventory` converge reliably with 5,000+ rows.
- `address` and `film` may diverge due to high-cardinality text columns.
  If divergence occurs, increase `--row-count` to 10,000+ or reduce noise multiplier.

Divergence produces NaN/Inf in the synthetic output and is reported as
`converged: No` in the per-table results.
