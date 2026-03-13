#!/usr/bin/env bash
# .claude/hooks/pre_tool_use.sh
#
# PreToolUse lifecycle hook.
#
# Executed by Claude Code before any Bash tool invocation. Detects whether
# the command starts a service or test runner that binds a localhost port,
# and sources the worktree's .env.local so the correct port block is active.
#
# This guarantees 4 parallel developer streams never collide on the same
# localhost port, even when running identical commands (pytest, uvicorn, npm).
#
# Environment variables injected by Claude Code:
#   CLAUDE_TOOL_INPUT   - The full bash command string about to be executed
#   WORKTREE_PATH       - Absolute path to the active worktree (if in a worktree)
#
# Behaviour:
#   - If WORKTREE_PATH is set and .env.local exists → source it, then exec the command
#   - If no worktree context → pass through unchanged (main workspace is unaffected)
#   - Intercept patterns: pytest, uvicorn, npm run, yarn, pnpm, python -m http.server
#
# Exit codes:
#   0 - Command may proceed (with or without env injection)

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Commands that bind ports and must have the worktree env injected
PORT_BINDING_PATTERNS=(
    "pytest"
    "uvicorn"
    "fastapi"
    "npm run"
    "yarn "
    "pnpm "
    "python -m http.server"
    "python -m pytest"
    "flask run"
    "gunicorn"
    "hypercorn"
    "celery"
    "huey"
)

# ---------------------------------------------------------------------------
# Early exit: no worktree context → nothing to do
# ---------------------------------------------------------------------------

if [[ -z "${WORKTREE_PATH:-}" ]]; then
    exit 0
fi

ENV_FILE="${WORKTREE_PATH}/.env.local"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "WARNING: Worktree detected (${WORKTREE_PATH}) but .env.local not found." \
         "Run worktree_create.sh to allocate ports." >&2
    exit 0
fi

# ---------------------------------------------------------------------------
# Check whether the incoming command matches a port-binding pattern
# ---------------------------------------------------------------------------

COMMAND="${CLAUDE_TOOL_INPUT:-}"
NEEDS_INJECTION=false

for pattern in "${PORT_BINDING_PATTERNS[@]}"; do
    if [[ "${COMMAND}" == *"${pattern}"* ]]; then
        NEEDS_INJECTION=true
        break
    fi
done

# ---------------------------------------------------------------------------
# Inject env if needed
# ---------------------------------------------------------------------------

if [[ "${NEEDS_INJECTION}" == "true" ]]; then
    echo "PreToolUse: sourcing ${ENV_FILE} for port-bound command: ${COMMAND:0:60}..."
    # shellcheck source=/dev/null
    set -a
    source "${ENV_FILE}"
    set +a
fi

exit 0
