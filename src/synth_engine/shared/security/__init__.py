"""Shared security utilities for the Conclave Engine.

This sub-package provides cross-cutting security primitives used by two
or more modules, currently:

- :mod:`synth_engine.shared.security.ale` — Application-Level Encryption
  (Fernet-based ``EncryptedString`` SQLAlchemy TypeDecorator).
"""
