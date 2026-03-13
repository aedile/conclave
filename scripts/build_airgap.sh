#!/usr/bin/env bash
# =============================================================================
# build_airgap.sh — Create a self-contained, offline-deployable bundle
#
# This script:
#   1. Pulls (or verifies local presence of) each required Docker image.
#   2. Saves each image as a .tar file into dist/images/.
#   3. Copies Compose files, scripts, and docs into dist/.
#   4. Writes a VERSION file derived from git-describe.
#   5. Compresses everything into a single .tar.gz in the workspace root.
#   6. Prints a sha256 checksum of the bundle.
#
# The resulting bundle can be transferred to an air-gapped host and loaded
# with `docker load -i dist/images/<name>.tar`.
#
# Prerequisites: docker, git, sha256sum (or shasum -a 256 on macOS)
# =============================================================================
set -euo pipefail

trap 'log "ERROR: build failed. Cleaning up ${DIST_DIR:-dist} ..."; rm -rf "${DIST_DIR:-dist}" conclave-bundle-*.tar.gz' ERR

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Images to include in the bundle.
IMAGES=(
    "conclave-engine:latest"
    "postgres:16-alpine"
    "redis:7-alpine"
    "minio/minio:RELEASE.2024-01-28T22-35-53Z"
    "jaegertracing/all-in-one:1.57"
)

DIST_DIR="dist"
IMAGES_DIR="${DIST_DIR}/images"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
    echo "[build_airgap] $*"
}

# Derive a safe filename from an image reference.
# e.g. "minio/minio:RELEASE.2024-01-28T22-35-53Z" -> "minio_minio_RELEASE.2024-01-28T22-35-53Z"
image_to_filename() {
    local image="$1"
    # Replace / and : with _ to produce a filesystem-safe name
    echo "${image//[:\/]/_}"
}

# ---------------------------------------------------------------------------
# Verify prerequisites
# ---------------------------------------------------------------------------

for tool in docker git; do
    if ! command -v "${tool}" > /dev/null 2>&1; then
        echo "[build_airgap] ERROR: required tool '${tool}' not found in PATH" >&2
        exit 1
    fi
done

# sha256sum (Linux) or shasum -a 256 (macOS)
if command -v sha256sum > /dev/null 2>&1; then
    CHECKSUM_CMD="sha256sum"
elif command -v shasum > /dev/null 2>&1; then
    CHECKSUM_CMD="shasum -a 256"
else
    echo "[build_airgap] ERROR: neither sha256sum nor shasum found" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Prepare dist/ directory
# ---------------------------------------------------------------------------

log "Preparing dist directory: ${DIST_DIR}"
rm -rf "${DIST_DIR}"
mkdir -p "${IMAGES_DIR}"

# ---------------------------------------------------------------------------
# Pull and save Docker images
# ---------------------------------------------------------------------------

for image in "${IMAGES[@]}"; do
    filename=$(image_to_filename "${image}")
    tar_path="${IMAGES_DIR}/${filename}.tar"

    # Check if the image is already present locally; pull only if missing.
    if docker image inspect "${image}" > /dev/null 2>&1; then
        log "Image already present locally, skipping pull: ${image}"
    else
        log "Pulling image: ${image}"
        docker pull "${image}"
    fi

    log "Saving image to ${tar_path}"
    docker save -o "${tar_path}" "${image}"
done

# ---------------------------------------------------------------------------
# Copy artefacts into dist/
# ---------------------------------------------------------------------------

log "Copying Compose files"
cp docker-compose.yml "${DIST_DIR}/"
cp docker-compose.override.yml "${DIST_DIR}/"

if [[ -f ".env.dev" ]]; then
    log "Copying .env.dev"
    cp .env.dev "${DIST_DIR}/"
fi

log "Copying scripts/"
cp -r scripts/ "${DIST_DIR}/scripts/"

log "Copying docs/"
cp -r docs/ "${DIST_DIR}/docs/"

# ---------------------------------------------------------------------------
# Write VERSION file
# ---------------------------------------------------------------------------

VERSION="$(git describe --tags --always --dirty 2>/dev/null || echo "unversioned")"
log "Bundle version: ${VERSION}"
echo "${VERSION}" > "${DIST_DIR}/VERSION"

# ---------------------------------------------------------------------------
# Compress everything
# ---------------------------------------------------------------------------

BUNDLE_NAME="conclave-bundle-${VERSION}.tar.gz"
log "Compressing to ${BUNDLE_NAME}"
tar -czf "${BUNDLE_NAME}" "${DIST_DIR}/"

# ---------------------------------------------------------------------------
# Print checksum
# ---------------------------------------------------------------------------

log "SHA-256 checksum:"
${CHECKSUM_CMD} "${BUNDLE_NAME}"

log "Bundle ready: ${BUNDLE_NAME}"
