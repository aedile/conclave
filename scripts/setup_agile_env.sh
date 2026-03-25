#!/usr/bin/env bash
# scripts/setup_agile_env.sh
#
# Task 0.6.1: Host Initialization — Agent Teams Feature Flag
#
# NOTE: This is a pre-Poetry bootstrap script. It runs before pyproject.toml
# and the Poetry virtual environment exist, so python3 is used directly
# (system Python). Once the Poetry project is initialized (Phase 1), all
# subsequent Python invocations must use `poetry run python3`.
#
# ChromaDB MCP integration was removed in T55.5 — the retrospective seeding
# scripts were deleted and the chromadb dependency was removed from pyproject.toml.

set -euo pipefail

echo "Starting Agile Environment Provisioning..."

# ---------------------------------------------------------------------------
# 1. Update shell profile with Agent Teams feature flag
# ---------------------------------------------------------------------------

PROFILE_FILE=""
if [ -f "$HOME/.zshrc" ]; then
    PROFILE_FILE="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    PROFILE_FILE="$HOME/.bashrc"
else
    PROFILE_FILE="$HOME/.bash_profile"
fi

echo "Updating $PROFILE_FILE..."
if ! grep -q "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1" "$PROFILE_FILE" 2>/dev/null; then
    echo "export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1" >> "$PROFILE_FILE"
    echo "Flag injected into profile."
else
    echo "Flag already present in profile."
fi

echo "Environment Provisioning Complete."
echo ""
echo "NEXT STEP: Run 'poetry install --with dev' to install all project dependencies."
