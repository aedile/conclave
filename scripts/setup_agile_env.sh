#!/usr/bin/env bash
# scripts/setup_agile_env.sh
#
# Task 0.6.1: Host Initialization & MCP Setup
#
# NOTE: This is a pre-Poetry bootstrap script. It runs before pyproject.toml
# and the Poetry virtual environment exist, so python3 is used directly
# (system Python). Once the Poetry project is initialized (Phase 1), all
# subsequent Python invocations must use `poetry run python3`.

set -euo pipefail

echo "Starting Autonomous Agile Environment Provisioning..."

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

# ---------------------------------------------------------------------------
# 2 & 3. Configure ChromaDB MCP Server in Claude config
# ---------------------------------------------------------------------------

echo "Configuring MCP Server..."
mkdir -p "$HOME/.claude"

cat <<EOF > "$HOME/.claude/claude_mcp.json"
{
  "mcpServers": {
    "chroma": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-chroma"
      ]
    }
  }
}
EOF
echo "ChromaDB MCP configured in claude_mcp.json"

# ---------------------------------------------------------------------------
# 4. Initialize ChromaDB namespaces
# ---------------------------------------------------------------------------

echo "Establishing ADRs, Retrospectives, and Constitution namespaces..."

if ! python3 -c "import chromadb" &> /dev/null; then
    echo "Installing chromadb via pip (pre-Poetry bootstrap)..."
    python3 -m pip install -q chromadb
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "${SCRIPT_DIR}/init_chroma.py"

echo "Environment Provisioning Complete."
echo ""
echo "NEXT STEP: Run 'python3 scripts/seed_chroma.py' to seed governance memory."
echo "Once pyproject.toml is initialized, use 'poetry run python3' for all commands."
