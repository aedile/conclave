"""Air-Gapped Synthetic Data Generation Engine.

This package implements a Python Modular Monolith for generating privacy-preserving
synthetic datasets using Differential Privacy (DP-SGD) with deterministic
format-preserving masking rules.

Architecture:
    bootstrapper  — Main API, DI configuration, and global middleware.
    modules       — Logical subpackages enforcing strict module boundaries:
                    ingestion, masking, synthesizer, privacy.
    shared        — Cross-cutting utilities (audit logging, cryptography).

CONSTITUTION Priority 0: No PII or secrets are handled or stored in this package.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__: str = _pkg_version("conclave-engine")
except PackageNotFoundError:
    # Fallback for editable installs that have not been built/installed
    __version__ = "1.0.0"
