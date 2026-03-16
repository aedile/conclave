"""Unit tests for HMAC-SHA256 signing primitives in synth_engine.shared.security.

These tests exercise the canonical import path and verify the low-level
contract of compute_hmac and verify_hmac directly, independent of
ModelArtifact serialisation.

Task: P8-T8.2 — Security Hardening (QA finding — direct primitive coverage)
"""

from __future__ import annotations

import pytest

from synth_engine.shared.security import (
    HMAC_DIGEST_SIZE,
    SecurityError,
    compute_hmac,
    verify_hmac,
)


def test_compute_hmac_returns_32_bytes() -> None:
    """compute_hmac must return exactly 32 bytes (HMAC-SHA256 digest size)."""
    key = b"a" * 32
    data = b"hello world"
    digest = compute_hmac(key, data)
    assert len(digest) == 32
    assert len(digest) == HMAC_DIGEST_SIZE


def test_compute_hmac_deterministic() -> None:
    """compute_hmac with the same key and data must always produce the same digest."""
    key = b"deterministic-key-32-bytes-exact"
    data = b"some payload bytes"
    digest_a = compute_hmac(key, data)
    digest_b = compute_hmac(key, data)
    assert digest_a == digest_b


def test_verify_hmac_true_on_correct_digest() -> None:
    """verify_hmac must return True when the digest matches key+data."""
    key = b"correct-key-32-bytes-padded-here"
    data = b"the data to authenticate"
    digest = compute_hmac(key, data)
    assert verify_hmac(key, data, digest) is True


def test_verify_hmac_false_on_wrong_key() -> None:
    """verify_hmac must return False (not raise) when the key is wrong."""
    correct_key = b"correct-key-32-bytes-padded-here"
    wrong_key = b"wrong---key-32-bytes-padded-here"
    data = b"the data to authenticate"
    digest = compute_hmac(correct_key, data)
    result = verify_hmac(wrong_key, data, digest)
    assert result is False


def test_verify_hmac_false_on_wrong_data() -> None:
    """verify_hmac must return False (not raise) when the data has been modified."""
    key = b"correct-key-32-bytes-padded-here"
    original_data = b"the original data"
    tampered_data = b"the tampered data"
    digest = compute_hmac(key, original_data)
    result = verify_hmac(key, tampered_data, digest)
    assert result is False


def test_security_error_importable_from_canonical_path() -> None:
    """SecurityError must be importable directly from synth_engine.shared.security."""
    assert issubclass(SecurityError, Exception)


pytestmark = pytest.mark.unit
