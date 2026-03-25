#!/usr/bin/env bash
# =============================================================================
# provision_pagila.sh — Provision the Pagila sample database for E2E testing
#
# Downloads the official Pagila (PostgreSQL Sakila) SQL files, verifies their
# SHA-256 checksums, creates the pagila database, and validates the load.
#
# Usage:
#   export PGHOST=localhost PGPORT=5432 PGUSER=postgres PGPASSWORD=secret
#   bash scripts/provision_pagila.sh
#
# Environment variables (all required):
#   PGHOST      — PostgreSQL server host (default: localhost)
#   PGPORT      — PostgreSQL server port (default: 5432)
#   PGUSER      — PostgreSQL superuser (default: postgres)
#   PGPASSWORD  — PostgreSQL password (read by psql automatically)
#
# The script is idempotent: running it multiple times always produces a
# clean Pagila database (DROP DATABASE IF EXISTS / CREATE DATABASE).
#
# Requirements:
#   - PostgreSQL >= 16 server and psql client
#   - curl
#   - sha256sum (Linux) or shasum (macOS)
#
# Source: https://github.com/devrimgunduz/pagila
# License: PostgreSQL License
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

log()   { echo -e "${_BOLD}[provision_pagila]${_RESET} $*"; }
ok()    { echo -e "${_GREEN}[OK]${_RESET} $*"; }
warn()  { echo -e "${_YELLOW}[WARN]${_RESET} $*"; }
error() { echo -e "${_RED}[ERROR]${_RESET} $*" >&2; }

# ---------------------------------------------------------------------------
# Connection parameters (PostgreSQL standard env vars)
# ---------------------------------------------------------------------------
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
# PGPASSWORD is read automatically by psql — never echoed here

# ---------------------------------------------------------------------------
# Download configuration
# ---------------------------------------------------------------------------
BASE_URL="https://raw.githubusercontent.com/devrimgunduz/pagila/master"

SCHEMA_FILE="pagila-schema.sql"
DATA_FILE="pagila-data.sql"

# SHA-256 checksums of the official Pagila master branch files.
# These were computed on 2026-03-24 from the devrimgunduz/pagila repository.
# If checksums fail, the upstream files have changed — update this script after
# verifying the new files are authentic.
SCHEMA_SHA256="8ce358e4c8014087b85296694a0893887bd7a4190e3ce407f2721b86b98e5707"  # pragma: allowlist secret
DATA_SHA256="880580fb2cd4daaa99f290ced264988cdd657b3158be63cd281466f796f6dbf2"    # pragma: allowlist secret

DATABASE="pagila"

# ---------------------------------------------------------------------------
# Temporary download directory — cleaned up on any exit
# ---------------------------------------------------------------------------
WORK_DIR=""

cleanup() {
    if [[ -n "${WORK_DIR}" ]] && [[ -d "${WORK_DIR}" ]]; then
        log "Cleaning up temporary download directory: ${WORK_DIR}"
        rm -rf "${WORK_DIR}"
    fi
}

trap cleanup EXIT

