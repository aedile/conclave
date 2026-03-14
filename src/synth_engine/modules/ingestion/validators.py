"""Connection string validation for the ingestion module.

Enforces SSL requirements for non-local PostgreSQL connections to prevent
credentials and data from travelling over unencrypted network paths.

Security context
----------------
- Local hosts (``localhost``, ``127.0.0.1``, ``::1``) are exempt from SSL
  enforcement because they traverse only the loopback interface.
- All remote hosts MUST specify ``sslmode=require`` in the connection URL
  query parameters.
- Malformed or non-PostgreSQL URLs are rejected immediately.

CONSTITUTION Priority 0: Security — SSL enforcement is mandatory for remote connections.
Task: P3-T3.1 — Target Ingestion Engine
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

_VALID_SCHEMES: frozenset[str] = frozenset(
    {
        "postgresql",
        "postgresql+psycopg2",
        "postgresql+asyncpg",
        "postgres",
        "postgres+psycopg2",
    }
)


def validate_connection_string(url: str) -> None:
    """Raise ValueError if the connection string is not safe for ingestion.

    Accepts local connections (loopback interface) without SSL. Requires
    ``sslmode=require`` for all remote hosts. Rejects malformed or
    non-PostgreSQL URLs.

    Args:
        url: A SQLAlchemy-style PostgreSQL connection URL, e.g.
            ``postgresql+psycopg2://user:pass@host:5432/db?sslmode=require``.

    Raises:
        ValueError: If the URL is malformed, uses an unsupported scheme, or
            connects to a remote host without ``sslmode=require``.
    """
    parsed = urlparse(url)

    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"Invalid connection URL — missing scheme or hostname: {url!r}")

    if parsed.scheme not in _VALID_SCHEMES:
        raise ValueError(f"Invalid connection URL — unsupported scheme {parsed.scheme!r}: {url!r}")

    host = parsed.hostname.lower()
    if host in _LOCAL_HOSTS:
        # Loopback connections are exempt from SSL enforcement.
        return

    # Remote host: require sslmode=require in query parameters.
    query_params = parse_qs(parsed.query)
    ssl_modes = query_params.get("sslmode", [])
    if "require" not in ssl_modes:
        raise ValueError(
            f"Remote host {host!r} requires sslmode=require in the connection URL. "
            f"Add '?sslmode=require' to prevent unencrypted data transmission."
        )
