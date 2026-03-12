# Phase 1: Project Initialization & Quality Gates

**Goal:** Establish the unbreakable security & quality gates (Priority 0 & 1). No code can be written until this infrastructure enforces the Constitution.

---

## Task 1.1: Configure CI/CD Pipeline & Scanners
**Assignee:** [Dev A]
**Priority:** Critical Path

### User Story / Agentic Goal
As a Security Engineer, I want the CI/CD pipeline and local pre-commit hooks to automatically enforce all code quality and security standards, so that no vulnerabilities, secrets, or poorly formatted code can be merged into the repository.

### Context & Constraints
*   Must enforce Priority 0 (Security) and Priority 1 (Quality Gates) from `CONSTITUTION.md`.
*   The system must act as a "Developer Constitution" enforcer. No bypass mechanisms are allowed.
*   Must prepare the pipeline for an offline/air-gapped environment (SBOM creation is mandatory).

### Acceptance Criteria
*   [ ] `gitleaks` is configured to run on every commit and blocks commits if secrets are detected.
*   [ ] `bandit` is configured to deeply scan the Python AST for security issues.
*   [ ] `ruff` is configured for aggressive linting and fast formatting.
*   [ ] `mypy` is configured in `strict` mode for all Python files.
*   [ ] `trivy` and `pip-audit` are configured for container and dependency vulnerability scanning.
*   [ ] SBOM generation (`Syft` or `CycloneDX`) is integrated into the build process.
*   [ ] `import-linter` is configured to enforce strict architectural module boundaries (e.g., Ingestion cannot depend on UI).

### Testing & Quality Gates
*   Simulate a failure for each scanner (e.g., introduce a fake secret to test `gitleaks`, a type error for `mypy`) to ensure the pipeline correctly fails and blocks the merge.

### Files to Create/Modify
*   [NEW] `.pre-commit-config.yaml`
*   [NEW] `.github/workflows/ci.yml` (or equivalent CI config)
*   [MODIFY] `pyproject.toml` (Add ruff, mypy, bandit configurations)

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Pipeline enforces all tools mandated by the Execution Plan.
2. **Coverage Gate:** N/A (Infrastructure task).
3. **Pipeline Green:** CI scripts run successfully against the current empty/scaffolded codebase.
4. **Peer Review:** Reviewed and approved by a secondary agent or human.
5. **Acceptance Verification:** All acceptance criteria are met.

---

## Task 1.2: Setup TDD Framework
**Assignee:** [Dev B]
**Priority:** Critical Path

### User Story / Agentic Goal
As a Software Developer, I want a robust, isolated testing environment that automatically checks test coverage, so that I can rigorously follow Test-Driven Development (TDD) without database state leakage across test runs.

### Context & Constraints
*   Must enforce Priority 3 (TDD) and Priority 4 (Comprehensive Testing).
*   Must prevent database race conditions and state leakage using transaction rollbacks.
*   Test coverage must rigidly fail the build if it drops below 90%.

### Acceptance Criteria
*   [ ] `pytest` is configured as the primary test runner.
*   [ ] `pytest-cov` is configured to enforce a strict `--cov-fail-under=90` threshold.
*   [ ] `pytest-postgresql` (or similar transaction rollback plugin) is integrated to ensure a pristine database state for every test iteration.
*   [ ] Base test directory structure (`tests/unit`, `tests/integration`, `tests/fixtures`) is scaffolded.

### Testing & Quality Gates
*   Write a dummy unit test.
*   Ensure that running `pytest` generates a coverage report and passes.
*   Ensure that running `pytest` with a coverage under 90% forces a non-zero exit code (CI failure).

### Files to Create/Modify
*   [MODIFY] `pyproject.toml` (Add pytest & pytest-cov configurations)
*   [NEW] `tests/conftest.py` (Setup DB rollback fixtures)

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Adheres to TDD guidelines. Database state isolation is proven.
2. **Coverage Gate:** Empty project tests pass with 100% coverage.
3. **Pipeline Green:** integrated successfully with the CI pipeline established in 1.1.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** All acceptance criteria are met.

---

## Task 1.3: Construct Base Docker Image
**Assignee:** [Dev A]
**Priority:** Blocked by 1.1 & 1.2

### User Story / Agentic Goal
As a DevOps Engineer, I want a secure, multi-stage Dockerfile that drops root privileges, so that the application runs safely in isolation without risking host-level compromise if a container escape occurs.

### Context & Constraints
*   Must support the Modular Monolith architecture.
*   Must integrate frontend (React) and backend (FastAPI) build processes.
*   Container must NOT run as the `root` user in the final stage.

### Acceptance Criteria
*   [ ] Dockerfile implements multi-stage builds (e.g., Node.js for frontend assets, Python for backend).
*   [ ] The final container runs as a non-root user (using `su-exec` or standard `USER` directives).
*   [ ] `tini` (or equivalent init process) is configured as the entrypoint to handle zombie processes and signal forwarding correctly.
*   [ ] Only necessary production dependencies and compiled assets are copied to the final stage.

### Testing & Quality Gates
*   `trivy` container scan shows zero critical or high vulnerabilities.
*   Verify the running user inside the container is not UID 0 (`root`).

### Files to Create/Modify
*   [NEW] `Dockerfile`
*   [NEW] `.dockerignore`
*   [NEW] `scripts/entrypoint.sh`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Secure containerization principles applied.
2. **Coverage Gate:** N/A.
3. **Pipeline Green:** Docker build succeeds in CI.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 1.4: Configure Container Security & Storage Policies
**Assignee:** [Dev B]
**Priority:** Blocked by 1.1 & 1.2

