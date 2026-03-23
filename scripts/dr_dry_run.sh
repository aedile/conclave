#!/usr/bin/env bash
# =============================================================================
# scripts/dr_dry_run.sh — Disaster Recovery Dry Run
#
# Validates core DR procedures against the local Docker Compose stack.
# This script is NOT for production — it is a validation tool for operators
# to verify their DR procedures work before an incident occurs.
#
# Usage:
#   ./scripts/dr_dry_run.sh
#
# Prerequisites:
#   - Docker Compose stack must be running: docker compose up -d
#   - Run from the repository root (or any directory; script locates repo root)
#
# WARNING: This script DESTROYS AND RECREATES a test table as part of DR
#          validation. It uses ONLY synthetic/ephemeral dr_test_ data —
#          NEVER real PII or application data.
#
# All backup files are written to /tmp/ and removed on exit.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers — honour NO_COLOR (https://no-color.org/)
# ---------------------------------------------------------------------------
if [[ -z "${NO_COLOR:-}" ]] && [[ -t 1 ]]; then
    _RED='\033[0;31m'
    _GREEN='\033[0;32m'
    _YELLOW='\033[1;33m'
    _BOLD='\033[1m'
    _RESET='\033[0m'
else
    _RED=''
    _GREEN=''
    _YELLOW=''
    _BOLD=''
    _RESET=''
fi

print_pass()  { printf "${_GREEN}[PASS]${_RESET}  %s\n" "$*"; }
print_fail()  { printf "${_RED}[FAIL]${_RESET}  %s\n" "$*"; }
print_info()  { printf "${_BOLD}[INFO]${_RESET}  %s\n" "$*"; }
print_warn()  { printf "${_YELLOW}[WARN]${_RESET}  %s\n" "$*"; }
print_head()  { printf "\n${_BOLD}=== %s ===${_RESET}\n" "$*"; }

# ---------------------------------------------------------------------------
# Globals — ephemeral resources tracked for cleanup
# ---------------------------------------------------------------------------
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DR_TABLE="dr_test_${TIMESTAMP}"
DR_REDIS_KEY="dr_test_key_${TIMESTAMP}"
BACKUP_FILE="/tmp/dr_dry_run_${TIMESTAMP}.dump"
FAILURES=0

# ---------------------------------------------------------------------------
# Locate repo root
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}" || exit 1

