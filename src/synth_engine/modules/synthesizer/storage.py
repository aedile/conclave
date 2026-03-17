"""Ephemeral storage client for the synthesizer module.

Provides ``EphemeralStorageClient``, which writes and reads per-table Parquet
files to/from an ephemeral MinIO bucket (``synth-ephemeral``).  The bucket is
backed by ``tmpfs`` in Docker Compose so all training artifacts evaporate when
the container stops — a privacy mandate: no training artifacts survive
termination.

Data flow
---------
SubsettingEngine output → Parquet files in ephemeral storage → synthesizer
reads Parquet.  The synthesizer does **not** re-read from PostgreSQL directly.
This preserves the module boundary: synthesizer depends on files, not ingestion.

Device selection
----------------
``FORCE_CPU=true`` in the environment forces CPU mode regardless of hardware.
``torch.cuda.is_available()`` result is logged at INFO on every call to
:func:`_log_device_selection`.

Testability
-----------
``EphemeralStorageClient`` accepts a ``StorageBackend`` Protocol instance as a
constructor parameter.  Unit tests inject an ``InMemoryBackend``; production
code injects ``MinioStorageBackend``.  No MinIO process is required for unit
tests.

Task: P4-T4.1 — GPU Passthrough & Ephemeral Storage
ADR: ADR-0017 (CTGAN + Opacus; ephemeral Parquet store between subsetting and
     synthesis steps)
"""

from __future__ import annotations

import io
import logging
import os
from typing import Protocol, cast, runtime_checkable

import pandas as pd

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Environment variable name for forcing CPU mode.
#: Set to ``"true"`` (case-insensitive) to bypass GPU detection.
FORCE_CPU_ENV_VAR: str = "FORCE_CPU"


# ---------------------------------------------------------------------------
# Device selection
# ---------------------------------------------------------------------------


def _log_device_selection() -> str:
    """Determine the compute device and log the selection at INFO level.

    Reads ``FORCE_CPU`` from the environment on every call (not cached at
    import time) so tests can use ``monkeypatch.setenv`` safely.

    The ``torch`` import is deferred to this function body so that environments
    installing only the default dependency group (without ``--with synthesizer``)
    do not encounter ``ModuleNotFoundError`` at import time.  This mirrors the
    boto3 deferred-import pattern used in ``MinioStorageBackend.__init__``.

    Returns:
        ``"cpu"`` if ``FORCE_CPU`` is set to a truthy value or if
        ``torch.cuda.is_available()`` returns ``False``.
        ``"cuda"`` if CUDA hardware is detected and ``FORCE_CPU`` is not set.
    """
    import torch  # deferred import: only needed when device selection is invoked

    force_cpu = os.environ.get(FORCE_CPU_ENV_VAR, "").strip().lower() in {
        "1",
        "true",
        "yes",
    }

    if force_cpu:
        _logger.info("FORCE_CPU=true — using CPU device (GPU passthrough disabled).")
        return "cpu"

    if torch.cuda.is_available():
        _logger.info("CUDA GPU detected — using cuda device for synthesis.")
        return "cuda"

    _logger.info(
        "No CUDA GPU detected — using CPU device for synthesis. "
        "Set FORCE_CPU=true to suppress this message in CPU-only environments."
    )
    return "cpu"