# ---------------------------------------------------------------------------
# Helper: portable sha256 computation
# ---------------------------------------------------------------------------
sha256_of() {
    local file="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${file}" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "${file}" | cut -d' ' -f1
    else
        error "Neither sha256sum nor shasum is available. Install one of them."
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Helper: run psql against the target database
# ---------------------------------------------------------------------------
psql_pagila() {
    psql \
        --host="${PGHOST}" \
        --port="${PGPORT}" \
        --username="${PGUSER}" \
        --dbname="${DATABASE}" \
        --no-password \
        "$@"
}

# Helper: run psql against postgres (maintenance database)
psql_postgres() {
    psql \
        --host="${PGHOST}" \
        --port="${PGPORT}" \
        --username="${PGUSER}" \
        --dbname="postgres" \
        --no-password \
        "$@"
}

# ---------------------------------------------------------------------------
# Step 1: Check prerequisites
# ---------------------------------------------------------------------------
log "Checking prerequisites..."

if ! command -v psql >/dev/null 2>&1; then
    error "psql is not installed or not on PATH. Install PostgreSQL client tools."
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    error "curl is not installed or not on PATH."
    exit 1
fi

ok "Prerequisites: psql and curl found."

# ---------------------------------------------------------------------------
# Step 2: Check PostgreSQL server version >= 16
# ---------------------------------------------------------------------------
log "Checking PostgreSQL server version..."

PG_VERSION_NUM="$(psql_postgres --tuples-only --no-align -c "SHOW server_version_num;")"
PG_VERSION_NUM="$(echo "${PG_VERSION_NUM}" | tr -d '[:space:]')"

if [[ -z "${PG_VERSION_NUM}" ]]; then
    error "Could not determine PostgreSQL server version. Check connection parameters."
    exit 1
fi

# server_version_num format: XXYYZZ (e.g., 160001 for 16.0.1)
MAJOR_VERSION=$(( PG_VERSION_NUM / 10000 ))

if (( MAJOR_VERSION < 16 )); then
    error "PostgreSQL version ${MAJOR_VERSION} is not supported. Pagila requires PostgreSQL >= 16."
    error "server_version_num = ${PG_VERSION_NUM}"
    exit 1
fi

ok "PostgreSQL version ${MAJOR_VERSION} (server_version_num=${PG_VERSION_NUM}) — OK."

# ---------------------------------------------------------------------------
# Step 3: Download Pagila SQL files to a temporary directory
# ---------------------------------------------------------------------------
WORK_DIR="$(mktemp -d)"
log "Downloading Pagila SQL files to ${WORK_DIR}..."

log "  Downloading ${SCHEMA_FILE}..."
curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --max-time 120 \
    "${BASE_URL}/${SCHEMA_FILE}" \
    --output "${WORK_DIR}/${SCHEMA_FILE}"

ok "  Downloaded ${SCHEMA_FILE}."

log "  Downloading ${DATA_FILE}..."
curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --max-time 300 \
    "${BASE_URL}/${DATA_FILE}" \
    --output "${WORK_DIR}/${DATA_FILE}"

ok "  Downloaded ${DATA_FILE}."

# ---------------------------------------------------------------------------
# Step 4: Verify SHA-256 checksums
# ---------------------------------------------------------------------------
log "Verifying SHA-256 checksums..."

ACTUAL_SCHEMA_SHA256="$(sha256_of "${WORK_DIR}/${SCHEMA_FILE}")"
ACTUAL_DATA_SHA256="$(sha256_of "${WORK_DIR}/${DATA_FILE}")"

if [[ "${ACTUAL_SCHEMA_SHA256}" != "${SCHEMA_SHA256}" ]]; then
    error "Checksum mismatch for ${SCHEMA_FILE}!"
    error "  Expected: ${SCHEMA_SHA256}"
    error "  Actual:   ${ACTUAL_SCHEMA_SHA256}"
    error "The file may have been tampered with or the upstream repo has changed."
    error "If the upstream file is legitimately updated, recalculate and update"
    error "SCHEMA_SHA256 in this script after verifying the new file is authentic."
    exit 1
fi

ok "  ${SCHEMA_FILE} checksum OK."

if [[ "${ACTUAL_DATA_SHA256}" != "${DATA_SHA256}" ]]; then
    error "Checksum mismatch for ${DATA_FILE}!"
    error "  Expected: ${DATA_SHA256}"
    error "  Actual:   ${ACTUAL_DATA_SHA256}"
    error "The file may have been tampered with or the upstream repo has changed."
    error "If the upstream file is legitimately updated, recalculate and update"
    error "DATA_SHA256 in this script after verifying the new file is authentic."
    exit 1
fi

ok "  ${DATA_FILE} checksum OK."

# ---------------------------------------------------------------------------
# Step 5: Drop and recreate the pagila database (idempotent)
# ---------------------------------------------------------------------------
log "Dropping existing '${DATABASE}' database if it exists..."

psql_postgres --command="DROP DATABASE IF EXISTS \"${DATABASE}\";" >/dev/null

log "Creating '${DATABASE}' database..."

psql_postgres --command="CREATE DATABASE \"${DATABASE}\";" >/dev/null

ok "Database '${DATABASE}' created."

# ---------------------------------------------------------------------------
# Step 6: Load schema and data
# ---------------------------------------------------------------------------
log "Loading Pagila schema..."
psql_pagila --file="${WORK_DIR}/${SCHEMA_FILE}" >/dev/null

ok "Schema loaded."

log "Loading Pagila data (this may take a minute)..."
psql_pagila --file="${WORK_DIR}/${DATA_FILE}" >/dev/null

ok "Data loaded."

# ---------------------------------------------------------------------------
# Step 7: Validate post-load row counts
# ---------------------------------------------------------------------------
log "Validating post-load row counts..."

CUSTOMER_COUNT="$(psql_pagila --tuples-only --no-align -c "SELECT COUNT(*) FROM customer;")"
CUSTOMER_COUNT="$(echo "${CUSTOMER_COUNT}" | tr -d '[:space:]')"

RENTAL_COUNT="$(psql_pagila --tuples-only --no-align -c "SELECT COUNT(*) FROM rental;")"
RENTAL_COUNT="$(echo "${RENTAL_COUNT}" | tr -d '[:space:]')"

if (( CUSTOMER_COUNT < 500 )); then
    error "Row count validation failed: customer table has ${CUSTOMER_COUNT} rows (expected >= 500)."
    error "The data load may be incomplete or the SQL file is corrupted."
    exit 1
fi

ok "  customer: ${CUSTOMER_COUNT} rows (>= 500 required)."

if (( RENTAL_COUNT < 40000 )); then
    error "Row count validation failed: rental table has ${RENTAL_COUNT} rows (expected >= 40000)."
    error "The data load may be incomplete or the SQL file is corrupted."
    exit 1
fi

ok "  rental: ${RENTAL_COUNT} rows (>= 40000 required)."

# ---------------------------------------------------------------------------
# Step 8: Validate FK constraints are satisfied
# ---------------------------------------------------------------------------
log "Validating foreign key (FK) constraints..."

FK_VIOLATION_COUNT="$(psql_pagila --tuples-only --no-align -c "
    SELECT COUNT(*)
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    WHERE c.contype = 'f'
      AND NOT c.convalidated;
")"
FK_VIOLATION_COUNT="$(echo "${FK_VIOLATION_COUNT}" | tr -d '[:space:]')"

if (( FK_VIOLATION_COUNT > 0 )); then
    error "FK constraint validation failed: ${FK_VIOLATION_COUNT} unvalidated foreign key constraint(s) found."
    error "The data may contain orphaned rows or the schema/data files are mismatched."
    exit 1
fi

ok "  All foreign key constraints are in validated state (${FK_VIOLATION_COUNT} NOT VALID constraints)."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
log ""
ok "${_BOLD}Pagila database provisioned successfully.${_RESET}"
log ""
log "Connection: psql -h ${PGHOST} -p ${PGPORT} -U ${PGUSER} -d ${DATABASE}"
log ""
log "Validation subset tables:"
log "  customer  — $(psql_pagila --tuples-only --no-align -c "SELECT COUNT(*) FROM customer;" | tr -d '[:space:]') rows"
log "  address   — $(psql_pagila --tuples-only --no-align -c "SELECT COUNT(*) FROM address;" | tr -d '[:space:]') rows"
log "  rental    — $(psql_pagila --tuples-only --no-align -c "SELECT COUNT(*) FROM rental;" | tr -d '[:space:]') rows"
log "  inventory — $(psql_pagila --tuples-only --no-align -c "SELECT COUNT(*) FROM inventory;" | tr -d '[:space:]') rows"
log "  film      — $(psql_pagila --tuples-only --no-align -c "SELECT COUNT(*) FROM film;" | tr -d '[:space:]') rows"
