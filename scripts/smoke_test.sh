#!/usr/bin/env bash
# scripts/smoke_test.sh
#
# Production smoke test — builds the Docker image, starts the full stack via
# docker compose, waits for health, runs basic HTTP assertions, then tears down.
#
# When to run:
#   - At phase boundary as part of the phase-boundary-auditor checks
#   - Before deploying to staging/production
#   - After Dockerfile or docker-compose.yml changes
#
# Prerequisites:
#   - Docker and docker compose (v2 plugin) installed
#   - Ports 8000, 5432, 6379 available (or as configured in compose files)
#
# Usage:
#   bash scripts/smoke_test.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers — honour NO_COLOR (https://no-color.org/)
# ---------------------------------------------------------------------------
if [[ -z "${NO_COLOR:-}" ]] && [[ -t 1 ]]; then
    _RED='\033[0;31m'
    _GREEN='\033[0;32m'
    _BOLD='\033[1m'
    _RESET='\033[0m'
else
    _RED=''
    _GREEN=''
    _BOLD=''
    _RESET=''
fi

print_pass() { printf "${_GREEN}[PASS]${_RESET}  %s\n" "$*"; }
print_fail() { printf "${_RED}[FAIL]${_RESET}  %s\n" "$*"; }
print_info() { printf "${_BOLD}[INFO]${_RESET}  %s\n" "$*"; }

# ---------------------------------------------------------------------------
# Locate repo root
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}" || exit 1

# ---------------------------------------------------------------------------
# Compose file selection
# ---------------------------------------------------------------------------
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.cpu.yml"

# ---------------------------------------------------------------------------
# Cleanup trap — always tear down the stack on exit
# ---------------------------------------------------------------------------
cleanup() {
    print_info "Tearing down docker compose stack..."
    docker compose ${COMPOSE_FILES} down -v 2>/dev/null || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Step 1: Build the production Docker image
# ---------------------------------------------------------------------------
print_info "Building production Docker image (conclave-engine:smoke-test)..."
docker build -t conclave-engine:smoke-test .

# ---------------------------------------------------------------------------
# Step 2: Start the full stack
# ---------------------------------------------------------------------------
print_info "Starting docker compose stack..."
docker compose ${COMPOSE_FILES} up -d

# ---------------------------------------------------------------------------
# Step 3: Wait for health check
# ---------------------------------------------------------------------------
print_info "Waiting for health endpoint (polling every 2s, max 60s)..."
HEALTH_URL="http://localhost:8000/health"
MAX_WAIT=60
ELAPSED=0

while [[ ${ELAPSED} -lt ${MAX_WAIT} ]]; do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${HEALTH_URL}" 2>/dev/null || echo "000")
    if [[ "${STATUS}" == "200" ]]; then
        print_pass "Health endpoint responded 200 after ${ELAPSED}s"
        break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

if [[ ${ELAPSED} -ge ${MAX_WAIT} ]]; then
    print_fail "Health endpoint did not respond 200 within ${MAX_WAIT}s (last status: ${STATUS})"
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 4: Smoke test assertions
# ---------------------------------------------------------------------------
FAILURES=0

# 4a: GET /health → 200
print_info "Checking GET /health → 200..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${HEALTH_URL}")
if [[ "${HTTP_CODE}" == "200" ]]; then
    print_pass "GET /health → ${HTTP_CODE}"
else
    print_fail "GET /health → ${HTTP_CODE} (expected 200)"
    FAILURES=$((FAILURES + 1))
fi

# 4b: Content-Security-Policy header present
print_info "Checking Content-Security-Policy header..."
CSP_HEADER=$(curl -s -I "${HEALTH_URL}" | grep -i "^content-security-policy:" || true)
if [[ -n "${CSP_HEADER}" ]]; then
    print_pass "Content-Security-Policy header present"
else
    print_fail "Content-Security-Policy header missing"
    FAILURES=$((FAILURES + 1))
fi

# 4c: X-Content-Type-Options header
print_info "Checking X-Content-Type-Options header..."
XCTO_HEADER=$(curl -s -I "${HEALTH_URL}" | grep -i "^x-content-type-options:" || true)
if [[ -n "${XCTO_HEADER}" ]]; then
    print_pass "X-Content-Type-Options header present"
else
    print_fail "X-Content-Type-Options header missing"
    FAILURES=$((FAILURES + 1))
fi

# ---------------------------------------------------------------------------
# Step 5–6: Report and exit (cleanup handled by trap)
# ---------------------------------------------------------------------------
echo ""
if [[ ${FAILURES} -eq 0 ]]; then
    print_pass "All smoke tests passed."
    exit 0
else
    print_fail "${FAILURES} smoke test(s) failed."
    exit 1
fi
