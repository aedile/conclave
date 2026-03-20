"""End-to-end load test script for the Conclave Engine -- 1M+ row dataset.

Runs the full Conclave Engine pipeline against a ~1,012,500-row dataset,
collects timing and throughput metrics, and writes results to
docs/e2e_load_test_results.json plus a human-readable summary to stdout.

Dataset:
- 50,000 customers
- 175,000 orders
- ~612,500 order_items (randint 1-6 items/order)
- 175,000 payments
- Total: ~1,012,500 rows

Usage:
    # Require live Docker services (full run)
    poetry run python3 scripts/e2e_load_test.py

    # Print plan without executing
    poetry run python3 scripts/e2e_load_test.py --dry-run

    # Override defaults
    poetry run python3 scripts/e2e_load_test.py \\
        --source-dsn postgresql://user:pass@localhost/source \\  # pragma: allowlist secret
        --target-dsn postgresql://user:pass@localhost/target \\  # pragma: allowlist secret
        --api-base-url http://localhost:8000 \\
        --results-path docs/e2e_load_test_results.json

Task: E2E 1M-row load test
CONSTITUTION Priority 0: All generated data is fictional (Faker). No real PII committed.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import logging
import platform
import subprocess  # nosec B404 -- used only for conclave-subset CLI invocation via argv list
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import click
import httpx
import jwt as pyjwt
import psutil

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants -- dev defaults from docker-compose
# ---------------------------------------------------------------------------

DEFAULT_SOURCE_DSN: str = (  # nosec B105 -- dev DSN, not a credential
    "postgresql://conclave:conclave@localhost:5432/conclave_source"  # pragma: allowlist secret
)
DEFAULT_TARGET_DSN: str = (  # nosec B105 -- dev DSN, not a credential
    "postgresql://conclave:conclave@localhost:5432/conclave_target"  # pragma: allowlist secret
)
DEFAULT_API_BASE_URL: str = "http://localhost:8000"
DEFAULT_RESULTS_PATH: str = "docs/e2e_load_test_results.json"
DEFAULT_VAULT_PASSPHRASE: str = "test-passphrase"  # noqa: S105  # nosec B105 -- dev secret
DEFAULT_LICENSE_KEY_PATH: str = "secrets/license_dev_private_key.pem"
POLL_INTERVAL_S: int = 30
POLL_TIMEOUT_S: int = 4 * 3600  # 4 hours

# ---------------------------------------------------------------------------
# Dataset sizing -- ~1,012,500 total rows
# ---------------------------------------------------------------------------

N_CUSTOMERS: int = 50_000
N_ORDERS: int = 175_000
ITEMS_PER_ORDER: int = 3  # average ~3.5 due to randint(1,6) in seed script
N_PAYMENTS: int = 175_000  # one per order

# Synthesis job parameters per table
JOB_PARAMS: dict[str, dict[str, Any]] = {
    "customers": {
        "total_epochs": 3,
        "num_rows": N_CUSTOMERS,
        "checkpoint_every_n": 3,
        "enable_dp": True,
        "noise_multiplier": 1.1,
        "max_grad_norm": 1.0,
    },
    "orders": {
        "total_epochs": 3,
        "num_rows": N_ORDERS,
        "checkpoint_every_n": 3,
        "enable_dp": True,
        "noise_multiplier": 5.0,
        "max_grad_norm": 1.0,
    },
    "order_items": {
        "total_epochs": 3,
        "num_rows": 200_000,
        "checkpoint_every_n": 3,
        "enable_dp": True,
        "noise_multiplier": 10.0,
        "max_grad_norm": 1.0,
    },
    "payments": {
        "total_epochs": 3,
        "num_rows": N_PAYMENTS,
        "checkpoint_every_n": 3,
        "enable_dp": False,
    },
}

TABLES_IN_ORDER: tuple[str, ...] = ("customers", "orders", "order_items", "payments")


# ---------------------------------------------------------------------------
# Pure helper functions -- unit-testable without I/O
# ---------------------------------------------------------------------------


def calculate_rows_per_sec(num_rows: int, duration_s: float) -> float:
    """Calculate rows-per-second throughput.

    Args:
        num_rows: Total rows processed.
        duration_s: Wall-clock duration in seconds.

    Returns:
        Rows per second, or 0.0 if duration_s is zero or negative.
    """
    if duration_s <= 0:
        return 0.0
    return round(num_rows / duration_s, 4)


def mb_from_bytes(n_bytes: int) -> float:
    """Convert a byte count to mebibytes (MiB), rounded to 2 decimal places.

    Args:
        n_bytes: Size in bytes.

    Returns:
        Size in MiB (1 MiB = 1,048,576 bytes).
    """
    if n_bytes == 0:
        return 0.0
    return round(n_bytes / (1024 * 1024), 2)


def collect_system_info() -> dict[str, Any]:
    """Collect hardware and OS information via psutil and platform stdlib.

    Returns:
        Dict with keys: platform (str), ram_gb (float), cpu_count (int).
    """
    vm = psutil.virtual_memory()
    ram_gb = round(vm.total / (1024**3), 2)
    cpu_count: int = psutil.cpu_count(logical=True) or 1
    return {
        "platform": platform.platform(),
        "ram_gb": ram_gb,
        "cpu_count": cpu_count,
    }


def build_results_dict(
    run_date: str,
    total_source_rows: int,
    dataset: dict[str, int],
    job_results: list[dict[str, Any]],
    cli_subsetting: dict[str, Any],
    shred_results: list[dict[str, Any]],
    system_info: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the final results dictionary for JSON serialisation.

    Args:
        run_date: ISO-8601 timestamp string for this run.
        total_source_rows: Approximate total rows loaded into the source DB.
        dataset: Mapping of table name to row count.
        job_results: List of per-job metric dicts.
        cli_subsetting: Metrics dict from the conclave-subset CLI run.
        shred_results: List of shred outcome dicts per job.
        system_info: Hardware/OS info from collect_system_info().

    Returns:
        A JSON-serialisable dict matching the schema specified in the task backlog.
    """
    return {
        "run_date": run_date,
        "total_source_rows": total_source_rows,
        "dataset": dataset,
        "jobs": job_results,
        "cli_subsetting": cli_subsetting,
        "shred_results": shred_results,
        "system": system_info,
    }


