"""Pagila provisioning infrastructure tests (T54.1).

Validates that the Pagila dataset provisioning script and documentation are
present, correct, and secure. These are pure content-inspection tests; no
running PostgreSQL instance is required.

Attack/negative test cases (per spec-challenger and Rule 22):
  - Script uses HTTPS not HTTP (prevents MITM on download)
  - Script verifies SHA-256 checksums (prevents tampered files)
  - Script does not hardcode credentials (prevents secret exposure)
  - Script has strict error handling (set -e, pipefail)
  - Script has a cleanup trap on failure (no partial state left)
  - Script is shellcheck-clean (no shell scripting bugs)

Feature test cases:
  - Script file exists and is executable
  - Script validates row counts post-load
  - Script checks PostgreSQL version >= 16
  - Script is idempotent (DROP DATABASE IF EXISTS)
  - README documents source and license
  - README lists the 5-table validation subset

Task: P54-T54.1 — Pagila Dataset Provisioning
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Repository root helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "provision_pagila.sh"
README_PATH = REPO_ROOT / "sample_data" / "pagila" / "README.md"


# ===========================================================================
# ATTACK TESTS — Negative / security cases (Rule 22, committed first)
# ===========================================================================


class TestProvisionScriptSecurity:
    """Security properties of the provisioning script."""

    def test_provision_script_is_shellcheck_clean(self) -> None:
        """provision_pagila.sh must pass shellcheck with no errors or warnings.

        shellcheck catches common shell scripting pitfalls including
        unquoted variables, word-splitting, and unsafe constructs.
        """
        assert SCRIPT_PATH.exists(), (
            f"provision_pagila.sh not found at {SCRIPT_PATH}. "
            "T54.1 requires the script to be created."
        )
        result = subprocess.run(
            ["shellcheck", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"shellcheck found issues in provision_pagila.sh:\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )

    def test_provision_script_uses_https_not_http(self) -> None:
        """provision_pagila.sh must not use plain HTTP URLs.

        All downloads must use HTTPS to prevent MITM attacks during
        Pagila SQL file retrieval. Plain 'http://' URLs are forbidden.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        # Find any http:// occurrences that are not https://
        import re

        plain_http_matches = re.findall(r'\bhttp://[^\s"\']+', content)
        assert plain_http_matches == [], (
            f"provision_pagila.sh contains plain HTTP URLs (must use HTTPS): {plain_http_matches}"
        )

    def test_provision_script_has_checksum_verification(self) -> None:
        """provision_pagila.sh must verify SHA-256 checksums of downloaded files.

        Pinned checksums guard against tampered or corrupted SQL files.
        The script must perform sha256 verification before loading any SQL.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        has_sha256 = "sha256" in content.lower() or "sha256sum" in content or "shasum" in content
        assert has_sha256, (
            "provision_pagila.sh must verify SHA-256 checksums of downloaded files. "
            "Pin the expected checksums in the script to detect tampering."
        )

    def test_provision_script_does_not_hardcode_credentials(self) -> None:
        """provision_pagila.sh must not hardcode passwords or credentials.

        Database credentials must be read from environment variables
        ($PGPASSWORD, $PGUSER, $PGHOST, $PGPORT), never hardcoded in the script.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        # Patterns that indicate hardcoded credentials
        import re

        # Look for password= or PASSWORD= followed by a literal value (not a variable ref)
        hardcoded_patterns = [
            r'password\s*=\s*["\'][^"\']+["\']',
            r'PASSWORD\s*=\s*["\'][^"\']+["\']',
            r'PGPASSWORD\s*=\s*["\'][^"\'$][^"\']*["\']',
        ]
        violations: list[str] = []
        for pattern in hardcoded_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            violations.extend(matches)
        assert violations == [], (
            f"provision_pagila.sh appears to hardcode credentials: {violations}. "
            "Use $PGPASSWORD, $PGUSER environment variables instead."
        )

    def test_provision_script_has_set_e_and_pipefail(self) -> None:
        """provision_pagila.sh must use 'set -euo pipefail' for strict error handling.

        Without 'set -e', the script would silently continue on command failures.
        Without 'pipefail', errors in piped commands go undetected.
        Both are required for safe, predictable shell scripting.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        has_set_e = "set -e" in content or "set -euo" in content or "set -eu" in content
        has_pipefail = "pipefail" in content
        assert has_set_e, (
            "provision_pagila.sh must use 'set -e' (or 'set -euo pipefail'). "
            "Without it, the script silently continues after command failures."
        )
        assert has_pipefail, (
            "provision_pagila.sh must use 'pipefail' (via 'set -euo pipefail'). "
            "Without it, errors in piped commands (e.g. curl | psql) go undetected."
        )

    def test_provision_script_cleans_up_on_failure(self) -> None:
        """provision_pagila.sh must have a trap/cleanup handler on failure.

        If the download or load fails, the script must remove any partially
        downloaded SQL files to avoid stale/corrupted state. A 'trap ... ERR'
        or 'trap ... EXIT' handler with cleanup logic is required.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        has_trap = "trap" in content
        has_cleanup = "cleanup" in content.lower() or "rm " in content or "rm\t" in content
        assert has_trap, (
            "provision_pagila.sh must define a 'trap' handler for cleanup on failure. "
            "Use 'trap cleanup ERR' or 'trap cleanup EXIT' to remove partial downloads."
        )
        assert has_cleanup, (
            "provision_pagila.sh must have cleanup logic (rm of downloaded SQL files). "
            "Partial downloads must be removed on script failure."
        )