# ---------------------------------------------------------------------------
# Storage backend Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol for ephemeral storage backends.

    Implementors must provide ``put`` and ``get`` methods.  The concrete
    ``MinioStorageBackend`` uses ``boto3`` to talk to a MinIO instance;
    unit tests inject an ``InMemoryBackend`` instead.
    """

    def put(self, bucket: str, key: str, data: bytes) -> None:
        """Store raw bytes under ``bucket/key``.

        Args:
            bucket: Target bucket name.
            key: Object key (e.g. ``"table_customers.parquet"``).
            data: Raw bytes to store.
        """
        ...  # pragma: no cover — abstract StorageBackend stub; body never executed

    def get(self, bucket: str, key: str) -> bytes:
        """Retrieve raw bytes from ``bucket/key``.

        Args:
            bucket: Source bucket name.
            key: Object key.

        Returns:
            Raw bytes previously stored at that key.

        Raises:
            KeyError: If the key does not exist.
        """
        ...  # pragma: no cover — abstract StorageBackend stub; body never executed


# ---------------------------------------------------------------------------
# Concrete backend — MinIO via boto3
# ---------------------------------------------------------------------------


class MinioStorageBackend:
    """S3-compatible MinIO backend using ``boto3``.

    This class is the production concrete implementation of
    :class:`StorageBackend`.  Unit tests do **not** instantiate this class —
    they use the injectable ``backend`` parameter on
    :class:`EphemeralStorageClient` to pass an ``InMemoryBackend`` instead.

    Args:
        endpoint_url: MinIO S3 API endpoint (e.g. ``"http://minio:9000"``).
            Must be an ``http://`` or ``https://`` URL.
        access_key: MinIO access key.  Must be non-empty.
        secret_key: MinIO secret key.  Must be non-empty.

    Raises:
        ValueError: If ``endpoint_url`` is not a valid http(s) URL, or if
            ``access_key`` or ``secret_key`` are empty strings.
    """

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        """Initialise the MinIO boto3 client.

        Args:
            endpoint_url: S3-compatible endpoint URL.  Must start with
                ``http://`` or ``https://``.
            access_key: Access key ID for authentication.  Must be non-empty.
            secret_key: Secret access key for authentication.  Must be non-empty.

        Raises:
            ValueError: If ``endpoint_url`` is not a valid http(s) URL, or if
                ``access_key`` or ``secret_key`` are empty strings.
        """
        if not endpoint_url or not endpoint_url.startswith(("http://", "https://")):
            raise ValueError(
                f"endpoint_url must be an http:// or https:// URL; got: {endpoint_url!r}"
            )
        if not access_key:
            raise ValueError("access_key must be a non-empty string")
        if not secret_key:
            raise ValueError("secret_key must be a non-empty string")

        import boto3  # deferred import: only needed when MinIO backend is used

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def __repr__(self) -> str:
        """Return a redacted representation that never exposes credentials.

        Returns:
            A string representation with endpoint_url and access_key redacted
            to prevent accidental credential leakage in logs or tracebacks.
        """
        return "MinioStorageBackend(endpoint_url=<redacted>, access_key=<redacted>)"

    def put(self, bucket: str, key: str, data: bytes) -> None:
        """Upload raw bytes to MinIO.

        Args:
            bucket: Target bucket name.
            key: Object key.
            data: Raw bytes to upload.
        """
        self._client.put_object(Bucket=bucket, Key=key, Body=data)

    def get(self, bucket: str, key: str) -> bytes:
        """Download raw bytes from MinIO.

        Args:
            bucket: Source bucket name.
            key: Object key.

        Returns:
            Raw bytes retrieved from the MinIO object.

        Raises:
            KeyError: If the object does not exist (wraps boto3 ClientError).
        """
        import botocore.exceptions  # deferred import

        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            return cast(bytes, response["Body"].read())
        except botocore.exceptions.ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("NoSuchKey", "404"):
                raise KeyError(f"{bucket}/{key}") from exc
            raise  # pragma: no cover — non-NoSuchKey ClientError; requires real S3/Minio for test


# ---------------------------------------------------------------------------
# EphemeralStorageClient
# ---------------------------------------------------------------------------


class EphemeralStorageClient:
    """Client for reading and writing Parquet files to ephemeral storage.

    Storage is ephemeral by design: the backing MinIO bucket uses ``tmpfs``
    in Docker Compose, so all data evaporates when the container stops.  This
    is a privacy mandate: no training artifacts may survive container
    termination.

    The client is injectable — pass a ``StorageBackend`` instance at
    construction time.  Unit tests pass an ``InMemoryBackend``; production
    wiring in ``bootstrapper/`` passes a ``MinioStorageBackend``.

    Args:
        bucket: Bucket name to use for all operations (e.g.
            ``"synth-ephemeral"``).
        backend: Storage backend implementation.  Must satisfy the
            :class:`StorageBackend` Protocol.

    Example::

        # Production wiring (bootstrapper/)
        backend = MinioStorageBackend(
            endpoint_url="http://minio-ephemeral:9000",
            access_key="...",
            secret_key="...",
        )
        client = EphemeralStorageClient(bucket="synth-ephemeral", backend=backend)

        # Unit test wiring
        backend = InMemoryBackend()
        client = EphemeralStorageClient(bucket="synth-ephemeral", backend=backend)
    """

    def __init__(self, bucket: str, backend: StorageBackend) -> None:
        """Initialise the client with a bucket name and backend.

        Args:
            bucket: Bucket name for all operations.
            backend: Concrete storage backend (injectable for testability).
        """
        self._bucket = bucket
        self._backend = backend

    def upload_parquet(self, key: str, df: pd.DataFrame) -> None:
        """Serialise a DataFrame to Parquet and upload it.

        Args:
            key: Object key (e.g. ``"table_customers.parquet"``).
            df: DataFrame to serialise.  Column types must be Parquet-compatible.
        """
        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False, engine="pyarrow")
        self._backend.put(self._bucket, key, buffer.getvalue())

    def download_parquet(self, key: str) -> pd.DataFrame:
        """Download a Parquet object and deserialise it to a DataFrame.

        Args:
            key: Object key previously uploaded via :meth:`upload_parquet`.

        Returns:
            DataFrame deserialised from the stored Parquet bytes.

        Raises:
            KeyError: If the key does not exist in the backend.
        """
        data = self._backend.get(self._bucket, key)
        return pd.read_parquet(io.BytesIO(data), engine="pyarrow")
