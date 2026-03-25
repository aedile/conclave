# End-to-End DP Synthesis Validation Results

**Task**: T54.3 — Validation Run & Results Documentation
**Status**: PENDING — template populated; run `scripts/validate_full_pipeline.py` to fill in results.

---

## How to Run

### Prerequisites

1. PostgreSQL is running and accessible (local or Docker).
2. The Pagila dataset has been loaded via `scripts/provision_pagila.sh` (T54.1).
3. Python dependencies are installed with the synthesizer group:
   ```
   poetry install --with synthesizer
   ```
4. Set `FORCE_CPU=true` if no GPU is available.
5. Optionally set `DB_DSN` to your PostgreSQL connection string
   (default: `postgresql://localhost/pagila`).

### Command

```bash
python scripts/validate_full_pipeline.py \
    --subset-size 500 \
    --epsilon 10.0 \
    --epochs 50 \
    --output-dir output/
```

The script writes a structured JSON report to
`output/validation-report-<timestamp>.json` (gitignored). Copy the key
metrics from that report into the sections below.

---

## Environment

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

| Component | Value |
|-----------|-------|
| Hardware | (to be filled — e.g., macOS ARM64, 10 cores, 24 GB RAM) |
| Python version | (to be filled — e.g., 3.14) |
| CTGAN version | (to be filled) |
| Opacus version | (to be filled) |
| SDV version | (to be filled) |
| cosmic-ray version | (to be filled) |
| Database | PostgreSQL (version to be filled) |
| Dataset | Pagila (5-table subset: customer, address, rental, inventory, film) |
| Force CPU | true |

---

## Configuration

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

| Parameter | Value |
|-----------|-------|
| Subset size | 500 |
| Epsilon | 10.0 (NOTE: chosen for validation speed, not production privacy — production deployments should use epsilon ≤ 1.0) |
| Delta | 1e-5 |
| CTGAN epochs | 50 |
| Force CPU | true |

---

## Pipeline Execution

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

| Stage | Duration | Details |
|-------|----------|---------|
| Schema reflection | (to be filled) | FK topology resolved |
| Subsetting | (to be filled) | 500 root rows selected |
| Masking | (to be filled) | PII-like columns masked |
| Statistical profiling | (to be filled) | Distribution detection |
| CTGAN training (DP-SGD) | (to be filled) | 50 epochs, discriminator-level DP |
| Synthetic generation | (to be filled) | Rows generated |
| FK post-processing | (to be filled) | Orphan removal |
| Output validation | (to be filled) | KS stats, FK integrity |
| **Total** | (to be filled) | |

---

## Schema Reflection

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

The 5-table Pagila subset forms the following FK chain:

```
film ← inventory ← rental ← customer ← address
```

| Table | PK | FK References | Notes |
|-------|----|---------------|-------|
| film | film_id | — | Root of FK chain |
| inventory | inventory_id | film_id → film.film_id | |
| rental | rental_id | inventory_id → inventory.inventory_id | |
| customer | customer_id | address_id → address.address_id | |
| address | address_id | — | |

(Full FK topology diagram to be added after reflection run.)

---

## Subsetting Results

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

| Table | Source Rows | Subset Rows Selected | Notes |
|-------|-------------|---------------------|-------|
| film | (to be filled) | (to be filled) | |
| inventory | (to be filled) | (to be filled) | FK-traversed from film |
| rental | (to be filled) | (to be filled) | FK-traversed from inventory |
| customer | (to be filled) | (to be filled) | FK-traversed from rental |
| address | (to be filled) | (to be filled) | FK-traversed from customer |

---

## Masking Verification

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

| Column | Table | Masking Applied | Verified |
|--------|-------|-----------------|---------|
| first_name | customer | FPE deterministic | (to be filled) |
| last_name | customer | FPE deterministic | (to be filled) |
| email | customer | FPE deterministic | (to be filled) |
| address | address | FPE deterministic | (to be filled) |
| phone | address | FPE deterministic | (to be filled) |

Verification method: assert no raw PII value from source appears unchanged in
synthetic output for any masked column.

---

## CTGAN Training with DP-SGD

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

| Table | Epochs | Final Generator Loss | Final Discriminator Loss | Epsilon Consumed | Notes |
|-------|--------|---------------------|--------------------------|-----------------|-------|
| film | 50 | (to be filled) | (to be filled) | (to be filled) | |
| inventory | 50 | (to be filled) | (to be filled) | (to be filled) | |
| rental | 50 | (to be filled) | (to be filled) | (to be filled) | |
| customer | 50 | (to be filled) | (to be filled) | (to be filled) | |
| address | 50 | (to be filled) | (to be filled) | (to be filled) | |

Training configuration: discriminator-level DP-SGD via Opacus (ADR-0036).
`secure_mode=False` for CPU-only validation (see ADR-0017a / ADR-0017 v2).

---

## Synthetic Output

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

| Table | Rows Generated (pre-post-process) | Rows After Orphan Removal | Notes |
|-------|------------------------------------|--------------------------|-------|
| film | (to be filled) | (to be filled) | |
| inventory | (to be filled) | (to be filled) | |
| rental | (to be filled) | (to be filled) | |
| customer | (to be filled) | (to be filled) | |
| address | (to be filled) | (to be filled) | |

---

## Statistical Comparison

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

Per-column Kolmogorov-Smirnov (KS) statistics comparing source subset
distribution to synthetic output distribution. KS statistic close to 0.0
indicates good distributional fidelity.

| Table | Column | KS Statistic | p-value | Observation |
|-------|--------|-------------|---------|-------------|
| film | rental_rate | (to be filled) | (to be filled) | |
| film | length | (to be filled) | (to be filled) | |
| customer | active | (to be filled) | (to be filled) | |
| rental | rental_date | (to be filled) | (to be filled) | |

(Full per-column table to be populated from validation JSON report.)

---

## FK Integrity Verification

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

Post-processing must eliminate all FK orphans before this check runs.

| FK Relationship | Orphan Count (pre-post-process) | Orphan Count (post-post-process) | Pass? |
|----------------|--------------------------------|----------------------------------|-------|
| inventory.film_id → film.film_id | (to be filled) | 0 | (to be filled) |
| rental.inventory_id → inventory.inventory_id | (to be filled) | 0 | (to be filled) |
| customer.address_id → address.address_id | (to be filled) | 0 | (to be filled) |

**Required result**: all orphan counts MUST be 0 after post-processing
for the validation to pass.

---

## Epsilon Budget Accounting

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

| Metric | Value |
|--------|-------|
| Epsilon allocated | 10.0 (NOTE: chosen for validation speed, not production privacy) |
| Delta allocated | 1e-5 |
| Epsilon consumed (total across tables) | (to be filled) |
| Delta consumed | (to be filled) |
| Budget remaining | (to be filled) |
| Over-budget? | (to be filled — must be NO for pass) |

---

## Anomalies & Observations

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

(Document any unexpected results here: training instability, anomalous KS
statistics, unusual epsilon consumption rates, masking edge cases, etc.
If none observed, write "None observed".)

---

## Conclusion

<!-- PENDING: populate after running scripts/validate_full_pipeline.py -->

| Check | Result |
|-------|--------|
| Schema reflection completed | (to be filled) |
| Subsetting traversed all 5 tables | (to be filled) |
| All PII-like columns masked | (to be filled) |
| CTGAN trained with DP-SGD | (to be filled) |
| Synthetic rows generated | (to be filled) |
| FK integrity: zero orphans | (to be filled) |
| Epsilon budget not exceeded | (to be filled) |
| Masking verification passed | (to be filled) |
| **Overall** | **PENDING** |
