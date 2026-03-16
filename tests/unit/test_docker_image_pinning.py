"""Unit tests for Docker image SHA-256 digest pinning.

These tests inspect Dockerfile and docker-compose.yml as text files to verify
that all base image references are pinned to SHA-256 digests for supply chain
security (ADV-014).

Tests are file-inspection tests: they read the configuration files and assert
structural invariants. They do NOT require a running Docker daemon.

ADV-015 (resolved P18-T18.2):
    The phantom tag ``pgbouncer/pgbouncer:1.23.1`` was replaced with
    ``edoburu/pgbouncer:v1.23.1-p3`` and SHA-256 pinned. See ADR-0031.
    All 9 external service images in docker-compose.yml are now pinned.
"""

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHA256_PATTERN = re.compile(r"@sha256:[a-f0-9]{64}")


def _extract_from_lines(dockerfile_text: str) -> list[str]:
    """Return all FROM lines from a Dockerfile.

    Args:
        dockerfile_text: Full text of the Dockerfile.

    Returns:
        List of FROM directive lines (stripped).
    """
    return [
        line.strip()
        for line in dockerfile_text.splitlines()
        if line.strip().upper().startswith("FROM")
    ]


def _extract_image_lines(compose_text: str) -> list[str]:
    """Return all ``image:`` lines from a docker-compose.yml.

    Excludes:
    - ``conclave-engine:latest`` — locally built image, no upstream registry.

    All external service images (including edoburu/pgbouncer since ADV-015
    resolution in P18-T18.2) are now SHA-256 pinned and included in checks.

    Args:
        compose_text: Full text of the docker-compose.yml.

    Returns:
        List of image directive lines (stripped) eligible for pinning checks.
    """
    return [
        line.strip()
        for line in compose_text.splitlines()
        if line.strip().startswith("image:") and "conclave-engine" not in line
    ]


# ---------------------------------------------------------------------------
# Dockerfile tests
# ---------------------------------------------------------------------------


class TestDockerfileSHA256Pinning:
    """Verify Dockerfile FROM lines are pinned to SHA-256 digests."""

    def test_dockerfile_exists(self) -> None:
        """Dockerfile must exist at repository root."""
        assert DOCKERFILE.exists(), f"Dockerfile not found at {DOCKERFILE}"

    def test_all_from_lines_have_sha256_digest(self) -> None:
        """Every FROM line in the Dockerfile must contain @sha256:<64-hex-chars>.

        This covers node:20-alpine (stage 1), python:3.14-slim (stages 2 & 3).
        """
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        assert from_lines, "No FROM lines found in Dockerfile"

        for line in from_lines:
            assert _SHA256_PATTERN.search(line), (
                f"FROM line is not SHA-256 pinned: {line!r}\n"
                "All FROM lines must use the form: image:tag@sha256:<digest>"
            )

    def test_no_adv014_todo_comments_remain(self) -> None:
        """No TODO(ADV-014) comments should remain — debt must be resolved."""
        content = DOCKERFILE.read_text()
        assert "TODO(ADV-014)" not in content, (
            "Dockerfile still contains TODO(ADV-014) comments. "
            "SHA-256 pinning must be applied and the TODO removed."
        )

    def test_python_stages_use_identical_digest(self) -> None:
        """Stages 2 (python-builder) and 3 (final) must pin the same python digest.

        Using different digests for the same base image across stages introduces
        a split-brain scenario that defeats reproducibility.
        """
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        python_digests = [
            _SHA256_PATTERN.search(line).group()  # type: ignore[union-attr]  # list comp guard guarantees search() is non-None; mypy cannot infer conditional filter
            for line in from_lines
            if "python" in line and _SHA256_PATTERN.search(line)
        ]
        assert len(python_digests) == 2, (
            f"Expected exactly 2 python FROM lines, found: {python_digests}"
        )
        assert python_digests[0] == python_digests[1], (
            "Python build stage and runtime stage use different SHA-256 digests. "
            f"Stage 2 digest: {python_digests[0]}, Stage 3 digest: {python_digests[1]}. "
            "Both must be identical for reproducible builds."
        )

    def test_version_tag_preserved_as_comment(self) -> None:
        """Each pinned FROM line must have a human-readable version tag comment nearby.

        The SHA-256 digest is opaque; a version tag comment preserves intent and
        makes future bumps reviewable.

        ADV-017 fix: BuildKit rejects inline ``# comment`` after ``AS stage-name``.
        The approved format is to place the version comment on the line immediately
        preceding the FROM directive.  This test accepts the comment on EITHER:

        - The FROM line itself:
          ``FROM image:tag@sha256:... AS name  # tag``
        - The line immediately before the FROM:
          ``# tag``
          ``FROM image:tag@sha256:... AS name``
        """
        all_lines = DOCKERFILE.read_text().splitlines()
        for i, line in enumerate(all_lines):
            stripped = line.strip()
            if not stripped.upper().startswith("FROM"):
                continue
            if not _SHA256_PATTERN.search(stripped):
                continue  # Non-pinned lines caught by other tests
            # Accept comment on this line OR on the immediately preceding non-empty line.
            preceding_line = ""
            for j in range(i - 1, -1, -1):
                candidate = all_lines[j].strip()
                if candidate:
                    preceding_line = candidate
                    break
            has_inline_comment = "#" in stripped
            has_preceding_comment = preceding_line.startswith("#") and len(preceding_line) > 1
            assert has_inline_comment or has_preceding_comment, (
                f"Pinned FROM line is missing a version tag comment "
                f"(neither inline nor on preceding line): {stripped!r}\n"
                "Accepted formats:\n"
                "  1. Inline:    FROM image:tag@sha256:<digest> AS name  # tag\n"
                "  2. Preceding: # tag  (on the line immediately before FROM)"
            )

    def test_node_from_line_pinned(self) -> None:
        """node:20-alpine (stage 1 frontend builder) must be SHA-256 pinned."""
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        node_lines = [line for line in from_lines if "node" in line]
        assert node_lines, "No node FROM line found in Dockerfile"
        assert _SHA256_PATTERN.search(node_lines[0]), (
            f"node FROM line not SHA-256 pinned: {node_lines[0]!r}"
        )

    def test_python_from_lines_pinned(self) -> None:
        """python:3.14-slim (stages 2 and 3) must be SHA-256 pinned."""
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        python_lines = [line for line in from_lines if "python" in line]
        assert len(python_lines) == 2, (
            f"Expected 2 python FROM lines, found {len(python_lines)}: {python_lines}"
        )
        for line in python_lines:
            assert _SHA256_PATTERN.search(line), f"python FROM line not SHA-256 pinned: {line!r}"


