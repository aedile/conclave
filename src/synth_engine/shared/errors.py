"""Shared error sanitization utilities for the Conclave Engine.

Provides :func:`safe_error_msg` to strip potentially sensitive information
(filesystem paths, SQL identifiers) from exception messages before they are
exposed via HTTP API responses or SSE events.

This addresses ADV-036 and ADV-044: raw exception messages from SQLAlchemy
and CTGAN must never be exposed to operators via SSE or API responses.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/`` or ``bootstrapper/``.

Task: P5-T5.1 — Task Orchestration API Core
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum length for a sanitized error message.
_MAX_ERROR_MSG_LENGTH: int = 500

#: Regex matching UNIX-style absolute paths: /one/or/more/segments
#: Matches e.g. /var/lib/data, /home/user/file.csv, /etc/passwd
_UNIX_PATH_RE: re.Pattern[str] = re.compile(r"/[a-zA-Z0-9_./-]+")

#: Regex matching Windows-style absolute paths: C:\path\to\file
#: Matches e.g. C:\Users\admin\data\file.csv
_WINDOWS_PATH_RE: re.Pattern[str] = re.compile(r"[A-Za-z]:\\[^\s]*")

#: Regex matching SQLAlchemy-style quoted table.column identifiers.
#: e.g. "synthesis_job.error_msg" or `table.column`
_SQL_IDENTIFIER_RE: re.Pattern[str] = re.compile(
    r'"[a-zA-Z_][a-zA-Z0-9_.]*\.[a-zA-Z_][a-zA-Z0-9_]*"'
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def safe_error_msg(msg: str) -> str:
    """Sanitize an error message for safe exposure via HTTP or SSE.

    Strips the following from the message:
    - UNIX-style filesystem paths (``/path/to/file``)
    - Windows-style filesystem paths (``C:\\path\\to\\file``)
    - SQLAlchemy-style quoted ``"table.column"`` identifiers

    The result is truncated to :data:`_MAX_ERROR_MSG_LENGTH` (500) characters.

    This function is the single point of sanitization for all error strings
    exposed via the REST API or SSE stream endpoints.  It addresses ADV-036
    (raw SQLAlchemy messages) and ADV-044 (raw CTGAN runtime error messages).

    Args:
        msg: Raw exception or error message string to sanitize.

    Returns:
        A sanitized, length-bounded error message safe for operator consumption.

    Examples:
        >>> safe_error_msg("Error at /var/lib/data/file.csv")
        'Error at [path]'
        >>> safe_error_msg('column "jobs.error_msg" missing')
        'column [identifier] missing'
    """
    if not msg:
        return msg

    # Strip Windows paths first (before UNIX pattern fires on slashes)
    result = _WINDOWS_PATH_RE.sub("[path]", msg)

    # Strip UNIX-style absolute paths
    result = _UNIX_PATH_RE.sub("[path]", result)

    # Strip SQLAlchemy quoted table.column identifiers
    result = _SQL_IDENTIFIER_RE.sub("[identifier]", result)

    # Truncate to maximum safe length
    return result[:_MAX_ERROR_MSG_LENGTH]
