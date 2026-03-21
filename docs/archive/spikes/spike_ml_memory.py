# HISTORICAL — DO NOT USE: Pre-production spike. See docs/archive/spikes/findings_spike_*.md for conclusions.
"""Spike A: ML Memory Physics & OSS Synthesizer Constraints.

Proves that a tabular ML synthesizer can train on a 500 MB dataset and
generate 1000 synthetic records while staying within a 2 GB memory ceiling.
Chunked/batched processing is the fallback proof of viability.

Run with:
    python spikes/spike_ml_memory.py

No external dependencies required. If numpy is importable, it is used for
speed in the generation phase; otherwise stdlib is used throughout.
"""

from __future__ import annotations

import csv
import logging
import math
import random
import resource
import sys
import tempfile
import tracemalloc
from pathlib import Path

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RANDOM_SEED: int = 42
"""Fixed seed for full determinism across runs."""

TARGET_CSV_SIZE_BYTES: int = 500 * 1024 * 1024
"""Target uncompressed CSV size: 500 MB."""

MEMORY_CEILING_BYTES: int = 2 * 1024 * 1024 * 1024
"""Hard memory ceiling: 2 GB (enforced via resource.RLIMIT_AS on Unix)."""

NUM_NUMERIC_COLS: int = 20
"""Number of numeric columns (mix of float and int) in the schema."""

NUM_CATEGORICAL_COLS: int = 2
"""Number of categorical (string) columns in the schema."""

CATEGORICAL_VOCAB: list[list[str]] = [
    ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"],
    ["north", "south", "east", "west", "central"],
]
"""Fixed vocabularies for categorical columns. No PII."""

CHUNK_SIZE_ROWS: int = 10_000
"""Number of rows processed per chunk during fitting."""

NUM_SYNTHETIC_RECORDS: int = 1000
"""Number of synthetic records to generate."""

PREVIEW_ROWS: int = 5
"""Number of rows to print to stdout as a preview."""

_MB: int = 1024 * 1024
"""Bytes per mebibyte — used in reporting."""


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def _build_header() -> list[str]:
    """Build the CSV column header list.

    Returns:
        A list of column name strings: id, num_0..num_19, cat_0, cat_1.
    """
    numeric_names = [f"num_{i}" for i in range(NUM_NUMERIC_COLS)]
    categorical_names = [f"cat_{i}" for i in range(NUM_CATEGORICAL_COLS)]
    return ["id", *numeric_names, *categorical_names]


def _random_row(row_id: int, rng: random.Random) -> list[str]:
    """Generate one row of synthetic-but-fictional data.

    Args:
        row_id: Integer identifier for this row.
        rng: Seeded Random instance.

    Returns:
        A list of string-encoded values matching the schema.
    """
    values: list[str] = [str(row_id)]
    for i in range(NUM_NUMERIC_COLS):
        if i % 3 == 0:
            # Integer column
            values.append(str(rng.randint(0, 1_000_000)))
        else:
            # Float column with varying scale
            values.append(f"{rng.gauss(0.0, 100.0):.6f}")
    for vocab in CATEGORICAL_VOCAB:
        values.append(rng.choice(vocab))
    return values


# ---------------------------------------------------------------------------
# Phase 1: CSV generation
# ---------------------------------------------------------------------------


