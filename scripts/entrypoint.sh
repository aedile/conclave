#!/bin/sh
# =============================================================================
# entrypoint.sh — Container entry point for the Conclave Engine
#
# Security model:
#   - tini (PID 1) calls this script, which calls su-exec.
#   - su-exec permanently drops from root (UID 0) to appuser (UID 1000)
#     before exec-ing the application CMD.
#   - Once exec'd, there is no way back to root inside the container.
#
# Usage (via Dockerfile ENTRYPOINT + CMD):
#   ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]
#   CMD        ["poetry", "run", "uvicorn", "..."]
# =============================================================================
set -eu

# The non-root user to execute as.  Matches Dockerfile useradd UID 1000.
APP_USER="${APP_USER:-appuser}"

echo "[entrypoint] Dropping privileges to ${APP_USER} and executing: $*"
exec su-exec "${APP_USER}" "$@"