# ===========================================================================
# FEATURE TESTS — Positive / correctness cases
# ===========================================================================


class TestProvisionScriptFeatures:
    """Feature correctness properties of the provisioning script."""

    def test_provision_script_exists_and_is_executable(self) -> None:
        """provision_pagila.sh must exist in scripts/ and have execute permission.

        The script must be executable so operators can run it directly
        without 'bash scripts/provision_pagila.sh'.
        """
        assert SCRIPT_PATH.exists(), (
            f"provision_pagila.sh not found at {SCRIPT_PATH}. "
            "Create scripts/provision_pagila.sh as part of T54.1."
        )
        assert SCRIPT_PATH.stat().st_mode & 0o111 != 0, (
            f"provision_pagila.sh at {SCRIPT_PATH} is not executable. "
            "Run: chmod +x scripts/provision_pagila.sh"
        )

    def test_provision_script_validates_row_counts(self) -> None:
        """provision_pagila.sh must validate post-load row counts.

        After loading Pagila, the script must assert:
          - customer table has >= 500 rows
          - rental table has >= 15000 rows

        This guards against partial loads or corrupt SQL files.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        has_customer_check = "500" in content and "customer" in content.lower()
        has_rental_check = "15000" in content and "rental" in content.lower()
        assert has_customer_check, (
            "provision_pagila.sh must validate that the customer table has >= 500 rows. "
            "Expected to find '500' and 'customer' in the script."
        )
        assert has_rental_check, (
            "provision_pagila.sh must validate that the rental table has >= 15000 rows. "
            "Expected to find '15000' and 'rental' in the script."
        )

    def test_provision_script_checks_postgres_version(self) -> None:
        """provision_pagila.sh must check that PostgreSQL version is >= 16.

        Pagila uses features present in PostgreSQL 16+. The script must
        detect and reject older PostgreSQL server versions with a clear
        error message.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        has_version_check = (
            "server_version_num" in content
            or "pg_version" in content.lower()
            or ("version" in content.lower() and "16" in content)
        )
        assert has_version_check, (
            "provision_pagila.sh must check that PostgreSQL version >= 16. "
            "Use 'SHOW server_version_num' or 'SELECT current_setting(...)' to detect version."
        )

    def test_provision_script_is_idempotent(self) -> None:
        """provision_pagila.sh must use DROP DATABASE IF EXISTS for idempotency.

        Running the script multiple times must produce the same end state
        without errors. The script must drop and recreate the pagila database
        using 'DROP DATABASE IF EXISTS' to handle the re-run case.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        has_drop_if_exists = (
            "DROP DATABASE IF EXISTS" in content or "drop database if exists" in content.lower()
        )
        assert has_drop_if_exists, (
            "provision_pagila.sh must use 'DROP DATABASE IF EXISTS pagila' for idempotency. "
            "This allows safe re-runs without manual cleanup."
        )

    def test_provision_script_downloads_from_official_repo(self) -> None:
        """provision_pagila.sh must download from the official Pagila GitHub repo.

        Downloads must come from https://github.com/devrimgunduz/pagila
        or its raw.githubusercontent.com equivalent, not mirrors or forks.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        has_official_source = (
            "devrimgunduz/pagila" in content
            or "raw.githubusercontent.com/devrimgunduz/pagila" in content
        )
        assert has_official_source, (
            "provision_pagila.sh must download from the official Pagila repo: "
            "https://github.com/devrimgunduz/pagila. "
            "Mirrors and forks are not acceptable."
        )

    def test_provision_script_uses_env_vars_for_connection(self) -> None:
        """provision_pagila.sh must use $PGHOST, $PGPORT, $PGUSER, $PGPASSWORD.

        Database connection parameters must be read from standard PostgreSQL
        environment variables to support different deployment environments
        without modifying the script.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        assert "PGHOST" in content, (
            "provision_pagila.sh must use $PGHOST for the database host. "
            "Do not hardcode localhost or any specific hostname."
        )
        assert "PGUSER" in content, "provision_pagila.sh must use $PGUSER for the database user."
        assert "PGPASSWORD" in content, (
            "provision_pagila.sh must reference $PGPASSWORD for authentication."
        )


# ===========================================================================
# FEATURE TESTS — README documentation
# ===========================================================================


class TestPagilaReadme:
    """Documentation requirements for sample_data/pagila/README.md."""

    def test_pagila_readme_exists(self) -> None:
        """sample_data/pagila/README.md must exist.

        The README documents the dataset source, license, and provisioning
        instructions for operators.
        """
        assert README_PATH.exists(), (
            f"sample_data/pagila/README.md not found at {README_PATH}. Create it as part of T54.1."
        )

    def test_pagila_readme_documents_source_and_license(self) -> None:
        """README must document the dataset source URL and PostgreSQL License.

        Operators need to know where the data came from and under what
        license it can be used.
        """
        assert README_PATH.exists(), f"sample_data/pagila/README.md not found at {README_PATH}."
        content = README_PATH.read_text(encoding="utf-8")
        has_source = "devrimgunduz/pagila" in content or "github.com/devrimgunduz" in content
        has_license = "PostgreSQL License" in content or "postgresql license" in content.lower()
        assert has_source, (
            "sample_data/pagila/README.md must document the dataset source URL: "
            "https://github.com/devrimgunduz/pagila"
        )
        assert has_license, (
            "sample_data/pagila/README.md must state the PostgreSQL License. "
            "The Pagila dataset is released under the PostgreSQL License."
        )

    def test_pagila_readme_lists_validation_subset_tables(self) -> None:
        """README must list all 5 tables in the validation subset.

        The 5-table subset used for E2E synthesis validation must be
        documented: customer, address, rental, inventory, film.
        """
        assert README_PATH.exists(), f"sample_data/pagila/README.md not found at {README_PATH}."
        content = README_PATH.read_text(encoding="utf-8")
        required_tables = ["customer", "address", "rental", "inventory", "film"]
        missing = [t for t in required_tables if t not in content.lower()]
        assert missing == [], (
            f"sample_data/pagila/README.md is missing validation subset tables: {missing}. "
            "All 5 tables (customer, address, rental, inventory, film) must be listed."
        )

    def test_pagila_readme_documents_provisioning_command(self) -> None:
        """README must document how to provision using scripts/provision_pagila.sh.

        Operators must be able to find the provisioning command in the README
        without reading the script itself.
        """
        assert README_PATH.exists(), f"sample_data/pagila/README.md not found at {README_PATH}."
        content = README_PATH.read_text(encoding="utf-8")
        has_script_ref = "provision_pagila.sh" in content
        assert has_script_ref, (
            "sample_data/pagila/README.md must reference scripts/provision_pagila.sh "
            "with the provisioning command."
        )

    def test_pagila_readme_has_table_list_with_row_counts(self) -> None:
        """README must include a table list with approximate row counts.

        Operators should be able to quickly understand the dataset size
        without loading it.
        """
        assert README_PATH.exists(), f"sample_data/pagila/README.md not found at {README_PATH}."
        content = README_PATH.read_text(encoding="utf-8")
        # Check for at least one row count figure (approximate counts)
        import re

        has_row_count = bool(re.search(r"\b\d{3,}\b", content))
        assert has_row_count, (
            "sample_data/pagila/README.md must list approximate row counts for tables. "
            "Include at least one numeric count (e.g., 599, 16044, 16049, 1000, 4581)."
        )


# ===========================================================================
# ADDITIONAL NEGATIVE TESTS — Edge cases for script robustness
# ===========================================================================


class TestProvisionScriptEdgeCases:
    """Edge case and robustness tests for the provisioning script."""

    def test_provision_script_has_shebang(self) -> None:
        """provision_pagila.sh must start with a bash shebang line.

        The shebang must reference bash explicitly (not /bin/sh) since the
        script uses bash-specific features.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        first_line = content.splitlines()[0] if content.splitlines() else ""
        assert first_line.startswith("#!/"), (
            "provision_pagila.sh must start with a shebang line (e.g., #!/usr/bin/env bash)."
        )
        assert "bash" in first_line, (
            f"provision_pagila.sh shebang must reference bash, got: '{first_line}'. "
            "Use '#!/usr/bin/env bash' not '#!/bin/sh' as the script uses bash features."
        )

    def test_provision_script_validates_fk_constraints(self) -> None:
        """provision_pagila.sh must validate foreign key constraints post-load.

        After loading schema and data, the script must verify that FK
        constraints are satisfied (no orphaned rows). This detects partial
        or corrupted data loads.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        content = SCRIPT_PATH.read_text(encoding="utf-8")
        has_fk_check = (
            "foreign key" in content.lower()
            or "fk" in content.lower()
            or "constraint" in content.lower()
            or "pg_constraint" in content
        )
        assert has_fk_check, (
            "provision_pagila.sh must validate FK constraints post-load. "
            "Query pg_constraint or run a test join to detect orphaned rows."
        )

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="shellcheck not available on Windows CI",
    )
    def test_provision_script_no_bashisms_flagged_by_shellcheck(self) -> None:
        """shellcheck must not flag any SC2006 (backtick) or SC2086 (unquoted var) issues.

        These are the two most common shell scripting errors that lead to
        word-splitting bugs and security issues.
        """
        assert SCRIPT_PATH.exists(), f"provision_pagila.sh not found at {SCRIPT_PATH}."
        result = subprocess.run(
            ["shellcheck", "--format=gcc", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        # Extract only SC2006 and SC2086 warnings
        import re

        critical_warnings = re.findall(r"SC2006|SC2086", result.stdout + result.stderr)
        assert critical_warnings == [], (
            f"shellcheck found critical warnings (SC2006=backtick, SC2086=unquoted) "
            f"in provision_pagila.sh: {critical_warnings}\n"
            f"Full output:\n{result.stdout}"
        )