def generate_large_csv(path: Path, target_bytes: int, rng: random.Random) -> int:
    """Write a CSV file of approximately target_bytes to path.

    Rows are written until the file reaches target_bytes. The function
    uses a write-ahead estimation (average bytes per row measured from the
    first 100 rows) to avoid stat() calls in the hot loop.

    Args:
        path: Destination file path.
        target_bytes: Approximate target file size in bytes.
        rng: Seeded Random instance.

    Returns:
        Total number of rows written (excluding header).
    """
    header = _build_header()
    rows_written = 0
    bytes_per_row_estimate: float = 0.0
    calibration_rows = 100

    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        fh.flush()

        # Calibration pass: write first 100 rows and measure avg row size
        start_pos = fh.tell()
        for i in range(calibration_rows):
            writer.writerow(_random_row(i, rng))
        fh.flush()
        end_pos = fh.tell()
        bytes_per_row_estimate = (end_pos - start_pos) / calibration_rows
        rows_written = calibration_rows

        # Bulk write until target reached
        estimated_rows_needed = int((target_bytes - end_pos) / bytes_per_row_estimate)
        for i in range(calibration_rows, calibration_rows + estimated_rows_needed):
            writer.writerow(_random_row(i, rng))
            rows_written += 1

    # Fine-tune: if still short, append rows; if over, that is acceptable
    actual_size = path.stat().st_size
    if actual_size < target_bytes:
        shortfall_rows = int((target_bytes - actual_size) / bytes_per_row_estimate) + 1
        with path.open("a", newline="") as fh:
            writer = csv.writer(fh)
            for i in range(rows_written, rows_written + shortfall_rows):
                writer.writerow(_random_row(i, rng))
                rows_written += 1

    return rows_written


# ---------------------------------------------------------------------------
# Welford online statistics accumulator
# ---------------------------------------------------------------------------


class WelfordAccumulator:
    """Single-pass online mean and variance using Welford's algorithm.

    Accumulates statistics incrementally without storing all values in
    memory. Safe for chunked processing of arbitrarily large datasets.

    Reference: B.P. Welford (1962), Technometrics 4(3):419-420.
    """

    def __init__(self) -> None:
        """Initialize an empty accumulator."""
        self.count: int = 0
        self.mean: float = 0.0
        self._m2: float = 0.0  # Sum of squared deviations from mean

    def update(self, value: float) -> None:
        """Incorporate one new value into the running statistics.

        Args:
            value: New observation to include.
        """
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self._m2 += delta * delta2

    def merge(self, other: WelfordAccumulator) -> None:
        """Merge another accumulator into this one (parallel aggregation).

        Uses Chan's parallel algorithm for combining Welford accumulators.
        Enables chunk-level parallelism if needed in future.

        Args:
            other: Another accumulator whose statistics will be merged.
        """
        if other.count == 0:
            return
        combined_count = self.count + other.count
        delta = other.mean - self.mean
        self.mean = (self.count * self.mean + other.count * other.mean) / combined_count
        self._m2 += other._m2 + delta * delta * self.count * other.count / combined_count
        self.count = combined_count

    @property
    def variance(self) -> float:
        """Sample variance (Bessel-corrected).

        Returns:
            Sample variance, or 1.0 as a fallback if count < 2.
        """
        if self.count < 2:
            return 1.0
        return self._m2 / (self.count - 1)

    @property
    def std(self) -> float:
        """Sample standard deviation.

        Returns:
            Square root of sample variance, minimum 1e-9 to avoid degenerate Gaussians.
        """
        return max(math.sqrt(self.variance), 1e-9)


# ---------------------------------------------------------------------------
# Phase 2: ChunkedGaussianSynthesizer
# ---------------------------------------------------------------------------


