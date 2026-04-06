"""Integration test suite configuration.

Sets process-level defaults required for tests that rely on pass-through
authentication mode (no JWT secret configured).

Pass-through mode was made an explicit opt-in by P79-F12.  Tests that call
``create_app()`` without configuring ``JWT_SECRET_KEY`` must set
``CONCLAVE_PASS_THROUGH_ENABLED=true`` or every unauthenticated request
will return 401 with an error directing operators to set the flag.

``os.environ.setdefault`` is used so this module-level default can be
overridden by individual tests that need to test the disabled-pass-through
path (e.g. ``test_tenant_isolation.py`` uses ``monkeypatch.delenv``).

Tests that explicitly configure ``JWT_SECRET_KEY`` are unaffected — the
auth middleware uses real JWT verification when a secret is present,
regardless of this flag.
"""

from __future__ import annotations

import os

# Enable pass-through mode for the integration test suite.
# Individual tests that need to verify pass-through-disabled behaviour
# (e.g. TestPassThroughRequiresExplicitOptIn) must call:
#   monkeypatch.delenv("CONCLAVE_PASS_THROUGH_ENABLED", raising=False)
# which will correctly remove this default for the duration of that test.
os.environ.setdefault("CONCLAVE_PASS_THROUGH_ENABLED", "true")