def build_dry_run_plan(
    source_dsn: str,
    api_base_url: str,
    n_customers: int,
    n_orders: int,
) -> str:
    """Build a human-readable execution plan string without making any I/O calls.

    Args:
        source_dsn: PostgreSQL DSN for the source database.
        api_base_url: Base URL of the Conclave Engine API.
        n_customers: Number of customer rows to be generated.
        n_orders: Number of order rows to be generated.

    Returns:
        Multi-line string describing the planned execution steps.
    """
    n_payments = n_orders
    # items_per_order ~ 3.5 average based on randint(1, 2*items_per_order)
    estimated_items = int(n_orders * 3.5)
    total_rows = n_customers + n_orders + estimated_items + n_payments

    lines = [
        "=== DRY RUN -- Conclave Engine 1M-row E2E Load Test ===",
        "",
        f"  API base URL   : {api_base_url}",
        f"  Source DSN     : {source_dsn.split('@')[-1]}",  # redact credentials
        "",
        "  Dataset to generate and load:",
        f"    customers   : {n_customers:,}",
        f"    orders      : {n_orders:,}",
        f"    order_items : ~{estimated_items:,}",
        f"    payments    : {n_payments:,}",
        f"    TOTAL       : ~{total_rows:,}",
        "",
        "  Steps:",
        "    1. Pre-flight health check",
        "    2. Generate & load data into PostgreSQL",
        "    3. Unseal vault",
        "    4. Activate license",
        "    5. Export tables to Parquet",
        "    6. Create synthesis jobs (4 tables)",
        "    7. Start all jobs",
        "    8. Poll for completion (30s interval, 4h timeout)",
        "    9. Collect metrics",
        "   10. Download artifacts",
        "   11. Run conclave-subset CLI subsetting test",
        "   12. Shred all artifacts",
        "   13. Write results JSON",
        "   14. Print summary",
        "",
        "  DRY RUN complete -- no HTTP calls made, no data written.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Seed module loader -- imports seed_sample_data from scripts/ at runtime
# ---------------------------------------------------------------------------


def _load_seed_module() -> Any:
    """Dynamically import seed_sample_data from the scripts/ directory.

    Returns:
        The imported seed_sample_data module.

    Raises:
        SystemExit: If the module cannot be located.
    """
    scripts_dir = Path(__file__).resolve().parent
    seed_path = scripts_dir / "seed_sample_data.py"
    if not seed_path.exists():
        logger.error("Cannot find scripts/seed_sample_data.py at %s", seed_path)
        sys.exit(1)

    spec = importlib.util.spec_from_file_location("seed_sample_data", seed_path)
    if spec is None or spec.loader is None:
        logger.error("Could not create import spec for %s", seed_path)
        sys.exit(1)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def step_preflight(api_base_url: str) -> None:
    """Verify Docker services are running via the /health endpoint.

    Args:
        api_base_url: Base URL of the Conclave Engine API.

    Raises:
        SystemExit: If the API is not reachable.
    """
    health_url = f"{api_base_url}/health"
    click.echo(f"[1/14] Pre-flight: checking {health_url} ...")
    try:
        resp = httpx.get(health_url, timeout=10.0)
        resp.raise_for_status()
        click.echo(f"       OK -- status {resp.status_code}")
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        click.echo(f"ERROR: Cannot reach {health_url}: {exc}", err=True)
        click.echo(
            "\nPlease start the Conclave Engine services:\n  docker compose up -d\nThen retry.",
            err=True,
        )
        sys.exit(1)


def step_generate_and_load(source_dsn: str) -> dict[str, int]:
    """Generate ~1M rows of fictional data and load into PostgreSQL.

    Args:
        source_dsn: PostgreSQL DSN for the source database.

    Returns:
        Dict mapping table name to number of rows loaded.
    """
    click.echo("[2/14] Generating & loading data ...")
    seed_mod = _load_seed_module()

    customers = seed_mod.generate_customers(n=N_CUSTOMERS, seed=42)
    click.echo(f"       generated {len(customers):,} customers")

    orders = seed_mod.generate_orders(customers=customers, n=N_ORDERS, seed=42)
    click.echo(f"       generated {len(orders):,} orders")

    items = seed_mod.generate_order_items(orders=orders, seed=42, items_per_order=ITEMS_PER_ORDER)
    click.echo(f"       generated {len(items):,} order_items")

    payments = seed_mod.generate_payments(orders=orders, n=N_PAYMENTS, seed=42)
    click.echo(f"       generated {len(payments):,} payments")

    ddl = seed_mod.build_ddl()
    tables = {
        "customers": customers,
        "orders": orders,
        "order_items": items,
        "payments": payments,
    }
    seed_mod._execute_against_db(dsn=source_dsn, ddl=ddl, tables=tables)
    click.echo("       data loaded into PostgreSQL")

    return {
        "customers": len(customers),
        "orders": len(orders),
        "order_items": len(items),
        "payments": len(payments),
    }


def step_unseal_vault(api_base_url: str, passphrase: str) -> None:
    """POST to /unseal. Treats 400 (already unsealed) as success.

    Args:
        api_base_url: Base URL of the Conclave Engine API.
        passphrase: Vault unseal passphrase.

    Raises:
        SystemExit: On HTTP or connection errors.
    """
    url = f"{api_base_url}/unseal"
    click.echo(f"[3/14] Unsealing vault at {url} ...")
    try:
        resp = httpx.post(url, json={"passphrase": passphrase}, timeout=30.0)
        if resp.status_code in (400, 409):
            # 400 = already unsealed (RFC 7807 response), 409 = legacy
            click.echo("       Vault already unsealed -- continuing")
        else:
            resp.raise_for_status()
            click.echo(f"       Vault unsealed -- status {resp.status_code}")
    except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
        click.echo(f"ERROR: Vault unseal failed: {exc}", err=True)
        sys.exit(1)


def step_activate_license(
    api_base_url: str,
    license_key_path: Path,
) -> None:
    """Activate the Conclave Engine license using a dev RS256 JWT.

    Reads the RSA private key from ``license_key_path``, fetches the
    container's hardware ID from ``GET /license/challenge``, signs a
    short-lived RS256 JWT, and posts it to ``POST /license/activate``.

    A 409 response (already activated) is treated as success.  Any other
    HTTP error causes the script to exit with a non-zero status.

    Args:
        api_base_url: Base URL of the Conclave Engine API.
        license_key_path: Path to the PEM-encoded RSA private key file.

    Raises:
        SystemExit: On HTTP or connection errors, or missing key file.
    """
    click.echo(f"[4/14] Activating license (key: {license_key_path}) ...")

    # -- Read private key -------------------------------------------------------
    if not license_key_path.exists():
        click.echo(
            f"ERROR: License private key not found at {license_key_path}. "
            "Run: openssl genrsa -out secrets/license_dev_private_key.pem 2048",
            err=True,
        )
        sys.exit(1)
    private_key_pem = license_key_path.read_text(encoding="utf-8")

    # -- Fetch hardware_id from challenge endpoint ------------------------------
    challenge_url = f"{api_base_url}/license/challenge"
    try:
        challenge_resp = httpx.get(challenge_url, timeout=10.0)
        challenge_resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
        click.echo(f"ERROR: License challenge failed: {exc}", err=True)
        sys.exit(1)

    hardware_id: str = challenge_resp.json()["hardware_id"]
    click.echo(f"       hardware_id: {hardware_id}")

    # -- Sign RS256 JWT ---------------------------------------------------------
    now = datetime.datetime.now(tz=datetime.UTC)
    claims: dict[str, Any] = {
        "hardware_id": hardware_id,
        "sub": "e2e-load-test",
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(hours=24)).timestamp()),
    }
    token = pyjwt.encode(claims, private_key_pem, algorithm="RS256")

    # -- POST to /license/activate ---------------------------------------------
    activate_url = f"{api_base_url}/license/activate"
    try:
        activate_resp = httpx.post(activate_url, json={"token": token}, timeout=30.0)
        if activate_resp.status_code == 409:
            click.echo("       License already activated on this hardware -- continuing")
            return
        activate_resp.raise_for_status()
        click.echo(f"       License activated -- status {activate_resp.status_code}")
    except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
        click.echo(f"ERROR: License activation failed: {exc}", err=True)
        sys.exit(1)