### User Story / Agentic Goal
As a Security Architect, I need strict container storage and memory policies defined so that underlying sensitive data cannot be extracted from disk, and sensitive cryptographic operations are protected from memory swapping.

### Context & Constraints
*   Supports the Air-Gapped / Dark Room compliance mandates.

### Acceptance Criteria
*   [ ] Define LUKS-based encrypted volume requirements or ensure application-level encryption handles disk I/O securely if host-level LUKS cannot be automated inside Docker.
*   [ ] Configure `IPC_LOCK` memory allocation capabilities to allow the container to lock sensitive cryptographic keys in memory and prevent them from being swapped to disk.

### Testing & Quality Gates
*   Verify Docker compose/run configurations correctly pass `cap_add: ['IPC_LOCK']`.

### Files to Create/Modify
*   [MODIFY] `docker-compose.yml` (or create base templates)
*   [NEW] `docs/infrastructure_security.md` (Document the storage encryption assumptions/requirements for the host).

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Memory and storage security modeled.
2. **Coverage Gate:** N/A.
3. **Pipeline Green:** N/A.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 1.5: Establish Local Developer Experience
**Assignee:** [Dev C]
**Priority:** Blocked by 1.3

### User Story / Agentic Goal
As a Software Developer, I want a robust local Docker Compose setup that mirrors production but allows for rapid iteration (hot-reloading), so that I can develop efficiently without configuration drift.

### Context & Constraints
*   Explicitly combat "Compose Config Drift" as mandated by DevOps review.

### Acceptance Criteria
*   [ ] Create a base `docker-compose.yml` that strictly defines the production-like topology (immutable).
*   [ ] Create a `docker-compose.override.yml` that applies developer-specific overrides (e.g., Uvicorn hot-reloading bind mounts).
*   [ ] Integrate a local MinIO container to simulate S3/Blob storage for local testing.
*   [ ] Integrate a local Jaeger UI container to catch OpenTelemetry traces locally.
*   [ ] Create a `seeds.py` or initialization script to prepopulate the local database with safe, dummy test data.

### Testing & Quality Gates
*   `docker-compose up` cleanly boots the entire stack locally.
*   Editing a local python file triggers Uvicorn hot-reload inside the container.

### Files to Create/Modify
*   [NEW] `docker-compose.yml`
*   [NEW] `docker-compose.override.yml`
*   [NEW] `scripts/seeds.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Dev environment matches production topology.
2. **Coverage Gate:** N/A.
3. **Pipeline Green:** N/A.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 1.6: Docker Hardening
**Assignee:** [Dev D]
**Priority:** Blocked by 1.4

### User Story / Agentic Goal
As a SRE, I need the Docker execution environment hardened against common operational failures and attacks, so that the system doesn't crash from log exhaustion or leak secrets via plaintext environment variables.

### Context & Constraints
*   Crucial DevOps mandates for stability in a dark room environment.

### Acceptance Criteria
*   [ ] Enforce `--read-only` root filesystems for production containers in `docker-compose.yml`.
*   [ ] Configure Redis service in Docker Compose to disable disk dumps (`--save "" --appendonly no`) if used purely for ephemeral tasks.
*   [ ] Establish strict Docker log-rotation boundaries (`max-size: "50m"`, `max-file: "3"`) to prevent disk exhaustion.
*   [ ] Eradicate plaintext `.env` file reliance for sensitive secrets; configure `docker-compose.yml` to utilize tmpfs-backed Docker Secrets injected at `/run/secrets/`.

### Testing & Quality Gates
*   Attempt to write a file to the container root filesystem; it must fail (read-only enforcement).
*   Verify secrets are readable only from `/run/secrets/`.

### Files to Create/Modify
*   [MODIFY] `docker-compose.yml`
*   [MODIFY] Application config loading logic to prefer `/run/secrets/` over `os.environ`.

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Docker hardening standards perfectly met.
2. **Coverage Gate:** N/A.
3. **Pipeline Green:** Hardened container boots successfully.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 1.7: Air-Gap Artifact Bundler
**Assignee:** [Dev A]
**Priority:** Blocked by 1.6

### User Story / Agentic Goal
As a Release Engineer, I need a deterministic script to bundle all necessary artifacts (containers, scripts, configuration) into a portable format, so that it can be securely transported via sneaker-net into the air-gapped environment for deployment.

### Context & Constraints
*   Cannot assume internet access during the deployment phase.
*   Everything required to run the application must be bundled.

### Acceptance Criteria
*   [ ] Create a `Makefile` target `build-airgap-bundle`.
*   [ ] The script pulls/builds all required Docker images (App, Postgres, Redis, MinIO, Jaeger, etc.).
*   [ ] The script uses `docker save` to export images to tar archives.
*   [ ] The script copies `docker-compose.yml`, required scripts, and documentation into a staging directory.
*   [ ] The script compresses the staging directory into a final, portable artifact (e.g., `conclave-bundle-vX.tar.gz`) for offline transport.

### Testing & Quality Gates
*   Run the script on a connected machine.
*   Transfer the bundle to a disconnected environment/VM, extract it, `docker load` the images, and `docker-compose up`. The application must start flawlessly without attempting to pull from Docker Hub.

### Files to Create/Modify
*   [NEW] `Makefile`
*   [NEW] `scripts/build_airgap.sh`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Deterministic offline deployment achieved.
2. **Coverage Gate:** N/A.
3. **Pipeline Green:** Build script executes without errors.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.
