#!/usr/bin/env bash
# bump_version.sh — Atomically update the version string in all 4 locations.
#
# Usage:
#   ./scripts/bump_version.sh <PEP-440-version>
#
# Example:
#   ./scripts/bump_version.sh 1.0.0rc1
#   ./scripts/bump_version.sh 1.0.0
#   ./scripts/bump_version.sh 2.3.0b2
#
# The version argument MUST be a valid PEP 440 string:
#   X.Y.Z              — stable release
#   X.Y.Z(a|b|rc)N     — pre-release (alpha, beta, release candidate)
#
# Hyphens are NOT allowed (use 1.0.0rc1, not 1.0.0-rc.1).
# A leading "v" is NOT allowed (use 1.0.0, not v1.0.0).
#
# NOTE: bootstrapper/main.py is NOT a bump target. It reads the version
# dynamically from synth_engine.__version__ (i.e. from __init__.py).
# Bumping __init__.py is sufficient to update the FastAPI app version.
#
# This script does NOT commit or tag — that is the operator's responsibility.
# It does NOT run poetry lock — call that separately if pyproject.toml changed.
#
# BUMP_ROOT environment variable:
#   Override the repository root directory. Used by tests to point the script
#   at a temporary replica rather than the real repo. Defaults to the directory
#   containing this script's parent (i.e. the repo root).
#
# Exit codes:
#   0  — success (or no-op when version already matches)
#   1  — invalid version string or missing target file

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve repository root
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# BUMP_ROOT can be overridden by tests to point at a tmp_path replica.
REPO_ROOT="${BUMP_ROOT:-"$(dirname "${SCRIPT_DIR}")"}"

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------
if [[ $# -ne 1 ]] || [[ -z "${1:-}" ]]; then
    echo "ERROR: exactly one argument required." >&2
    echo "Usage: $0 <PEP-440-version>  (e.g. 1.0.0rc1)" >&2
    exit 1
fi

NEW_VERSION="${1}"

# PEP 440 validation: X.Y.Z or X.Y.Z(a|b|rc)N only.
# We deliberately exclude: hyphens, leading 'v', '.dev', '.post', epoch prefixes.
PEP440_RE='^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$'
if ! echo "${NEW_VERSION}" | grep -qE "${PEP440_RE}"; then
    echo "ERROR: '${NEW_VERSION}' is not a valid PEP 440 version string." >&2
    echo "       Accepted formats: X.Y.Z  X.Y.ZaN  X.Y.ZbN  X.Y.ZrcN" >&2
    echo "       Do NOT use hyphens (1.0.0-rc.1) or a leading 'v' (v1.0.0)." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve the 4 target file paths
# ---------------------------------------------------------------------------
PYPROJECT="${REPO_ROOT}/pyproject.toml"
INIT_PY="${REPO_ROOT}/src/synth_engine/__init__.py"
LICENSING_PY="${REPO_ROOT}/src/synth_engine/shared/security/licensing.py"
OPENAPI_JSON="${REPO_ROOT}/docs/api/openapi.json"

# ---------------------------------------------------------------------------
# Pre-flight: verify all target files exist before making any change
# ---------------------------------------------------------------------------
MISSING=0
for FILE in "${PYPROJECT}" "${INIT_PY}" "${LICENSING_PY}" "${OPENAPI_JSON}"; do
    if [[ ! -f "${FILE}" ]]; then
        echo "ERROR: required file not found: ${FILE}" >&2
        MISSING=1
    fi
done
if [[ "${MISSING}" -ne 0 ]]; then
    echo "ERROR: aborting — one or more target files are missing (no files were changed)." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Detect current version (from pyproject.toml) for idempotency check
# ---------------------------------------------------------------------------
CURRENT_VERSION="$(grep -E '^version = "' "${PYPROJECT}" | head -1 | sed 's/version = "//;s/"//')"

if [[ "${CURRENT_VERSION}" == "${NEW_VERSION}" ]]; then
    echo "INFO: version is already ${NEW_VERSION} — nothing to do (idempotent no-op)."
    exit 0
fi

echo "Bumping version: ${CURRENT_VERSION} -> ${NEW_VERSION}"

# ---------------------------------------------------------------------------
# Perform all substitutions.
# sed -i '' on macOS; sed -i on Linux — use perl for portability.
# ---------------------------------------------------------------------------

# 1. pyproject.toml: version = "OLD" -> version = "NEW"
#    Only the [tool.poetry] section's version field (line 3 in canonical form).
#    We match the first occurrence of `version = "..."` to avoid touching
#    dependency version constraints.
perl -i -0pe \
    "s/(\\[tool\\.poetry\\][^\\[]*\\n(?:(?!\\[)[^\\n]*\\n)*?version = \")([^\"]+)(\")/\${1}${NEW_VERSION}\${3}/m" \
    "${PYPROJECT}"

# 2. src/synth_engine/__init__.py: __version__ = "OLD" -> __version__ = "NEW"
perl -i -pe "s/^(__version__ = \")([^\"]+)(\")/\${1}${NEW_VERSION}\${3}/" "${INIT_PY}"

# 3. shared/security/licensing.py: _APP_VERSION: str = "OLD" -> "NEW"
perl -i -pe "s/^(_APP_VERSION: str = \")([^\"]+)(\")/\${1}${NEW_VERSION}\${3}/" "${LICENSING_PY}"

# 4. docs/api/openapi.json: "version": "OLD" -> "version": "NEW"
perl -i -pe "s/(\"version\": \")([^\"]+)(\")/\${1}${NEW_VERSION}\${3}/" "${OPENAPI_JSON}"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Version bump complete: ${CURRENT_VERSION} -> ${NEW_VERSION}"
echo ""
echo "Updated files:"
echo "  ${PYPROJECT}"
echo "  ${INIT_PY}"
echo "  ${LICENSING_PY}"
echo "  ${OPENAPI_JSON}"
echo ""
echo "NOTE: bootstrapper/main.py reads version dynamically from __init__.py"
echo "      and does NOT need to be updated separately."
echo ""
echo "Next steps:"
echo "  1. Run: poetry lock --no-update   (to refresh the lock file)"
echo "  2. Run: git add -p && git commit  (to commit the version bump)"
# Emit the correct git tag hint depending on whether this is an RC release.
# ADV-P51-02: the old expansion v${NEW_VERSION%rc*}-rc.${NEW_VERSION##*rc}
# produced v1.0.0-rc.1.0.0 for stable versions. Use a conditional instead.
if [[ "${NEW_VERSION}" =~ rc[0-9]+$ ]]; then
    RC_NUM="${NEW_VERSION##*rc}"
    BASE="${NEW_VERSION%rc*}"
    echo "  3. Tag:  git tag v${BASE}-rc.${RC_NUM}  (semver pre-release tag)"
else
    echo "  3. Tag:  git tag v${NEW_VERSION}"
fi
