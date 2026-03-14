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
- Error messages NEVER expose embedded credentials; ``_sanitize_url`` strips
  userinfo before interpolation (CONSTITUTION Priority 0).

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


def _sanitize_url(url: str) -> str:
    """Return the URL with credentials stripped for safe inclusion in error messages.

    Replaces the userinfo component (user:password@) with an empty string so
    that exception messages never expose auth material.

    Args:
        url: A raw connection URL, potentially containing credentials.

    Returns:
        A credential-free representation suitable for log output, e.g.
        ``postgresql+psycopg2://host:5432/db``.
    """
    try:
        parsed = urlparse(url)
        # Reconstruct netloc without userinfo: just hostname[:port]
        host_part = parsed.hostname or ""
        if parsed.port:
            host_part = f"{host_part}:{parsed.port}"
        sanitized = parsed._replace(netloc=host_part)
        return sanitized.geturl()
    except Exception:  # Broad catch: urlparse internals may raise on exotic inputs
        return "<unparseable URL>"


def validate_connection_string(url: str) -> None:
    """Raise ValueError if the connection string is not safe for ingestion.

    Accepts local connections (loopback interface) without SSL. Requires
    ``sslmode=require`` for all remote hosts. Rejects malformed or
    non-PostgreSQL URLs.

    Args:
        url: A SQLAlchemy-style PostgreSQL connection URL, e.g.
            ``postgresql+psycopg2://<user>:<password>@host:5432/db?sslmode=require``.

    Raises:
        ValueError: If the URL is malformed, uses an unsupported scheme, or
            connects to a remote host without ``sslmode=require``.
    """
    parsed = urlparse(url)

    if not parsed.scheme or not parsed.hostname:
        raise ValueError(
            f"Invalid connection URL — missing scheme or hostname: {_sanitize_url(url)!r}"
        )

    if parsed.scheme not in _VALID_SCHEMES:
        raise ValueError(
            f"Invalid connection URL — unsupported scheme {parsed.scheme!r}: {_sanitize_url(url)!r}"
        )

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
