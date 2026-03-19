# Phase 5: Orchestration, UI, & Licensing

**Goal:** Fulfill the WCAG 2.1 AA mandates, deliver the offline dashboard via React, and secure network licensing.

---

## Task 5.1: Build Task Orchestration API Core
**Assignee:** [Dev A]
**Priority:** Blocked by 3.5
**Estimated Effort:** 4 days

### User Story / Agentic Goal
As a Frontend Developer, I want a robust, fully documented REST API layer wrapping the asynchronous Huey jobs, so that the React SPA can enqueue long-running data synthesis jobs and monitor their progress without managing complex socket states.

### Context & Constraints
*   All error responses must strictly adhere to RFC 7807 (Problem Details for HTTP APIs).
*   Must utilize Server-Sent Events (SSE) for unidirectional progress streaming, avoiding full WebSockets due to common enterprise firewall interference.

### Acceptance Criteria
*   [ ] Implement standard CRUD endpoints for Jobs, Connections, and Settings with Cursor-Based Pagination.
*   [ ] Implement a `POST /jobs/{id}/start` endpoint that enqueues the Huey job and returns an immediate 202 Accepted.
*   [ ] Implement a `GET /jobs/{id}/stream` endpoint that yields Server-Sent Events (SSE) representing real-time percentage progress and logs from the underlying Huey worker.
*   [ ] Configure `datamodel-code-generator` (or similar) to automatically output TypeScript interfaces from the FastAPI Pydantic models during the build process to guarantee frontend/backend type sync.

### Testing & Quality Gates
*   Write an integration test that creates a mock 10-second Huey job, connects to the SSE endpoint, and verifies that it receives sequential `progress` events (10%, 20%, etc.) until `complete`.
*   Verify that any unhandled exception in an endpoint yields a valid RFC 7807 JSON response (with `type`, `title`, `status`, and `detail` fields).

### Files to Create/Modify
*   [NEW] `src/synth_engine/api/routers/jobs.py`
*   [NEW] `src/synth_engine/api/sse.py`
*   [NEW] `src/synth_engine/api/errors (rfc7807 formatter).py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** RFC 7807 and SSE implemented correctly.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 5.2: Implement Offline License Activation Protocol
**Assignee:** [Dev B]
**Priority:** Blocked by 3.5
**Estimated Effort:** 4 days

### User Story / Agentic Goal
As a System Admin, I want to activate the software in a disconnected environment securely, so that I can prove I have a valid enterprise license without the software "phoning home" to an external server.

### Context & Constraints
*   System is strictly air-gapped.
*   Offline activation usually requires a challenge/response mechanism visually (e.g., QR codes) or via copy-pasted cryptographic payloads.

### Acceptance Criteria
*   [ ] Implement a `/license/challenge` API that generates a hardware-bound payload (e.g., hashing the machine's MAC address + static app seed) and renders it as a QR code or text block.
*   [ ] Implement a `/license/activate` API that accepts a signed JWT provided by the human operator (generated externally on a connected device by a central licensing server).
*   [ ] The application validates the JWT's signature (using an embedded public key) and verifies the `hardware_id` claim matches the local machine.
*   [ ] Ensure `is_sealed` or similar global blocks enforce license validity.

### Testing & Quality Gates
*   Write a unit test generating a mock signed JWT on the fly with a matching hardware ID. The `/activate` endpoint must accept it.
*   Modify the JWT's hardware ID claim or signature. The `/activate` endpoint must reject it.

### Files to Create/Modify
*   [NEW] `src/synth_engine/shared/security/licensing.py`
*   [MODIFY] `src/synth_engine/api/routers/system.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Zero external network calls required.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 5.3: Build Accessible React SPA & "Vault Unseal"
**Assignee:** [Dev C]
**Priority:** Blocked by 3.5
**Estimated Effort:** 6 days

### User Story / Agentic Goal
As a Compliance Officer, I want a clean, strictly accessible (WCAG 2.1 AA) web dashboard starting with a secure "Vault Unseal" screen, so that I can use the tool effectively regardless of visual or physical impairments, and without the browser attempting to load external assets.

### Context & Constraints
*   Strict `Content-Security-Policy` (CSP). NO external CDNs (no Google Fonts, no external Tailwind CDN).
*   Must pass automated `@axe-core` accessibility checks.