def step_export_parquet(
    source_dsn: str,
    tmp_dir: Path,
    table_row_counts: dict[str, int],
) -> dict[str, Path]:
    """Export each table from PostgreSQL to a Parquet file.

    Args:
        source_dsn: PostgreSQL DSN for the source database.
        tmp_dir: Directory where Parquet files are written.
        table_row_counts: Mapping of table name to expected row count (for logging).

    Returns:
        Mapping of table name to Path of the exported Parquet file.

    Raises:
        SystemExit: If a required library is missing or a query fails.
    """
    click.echo("[5/14] Exporting tables to Parquet ...")
    try:
        import pandas as pd
        from sqlalchemy import create_engine
    except ImportError as exc:
        click.echo(f"ERROR: Missing dependency: {exc}", err=True)
        sys.exit(1)

    engine = create_engine(source_dsn)
    parquet_paths: dict[str, Path] = {}
    container_dir = "/app/e2e_parquet"  # writable rootfs (read_only: false in override)

    # Create the directory inside the app container for Parquet files
    subprocess.run(  # nosec B603, B607 — trusted argv
        ["docker", "exec", "synthetic_data-app-1", "mkdir", "-p", container_dir],
        check=True,
        capture_output=True,
    )

    for table in TABLES_IN_ORDER:
        pq_path = tmp_dir / f"{table}.parquet"
        df: pd.DataFrame = pd.read_sql_table(table, con=engine)
        df.to_parquet(pq_path, index=False)
        row_count = table_row_counts.get(table, len(df))

        # Copy Parquet file into the app container so the API can read it
        container_path = f"{container_dir}/{table}.parquet"
        subprocess.run(  # nosec B603, B607 — trusted argv
            ["docker", "cp", str(pq_path), f"synthetic_data-app-1:{container_path}"],
            check=True,
            capture_output=True,
        )
        click.echo(f"       {table}: {row_count:,} rows -> container:{container_path}")
        # Store the container-internal path (what the API sees)
        parquet_paths[table] = Path(container_path)

    return parquet_paths


