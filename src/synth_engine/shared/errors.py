"""Shared error sanitization utilities for the Conclave Engine.

Provides :func:`safe_error_msg` to strip potentially sensitive information
(filesystem paths, SQL identifiers, Python module paths) from exception
messages before they are exposed via HTTP API responses or SSE events.

This addresses ADV-036 and ADV-044: raw exception messages from SQLAlchemy
and CTGAN must never be exposed to operators via SSE or API responses.

HTTP-safety classification for exception types
-----------------------------------------------
The following exception types are classified as **HTTP-safe** — their
sanitized messages may appear in HTTP 4xx/5xx response bodies:

- :exc:`~synth_engine.shared.exceptions.BudgetExhaustionError` — operator
  needs to know why the job failed so they can request a budget refresh.
- :exc:`~synth_engine.shared.exceptions.OOMGuardrailError` — operator needs
  the reduction factor to resize their dataset.
- :exc:`~synth_engine.shared.exceptions.VaultSealedError` — operator needs to
  know to call ``POST /unseal`` before retrying.

The following are **logged-only** and must NOT appear in HTTP response bodies:

- :exc:`~synth_engine.shared.exceptions.PrivilegeEscalationError` — may
  contain database role names or privilege details.
- :exc:`~synth_engine.shared.exceptions.ArtifactTamperingError` — may contain
  artifact paths or signing-key hints.

All error messages passed to HTTP responses MUST be sanitized through
:func:`safe_error_msg` before exposure.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/`` or ``bootstrapper/``.

Task: P5-T5.1 — Task Orchestration API Core
Task: P26-T26.2 — Add Python module path stripping (AC5)
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

#: Regex matching Python dotted module paths rooted at ``synth_engine``.
#: Strips fully-qualified exception names such as:
#:   synth_engine.modules.privacy.dp_engine.BudgetExhaustionError
#:   synth_engine.shared.exceptions.SynthEngineError
#:   synth_engine.bootstrapper.errors.RFC7807Middleware
#: The pattern matches the dotted prefix up to (but not including) any
#: trailing colon, space, or end-of-string so that a human-readable suffix
#: like ": budget gone" is preserved.
_MODULE_PATH_RE: re.Pattern[str] = re.compile(
    r"synth_engine(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+(?=\s*[:(\s]|$)"
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
    - Python dotted module paths rooted at ``synth_engine``
      (e.g. ``synth_engine.modules.privacy.dp_engine.BudgetExhaustionError``)

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
        >>> safe_error_msg("synth_engine.modules.privacy.dp_engine.BudgetExhaustionError: out")
        '[module]: out'
    """
    if not msg:
        return msg

    # Strip Windows paths first (before UNIX pattern fires on slashes)
    result = _WINDOWS_PATH_RE.sub("[path]", msg)

    # Strip UNIX-style absolute paths
    result = _UNIX_PATH_RE.sub("[path]", result)

    # Strip SQLAlchemy quoted table.column identifiers
    result = _SQL_IDENTIFIER_RE.sub("[identifier]", result)

    # Strip Python module paths rooted at synth_engine
    result = _MODULE_PATH_RE.sub("[module]", result)

    # Truncate to maximum safe length
    return result[:_MAX_ERROR_MSG_LENGTH]
