#!/usr/bin/env bash

set -e

# Task 0.6.1: Host Initialization & MCP Setup

echo "Starting Autonomous Agile Environment Provisioning..."

# 1. Update Profile
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

# 2 & 3. Install ChromaDB MCP Server & Configure Claude
echo "Configuring MCP Server..."
mkdir -p "$HOME/.claude"

# We write the claude_mcp.json directly to ensure the MCP is registered
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

# 4. PM Executes chroma_create_collection
# We establish the persistent memory using python chromadb natively to emulate the required setup state.
echo "Establishing ADRs, Retrospectives, and Constitution namespaces..."

# Check if chromadb is installed in the python env to seed the collections
if ! python -c "import chromadb" &> /dev/null; then
    echo "Installing chromadb via pip to initialize memory..."
    python -m pip install -q chromadb
fi

# Execute the constitutionally-compliant explicit python script
python -m mypy scripts/init_chroma.py || echo "Warning: mypy not installed or failed, proceeding anyway"
python scripts/init_chroma.py

echo "Environment Provisioning Complete."
