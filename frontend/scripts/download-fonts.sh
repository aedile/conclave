#!/usr/bin/env bash
# download-fonts.sh — Download Inter WOFF2 fonts for local bundling.
#
# Inter is licensed under the SIL Open Font License 1.1 (OFL-1.1).
# See: https://rsms.me/inter/
#
# These fonts are bundled in the repository rather than fetched from a CDN
# to comply with the strict CSP policy (font-src 'self') and air-gapped
# deployment requirements.
#
# Usage: bash frontend/scripts/download-fonts.sh
#
# Downloads:
#   frontend/src/assets/fonts/Inter-Regular.woff2
#   frontend/src/assets/fonts/Inter-Bold.woff2

set -euo pipefail

FONT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/src/assets/fonts"

echo "Creating font directory: ${FONT_DIR}"
mkdir -p "${FONT_DIR}"

INTER_VERSION="4.1"
BASE_URL="https://github.com/rsms/inter/releases/download/v${INTER_VERSION}"

echo "Downloading Inter ${INTER_VERSION} WOFF2 fonts..."

curl -fsSL \
  "${BASE_URL}/Inter-4.1.zip" \
  -o /tmp/inter.zip

unzip -jo /tmp/inter.zip \
  "Inter Desktop/Inter-Regular.woff2" \
  "Inter Desktop/Inter-Bold.woff2" \
  -d "${FONT_DIR}"

rm /tmp/inter.zip

echo ""
echo "Fonts downloaded to ${FONT_DIR}:"
ls -lh "${FONT_DIR}"
echo ""
echo "IMPORTANT: Commit these files to the repository:"
echo "  git add frontend/src/assets/fonts/"
echo "  git commit -m 'chore: bundle Inter WOFF2 fonts for air-gapped deployment'"