# ---------------------------------------------------------------------------
# Cleanup trap — runs on EXIT (normal, error, or SIGINT)
# Drops the test table, deletes the Redis key, and removes the backup file.
# ---------------------------------------------------------------------------
# shellcheck disable=SC2317,SC2329  # false positive: cleanup is invoked via trap EXIT
cleanup() {
    print_info "Running cleanup..."

    # Drop dr_test_ table if it exists
    docker compose exec -T postgres psql -U conclave -d conclave \
        -c "DROP TABLE IF EXISTS ${DR_TABLE};" \
        >/dev/null 2>&1 || true

    # Delete dr_test_ Redis key if Redis is running
    docker compose exec -T redis redis-cli DEL "${DR_REDIS_KEY}" \
        >/dev/null 2>&1 || true

    # Remove backup file from /tmp/
    if [[ -f "${BACKUP_FILE}" ]]; then
        rm -f "${BACKUP_FILE}"
        print_info "Removed backup file: ${BACKUP_FILE}"
    fi

    print_info "Cleanup complete."
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Preflight: verify Docker Compose stack is running
# ---------------------------------------------------------------------------
preflight_check() {
    print_head "Preflight: Docker Stack Availability"

    if ! docker compose ps --quiet 2>/dev/null | grep -q .; then
        print_fail "Docker Compose stack does not appear to be running."
        print_fail "Start the stack first: docker compose up -d"
        exit 1
    fi

    # Verify the postgres service is healthy
    if ! docker compose exec -T postgres pg_isready -U conclave >/dev/null 2>&1; then
        print_fail "PostgreSQL service is not ready. Ensure 'docker compose up -d' has completed."
        exit 1
    fi

    # Verify the redis service is reachable
    if ! docker compose exec -T redis redis-cli ping >/dev/null 2>&1; then
        print_fail "Redis service is not reachable. Ensure 'docker compose up -d' has completed."
        exit 1
    fi

    print_pass "Docker Compose stack is running and core services are reachable."
}

# ---------------------------------------------------------------------------
# Scenario 1: Database Backup & Restore
# ---------------------------------------------------------------------------
scenario_1_db_backup_restore() {
    print_head "Scenario 1: Database Backup and Restore"

    # Step 1: Create a test table with synthetic data
    print_info "Creating synthetic test table: ${DR_TABLE}"
    if ! docker compose exec -T postgres psql -U conclave -d conclave \
        -c "CREATE TABLE ${DR_TABLE} (id SERIAL PRIMARY KEY, value TEXT NOT NULL);" \
        -c "INSERT INTO ${DR_TABLE} (value) VALUES ('dr_test_row_alpha'), ('dr_test_row_beta'), ('dr_test_row_gamma');" \
        >/dev/null 2>&1; then
        print_fail "Scenario 1: Failed to create test table ${DR_TABLE}"
        FAILURES=$((FAILURES + 1))
        return
    fi
    print_pass "Scenario 1: Test table created with 3 synthetic rows."

    # Step 2: Create a backup via pg_dump inside the postgres container,
    # then copy the dump file out to /tmp/ on the host.
    print_info "Running pg_dump inside postgres container..."
    if ! docker compose exec -T postgres pg_dump \
        -U conclave \
        -F c \
        -t "${DR_TABLE}" \
        -f "/tmp/dr_backup_${TIMESTAMP}.dump" \
        conclave 2>/dev/null; then
        print_fail "Scenario 1: pg_dump failed."
        FAILURES=$((FAILURES + 1))
        return
    fi

    # Copy backup from container /tmp/ to host /tmp/
    if ! docker compose cp "postgres:/tmp/dr_backup_${TIMESTAMP}.dump" \
        "${BACKUP_FILE}" 2>/dev/null; then
        print_fail "Scenario 1: Failed to copy backup file from container."
        FAILURES=$((FAILURES + 1))
        return
    fi
    print_pass "Scenario 1: Backup created at ${BACKUP_FILE}"

    # Remove the in-container backup immediately after copy
    docker compose exec -T postgres rm -f "/tmp/dr_backup_${TIMESTAMP}.dump" \
        >/dev/null 2>&1 || true

    # Step 3: Drop the test table (simulating data loss)
    print_info "Dropping test table to simulate data loss..."
    if ! docker compose exec -T postgres psql -U conclave -d conclave \
        -c "DROP TABLE IF EXISTS ${DR_TABLE};" \
        >/dev/null 2>&1; then
        print_fail "Scenario 1: Failed to drop test table for recovery simulation."
        FAILURES=$((FAILURES + 1))
        return
    fi
    print_pass "Scenario 1: Test table dropped (data loss simulated)."

    # Step 4: Restore from backup
    print_info "Copying backup file back into container for restore..."
    if ! docker compose cp \
        "${BACKUP_FILE}" \
        "postgres:/tmp/dr_restore_${TIMESTAMP}.dump" 2>/dev/null; then
        print_fail "Scenario 1: Failed to copy backup into container for restore."
        FAILURES=$((FAILURES + 1))
        return
    fi

    print_info "Restoring from backup via pg_restore..."
    if ! docker compose exec -T postgres pg_restore \
        -U conclave \
        -d conclave \
        --no-owner \
        "/tmp/dr_restore_${TIMESTAMP}.dump" \
        >/dev/null 2>&1; then
        print_fail "Scenario 1: pg_restore failed."
        # Clean up in-container restore file
        docker compose exec -T postgres rm -f "/tmp/dr_restore_${TIMESTAMP}.dump" \
            >/dev/null 2>&1 || true
        FAILURES=$((FAILURES + 1))
        return
    fi

    # Remove the in-container restore dump
    docker compose exec -T postgres rm -f "/tmp/dr_restore_${TIMESTAMP}.dump" \
        >/dev/null 2>&1 || true

    # Step 5: Verify the restored table and data
    print_info "Verifying restored data..."
    ROW_COUNT=$(
        docker compose exec -T postgres psql -U conclave -d conclave \
            -t -c "SELECT COUNT(*) FROM ${DR_TABLE};" 2>/dev/null \
        | tr -d '[:space:]'
    )

    if [[ "${ROW_COUNT}" == "3" ]]; then
        print_pass "Scenario 1: Restored table contains 3 rows — backup/restore validated."
    else
        print_fail "Scenario 1: Expected 3 rows after restore, got '${ROW_COUNT}'."
        FAILURES=$((FAILURES + 1))
    fi
}

# ---------------------------------------------------------------------------
# Scenario 2: Service Recovery
# ---------------------------------------------------------------------------
scenario_2_service_recovery() {
    print_head "Scenario 2: Service Recovery (App Container)"

    # Step 1: Check all services are healthy
    print_info "Checking current stack health..."
    docker compose ps 2>/dev/null || true

    # Step 2: Stop the app container
    print_info "Stopping app service..."
    if ! docker compose stop app 2>/dev/null; then
        print_fail "Scenario 2: Failed to stop app service."
        FAILURES=$((FAILURES + 1))
        return
    fi
    print_pass "Scenario 2: App service stopped."

    # Step 3: Wait 5 seconds (simulates brief outage)
    print_info "Waiting 5 seconds (simulating outage)..."
    sleep 5

    # Step 4: Restart the app container
    print_info "Starting app service..."
    if ! docker compose start app 2>/dev/null; then
        print_fail "Scenario 2: Failed to start app service."
        FAILURES=$((FAILURES + 1))
        return
    fi
    print_pass "Scenario 2: App service started."

    # Step 5: Poll /ready until 200 or timeout
    print_info "Polling /ready endpoint (up to 60s)..."
    READY_URL="http://localhost:8000/ready"
    MAX_WAIT=60
    ELAPSED=0

    while [[ ${ELAPSED} -lt ${MAX_WAIT} ]]; do
        HTTP_STATUS=$(
            curl -s -o /dev/null -w "%{http_code}" \
                --max-time 3 \
                "${READY_URL}" 2>/dev/null \
            || echo "000"
        )
        if [[ "${HTTP_STATUS}" == "200" ]]; then
            print_pass "Scenario 2: /ready returned 200 after ${ELAPSED}s — service recovery validated."
            return
        fi
        sleep 2
        ELAPSED=$((ELAPSED + 2))
    done

    print_fail "Scenario 2: /ready did not return 200 within ${MAX_WAIT}s (last status: ${HTTP_STATUS})."
    FAILURES=$((FAILURES + 1))
}

# ---------------------------------------------------------------------------
# Scenario 3: Redis Recovery
# ---------------------------------------------------------------------------
scenario_3_redis_recovery() {
    print_head "Scenario 3: Redis Recovery"

    # Step 1: Verify Redis is running
    if ! docker compose exec -T redis redis-cli ping >/dev/null 2>&1; then
        print_fail "Scenario 3: Redis is not reachable before test."
        FAILURES=$((FAILURES + 1))
        return
    fi
    print_pass "Scenario 3: Redis is reachable."

    # Step 2: Write a test key
    print_info "Writing test key: ${DR_REDIS_KEY}"
    if ! docker compose exec -T redis redis-cli SET "${DR_REDIS_KEY}" "dr_test_value" \
        >/dev/null 2>&1; then
        print_fail "Scenario 3: Failed to write test key to Redis."
        FAILURES=$((FAILURES + 1))
        return
    fi
    print_pass "Scenario 3: Test key written."

    # Step 3: Stop Redis
    print_info "Stopping Redis service..."
    if ! docker compose stop redis 2>/dev/null; then
        print_fail "Scenario 3: Failed to stop Redis service."
        FAILURES=$((FAILURES + 1))
        return
    fi
    print_pass "Scenario 3: Redis stopped."

    # Step 4: Start Redis
    print_info "Starting Redis service..."
    if ! docker compose start redis 2>/dev/null; then
        print_fail "Scenario 3: Failed to start Redis service."
        FAILURES=$((FAILURES + 1))
        return
    fi

    # Wait for Redis to be ready
    ELAPSED=0
    MAX_REDIS_WAIT=30
    until docker compose exec -T redis redis-cli ping >/dev/null 2>&1 \
          || [[ ${ELAPSED} -ge ${MAX_REDIS_WAIT} ]]; do
        sleep 1
        ELAPSED=$((ELAPSED + 1))
    done

    if [[ ${ELAPSED} -ge ${MAX_REDIS_WAIT} ]]; then
        print_fail "Scenario 3: Redis did not become ready within ${MAX_REDIS_WAIT}s."
        FAILURES=$((FAILURES + 1))
        return
    fi
    print_pass "Scenario 3: Redis restarted and ready."

    # Step 5: Verify the key is gone (persistence is disabled per docker-compose.yml:
    # `redis-server --save "" --appendonly no`)
    KEY_EXISTS=$(
        docker compose exec -T redis redis-cli EXISTS "${DR_REDIS_KEY}" 2>/dev/null \
        | tr -d '[:space:]'
    )

    if [[ "${KEY_EXISTS}" == "0" ]]; then
        print_pass "Scenario 3: Test key is absent after restart — ephemeral behavior confirmed."
    else
        # Persistence may be configured on this deployment; warn but don't fail
        print_warn "Scenario 3: Test key still exists after Redis restart (persistence may be enabled)."
        print_warn "docker-compose.yml default disables persistence. Check your override files."
    fi

    # Cleanup is handled by the EXIT trap (redis-cli DEL)
    print_pass "Scenario 3: Redis recovery validated."
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    printf "${_BOLD}Conclave DR Dry Run — %s${_RESET}\n" "$(date)"
    printf "Repository: %s\n\n" "${REPO_ROOT}"

    preflight_check

    scenario_1_db_backup_restore
    scenario_2_service_recovery
    scenario_3_redis_recovery

    # Final report
    print_head "Results"
    if [[ ${FAILURES} -eq 0 ]]; then
        print_pass "All DR scenarios passed. Disaster recovery procedures are validated."
        exit 0
    else
        print_fail "${FAILURES} DR scenario(s) FAILED. Review output above."
        exit 1
    fi
}

main "$@"
