"""Unit tests for the safe_error_msg() sanitization helper (ADV-036+044).

Tests follow TDD RED phase — all tests must fail before implementation.

Task: P5-T5.1 — Task Orchestration API Core
CONSTITUTION Priority 3: TDD — RED phase
"""

import pytest

pytestmark = pytest.mark.unit


class TestSafeErrorMsg:
    """Unit tests for safe_error_msg() in shared/errors.py."""

    def test_strips_unix_filesystem_path(self) -> None:
        """safe_error_msg() must strip UNIX-style filesystem paths."""
        from synth_engine.shared.errors import safe_error_msg

        msg = "Error reading /var/lib/postgresql/data/base/12345/16384"
        result = safe_error_msg(msg)
        assert "/var/lib" not in result

    def test_strips_absolute_path_with_extension(self) -> None:
        """safe_error_msg() must strip file paths with extensions."""
        from synth_engine.shared.errors import safe_error_msg

        msg = "Could not open /home/user/data/secret.csv for reading"
        result = safe_error_msg(msg)
        assert "/home/user" not in result

    def test_truncates_to_max_length(self) -> None:
        """safe_error_msg() must truncate messages to 500 characters."""
        from synth_engine.shared.errors import safe_error_msg

        long_msg = "A" * 1000
        result = safe_error_msg(long_msg)
        assert len(result) <= 500

    def test_does_not_truncate_short_message(self) -> None:
        """safe_error_msg() must not modify messages shorter than 500 chars."""
        from synth_engine.shared.errors import safe_error_msg

        msg = "Simple error message"
        result = safe_error_msg(msg)
        assert result == msg

    def test_strips_sqlalchemy_table_column_names(self) -> None:
        """safe_error_msg() must strip SQLAlchemy-style table.column references."""
        from synth_engine.shared.errors import safe_error_msg

        msg = 'column "synthesis_job.error_msg" does not exist'
        result = safe_error_msg(msg)
        assert "synthesis_job" not in result

    def test_returns_empty_string_for_empty_input(self) -> None:
        """safe_error_msg() must handle empty strings gracefully."""
        from synth_engine.shared.errors import safe_error_msg

        assert safe_error_msg("") == ""

    def test_preserves_safe_message_content(self) -> None:
        """safe_error_msg() must preserve non-sensitive content."""
        from synth_engine.shared.errors import safe_error_msg

        msg = "Job failed due to out-of-memory condition"
        result = safe_error_msg(msg)
        assert "out-of-memory" in result

    def test_strips_windows_style_path(self) -> None:
        """safe_error_msg() must strip Windows-style filesystem paths."""
        from synth_engine.shared.errors import safe_error_msg

        msg = r"File not found: C:\Users\admin\data\file.csv"
        result = safe_error_msg(msg)
        assert "C:\\Users" not in result

    def test_strips_multiple_paths_in_single_message(self) -> None:
        """safe_error_msg() must strip all paths in a multi-path message."""
        from synth_engine.shared.errors import safe_error_msg

        msg = "Cannot copy /src/file.py to /dst/file.py"
        result = safe_error_msg(msg)
        assert "/src/" not in result
        assert "/dst/" not in result

    def test_exact_max_length_boundary(self) -> None:
        """safe_error_msg() must keep exactly 500-char messages unchanged."""
        from synth_engine.shared.errors import safe_error_msg

        msg = "B" * 500
        result = safe_error_msg(msg)
        assert len(result) == 500
