# Phase 0.6: Autonomous Agile Environment Provisioning

**Goal:** Establish the native Claude Code Agent Teams environment, utilizing local ChromaDB MCP for persistent semantic memory and Git Worktrees for parallel, collision-free execution, prior to any technical development.

---

## Task 0.6.1: Host Initialization & MCP Setup
**Assignee:** [PM / System Admin]
**Priority:** Critical Path
**Estimated Effort:** 1 day

### User Story / Agentic Goal
As the Lead Orchestrator, I need the Claude Code CLI configured for experimental Agent Teams and a local Vector Database, so that I can spawn parallel developer streams that share a persistent "Agile Memory" without requiring external API calls.

### Context & Constraints
*   Requires Apple Silicon M4 unified memory architecture.
*   Must utilize the official `@modelcontextprotocol/server-chroma`.

### Acceptance Criteria
*   [ ] The host environment `.bashrc` or `.zshrc` is updated to export `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`.
*   [ ] The ChromaDB MCP server is installed (`npx -y @modelcontextprotocol/server-chroma`).
*   [ ] The Claude Code MCP configuration (`~/.claude/claude_mcp.json` or equivalent) is updated to include the `chroma` server.
*   [ ] The PM executes `chroma_create_collection` to establish the `ADRs`, `Retrospectives`, and `Constitution` namespaces.

### Files to Create/Modify
*   [NEW] `scripts/setup_agile_env.sh`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** MCP and Agent Teams enabled.
2. **Coverage Gate:** N/A.
3. **Pipeline Green:** Local setup script executes without error.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 0.6.2: Memory Seeding (Governance)
**Assignee:** [PM]
**Priority:** Blocked by 0.6.1
**Estimated Effort:** 1 day

### User Story / Agentic Goal
As the PM, I want the project's core constitutional rules and architectural mandates vectorized into the shared memory, so that every Developer Stream automatically retrieves them via RAG before writing code, preventing context drift.

### Context & Constraints
*   Must use explicit `chroma_add_documents` calls.

### Acceptance Criteria
*   [ ] Read the contents of `CONSTITUTION.md`.
*   [ ] Read the contents of `docs/ARCHITECTURAL_REQUIREMENTS.md`.
*   [ ] Execute MCP tool calls to inject these documents into the `Constitution` and `ADRs` ChromaDB collections.
*   [ ] Verify retrieval by simulating a query for "What is the logging policy?" from the database.

### Files to Create/Modify
*   [MODIFY] Local ChromaDB instance (State change, no file commit required in repository).

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Persistent governance memory established.
2. **Coverage Gate:** N/A.
3. **Pipeline Green:** N/A.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 0.6.3: Team Scaffolding & Git Worktree Hooks
**Assignee:** [PM / System Admin]
**Priority:** Blocked by 0.6.1
**Estimated Effort:** 1-2 days

### User Story / Agentic Goal
As a DevOps Engineer, I need native Git Worktree hooks and distinct Developer Streams configured in Claude, so that when 4 distinct agents spawn, their file modifications are perfectly isolated and localhost ports do not collide.

### Context & Constraints
*   Must utilize Claude Code's native hooks (`PreToolUse`, `WorktreeCreate`).

### Acceptance Criteria
*   [ ] Utilize the `TeamCreate` internal command to scaffold the `~/.claude/teams/conclave-engine` directory with 1 PM and 4 Developer configurations.
*   [ ] Create a `.claude/hooks/worktree_create.sh` script that allocates a block of 5 unique localhost ports to a `.env.local` file when a worktree is created.
*   [ ] Create a `.claude/hooks/pre_tool_use.sh` script that intercepts commands like `pytest` or `npm run dev` and automatically injects the worktree's specific port block from the `.env.local`.

### Files to Create/Modify
*   [NEW] `.claude/hooks/worktree_create.sh`
*   [NEW] `.claude/hooks/pre_tool_use.sh`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Solves `EADDRINUSE` port hoarding and file collisions.
2. **Coverage Gate:** N/A.
3. **Pipeline Green:** Hooks execute correctly.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 0.6.4: Task Queue Initialization & JSON Migration
**Assignee:** [PM]
**Priority:** Blocked by 0.6.3
**Estimated Effort:** 1 day

### User Story / Agentic Goal
As an Agile PM, I want all the markdown-based backlog tasks migrated into Claude Code's native JSON task queue, so that the Developer Streams can autonomously claim tasks based on computational dependencies without deadlocking.

### Context & Constraints
*   Must utilize the `TaskCreate` tool to generate the JSON lock files in `~/.claude/tasks/conclave-engine/`.
*   Must enforce the graph dependencies (e.g., Phase 1 blocks Phase 2).

### Acceptance Criteria
*   [ ] The PM parses `docs/backlog/phase-*.md`.
*   [ ] The PM invokes `TaskCreate` for each identified constraint, explicitly setting the `blocks` or `depends_on` parameters mathematically.
*   [ ] Verify the queue structure in the local `.claude` directory is populated and valid.

### Files to Create/Modify
*   [MODIFY] Local `~/.claude/tasks/` state (No Git commit required).

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Native task queuing operational.
2. **Coverage Gate:** N/A.
3. **Pipeline Green:** N/A.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.
