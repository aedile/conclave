#!/usr/bin/env bash
# =============================================================================
# validate_airgap.sh — Validate a Conclave air-gap bundle end-to-end
#
# Usage:
#   ./scripts/validate_airgap.sh [path-to-bundle.tar.gz]
#
# If no bundle path is supplied, 'make build-airgap-bundle' is called first.
#
# Steps:
#   1. Locate or build the bundle.
#   2. Extract to a temp directory.
#   3. Verify required files: docker-compose.yml, VERSION, images/ (>=3 .tar).
#   4. Load Docker images from images/*.tar.
#   5. Start the stack (docker compose up -d, no override file).
#   6. Poll GET /health up to 60s — 200 OK (even sealed) is sufficient.
#   7. Print success summary.
#   8. Teardown (trapped on EXIT).
#
# Security notes:
#   - Does NOT attempt vault unseal (requires passphrase — unsafe for scripts).
#   - Does NOT attempt license activation.
#   - Uses a distinct project name to avoid conflicts with running stacks.
#
# Prerequisites: docker, curl, tar
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

readonly COMPOSE_PROJECT="conclave-validation"
readonly HEALTH_TIMEOUT=60
readonly HEALTH_URL="http://localhost:8000/health"
readonly MIN_IMAGES=3

# ---------------------------------------------------------------------------
# State — populated during execution, used in cleanup trap
# ---------------------------------------------------------------------------

TMPDIR_WORK=""
COMPOSE_FILE=""

# ---------------------------------------------------------------------------
# Cleanup — always runs on EXIT
# ---------------------------------------------------------------------------

cleanup() {
    local exit_code=$?

    if [[ -n "${COMPOSE_FILE}" && -f "${COMPOSE_FILE}" ]]; then
        log "Tearing down compose stack (project: ${COMPOSE_PROJECT}) ..."
        docker compose \
            --project-name "${COMPOSE_PROJECT}" \
            --file "${COMPOSE_FILE}" \
            down -v 2>/dev/null || true
    fi

    if [[ -n "${TMPDIR_WORK}" && -d "${TMPDIR_WORK}" ]]; then
        log "Removing temp directory: ${TMPDIR_WORK}"
        rm -rf "${TMPDIR_WORK}"
    fi

    if [[ ${exit_code} -ne 0 ]]; then
        log "ERROR: validation failed (exit code ${exit_code})"
    fi
}

trap cleanup EXIT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
    echo "[validate_airgap] $*"
}