class ChunkedGaussianSynthesizer:
    """Fits per-column Gaussian distributions from a CSV in fixed-size chunks.

    Reads the CSV in chunks of chunk_size rows to keep memory usage
    constant regardless of dataset size. After fitting, generates new
    records by sampling from the learned per-column Gaussians.

    Categorical columns are handled by learning their empirical frequency
    distribution and sampling from it proportionally.

    This implementation deliberately avoids numpy to prove stdlib-only
    viability. If numpy is importable, generation uses np.random.default_rng
    for speed but the fitting phase remains pure stdlib.
    """

    def __init__(self, chunk_size: int = CHUNK_SIZE_ROWS) -> None:
        """Initialise an unfitted synthesizer.

        Args:
            chunk_size: Number of CSV rows to load into memory at once
                during fitting. Defaults to CHUNK_SIZE_ROWS.
        """
        self.chunk_size = chunk_size
        self._numeric_accumulators: dict[str, WelfordAccumulator] = {}
        self._categorical_counts: dict[str, dict[str, int]] = {}
        self._column_types: dict[str, str] = {}  # "id" | "numeric" | "categorical"
        self._column_order: list[str] = []  # Ordered non-id columns from CSV header
        self._is_fitted: bool = False
        self._rows_seen: int = 0

    def fit(self, csv_path: Path) -> None:
        """Fit the synthesizer from a CSV file using chunked processing.

        Streams through the CSV in chunks of self.chunk_size rows. For
        each numeric column, updates a WelfordAccumulator. For each
        categorical column, updates a frequency count dict.

        The 'id' column is identified by name and skipped during generation.
        The original header order is preserved for schema-aligned output.

        Args:
            csv_path: Path to the input CSV file.
        """
        with csv_path.open("r", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ValueError("CSV has no header row.")

            # Detect column types from the header, preserving original order
            for col in reader.fieldnames:
                if col == "id":
                    self._column_types[col] = "id"
                elif col.startswith("num_"):
                    self._column_types[col] = "numeric"
                    self._column_order.append(col)
                    self._numeric_accumulators[col] = WelfordAccumulator()
                elif col.startswith("cat_"):
                    self._column_types[col] = "categorical"
                    self._column_order.append(col)
                    self._categorical_counts[col] = {}

            chunk: list[dict[str, str]] = []
            for row in reader:
                chunk.append(row)
                if len(chunk) >= self.chunk_size:
                    self._process_chunk(chunk)
                    chunk = []

            # Flush remaining rows
            if chunk:
                self._process_chunk(chunk)

        self._is_fitted = True

    def _process_chunk(self, chunk: list[dict[str, str]]) -> None:
        """Process one chunk of rows, updating internal statistics.

        Args:
            chunk: List of row dicts from csv.DictReader.
        """
        for row in chunk:
            self._rows_seen += 1
            for col, col_type in self._column_types.items():
                if col_type == "numeric":
                    raw = row.get(col, "0")
                    try:
                        self._numeric_accumulators[col].update(float(raw))
                    except ValueError as exc:
                        logger.warning(
                            "Skipping non-numeric value in column %r (row %d): %s",
                            col,
                            self._rows_seen,
                            exc,
                        )
                elif col_type == "categorical":
                    val = row.get(col, "")
                    counts = self._categorical_counts[col]
                    counts[val] = counts.get(val, 0) + 1

    def _sample_categorical(self, col: str, rng: random.Random) -> str:
        """Sample one value from a column's empirical distribution.

        Args:
            col: Column name.
            rng: Random instance.

        Returns:
            A sampled category value.
        """
        counts = self._categorical_counts[col]
        total = sum(counts.values())
        if total == 0:
            return ""
        r = rng.random() * total
        cumulative = 0.0
        for val, count in counts.items():
            cumulative += count
            if r <= cumulative:
                return val
        return next(iter(counts))  # Fallback for floating-point edge

    def generate(
        self, n: int, rng: random.Random | None = None, numpy_seed: int = RANDOM_SEED + 1
    ) -> list[list[str]]:
        """Generate n synthetic records from the fitted model.

        Each numeric column is sampled from N(mu, sigma^2) using the
        fitted Welford statistics. Categorical columns are sampled from
        their empirical frequency distributions.

        Column order in output matches the original CSV schema order.
        If numpy is importable, uses np.random.default_rng(numpy_seed) for
        vectorised numeric generation. Otherwise falls back to random.gauss.

        Args:
            n: Number of synthetic records to generate.
            rng: Optional Random instance for reproducibility. If None,
                a new Random seeded with RANDOM_SEED + 1 is created.
            numpy_seed: Integer seed for the numpy default_rng instance
                used in the numpy generation path. Defaults to RANDOM_SEED + 1.

        Returns:
            List of n rows, each a list of string-encoded values with id
            followed by columns in the original schema order.

        Raises:
            RuntimeError: If called before fit().
        """
        if not self._is_fitted:
            raise RuntimeError("Call fit() before generate().")
        if rng is None:
            # nosec B311 — PRNG is intentional; synthetic data generation is not
            # a security/cryptographic use case.
            rng = random.Random(RANDOM_SEED + 1)  # nosec B311  # noqa: S311

        # Attempt numpy import for fast vectorised generation
        numpy_available = False
        try:
            import numpy as np  # type: ignore[import-not-found]

            numpy_available = True
        except ImportError:
            pass

        rows: list[list[str]] = []

        if numpy_available:
            import numpy as np  # type: ignore[import-not-found]

            # Use a seeded Generator (not the global numpy RNG) for reproducibility.
            # ADV-008: np.random.default_rng(seed) replaces the legacy np.random.normal
            # which used the unseeded global RNG state.
            np_rng = np.random.default_rng(numpy_seed)

            # Pre-generate all numeric columns at once (n x NUM_NUMERIC_COLS)
            numeric_samples: dict[str, list[float]] = {}
            for col in self._column_order:
                if self._column_types[col] == "numeric":
                    acc = self._numeric_accumulators[col]
                    numeric_samples[col] = np_rng.normal(acc.mean, acc.std, n).tolist()

            for i in range(n):
                row: list[str] = [str(i)]
                for col in self._column_order:
                    if self._column_types[col] == "numeric":
                        row.append(f"{numeric_samples[col][i]:.6f}")
                    else:
                        row.append(self._sample_categorical(col, rng))
                rows.append(row)
        else:
            # Pure stdlib path
            for i in range(n):
                row = [str(i)]
                for col in self._column_order:
                    if self._column_types[col] == "numeric":
                        acc = self._numeric_accumulators[col]
                        val = rng.gauss(acc.mean, acc.std)
                        row.append(f"{val:.6f}")
                    else:
                        row.append(self._sample_categorical(col, rng))
                rows.append(row)

        return rows


# ---------------------------------------------------------------------------
# Memory enforcement
# ---------------------------------------------------------------------------


def enforce_memory_ceiling(ceiling_bytes: int) -> bool:
    """Attempt to cap this process's virtual address space.

    Uses resource.setrlimit(RLIMIT_AS) on Unix. On platforms where
    RLIMIT_AS is unavailable (e.g. macOS with SIP), logs a warning and
    returns False; the script continues without enforcement.

    Args:
        ceiling_bytes: Maximum allowed virtual address space in bytes.

    Returns:
        True if the limit was set successfully, False otherwise.
    """
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        # Only lower the ceiling — never raise it above the hard limit
        new_soft = min(ceiling_bytes, hard) if hard > 0 else ceiling_bytes
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, hard))
        return True
    except (OSError, AttributeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------


def _snapshot_peak_mb() -> float:
    """Return the tracemalloc peak allocation in mebibytes.

    Returns:
        Peak memory in MiB since tracemalloc was started.
    """
    _, peak = tracemalloc.get_traced_memory()
    return peak / _MB


def _reset_peak() -> None:
    """Reset the tracemalloc peak watermark."""
    tracemalloc.reset_peak()


# ---------------------------------------------------------------------------
# Findings table
# ---------------------------------------------------------------------------


def _print_findings_table(measurements: dict[str, float], ceiling_mb: float) -> None:
    """Print a formatted findings table to stdout.

    Args:
        measurements: Dict mapping phase name to peak MB for that phase.
        ceiling_mb: Memory ceiling in MiB for the PASS/FAIL column.
    """
    print()
    print("=" * 65)
    print("  SPIKE A — Memory Profile Findings")
    print("=" * 65)
    print(f"  {'Phase':<35} {'Peak MiB':>10}  {'Status':>8}")
    print("-" * 65)
    for phase, peak_mb in measurements.items():
        status = "PASS" if peak_mb < ceiling_mb else "FAIL"
        print(f"  {phase:<35} {peak_mb:>10.1f}  {status:>8}")
    print("-" * 65)
    total_peak = max(measurements.values())
    overall = "PASS" if total_peak < ceiling_mb else "FAIL"
    print(f"  {'Overall peak':<35} {total_peak:>10.1f}  {overall:>8}")
    print(f"  {'Ceiling':<35} {ceiling_mb:>10.1f}")
    print("=" * 65)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the Spike A memory physics experiment end-to-end.

    Phases:
        1. Enforce 2 GB memory ceiling via RLIMIT_AS (Unix only).
        2. Generate a ~500 MB CSV with fictional tabular data.
        3. Fit a ChunkedGaussianSynthesizer from the CSV in 10k-row chunks.
        4. Generate 1000 synthetic records.
        5. Print the first 5 rows and a findings table.
        6. Validate peak allocation stayed under ceiling.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    random.seed(RANDOM_SEED)
    # nosec B311 — PRNG is intentional; synthetic data generation is not
    # a security/cryptographic use case.
    rng = random.Random(RANDOM_SEED)  # nosec B311  # noqa: S311

    tracemalloc.start()
    ceiling_mb = MEMORY_CEILING_BYTES / _MB
    measurements: dict[str, float] = {}

    # --- Memory ceiling ---
    ceiling_set = enforce_memory_ceiling(MEMORY_CEILING_BYTES)
    print(
        f"[spike-a] 2 GB RLIMIT_AS enforcement: "
        f"{'active' if ceiling_set else 'unavailable (platform)'}"
    )

    # --- Phase 1: CSV generation ---
    print(f"[spike-a] Generating ~500 MB fictional CSV (seed={RANDOM_SEED})...")
    _reset_peak()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, dir=tempfile.gettempdir()
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        rows_written = generate_large_csv(tmp_path, TARGET_CSV_SIZE_BYTES, rng)
        actual_size_mb = tmp_path.stat().st_size / _MB
        measurements["CSV generation"] = _snapshot_peak_mb()
        print(
            f"[spike-a] CSV written: {rows_written:,} rows, {actual_size_mb:.1f} MiB "
            f"(peak alloc: {measurements['CSV generation']:.1f} MiB)"
        )

        # --- Phase 2: Model fitting (chunked) ---
        print(f"[spike-a] Fitting ChunkedGaussianSynthesizer (chunk_size={CHUNK_SIZE_ROWS:,})...")
        _reset_peak()
        synthesizer = ChunkedGaussianSynthesizer(chunk_size=CHUNK_SIZE_ROWS)
        synthesizer.fit(tmp_path)
        measurements["Chunked model fit"] = _snapshot_peak_mb()
        print(
            f"[spike-a] Fit complete: {synthesizer._rows_seen:,} rows seen "
            f"(peak alloc: {measurements['Chunked model fit']:.1f} MiB)"
        )

        # --- Phase 3: Synthetic generation ---
        print(f"[spike-a] Generating {NUM_SYNTHETIC_RECORDS:,} synthetic records...")
        _reset_peak()
        synthetic_rows = synthesizer.generate(NUM_SYNTHETIC_RECORDS)
        measurements["Synthetic generation (1000 rows)"] = _snapshot_peak_mb()
        print(
            f"[spike-a] Generation complete "
            f"(peak alloc: {measurements['Synthetic generation (1000 rows)']:.1f} MiB)"
        )

    finally:
        tmp_path.unlink(missing_ok=True)

    tracemalloc.stop()

    # --- Output: preview ---
    header = _build_header()
    print()
    print(f"[spike-a] First {PREVIEW_ROWS} synthetic rows (CSV preview):")
    writer = csv.writer(sys.stdout)
    writer.writerow(header)
    for row in synthetic_rows[:PREVIEW_ROWS]:
        writer.writerow(row)

    # --- Findings table ---
    _print_findings_table(measurements, ceiling_mb)

    # --- Validation ---
    peak_mb = max(measurements.values())
    if peak_mb >= ceiling_mb:
        raise RuntimeError(
            f"Peak allocation {peak_mb:.1f} MiB exceeded ceiling {ceiling_mb:.0f} MiB"
        )
    print(f"\n[spike-a] VALIDATED: peak {peak_mb:.1f} MiB < ceiling {ceiling_mb:.0f} MiB")


if __name__ == "__main__":
    main()
