"""Tests for the GitHub Actions release workflow structural integrity.

This module validates:
- The release workflow YAML can be parsed and has required top-level keys.
- All `uses:` references are SHA-pinned (never @main or bare @v<N> tags).
- Permissions are scoped per-job (not a global write block).
- Job dependency chain is correctly wired (build-release needs validate-tag, etc.).
- Negative cases: missing keys, unpinned actions, forbidden global write perms.
- Tag regex is end-anchored to reject arbitrary suffixes (ADV-P51-01).

Implementation note — PyYAML `on` keyword coercion:
  PyYAML's `safe_load` converts the bare YAML keyword `on` to Python `True`
  because `on` / `off` / `yes` / `no` are Boolean synonyms in YAML 1.1
  (the version PyYAML implements). GitHub Actions parsers use YAML 1.2 where
  this coercion does not apply. `_load_workflow()` normalizes the parsed dict
  by replacing the `True` key with `"on"` so tests can use the expected string
  key. This normalization is local to the test helpers and does not affect the
  actual workflow file.

Google-style docstrings are applied throughout.

T51.2 — GitHub Actions Release Workflow
ADV-P51-01 — Tag regex end-anchor fix
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.infrastructure]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKFLOW_PATH = Path(__file__).parents[2] / ".github" / "workflows" / "release.yml"

# Pattern that matches a correctly SHA-pinned action reference.
# Acceptable: <owner>/<repo>@<40-hex-chars>
# Rejected:   @main, @master, @v4, @v4.2.2, @latest
_SHA_RE = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")

# The end-anchored tag validation regex required by ADV-P51-01.
# Accepts: v1.0.0, v1.0.0-rc.1, v0.1.0-alpha.1
# Rejects: v1.0.0.evil, v1.0.0-injected-payload (arbitrary suffixes beyond semver)
_REQUIRED_TAG_REGEX = r"^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9._-]+)?$"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_workflow() -> dict[str, Any]:
    """Load and parse the release workflow YAML with PyYAML boolean normalisation.

    PyYAML's `safe_load` converts the YAML 1.1 keyword ``on`` to Python
    ``True``.  GitHub Actions uses YAML 1.2 where ``on`` is a plain string.
    This function normalises the top-level key so that tests can reference
    ``workflow["on"]`` as expected.

    Returns:
        Parsed and normalised YAML content as a dictionary.

    Raises:
        FileNotFoundError: If the workflow file does not exist.
        yaml.YAMLError: If the file is not valid YAML.
    """
    raw: dict[Any, Any] = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    # PyYAML coerces `on:` → True. Re-key it to the canonical string "on".
    if True in raw and "on" not in raw:
        raw["on"] = raw.pop(True)
    return raw  # type: ignore[return-value]


def _collect_uses_values(data: Any) -> list[str]:
    """Recursively collect every ``uses:`` value in a parsed YAML structure.

    Args:
        data: Arbitrary parsed YAML value (dict, list, or scalar).

    Returns:
        Flat list of strings that appeared as values under ``uses:`` keys.
    """
    results: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "uses" and isinstance(value, str):
                results.append(value)
            else:
                results.extend(_collect_uses_values(value))
    elif isinstance(data, list):
        for item in data:
            results.extend(_collect_uses_values(item))
    return results


def _extract_tag_regex_from_workflow() -> str | None:
    """Extract the tag-validation regex string from the release workflow file.

    Reads the raw workflow text and locates the grep -qE pattern used in the
    validate-tag step. Returns the regex string (without surrounding quotes)
    or None if not found.

    Returns:
        The regex pattern string, or None if the grep pattern is not found.
    """
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # Match: grep -qE '<pattern>' or grep -qE "<pattern>"
    match = re.search(r"grep\s+-qE\s+['\"]([^'\"]+)['\"]", text)
    if match:
        return match.group(1)
    return None


# ===========================================================================
# ATTACK / NEGATIVE TESTS — verify structural security properties
# ===========================================================================


class TestReleaseWorkflowSecurity:
    """Attack and negative tests for the release workflow supply-chain hardening."""

    def test_workflow_file_exists(self) -> None:
        """Release workflow must exist at the canonical path.

        The absence of the file is the first failure mode; all other tests
        depend on this assertion.
        """
        assert WORKFLOW_PATH.exists(), (
            f"Release workflow not found at {WORKFLOW_PATH}. "
            "Create .github/workflows/release.yml as specified in T51.2."
        )

    def test_no_unpinned_action_uses_main(self) -> None:
        """No ``uses:`` line may reference @main or @master.

        ``@main`` references are mutable; they bypass supply-chain integrity
        guarantees and violate the SHA-pinning mandate established in T3.5.1.
        """
        workflow = _load_workflow()
        uses_values = _collect_uses_values(workflow)
        violations = [u for u in uses_values if u.endswith("@main") or u.endswith("@master")]
        assert violations == [], (
            f"Unpinned @main/@master action references found: {violations}. "
            "All actions must use immutable SHA pins."
        )

    def test_no_bare_version_tag_actions(self) -> None:
        """No ``uses:`` line may reference a bare semver tag without a SHA.

        Bare tags like ``@v4`` or ``@v4.2.2`` are mutable pointers that can
        be force-pushed by the action author, enabling supply-chain attacks.
        Every action must be pinned to a 40-character commit SHA.
        """
        workflow = _load_workflow()
        uses_values = _collect_uses_values(workflow)
        bare_tag_re = re.compile(r"@v\d+(\.\d+)*$")
        violations = [u for u in uses_values if bare_tag_re.search(u)]
        assert violations == [], (
            f"Bare version-tag action references found: {violations}. "
            "Replace with SHA-pinned references (e.g. actions/checkout@<40-char-sha>)."
        )

    def test_all_uses_values_are_sha_pinned(self) -> None:
        """Every ``uses:`` value must be pinned to a 40-character commit SHA.

        This is the positive form of the supply-chain hardening test. Each
        action reference is matched against the canonical SHA pattern.
        """
        workflow = _load_workflow()
        uses_values = _collect_uses_values(workflow)
        assert len(uses_values) >= 1, (
            "Workflow has no `uses:` references — cannot validate pinning."
        )
        violations = [u for u in uses_values if not _SHA_RE.match(u)]
        assert violations == [], (
            f"Non-SHA-pinned action references found: {violations}. "
            "Every `uses:` line must match <owner>/<repo>@<40-hex-sha>."
        )

    def test_no_global_write_permissions(self) -> None:
        """Global ``permissions: write-all`` or ``contents: write`` must not appear.

        Per the task spec, permissions must be scoped per-job. A global
        ``contents: write`` block grants excessive scope to ALL jobs including
        potential future additions, violating the principle of least privilege.
        """
        workflow = _load_workflow()
        global_perms = workflow.get("permissions", {})
        if isinstance(global_perms, dict):
            contents_perm = global_perms.get("contents", "read")
            assert contents_perm != "write", (
                "Global `permissions.contents: write` found. "
                "Write permissions must be scoped to the specific job that requires them."
            )
        elif global_perms == "write-all":
            pytest.fail(
                "Global `permissions: write-all` found. "
                "Permissions must be scoped per-job to satisfy least-privilege."
            )

    def test_trigger_is_tag_push_only(self) -> None:
        """The workflow must trigger only on ``v*`` tag pushes.

        A release workflow that triggers on branch pushes would run on every
        commit to main, creating spurious release artifacts and cluttering the
        release history.
        """
        workflow = _load_workflow()
        on_block = workflow.get("on", {})
        push_block = on_block.get("push", {}) if isinstance(on_block, dict) else {}
        tags = push_block.get("tags", [])
        assert tags == ["v*"], (
            f"Expected trigger `on.push.tags: ['v*']`, got: {tags}. "
            "Release workflow must only trigger on version tag pushes."
        )
        branches = push_block.get("branches", [])
        assert branches == [], (
            f"Release workflow has branch triggers: {branches}. "
            "This workflow must only trigger on tag pushes, not branch pushes."
        )

    def test_publish_cannot_run_without_build(self) -> None:
        """publish-release must declare ``needs: [build-release]``.

        If publish-release can run without build-release completing, a build
        failure would not prevent a broken or empty release from being published.
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        publish_job = jobs.get("publish-release", {})
        needs = publish_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "build-release" in needs, (
            f"publish-release.needs does not include 'build-release'. "
            f"Got: {needs}. Build failure would not block publish."
        )

    def test_publish_explicitly_depends_on_validate_tag(self) -> None:
        """publish-release must explicitly list ``validate-tag`` in its needs.

        GitHub Actions does NOT implicitly propagate job outputs through a
        transitive needs chain. If publish-release only declares
        ``needs: [build-release]``, then ``needs.validate-tag.outputs.*``
        expressions inside publish-release resolve to empty strings at
        runtime — even though validate-tag ran earlier in the pipeline.

        Listing validate-tag explicitly in publish-release.needs guarantees
        that the output context is available and the TAG/VERSION env vars
        are populated correctly. See P51 DevOps review finding (HIGH).
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        publish_job = jobs.get("publish-release", {})
        needs = publish_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "validate-tag" in needs, (
            f"publish-release.needs does not include 'validate-tag'. "
            f"Got: {needs}. "
            "needs.validate-tag.outputs.* will be empty at runtime without "
            "an explicit dependency. Add 'validate-tag' to publish-release.needs."
        )

    def test_build_cannot_run_without_validate(self) -> None:
        """build-release must declare ``needs: [validate-tag]``.

        If build-release can run without validate-tag, invalid tags could
        trigger an expensive Docker build before validation fails.
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        build_job = jobs.get("build-release", {})
        needs = build_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "validate-tag" in needs, (
            f"build-release.needs does not include 'validate-tag'. "
            f"Got: {needs}. Invalid tags would trigger expensive Docker builds."
        )

    def test_invalid_tag_format_fails_validate_job(self) -> None:
        """The validate-tag job must contain a step that checks the tag format.

        A workflow that accepts any tag (e.g. ``not-a-version`` or
        ``release-foo``) provides no version-format guarantee and produces
        confusingly named artifacts.
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        validate_job = jobs.get("validate-tag", {})
        steps = validate_job.get("steps", [])
        step_names = [s.get("name", "") for s in steps]
        step_runs = [s.get("run", "") for s in steps]
        all_content = " ".join(step_names + step_runs).lower()
        has_validation = any(
            keyword in all_content
            for keyword in ["validate", "verify", "check", "^v", "regex", "format", "semver"]
        )
        assert has_validation, (
            f"validate-tag job has no tag-format validation step. "
            f"Step names found: {step_names}. "
            "Add a step that verifies the tag matches v<semver> pattern."
        )

    def test_tag_regex_rejects_arbitrary_suffix(self) -> None:
        """Tag validation regex must be end-anchored to reject arbitrary suffixes.

        The unanchored pattern ``^v[0-9]+\\.[0-9]+\\.[0-9]+`` would accept
        ``v1.0.0.evil`` and ``v1.0.0-injected-payload`` because it only
        checks the prefix. The required pattern must end with ``$`` so that
        tags with arbitrary trailing content are rejected.

        ADV-P51-01: end-anchor the release tag validation regex.
        """
        tag_regex = _extract_tag_regex_from_workflow()
        assert tag_regex is not None, (
            "Could not find a grep -qE pattern in the release workflow. "
            "The validate-tag step must use grep -qE '<pattern>' to validate tags."
        )
        compiled = re.compile(tag_regex)
        rejected = ["v1.0.0.evil", "v1.0.0-injected-payload", "v1.0.0extra", "v1.0.0.0"]
        for bad_tag in rejected:
            assert not compiled.search(bad_tag), (
                f"Tag regex '{tag_regex}' incorrectly accepted malformed tag '{bad_tag}'. "
                "The regex must be end-anchored with '$' to reject arbitrary suffixes."
            )

    def test_tag_regex_accepts_valid_semver_prerelease(self) -> None:
        """Tag validation regex must accept standard semver stable and pre-release tags.

        The end-anchored regex must still accept the common valid forms:
        ``v1.0.0`` (stable), ``v1.0.0-rc.1`` (release candidate),
        and ``v1.0.0-alpha.1`` (alpha pre-release).

        ADV-P51-01: verify valid tags are not rejected by the fixed regex.
        """
        tag_regex = _extract_tag_regex_from_workflow()
        assert tag_regex is not None, (
            "Could not find a grep -qE pattern in the release workflow. "
            "The validate-tag step must use grep -qE '<pattern>' to validate tags."
        )
        compiled = re.compile(tag_regex)
        accepted = ["v1.0.0", "v1.0.0-rc.1", "v1.0.0-alpha.1", "v2.3.4", "v0.1.0-beta.2"]
        for good_tag in accepted:
            assert compiled.search(good_tag), (
                f"Tag regex '{tag_regex}' incorrectly rejected valid tag '{good_tag}'. "
                "The regex must accept v<major>.<minor>.<patch> and semver pre-release forms."
            )


# ===========================================================================
# STRUCTURAL / FEATURE TESTS — verify required workflow elements are present
# ===========================================================================


class TestReleaseWorkflowStructure:
    """Structural tests verifying required workflow elements and job topology."""

    def test_workflow_has_required_top_level_keys(self) -> None:
        """Workflow must have name, on, and jobs keys at the top level.

        These are the three non-optional top-level keys in every GitHub
        Actions workflow file. Note: PyYAML normalisation converts the bare
        ``on:`` YAML key to the string ``"on"`` before this assertion runs.
        """
        workflow = _load_workflow()
        assert "name" in workflow, "Workflow missing top-level `name:` key."
        assert "on" in workflow, (
            "Workflow missing top-level `on:` key. "
            "(PyYAML boolean normalisation: True → 'on' was applied.)"
        )
        assert "jobs" in workflow, "Workflow missing top-level `jobs:` key."

    def test_workflow_has_three_required_jobs(self) -> None:
        """Workflow must define validate-tag, build-release, and publish-release jobs.

        These three jobs are specified in the T51.2 task definition.
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        required_jobs = {"validate-tag", "build-release", "publish-release"}
        missing = required_jobs - set(jobs.keys())
        assert missing == set(), (
            f"Required jobs missing from workflow: {missing}. Defined jobs: {set(jobs.keys())}."
        )

    def test_publish_release_job_has_contents_write_permission(self) -> None:
        """The publish-release job must have ``permissions.contents: write``.

        Creating a GitHub Release and uploading assets requires write access
        to repository contents. Per the task spec, this must be scoped to the
        publish-release job, not granted globally.
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        publish_perms = jobs.get("publish-release", {}).get("permissions", {})
        build_perms = jobs.get("build-release", {}).get("permissions", {})
        has_write = (
            publish_perms.get("contents") == "write" or build_perms.get("contents") == "write"
        )
        assert has_write, (
            "Neither publish-release nor build-release has `permissions.contents: write`. "
            "Creating GitHub Releases requires this permission scoped to the relevant job."
        )

    def test_build_release_installs_dev_and_synthesizer_deps(self) -> None:
        """build-release must install dependencies with ``--with dev,synthesizer``.

        The SBOM generation step requires the synthesizer group to enumerate
        all dependencies including SDV/CTGAN. Installing only ``--with dev``
        would produce an incomplete SBOM.
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        build_steps = jobs.get("build-release", {}).get("steps", [])
        install_runs = [
            s.get("run", "") for s in build_steps if "install" in s.get("name", "").lower()
        ]
        all_install_text = " ".join(install_runs)
        has_synthesizer = (
            "--with dev,synthesizer" in all_install_text or "--with synthesizer" in all_install_text
        )
        assert has_synthesizer, (
            f"build-release does not install the synthesizer dependency group. "
            f"Install steps found: {install_runs}. "
            "SBOM generation requires `poetry install --with dev,synthesizer`."
        )

    def test_build_release_generates_sbom(self) -> None:
        """build-release must contain a step that generates the CycloneDX SBOM.

        An air-gap bundle without an SBOM provides no dependency transparency
        for the operator deploying to a restricted environment.
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        build_steps = jobs.get("build-release", {}).get("steps", [])
        step_runs = " ".join(s.get("run", "") for s in build_steps)
        assert "cyclonedx" in step_runs.lower() or "sbom" in step_runs.lower(), (
            "build-release has no SBOM generation step. "
            "A step running `cyclonedx-py poetry` is required per T51.2."
        )

    def test_build_release_computes_checksums(self) -> None:
        """build-release must compute SHA-256 checksums of release artifacts.

        Operators in air-gapped environments must be able to verify artifact
        integrity without network access to GitHub. A sha256sums.txt file
        satisfies this requirement.
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        build_steps = jobs.get("build-release", {}).get("steps", [])
        step_runs = " ".join(s.get("run", "") for s in build_steps)
        has_checksum = "sha256" in step_runs.lower() or "sha256sums" in step_runs.lower()
        assert has_checksum, (
            "build-release has no SHA-256 checksum step. "
            "sha256sums.txt must be computed for all release artifacts per T51.2."
        )

    def test_build_release_builds_docker_image(self) -> None:
        """build-release must build the Docker image tagged with the release version.

        The air-gap bundle includes Docker images; the release job must build
        the production image before the bundle script can save it.
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        build_steps = jobs.get("build-release", {}).get("steps", [])
        step_runs = " ".join(s.get("run", "") for s in build_steps)
        assert "docker build" in step_runs, (
            "build-release has no `docker build` step. "
            "The production image must be built as part of the release job per T51.2."
        )

    def test_publish_release_uses_gh_release_create(self) -> None:
        """publish-release must create the GitHub Release using ``gh release create``.

        Using the GitHub CLI ensures consistent release creation with proper
        checksums, notes, and asset attachment in a single atomic operation.
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        publish_steps = jobs.get("publish-release", {}).get("steps", [])
        step_runs = " ".join(s.get("run", "") for s in publish_steps)
        assert "gh release create" in step_runs, (
            "publish-release has no `gh release create` step. "
            "The GitHub CLI must be used to create the release per T51.2."
        )

    def test_validate_tag_job_extracts_version(self) -> None:
        """validate-tag must extract the version from the tag and export it.

        Downstream jobs (build-release, publish-release) need the version
        string to tag Docker images and name artifacts correctly. It must be
        exposed via GITHUB_OUTPUT or GITHUB_ENV.
        """
        workflow = _load_workflow()
        jobs = workflow.get("jobs", {})
        validate_steps = jobs.get("validate-tag", {}).get("steps", [])
        step_runs = " ".join(s.get("run", "") for s in validate_steps)
        has_output = "GITHUB_OUTPUT" in step_runs or "GITHUB_ENV" in step_runs
        assert has_output, (
            "validate-tag does not export the extracted version via GITHUB_OUTPUT or GITHUB_ENV. "
            "Downstream jobs need the version string for artifact naming."
        )

    def test_build_release_uploads_artifacts(self) -> None:
        """build-release must upload artifacts so publish-release can download them.

        GitHub Actions jobs run in isolated environments. Artifacts produced
        by build-release must be uploaded for publish-release to access them.
        """
        workflow = _load_workflow()
        uses_values = _collect_uses_values(workflow.get("jobs", {}).get("build-release", {}))
        has_upload = any("upload-artifact" in u for u in uses_values)
        assert has_upload, (
            "build-release does not use actions/upload-artifact. "
            "Release artifacts must be uploaded so publish-release can access them."
        )

    def test_publish_release_downloads_artifacts(self) -> None:
        """publish-release must download artifacts built by build-release.

        Without a download step, the publish job cannot attach the air-gap
        bundle, SBOM, and checksums to the GitHub Release.
        """
        workflow = _load_workflow()
        uses_values = _collect_uses_values(workflow.get("jobs", {}).get("publish-release", {}))
        has_download = any("download-artifact" in u for u in uses_values)
        assert has_download, (
            "publish-release does not use actions/download-artifact. "
            "Release assets built by build-release must be downloaded before publishing."
        )

    def test_workflow_name_is_descriptive(self) -> None:
        """Workflow name must be non-empty and descriptive (not a default placeholder).

        An empty or generic name makes the GitHub Actions UI difficult to
        navigate when multiple workflows exist.
        """
        workflow = _load_workflow()
        name = workflow.get("name", "")
        assert isinstance(name, str), (
            f"Workflow name '{name}' is not a string — `name:` key is malformed."
        )
        assert len(name) >= 5, (
            f"Workflow name '{name}' is too short (< 5 chars). "
            "Use a descriptive name like 'Release' or 'Release Engineering'."
        )
        forbidden = {"ci", "workflow", "untitled", "test"}
        assert name.lower().strip() not in forbidden, (
            f"Workflow name '{name}' is a generic placeholder. "
            "Choose a descriptive name specific to the release process."
        )