die() {
    echo "[validate_airgap] ERROR: $*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Verify prerequisites
# ---------------------------------------------------------------------------

for tool in docker curl tar; do
    if ! command -v "${tool}" > /dev/null 2>&1; then
        die "required tool '${tool}' not found in PATH"
    fi
done

# Confirm Docker daemon is reachable (not just the CLI binary)
if ! docker info > /dev/null 2>&1; then
    die "Docker daemon is not running or not accessible. Start Docker and retry."
fi

# ---------------------------------------------------------------------------
# Locate (or build) the bundle
# ---------------------------------------------------------------------------

BUNDLE_PATH="${1:-}"

if [[ -z "${BUNDLE_PATH}" ]]; then
    log "No bundle path supplied — building a fresh bundle via 'make build-airgap-bundle' ..."
    make build-airgap-bundle
    # The script produces conclave-bundle-<version>.tar.gz in the current dir.
    # Use find + sort rather than ls to handle non-alphanumeric filenames safely.
    BUNDLE_PATH=$(find . -maxdepth 1 -name "conclave-bundle-*.tar.gz" -print | sort | tail -1)
    [[ -n "${BUNDLE_PATH}" ]] || die "make build-airgap-bundle ran but no conclave-bundle-*.tar.gz found"
fi

[[ -f "${BUNDLE_PATH}" ]] || die "bundle not found: ${BUNDLE_PATH}"
log "Using bundle: ${BUNDLE_PATH}"

# ---------------------------------------------------------------------------
# Extract bundle to a temp directory
# ---------------------------------------------------------------------------

TMPDIR_WORK=$(mktemp -d)
log "Extracting bundle to ${TMPDIR_WORK} ..."
tar -xzf "${BUNDLE_PATH}" -C "${TMPDIR_WORK}"

# The build script produces: dist/<files> — the extracted structure is dist/
EXTRACTED_ROOT="${TMPDIR_WORK}/dist"
if [[ ! -d "${EXTRACTED_ROOT}" ]]; then
    # Some bundles may extract without the dist/ wrapper — tolerate both
    EXTRACTED_ROOT="${TMPDIR_WORK}"
fi

# ---------------------------------------------------------------------------
# Verify required files
# ---------------------------------------------------------------------------

log "Verifying bundle contents ..."

[[ -f "${EXTRACTED_ROOT}/docker-compose.yml" ]] \
    || die "bundle is missing docker-compose.yml"

[[ -f "${EXTRACTED_ROOT}/VERSION" ]] \
    || die "bundle is missing VERSION file"

[[ -d "${EXTRACTED_ROOT}/images" ]] \
    || die "bundle is missing images/ directory"

image_count=$(find "${EXTRACTED_ROOT}/images" -maxdepth 1 -name "*.tar" | wc -l)
image_count=$(echo "${image_count}" | tr -d ' ')
if [[ "${image_count}" -lt "${MIN_IMAGES}" ]]; then
    die "bundle images/ contains only ${image_count} .tar file(s); expected at least ${MIN_IMAGES} (3)"
fi

BUNDLE_VERSION=$(cat "${EXTRACTED_ROOT}/VERSION")
log "Bundle version  : ${BUNDLE_VERSION}"
log "Image tar files : ${image_count}"

# ---------------------------------------------------------------------------
# Load Docker images
# ---------------------------------------------------------------------------

log "Loading ${image_count} Docker image(s) ..."
LOADED_IMAGES=()

while IFS= read -r tar_file; do
    log "  docker load -i ${tar_file}"
    loaded_output=$(docker load -i "${tar_file}" 2>&1)
    LOADED_IMAGES+=("${loaded_output}")
    log "  ${loaded_output}"
done < <(find "${EXTRACTED_ROOT}/images" -maxdepth 1 -name "*.tar" | sort)

# ---------------------------------------------------------------------------
# Start the compose stack
# ---------------------------------------------------------------------------

COMPOSE_FILE="${EXTRACTED_ROOT}/docker-compose.yml"
log "Starting stack (project: ${COMPOSE_PROJECT}) ..."

docker compose \
    --project-name "${COMPOSE_PROJECT}" \
    --file "${COMPOSE_FILE}" \
    up -d

# ---------------------------------------------------------------------------
# Health check — poll /health up to HEALTH_TIMEOUT seconds
# ---------------------------------------------------------------------------

log "Polling ${HEALTH_URL} (timeout: ${HEALTH_TIMEOUT}s) ..."

elapsed=0
while [[ "${elapsed}" -lt "${HEALTH_TIMEOUT}" ]]; do
    http_status=$(curl -s -o /dev/null -w "%{http_code}" "${HEALTH_URL}" 2>/dev/null || true)
    if [[ "${http_status}" == "200" ]]; then
        log "Health check passed (HTTP 200) after ${elapsed}s"
        break
    fi
    sleep 2
    elapsed=$(( elapsed + 2 ))
done

if [[ "${elapsed}" -ge "${HEALTH_TIMEOUT}" ]]; then
    die "Health check timed out after ${HEALTH_TIMEOUT}s — stack did not become healthy"
fi

# ---------------------------------------------------------------------------
# Success summary
# ---------------------------------------------------------------------------

running_services=$(
    docker compose \
        --project-name "${COMPOSE_PROJECT}" \
        --file "${COMPOSE_FILE}" \
        ps --services 2>/dev/null || echo "(unable to list services)"
)

log ""
log "========================================"
log "  AIR-GAP VALIDATION PASSED"
log "========================================"
log "  Bundle          : ${BUNDLE_PATH}"
log "  Version         : ${BUNDLE_VERSION}"
log "  Images loaded   : ${image_count}"
log "  Services running:"
while IFS= read -r svc; do
    [[ -n "${svc}" ]] && log "    - ${svc}"
done <<< "${running_services}"
log "========================================"
log ""