def step_create_jobs(
    api_base_url: str,
    parquet_paths: dict[str, Path],
) -> dict[str, int]:
    """Create synthesis jobs via POST /jobs for each table.

    Args:
        api_base_url: Base URL of the Conclave Engine API.
        parquet_paths: Mapping of table name to Parquet file path.

    Returns:
        Mapping of table name to job ID returned by the API.

    Raises:
        SystemExit: On HTTP or connection errors.
    """
    click.echo("[6/14] Creating synthesis jobs ...")
    jobs_url = f"{api_base_url}/jobs"
    table_to_job_id: dict[str, int] = {}

    for table in TABLES_IN_ORDER:
        params = dict(JOB_PARAMS[table])
        params["table_name"] = table
        params["parquet_path"] = str(parquet_paths[table])

        try:
            resp = httpx.post(jobs_url, json=params, timeout=30.0)
            resp.raise_for_status()
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
            click.echo(f"ERROR: Failed to create job for {table}: {exc}", err=True)
            sys.exit(1)

        job_id: int = resp.json()["id"]
        table_to_job_id[table] = job_id
        click.echo(f"       {table}: job_id={job_id}")

    return table_to_job_id


def step_start_jobs(api_base_url: str, table_to_job_id: dict[str, int]) -> None:
    """POST /jobs/{id}/start for each job.

    Args:
        api_base_url: Base URL of the Conclave Engine API.
        table_to_job_id: Mapping of table name to job ID.

    Raises:
        SystemExit: On HTTP or connection errors.
    """
    click.echo("[7/14] Starting all synthesis jobs ...")
    for table, job_id in table_to_job_id.items():
        url = f"{api_base_url}/jobs/{job_id}/start"
        try:
            resp = httpx.post(url, timeout=30.0)
            resp.raise_for_status()
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
            click.echo(f"ERROR: Failed to start job {job_id} ({table}): {exc}", err=True)
            sys.exit(1)
        click.echo(f"       {table} (job_id={job_id}): started")


