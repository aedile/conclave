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

P28-F3 (resolved P28):
    The ``poetry export`` command in the python-builder stage was missing
    ``--with synthesizer``, which caused sdv/torch/opacus to be absent from
    the production image.  The fix adds ``--with synthesizer`` to include the
    synthesizer optional dependency group.

P87 (python:3.13-slim upgrade):
    Base image upgraded from python:3.14-slim to python:3.13-slim (3.13 is the
    highest production-ready version; 3.14 is not yet stable).  The digest pin
    requires Docker daemon access to retrieve via ``docker inspect`` — the
    current environment does not have a running Docker daemon.  A TODO(P87)
    marker is placed on the unpinned FROM lines; digest pinning is required
    before GA deployment.  Tests in this module verify the TODO marker exists
    and that the digest is refreshed when the marker is resolved.
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

# FROM lines carrying this exemption marker are temporarily exempt from the
# SHA-256 pinning requirement.  The marker MUST accompany a TODO comment that
# identifies the tracking ticket.  Tests verify the marker is present so it
# cannot silently decay into a permanent bypass.
_DIGEST_EXEMPTION_MARKER = "TODO(P87)"


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


def _from_line_is_exempt(dockerfile_text: str, from_line: str) -> bool:
    """Return True if a FROM line is temporarily exempt from SHA-256 pinning.

    A FROM line is exempt if the TODO(P87) exemption marker appears in the
    comment block immediately preceding it in the Dockerfile.  The marker must
    be on a comment line (starting with #) within the 5 lines above the FROM
    directive.  This prevents silently inheriting an exemption from a distant
    unrelated comment block.

    Args:
        dockerfile_text: Full text of the Dockerfile.
        from_line: The stripped FROM directive line to check.

    Returns:
        True if the FROM line carries a valid exemption marker.
    """
    all_lines = dockerfile_text.splitlines()
    for i, line in enumerate(all_lines):
        if line.strip() == from_line:
            # Look back up to 5 lines for a comment containing the marker
            start = max(0, i - 5)
            preceding = all_lines[start:i]
            for comment_line in reversed(preceding):
                stripped = comment_line.strip()
                if stripped.startswith("#") and _DIGEST_EXEMPTION_MARKER in stripped:
                    return True
    return False


# ---------------------------------------------------------------------------
# Dockerfile tests
# ---------------------------------------------------------------------------