### Acceptance Criteria
*   [ ] Scaffold a modern React application (e.g., via Vite).
*   [ ] Configure strict CSP headers served via the web server (or meta tags if standalone) denying all external `script-src`, `font-src`, and `style-src`.
*   [ ] Bundle local fonts (WOFF2) directly into the repository.
*   [ ] Implement the root-level `/unseal` Router guard. If the backend returns `423 Locked`, the user is forced to this screen to enter the master passphrase.
*   [ ] Ensure the Unseal input explicitly differentiates between "Network Error" and "Invalid Passphrase" to prevent confusing operators.

### Testing & Quality Gates
*   Run `@axe-core/playwright` on the Unseal screen. Must report 0 violations.
*   Verify via network intercept tools that NO requests attempt to communicate outside `localhost` or the provided domain.

### Files to Create/Modify
*   [NEW] `frontend/package.json`
*   [NEW] `frontend/src/App.tsx`
*   [NEW] `frontend/src/routes/Unseal.tsx`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Offline CSP and WOFF2 bundling confirmed.
2. **Coverage Gate:** >= 90% (Frontend Unit Tests via Vitest/Jest).
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 5.4: Develop Data Synthesis Dashboard UX
**Assignee:** [Dev D]
**Priority:** Blocked by 3.5
**Estimated Effort:** 4 days

### User Story / Agentic Goal
As a Data Scientist, I want a predictable, recoverable dashboard for monitoring 3-hour long generation jobs, so that if I accidentally refresh my browser tab, I don't lose the progress tracker for the running backend job.

### Context & Constraints
*   Must consume the SSE streams created in Task 5.1.
*   Must gracefully handle RFC 7807 errors.

### Acceptance Criteria
*   [ ] Implement the `JobDashboard` component that displays active jobs.
*   [ ] Implement `EventSource` logic to consume the SSE progress endpoint.
*   [ ] Implement structural `aria-live="polite"` regions so screen readers announce "Job Synthesis reached 50%" without requiring user interaction.
*   [ ] Store the currently active `JobId` in browser `localStorage`. On component mount, automatically attempt to reconnect to the stream if an active job is found, preventing rehydration loss on page refresh.
*   [ ] Implement a global error boundary/toast system that neatly parses RFC 7807 JSON formats and renders human-readable remediation cards.

### Testing & Quality Gates
*   Run `@axe-core/playwright` on the Dashboard screen while a mock job progresses. Must report 0 violations.
*   Integration test in Playwright: Start a job, force a page reload (`page.reload()`), and assert the progress bar correctly resumes its state.

### Files to Create/Modify
*   [NEW] `frontend/src/routes/Dashboard.tsx`
*   [NEW] `frontend/src/hooks/useSSE.ts`
*   [NEW] `frontend/src/components/AriaLive.tsx`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** State rehydration and accessibility mandates met.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 5.5: Implement Cryptographic Shredding & Re-Keying API
**Assignee:** [Dev A]
**Priority:** Blocked by 5.1
**Estimated Effort:** 3 days

### User Story / Agentic Goal
As a Security Officer, I want an API endpoint that initiates an instantaneous cryptographic shred of all PII data or rotates the Application-Level Encryption keys, so that we can comply with emergency data spillage protocols.

### Context & Constraints
*   Relies on the ALE implementation from Task 2.2.
*   Must be an asynchronous Huey task due to the volume of data that might need re-encryption.

### Acceptance Criteria
*   [ ] Implement `POST /security/keys/rotate` endpoint (triggering a Huey task).
*   [ ] The job must iterate over all tables containing encrypted PII, decrypt using the *old* KEK, re-encrypt using the *new* KEK, and commit the changes.
*   [ ] Implement `POST /security/shred` endpoint that immediately zeroizes the master wrapping key entirely, rendering all DB cipher-text permanently unrecoverable, followed by a `pg_stat_file` or equivalent wipe confirmation.

### Testing & Quality Gates
*   Integration test: Insert a PII record. Call `/rotate`. Assert the raw database cipher-text has mathematically changed, but ORM decryption still returns the correct original value.
*   Integration test: Call `/shred`. Attempt to read the PII record via ORM. Assert it explicitly fails with a `DecryptionError`.

### Files to Create/Modify
*   [NEW] `src/synth_engine/api/routers/security.py`
*   [NEW] `src/synth_engine/shared/security/rotation.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Supports zeroization mandates.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.