def step_poll_jobs(
    api_base_url: str,
    table_to_job_id: dict[str, int],
) -> dict[str, dict[str, Any]]:
    """Poll GET /jobs/{id} every 30 s until all jobs complete or timeout.

    Args:
        api_base_url: Base URL of the Conclave Engine API.
        table_to_job_id: Mapping of table name to job ID.

    Returns:
        Mapping of table name to final job response dict (including timing keys
        ``_start_time`` and ``_end_time`` in epoch seconds).
    """
    click.echo("[8/14] Polling for job completion (30s interval, 4h timeout) ...")
    terminal_statuses = {"COMPLETE", "FAILED"}
    job_data: dict[str, dict[str, Any]] = {}
    job_start: dict[str, float] = {t: time.monotonic() for t in table_to_job_id}
    pending = set(table_to_job_id.keys())
    absolute_deadline = time.monotonic() + POLL_TIMEOUT_S

    while pending and time.monotonic() < absolute_deadline:
        time.sleep(POLL_INTERVAL_S)
        completed_this_round: list[str] = []
        for table in list(pending):
            job_id = table_to_job_id[table]
            url = f"{api_base_url}/jobs/{job_id}"
            try:
                resp = httpx.get(url, timeout=30.0)
                resp.raise_for_status()
            except Exception as exc:  # ADV-E2E-01: broad catch — non-fatal poll, keeps loop running
                click.echo(f"       WARNING: poll error for {table} job {job_id}: {exc}")
                continue

            body: dict[str, Any] = resp.json()
            status: str = body.get("status", "UNKNOWN")
            click.echo(f"       {table} (job_id={job_id}): {status}")

            if status in terminal_statuses:
                body["_start_time"] = job_start[table]
                body["_end_time"] = time.monotonic()
                job_data[table] = body
                completed_this_round.append(table)

        pending -= set(completed_this_round)

    # Mark any jobs that timed out
    for table in pending:
        job_id = table_to_job_id[table]
        click.echo(f"       TIMEOUT: {table} (job_id={job_id}) did not complete within 4 hours")
        job_data[table] = {
            "status": "TIMEOUT",
            "id": job_id,
            "_start_time": job_start[table],
            "_end_time": time.monotonic(),
        }

    return job_data