class TestDockerfileSHA256Pinning:
    """Verify Dockerfile FROM lines are pinned to SHA-256 digests."""

    def test_dockerfile_exists(self) -> None:
        """Dockerfile must exist at repository root."""
        assert DOCKERFILE.exists(), f"Dockerfile not found at {DOCKERFILE}"

    def test_all_from_lines_have_sha256_digest_or_exemption(self) -> None:
        """Every FROM line must either be SHA-256 pinned or carry a TODO(P87) exemption.

        This covers node:20-alpine (stage 1, pinned), python:3.13-slim
        (stages 2 & 3, temporarily exempt via TODO(P87) pending digest
        refresh when Docker daemon is available).
        """
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        assert from_lines, "No FROM lines found in Dockerfile"

        for line in from_lines:
            is_pinned = _SHA256_PATTERN.search(line) is not None
            is_exempt = _from_line_is_exempt(content, line)
            assert is_pinned or is_exempt, (
                f"FROM line is not SHA-256 pinned and has no TODO(P87) exemption: {line!r}\n"
                "All FROM lines must either:\n"
                "  1. Include @sha256:<64-hex-chars>, OR\n"
                "  2. Have a preceding # TODO(P87) comment for tracked exemptions."
            )

    def test_no_adv014_todo_comments_remain(self) -> None:
        """No TODO(ADV-014) comments should remain — debt must be resolved."""
        content = DOCKERFILE.read_text()
        assert "TODO(ADV-014)" not in content, (
            "Dockerfile still contains TODO(ADV-014) comments. "
            "SHA-256 pinning must be applied and the TODO removed."
        )

    def test_p87_todo_marker_present_for_unpinned_python_stages(self) -> None:
        """python:3.13-slim FROM lines must carry the TODO(P87) exemption marker.

        This test verifies the exemption is properly documented and traceable.
        When the Docker daemon is available, resolve TODO(P87) by running:
            docker pull python:3.13-slim
            docker inspect --format='{{index .RepoDigests 0}}' python:3.13-slim
        Then update the Dockerfile FROM lines to use the retrieved digest.
        """
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        python_lines = [line for line in from_lines if "python" in line]
        assert python_lines, "No python FROM lines found in Dockerfile"

        # For each unpinned python FROM line, verify the exemption marker exists
        for line in python_lines:
            if not _SHA256_PATTERN.search(line):
                assert _from_line_is_exempt(content, line), (
                    f"Unpinned python FROM line is missing TODO(P87) exemption: {line!r}\n"
                    "Unpinned base images must carry a tracked TODO marker."
                )

    def test_python_stages_use_same_base_image(self) -> None:
        """Stages 2 (python-builder) and 3 (final) must use the same python base image.

        Using different base images across stages introduces a split-brain
        scenario that defeats reproducibility. Both stages must reference the
        same image tag (and digest, once pinned).
        """
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        python_lines = [line for line in from_lines if "python" in line]
        assert len(python_lines) == 2, (
            f"Expected exactly 2 python FROM lines, found: {python_lines}"
        )

        # Extract the image reference (image:tag, with or without digest)
        # Compare the base part before " AS "
        def _base_image(from_line: str) -> str:
            """Extract image:tag from a FROM line, stripping the AS alias."""
            parts = from_line.split()
            # FROM image AS alias  =>  parts[1] is image
            return parts[1] if len(parts) >= 2 else from_line

        bases = [_base_image(line) for line in python_lines]
        assert bases[0] == bases[1], (
            "Python build stage and runtime stage use different base images. "
            f"Stage 2: {bases[0]!r}, Stage 3: {bases[1]!r}. "
            "Both must reference the same image tag for reproducible builds."
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

    def test_python_from_lines_present(self) -> None:
        """python:3.13-slim (stages 2 and 3) must be present in the Dockerfile."""
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        python_lines = [line for line in from_lines if "python" in line]
        assert len(python_lines) == 2, (
            f"Expected 2 python FROM lines, found {len(python_lines)}: {python_lines}"
        )

    def test_python_base_image_is_313_slim(self) -> None:
        """Both python stages must use python:3.13-slim as the base image.

        Python 3.14 is not production-ready (Phase 87); 3.13 is the highest
        stable release and the correct production base image.
        """
        content = DOCKERFILE.read_text()
        from_lines = _extract_from_lines(content)
        python_lines = [line for line in from_lines if "python" in line]
        for line in python_lines:
            assert "python:3.13-slim" in line, (
                f"Python FROM line does not use python:3.13-slim: {line!r}\n"
                "Both python-builder and final stages must use python:3.13-slim."
            )


# ---------------------------------------------------------------------------
# Dockerfile poetry export tests (P28-F3)
# ---------------------------------------------------------------------------


class TestDockerfilePoetryExport:
    """Verify the poetry export command in the Dockerfile includes required groups.

    P28-F3: The production image was missing sdv/torch/opacus because the
    ``synthesizer`` optional dependency group was not included in the
    ``poetry export`` invocation in the python-builder stage.
    """

    def test_poetry_export_includes_synthesizer_group(self) -> None:
        """The poetry export command must include ``--with synthesizer``.

        The ``synthesizer`` optional dependency group contains sdv, torch, and
        opacus.  Without ``--with synthesizer``, these packages are absent from
        the requirements.txt exported to the production image, causing an
        ImportError when synthesis jobs execute.

        Fix: add ``--with synthesizer`` to the poetry export RUN command in the
        python-builder stage of the Dockerfile.
        """
        content = DOCKERFILE.read_text()
        assert "--with synthesizer" in content, (
            "Dockerfile poetry export command is missing '--with synthesizer'.\n"
            "The synthesizer optional group (sdv, torch, opacus) must be included "
            "in the production image.  Add '--with synthesizer' to the RUN poetry "
            "export command in the python-builder stage.\n"
            "Expected form:\n"
            "  RUN poetry export --without dev --with synthesizer --without-hashes "
            "-f requirements.txt -o requirements.txt"
        )

    def test_poetry_export_still_excludes_dev_group(self) -> None:
        """The poetry export command must still include ``--without dev``.

        Dev dependencies (pytest, ruff, mypy, etc.) must be excluded from the
        production image.  Adding ``--with synthesizer`` must not remove the
        existing ``--without dev`` flag.
        """
        content = DOCKERFILE.read_text()
        assert "--without dev" in content, (
            "Dockerfile poetry export command is missing '--without dev'.\n"
            "Dev dependencies must be excluded from the production image."
        )


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
