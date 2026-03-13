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

__version__ = "0.1.0"