def step_collect_metrics(
    api_base_url: str,
    table_to_job_id: dict[str, int],
    job_responses: dict[str, dict[str, Any]],
    tmp_dir: Path,
) -> list[dict[str, Any]]:
    """Download artifacts and assemble per-job metric dicts.

    Args:
        api_base_url: Base URL of the Conclave Engine API.
        table_to_job_id: Mapping of table name to job ID.
        job_responses: Final poll responses per table.
        tmp_dir: Directory to save downloaded artifacts.

    Returns:
        List of job metric dicts in TABLES_IN_ORDER order.
    """
    click.echo("[9/14] Collecting metrics ...")
    click.echo("[10/14] Downloading artifacts ...")
    job_results: list[dict[str, Any]] = []

    for table in TABLES_IN_ORDER:
        job_id = table_to_job_id[table]
        body = job_responses.get(table, {})
        status: str = body.get("status", "UNKNOWN")

        # Duration
        start_time: float = body.get("_start_time", 0.0)
        end_time: float = body.get("_end_time", start_time)
        duration_s = round(end_time - start_time, 2)

        num_rows: int = JOB_PARAMS[table]["num_rows"]
        rows_per_sec = calculate_rows_per_sec(num_rows=num_rows, duration_s=duration_s)
        epsilon_spent: float | None = body.get("actual_epsilon")
        dp_enabled: bool = JOB_PARAMS[table]["enable_dp"]
        noise_multiplier: float = JOB_PARAMS[table].get("noise_multiplier", 0.0)

        # Download artifact
        artifact_size_mb = 0.0
        if status == "COMPLETE":
            artifact_path = tmp_dir / f"{table}_synthetic.parquet"
            download_url = f"{api_base_url}/jobs/{job_id}/download"
            try:
                resp = httpx.get(download_url, timeout=120.0)
                resp.raise_for_status()
                artifact_path.write_bytes(resp.content)
                artifact_size_mb = mb_from_bytes(len(resp.content))
                click.echo(f"       {table}: downloaded {artifact_size_mb} MiB")
            except Exception as exc:  # ADV-E2E-01: broad catch — non-fatal, avoids aborting metrics
                click.echo(f"       WARNING: download failed for {table}: {exc}")

        job_results.append(
            {
                "table": table,
                "status": status,
                "duration_s": duration_s,
                "rows_per_sec": rows_per_sec,
                "epsilon_spent": epsilon_spent,
                "artifact_size_mb": artifact_size_mb,
                "dp_enabled": dp_enabled,
                "noise_multiplier": noise_multiplier,
            }
        )

    return job_results


