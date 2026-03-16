#!/usr/bin/env bash
# scripts/verify_integration_count.sh
#
# CI gate: verify that the integration test suite collects at least 1 test.
#
# Without this guard, a failure to start the PostgreSQL service causes
# all integration tests to be skipped silently, producing a false green
# (0 tests run = "pass" in the default pytest exit-code model).
#
# This script runs pytest --collect-only -q over tests/integration/ and
# asserts that the collected count is > 0.  If the count is 0, it exits
# with code 1 so that CI fails loudly.
#
# Usage:
#   ./scripts/verify_integration_count.sh
#
# Expected call site in .github/workflows/ci.yml:
#   - name: Verify integration test count > 0
#     run: bash scripts/verify_integration_count.sh
#
# This script must be called BEFORE "Run integration tests" so that a zero
# collection count is caught before the empty run is reported as a pass.
#
# Exit codes:
#   0  — at least 1 integration test collected (gate passes)
#   1  — 0 tests collected (gate fails — PostgreSQL likely unavailable)
#   2  — unexpected error in collection step

set -euo pipefail

# ---------------------------------------------------------------------------
# Locate the repo root relative to this script's location.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

# ---------------------------------------------------------------------------
# Run pytest --collect-only and extract the collected test count.
# The last summary line from --collect-only -q looks like:
#   "42 tests collected in 0.35s"
# or, when nothing is collected:
#   "no tests ran"
#
# We grep for the integer before " test" or " tests" in that summary line.
# ---------------------------------------------------------------------------
echo "[verify_integration_count] Running pytest --collect-only on tests/integration/ ..."

COLLECT_OUTPUT="$(
    poetry run pytest tests/integration/ \
        --collect-only \
        -q \
        --no-header \
        --no-cov \
        -p pytest_postgresql \
        -m "not synthesizer" \
        2>&1
)" || COLLECT_EXIT=$?

# A non-zero exit from --collect-only usually means an import error or
# collection error, not zero-tests.  Treat it as an unexpected error.
if [[ "${COLLECT_EXIT:-0}" -ne 0 ]]; then
    echo "[verify_integration_count] ERROR: pytest --collect-only exited with ${COLLECT_EXIT:-0}."
    echo "--- collection output ---"
    echo "${COLLECT_OUTPUT}"
    echo "-------------------------"
    exit 2
fi

echo "${COLLECT_OUTPUT}"

# ---------------------------------------------------------------------------
# Extract the collected test count from the summary line.
# Match patterns:  "42 tests collected"  or  "1 test collected"
# ---------------------------------------------------------------------------
COLLECTED_COUNT=$(
    echo "${COLLECT_OUTPUT}" \
        | grep -Eo '[0-9]+ tests? collected' \
        | grep -Eo '^[0-9]+' \
        || echo "0"
)

echo ""
echo "[verify_integration_count] Collected integration tests: ${COLLECTED_COUNT}"

if [[ "${COLLECTED_COUNT}" -gt 0 ]]; then
    echo "[verify_integration_count] PASS: integration test count > 0."
    exit 0
else
    echo "[verify_integration_count] FAIL: 0 integration tests collected."
    echo ""
    echo "Possible causes:"
    echo "  1. PostgreSQL service failed to start — pg_ctl must be on PATH."
    echo "  2. All tests are marked with a skip guard that fired at collection time."
    echo "  3. tests/integration/ is empty."
    echo ""
    echo "Ensure PostgreSQL 16 is installed and pg_ctl is on PATH before running"
    echo "integration tests."
    exit 1
fi