# ---------------------------------------------------------------------------
# docker-compose.yml tests
# ---------------------------------------------------------------------------


class TestDockerComposeSHA256Pinning:
    """Verify docker-compose.yml service image references are SHA-256 pinned."""

    def test_docker_compose_exists(self) -> None:
        """docker-compose.yml must exist at repository root."""
        assert DOCKER_COMPOSE.exists(), f"docker-compose.yml not found at {DOCKER_COMPOSE}"

    def test_all_external_image_lines_have_sha256_digest(self) -> None:
        """Every external service image in docker-compose.yml must be SHA-256 pinned.

        Excludes ``conclave-engine:latest`` — locally built image, no registry source.

        All 9 external service images are now SHA-256 pinned following ADV-015
        resolution (P18-T18.2): edoburu/pgbouncer:v1.23.1-p3 replaces the phantom
        pgbouncer/pgbouncer:1.23.1 tag and is pinned to its SHA-256 digest.
        """
        content = DOCKER_COMPOSE.read_text()
        image_lines = _extract_image_lines(content)
        assert image_lines, "No external image lines found in docker-compose.yml"

        failing: list[str] = []
        for line in image_lines:
            if not _SHA256_PATTERN.search(line):
                failing.append(line)

        assert not failing, (
            "The following docker-compose.yml image lines are not SHA-256 pinned:\n"
            + "\n".join(f"  {line}" for line in failing)
            + "\nAll external service images must use image:tag@sha256:<digest> format."
        )

    def test_phantom_pgbouncer_tag_absent(self) -> None:
        """The phantom tag pgbouncer/pgbouncer:1.23.1 must NOT exist in docker-compose.yml.

        This tag does not exist in Docker Hub (confirmed via Registry v2 API, 2026-03-16).
        It was replaced with edoburu/pgbouncer:v1.23.1-p3 in P18-T18.2 (ADR-0031, ADV-015).
        """
        content = DOCKER_COMPOSE.read_text()
        assert "pgbouncer/pgbouncer:1.23.1" not in content, (
            "docker-compose.yml still references the phantom tag pgbouncer/pgbouncer:1.23.1. "
            "This tag does not exist in Docker Hub and must not reappear. "
            "The replacement is edoburu/pgbouncer:v1.23.1-p3 (ADR-0031)."
        )

    def test_pgbouncer_uses_edoburu_image(self) -> None:
        """pgbouncer service must use edoburu/pgbouncer:v1.23.1-p3.

        The community-maintained edoburu/pgbouncer image provides PgBouncer 1.23.1
        and replaces the non-existent official pgbouncer/pgbouncer:1.23.1 tag.
        See ADR-0031 for the substitution rationale.
        """
        content = DOCKER_COMPOSE.read_text()
        assert "edoburu/pgbouncer" in content, (
            "docker-compose.yml must reference edoburu/pgbouncer as the pgbouncer service image. "
            "See ADR-0031 for the substitution rationale."
        )

    def test_pgbouncer_image_sha256_pinned(self) -> None:
        """edoburu/pgbouncer image line must include a SHA-256 digest.

        Resolves ADV-015: pgbouncer is now fully SHA-256 pinned as part of the
        complete 9-of-9 external service image pinning.
        """
        content = DOCKER_COMPOSE.read_text()
        for line in content.splitlines():
            if "edoburu/pgbouncer" in line and line.strip().startswith("image:"):
                assert _SHA256_PATTERN.search(line), (
                    f"edoburu/pgbouncer image line is not SHA-256 pinned: {line.strip()!r}\n"
                    "Format must be: image: edoburu/pgbouncer:tag@sha256:<digest>"
                )
                return
        pytest.fail("No edoburu/pgbouncer image line found in docker-compose.yml")

    def test_no_warning_p17_t17_1_comment(self) -> None:
        """WARNING(P17-T17.1) comment must not remain in docker-compose.yml.

        The temporary WARNING marker from P17-T17.1 was a placeholder for ADV-015.
        Now that ADV-015 is resolved in P18-T18.2, the marker must be removed.
        """
        content = DOCKER_COMPOSE.read_text()
        assert "WARNING(P17-T17.1)" not in content, (
            "docker-compose.yml still contains WARNING(P17-T17.1) comment. "
            "This was a temporary ADV-015 marker; it must be removed after the "
            "pgbouncer image fix in P18-T18.2."
        )

    def test_redis_image_pinned(self) -> None:
        """redis:7-alpine must be SHA-256 pinned."""
        content = DOCKER_COMPOSE.read_text()
        for line in content.splitlines():
            if "redis" in line and line.strip().startswith("image:"):
                assert _SHA256_PATTERN.search(line), (
                    f"redis image line not SHA-256 pinned: {line.strip()!r}"
                )
                return
        pytest.fail("No redis image line found in docker-compose.yml")

    def test_postgres_image_pinned(self) -> None:
        """postgres:16-alpine must be SHA-256 pinned."""
        content = DOCKER_COMPOSE.read_text()
        for line in content.splitlines():
            if "postgres" in line and line.strip().startswith("image:"):
                assert _SHA256_PATTERN.search(line), (
                    f"postgres image line not SHA-256 pinned: {line.strip()!r}"
                )
                return
        pytest.fail("No postgres image line found in docker-compose.yml")

    def test_prometheus_image_pinned(self) -> None:
        """prom/prometheus:v2.53.0 must be SHA-256 pinned."""
        content = DOCKER_COMPOSE.read_text()
        for line in content.splitlines():
            if "prometheus" in line and line.strip().startswith("image:"):
                assert _SHA256_PATTERN.search(line), (
                    f"prometheus image line not SHA-256 pinned: {line.strip()!r}"
                )
                return
        pytest.fail("No prometheus image line found in docker-compose.yml")

    def test_alertmanager_image_pinned(self) -> None:
        """prom/alertmanager:v0.27.0 must be SHA-256 pinned."""
        content = DOCKER_COMPOSE.read_text()
        for line in content.splitlines():
            if "alertmanager" in line and line.strip().startswith("image:"):
                assert _SHA256_PATTERN.search(line), (
                    f"alertmanager image line not SHA-256 pinned: {line.strip()!r}"
                )
                return
        pytest.fail("No alertmanager image line found in docker-compose.yml")

    def test_grafana_image_pinned(self) -> None:
        """grafana/grafana:11.3.0 must be SHA-256 pinned."""
        content = DOCKER_COMPOSE.read_text()
        for line in content.splitlines():
            if "grafana" in line and line.strip().startswith("image:"):
                assert _SHA256_PATTERN.search(line), (
                    f"grafana image line not SHA-256 pinned: {line.strip()!r}"
                )
                return
        pytest.fail("No grafana image line found in docker-compose.yml")

    def test_minio_image_pinned(self) -> None:
        """minio/minio must be SHA-256 pinned."""
        content = DOCKER_COMPOSE.read_text()
        for line in content.splitlines():
            if "minio" in line and line.strip().startswith("image:"):
                assert _SHA256_PATTERN.search(line), (
                    f"minio image line not SHA-256 pinned: {line.strip()!r}"
                )
                return
        pytest.fail("No minio image line found in docker-compose.yml")

    def test_version_tags_preserved_in_comments(self) -> None:
        """Each pinned image line must retain a human-readable version comment.

        The SHA-256 digest is opaque; the trailing comment preserves the
        version tag for human readability and auditing.
        """
        content = DOCKER_COMPOSE.read_text()
        image_lines = _extract_image_lines(content)
        for line in image_lines:
            if not _SHA256_PATTERN.search(line):
                continue  # Non-pinned lines caught by other tests
            assert "#" in line, (
                f"Pinned image line is missing a version tag comment: {line!r}\n"
                "Format must be: image: registry/image:tag@sha256:<digest>  # tag"
            )