def step_cli_subsetting(source_dsn: str, target_dsn: str) -> dict[str, Any]:
    """Run conclave-subset CLI and record timing and success/failure.

    Args:
        source_dsn: PostgreSQL DSN for the source database.
        target_dsn: PostgreSQL DSN for the target database.

    Returns:
        Dict with keys: status, duration_s, seed_rows, total_rows_subsetted.
    """
    click.echo("[11/14] Running conclave-subset CLI ...")
    # ADVISORY: DSN is visible via ps on shared systems. Acceptable for dev-only script.
    # For production use, pass DSN via environment variable.
    cmd = [
        "conclave-subset",
        "--source",
        source_dsn,
        "--target",
        target_dsn,
        "--seed-table",
        "customers",
        "--seed-query",
        "SELECT * FROM customers LIMIT 100",
        "--mask",
    ]
    t0 = time.monotonic()
    status = "failed"
    duration_s = 0.0
    try:
        proc = subprocess.run(  # nosec B603 -- argv list, not shell=True
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        duration_s = round(time.monotonic() - t0, 2)
        status = "success" if proc.returncode == 0 else "failed"
        if proc.returncode != 0:
            click.echo(f"       WARNING: conclave-subset exited {proc.returncode}")
            click.echo(f"       stderr: {proc.stderr[:500]}")
        else:
            click.echo(f"       OK -- completed in {duration_s}s")
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        duration_s = round(time.monotonic() - t0, 2)
        status = "failed"
        click.echo(f"       WARNING: conclave-subset failed: {exc}")

    return {
        "status": status,
        "duration_s": duration_s,
        "seed_rows": 100,
        "total_rows_subsetted": None,  # not yet parsed from output
    }


def step_shred_jobs(
    api_base_url: str,
    table_to_job_id: dict[str, int],
) -> list[dict[str, Any]]:
    """POST /jobs/{id}/shred for each job and record outcomes.

    Args:
        api_base_url: Base URL of the Conclave Engine API.
        table_to_job_id: Mapping of table name to job ID.

    Returns:
        List of dicts with job_id and status keys.
    """
    click.echo("[12/14] Shredding artifacts ...")
    shred_results: list[dict[str, Any]] = []
    for table in TABLES_IN_ORDER:
        job_id = table_to_job_id[table]
        url = f"{api_base_url}/jobs/{job_id}/shred"
        try:
            resp = httpx.post(url, timeout=60.0)
            resp.raise_for_status()
            status = "success"
            click.echo(f"       {table} (job_id={job_id}): shredded")
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
            status = "failed"
            click.echo(f"       WARNING: shred failed for {table} job {job_id}: {exc}")
        shred_results.append({"job_id": job_id, "status": status})
    return shred_results


def step_write_results(results: dict[str, Any], results_path: Path) -> None:
    """Serialise results dict to JSON and write to disk.

    Args:
        results: Assembled results dict from build_results_dict().
        results_path: Destination path for the JSON file.
    """
    click.echo(f"[13/14] Writing results to {results_path} ...")
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    click.echo(f"       Results written to {results_path}")


def step_print_summary(results: dict[str, Any]) -> None:
    """Print a human-readable summary table to stdout.

    Args:
        results: Results dict from build_results_dict().
    """
    click.echo("\n[14/14] Summary")
    click.echo("=" * 72)
    click.echo(f"Run date       : {results['run_date']}")
    click.echo(f"Total rows     : {results['total_source_rows']:,}")
    click.echo("")
    click.echo(
        f"{'Table':<15} {'Status':<12} {'Duration(s)':>12} "
        f"{'Rows/s':>10} {'Epsilon':>9} {'Size(MiB)':>10} {'DP':>5}"
    )
    click.echo("-" * 72)
    for job in results["jobs"]:
        epsilon = job.get("epsilon_spent")
        eps_str = f"{epsilon:.3f}" if epsilon is not None else "   N/A"
        dp_flag = "yes" if job.get("dp_enabled") else "no"
        click.echo(
            f"{job['table']:<15} {job['status']:<12} {job['duration_s']:>12.1f} "
            f"{job['rows_per_sec']:>10.0f} {eps_str:>9} "
            f"{job['artifact_size_mb']:>10.2f} {dp_flag:>5}"
        )
    click.echo("-" * 72)

    cli = results.get("cli_subsetting", {})
    click.echo(
        f"\nconclave-subset : {cli.get('status', 'N/A')} "
        f"in {cli.get('duration_s', 0):.1f}s "
        f"(seed_rows={cli.get('seed_rows', 'N/A')})"
    )

    sys_info = results.get("system", {})
    click.echo(
        f"\nSystem : {sys_info.get('platform', 'N/A')}, "
        f"{sys_info.get('ram_gb', 0):.1f} GiB RAM, "
        f"{sys_info.get('cpu_count', 0)} CPUs"
    )
    click.echo("=" * 72)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


@click.command()
@click.option(
    "--source-dsn",
    default=DEFAULT_SOURCE_DSN,
    show_default=False,
    envvar="SOURCE_DSN",
    help="PostgreSQL DSN for the source database.",
)
@click.option(
    "--target-dsn",
    default=DEFAULT_TARGET_DSN,
    show_default=False,
    envvar="TARGET_DSN",
    help="PostgreSQL DSN for the subset target database.",
)
@click.option(
    "--api-base-url",
    default=DEFAULT_API_BASE_URL,
    show_default=True,
    envvar="API_BASE_URL",
    help="Base URL of the Conclave Engine API.",
)
@click.option(
    "--results-path",
    default=DEFAULT_RESULTS_PATH,
    show_default=True,
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    help="Output path for the results JSON file.",
)
@click.option(
    "--vault-passphrase",
    default=DEFAULT_VAULT_PASSPHRASE,
    show_default=False,
    envvar="VAULT_PASSPHRASE",
    help="Vault unseal passphrase (dev default: test-passphrase).",
)
@click.option(
    "--license-key-path",
    default=DEFAULT_LICENSE_KEY_PATH,
    show_default=True,
    type=click.Path(dir_okay=False, path_type=Path),
    envvar="LICENSE_KEY_PATH",
    help="Path to the dev RSA private key for license JWT signing.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the execution plan without running any steps.",
)
def main(
    source_dsn: str,
    target_dsn: str,
    api_base_url: str,
    results_path: Path,
    vault_passphrase: str,
    license_key_path: Path,
    dry_run: bool,
) -> None:
    """Run the Conclave Engine 1M-row end-to-end load test.

    Generates ~1,012,500 rows of fictional data, runs all synthesis jobs,
    exercises the CLI subsetting tool, shreds artifacts, and writes metrics
    to RESULTS_PATH.

    All generated data is fictional (Faker). No real PII is produced or stored.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    if dry_run:
        click.echo(
            build_dry_run_plan(
                source_dsn=source_dsn,
                api_base_url=api_base_url,
                n_customers=N_CUSTOMERS,
                n_orders=N_ORDERS,
            )
        )
        return

    run_date = datetime.datetime.now(tz=datetime.UTC).isoformat()
    system_info = collect_system_info()

    with tempfile.TemporaryDirectory(prefix="conclave_e2e_") as _tmp:
        tmp_dir = Path(_tmp)

        # Steps 1-3
        step_preflight(api_base_url)
        table_row_counts = step_generate_and_load(source_dsn)
        step_unseal_vault(api_base_url, vault_passphrase)

        # Step 4 - License activation
        step_activate_license(
            api_base_url=api_base_url,
            license_key_path=license_key_path,
        )

        # Step 5 - Parquet export
        parquet_paths = step_export_parquet(
            source_dsn=source_dsn,
            tmp_dir=tmp_dir,
            table_row_counts=table_row_counts,
        )

        # Steps 6-8 - jobs
        table_to_job_id = step_create_jobs(api_base_url, parquet_paths)
        step_start_jobs(api_base_url, table_to_job_id)
        job_responses = step_poll_jobs(api_base_url, table_to_job_id)

        # Steps 9-10 - metrics + downloads
        job_results = step_collect_metrics(
            api_base_url=api_base_url,
            table_to_job_id=table_to_job_id,
            job_responses=job_responses,
            tmp_dir=tmp_dir,
        )

        # Step 11 - CLI subsetting
        cli_result = step_cli_subsetting(source_dsn=source_dsn, target_dsn=target_dsn)

        # Step 12 - shred
        shred_results = step_shred_jobs(api_base_url, table_to_job_id)

    # Steps 13-14 - results
    total_source_rows = sum(table_row_counts.values())
    results = build_results_dict(
        run_date=run_date,
        total_source_rows=total_source_rows,
        dataset=table_row_counts,
        job_results=job_results,
        cli_subsetting=cli_result,
        shred_results=shred_results,
        system_info=system_info,
    )

    step_write_results(results, results_path)
    step_print_summary(results)


if __name__ == "__main__":
    main()
