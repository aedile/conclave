"""Unit tests for Docker image SHA-256 digest pinning.

These tests inspect Dockerfile and docker-compose.yml as text files to verify
that all base image references are pinned to SHA-256 digests for supply chain
security (ADV-014).

Tests are file-inspection tests: they read the configuration files and assert
structural invariants. They do NOT require a running Docker daemon.
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

    Args:
        compose_text: Full text of the docker-compose.yml.

    Returns:
        List of image directive lines (stripped).
    """
    return [
        line.strip()
        for line in compose_text.splitlines()
        if line.strip().startswith("image:")
        # Exclude build-artifact images (no registry source to pin)
        and "conclave-engine" not in line
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
            _SHA256_PATTERN.search(line).group()  # type: ignore[union-attr]
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
        """Each pinned FROM line must retain its human-readable version tag comment.

        The SHA-256 digest is opaque; the version tag comment preserves intent
        and makes future bumps reviewable.
        """
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        for line in from_lines:
            if not _SHA256_PATTERN.search(line):
                continue  # Non-pinned lines caught by other tests
            assert "#" in line, (
                f"Pinned FROM line is missing a version tag comment: {line!r}\n"
                "Format must be: FROM image:tag@sha256:<digest> # tag-for-humans"
            )

    def test_node_from_line_pinned(self) -> None:
        """node:20-alpine (stage 1 frontend builder) must be SHA-256 pinned."""
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        node_lines = [l for l in from_lines if "node" in l]
        assert node_lines, "No node FROM line found in Dockerfile"
        assert _SHA256_PATTERN.search(node_lines[0]), (
            f"node FROM line not SHA-256 pinned: {node_lines[0]!r}"
        )

    def test_python_from_lines_pinned(self) -> None:
        """python:3.14-slim (stages 2 and 3) must be SHA-256 pinned."""
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        python_lines = [l for l in from_lines if "python" in l]
        assert len(python_lines) == 2, (
            f"Expected 2 python FROM lines, found {len(python_lines)}: {python_lines}"
        )
        for line in python_lines:
            assert _SHA256_PATTERN.search(line), (
                f"python FROM line not SHA-256 pinned: {line!r}"
            )


# ---------------------------------------------------------------------------
# docker-compose.yml tests
# ---------------------------------------------------------------------------


class TestDockerComposeSHA256Pinning:
    """Verify docker-compose.yml service image references are SHA-256 pinned."""

    def test_docker_compose_exists(self) -> None:
        """docker-compose.yml must exist at repository root."""
        assert DOCKER_COMPOSE.exists(), (
            f"docker-compose.yml not found at {DOCKER_COMPOSE}"
        )

    def test_all_external_image_lines_have_sha256_digest(self) -> None:
        """Every external service image in docker-compose.yml must be SHA-256 pinned.

        Excludes `conclave-engine:latest` which is a locally built image with
        no upstream registry reference.

        NOTE: pgbouncer/pgbouncer:1.23.1 does not exist in Docker Hub (the tag
        is unknown — only versions up to 1.15.0 are published). This image line
        requires investigation (see RETRO_LOG ADV-014 finding). This test will
        fail until that line is either replaced with a valid image+digest or
        the line is removed.
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
            + "\n".join(f"  {l}" for l in failing)
            + "\nAll external service images must use image:tag@sha256:<digest> format."
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
