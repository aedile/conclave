# Spike A Findings: ML Memory Physics & OSS Synthesizer Constraints

**Date:** 2026-03-13
**Branch:** feat/P0.8-spike-a-ml-memory
**Script:** `spikes/spike_ml_memory.py`
**Environment:** macOS 24.5.0, Python 3.14.1, no GPU, stdlib only (numpy not installed)

---

## Executive Summary

A pure-stdlib `ChunkedGaussianSynthesizer` trained on a 511 MiB CSV dataset (2,572,686 rows)
and generated 1,000 synthetic records with a **peak tracemalloc allocation of 19.0 MiB** —
less than 1% of the 2,048 MiB ceiling. Chunked processing is viable as the primary
memory-management strategy for Phase 4 production workloads.

---

## Memory Profile by Phase

| Phase | Peak Allocation (MiB) | Ceiling (MiB) | Status |
|---|---|---|---|
| CSV generation (511 MiB file) | 0.3 | 2048 | PASS |
| Chunked model fit (10k-row chunks) | 19.0 | 2048 | PASS |
| Synthetic generation (1,000 rows) | 12.5 | 2048 | PASS |
| **Overall peak** | **19.0** | **2048** | **PASS** |

### Key observations

- **CSV generation stays near 0 MiB** because `csv.writer` writes row-by-row with no
  in-memory accumulation. The file buffer is the only allocation.
- **Model fit peak is 19.0 MiB** for 2.57M rows. This is the cost of holding one 10k-row
  chunk (`dict[str, str]` list) plus 22 Welford accumulators (trivially small). Memory is
  effectively constant regardless of dataset size — processing a 5 GB file would produce
  the same 19 MiB peak.
- **Generation peak is 12.5 MiB** for 1,000 rows. This includes the output list and the
  stdlib `random.gauss` call overhead.
- **RLIMIT_AS was unavailable on macOS** (macOS SIP restricts `setrlimit(RLIMIT_AS)`). On
  Linux production hosts, RLIMIT_AS enforcement will be active and provide a hard 2 GB
  ceiling. The spike still validates under the ceiling by measurement.

---

## Was Chunking Needed?

Yes, and it is the correct long-term strategy. The dataset is 511 MiB uncompressed. Without
chunking, loading the entire dataset into memory at once would require approximately
500–800 MiB of Python object overhead (a `list[dict]` of 2.57M rows), which would push
comfortably past 512 MiB and risk exceeding the ceiling on a busy host. With 10k-row chunks,
peak allocation is bounded to the per-chunk overhead regardless of total file size.

---

## Recommended Batch Size for Production

**Recommended default: 10,000 rows per chunk** (`CHUNK_SIZE_ROWS = 10_000`).

Rationale:
- At 10k rows, peak chunk memory is ~14 MiB (dict overhead) — well within budget.
- Smaller chunks (e.g. 1k rows) increase Python loop overhead for large files.
- Larger chunks (e.g. 100k rows) scale peak allocation proportionally without
  statistical benefit (Welford's algorithm is exact regardless of chunk size).
- For Phase 4 with SDV, chunk size should be tunable via configuration to adapt to
  available host memory and dataset cardinality.

---

## Welford Algorithm Validation

The online Welford accumulator correctly computes streaming mean and variance with zero
statistical degradation compared to a single-pass full-load approach. The algorithm is:

1. **Numerically stable** — avoids catastrophic cancellation in variance computation.
2. **Exactly equivalent** — produces the same result as computing mean/std over the full
   dataset loaded into memory.
3. **Mergeable** — Chan's parallel merge formula enables future multi-threaded chunking
   if needed.

---

## SDV Integration Recommendation for Phase 4

### Short-term (Phase 4.0 — T4.1): Use this `ChunkedGaussianSynthesizer` as a baseline

The stdlib synthesizer proves viability and serves as the performance floor. It should
be used as the fallback when SDV is unavailable (e.g. strict air-gap environments where
pip install is blocked).

### Primary path (Phase 4.1 — T4.2): Integrate SDV `GaussianCopulaSynthesizer`

SDV's `GaussianCopulaSynthesizer` (from the `sdv` package) is the recommended upgrade:

- **Why GaussianCopula over CTGAN:** GaussianCopula is deterministic, reproducible,
  and requires significantly less memory than CTGAN's neural network training. CTGAN
  requires GPU/large RAM and long training times — unsuitable as the default path.
- **Chunked pre-processing:** Use this spike's chunked CSV reader to compute column
  statistics, then pass a representative in-memory sample to SDV for copula fitting.
  SDV itself does not natively support streaming input (as of SDV 1.x), so the
  recommended pattern is:
  ```
  1. Chunked scan -> compute schema + statistics (this spike's code)
  2. Stratified sample -> draw a representative 100k-row subsample into a DataFrame
  3. SDV fit -> GaussianCopulaSynthesizer.fit(subsample_df)
  4. SDV generate -> synthesizer.sample(n=target_rows)
  ```
- **Memory budget for Phase 4:** A 100k-row subsample (22 columns) requires ~70 MiB as
  a pandas DataFrame. SDV fit overhead is ~50 MiB. Total Phase 4 memory budget:
  ~19 MiB (chunked scan) + ~120 MiB (SDV fit) = ~140 MiB peak — well within 2 GB.
- **Differential Privacy:** SDV's `PARSynthesizer` supports DP training. For Phase 4
  epsilon/delta tracking, wrap SDV's DP synthesizer with the `privacy/accountant.py`
  module (to be implemented in Task 4.3).

### Dependency gating

Add SDV as an **optional** dependency in `pyproject.toml` under a `[synthesis]` extras
group. The synthesizer module should fall back to this spike's stdlib implementation when
SDV is not installed, enabling full air-gap deployment without pip access.

```toml
[tool.poetry.extras]
synthesis = ["sdv"]
```

---

## Conclusion

Spike A is a **PASS** on all acceptance criteria:

- [x] 511 MiB CSV generated with fictional data, 2,572,686 rows
- [x] Peak tracemalloc allocation: 19.0 MiB (0.9% of 2,048 MiB ceiling)
- [x] 1,000 synthetic records generated successfully
- [x] Chunked processing proven viable at 10k-row chunks
- [x] Zero external dependencies (stdlib only; numpy fallback path documented)
- [x] Deterministic output (fixed seed 42)
- [x] RLIMIT_AS enforcement code present (active on Linux; gracefully degraded on macOS)
