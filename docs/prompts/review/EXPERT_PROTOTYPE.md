# Expert "Everything" Review & Prototyping Audit

**Date:** 2026-03-12
**Scope:** Master execution plan, architectural viability, and technical risk assessment.

## Executive Summary
You are entirely correct to be suspicious. The current backlog is an excellent roadmap for *building a web application*, but it assumes the core *mathematics and physics* of synthetic data generation are simple integrations. They are not. 

By jumping straight from "Architectural Design" (Phase 0) to "Production CI/CD & Monolith Construction" (Phase 1), we have bypassed a critical agile step: **Technical Spikes (Fast-Fail Prototyping)**. If we proceed as-is, we risk spending 4 weeks building a beautiful, secure, air-gapped React SPA and PostgreSQL database, only to discover in Phase 4 that the Open-Source Synthesizer cannot run within our container memory limits, or that Format-Preserving Encryption destroys our database performance.

## Critical Architectural Uncertainties

The following are the largest "Black Boxes" in the current design that lack empirical validation:

### 1. The ML Synthesizer & Memory Constraints (Phase 4 Risk)
*   **The Assumption:** We can seamlessly run `SDV` (Synthetic Data Vault) or `OpenDP` inside a strictly constrained Docker container using CPU or pass-through GPU to generate relational datasets.
*   **The Reality:** Deep learning models for tabular data (like CTGAN or DP-SGD) are notorious memory hogs. They can easily trigger an Out-Of-Memory (OOM) kill from the Linux kernel if they attempt to load a massive Pandas dataframe into RAM before GPU offloading. 
*   **The Unknown:** Can we actually chunk and stream data into these open-source synthesizers without rewriting them from scratch? 

### 2. Format-Preserving Encryption vs. LUHN (Phase 3 Risk)
*   **The Assumption:** We can deterministically encrypt a Credit Card number so it always masks to the same fake number, while ensuring the fake number still passes the LUHN algorithm (modulus 10) validation.
*   **The Reality:** Standard AES encryption ruins formatting. Open-source Format-Preserving Encryption (FPE) libraries in Python (like `pyfpe`) are often abandoned, restrictive, or mathematically slow.
*   **The Unknown:** Is there a maintained, production-ready Python library we can use for deterministic FPE, or will the autonomous agents get stuck trying to invent a new cryptographic standard?

### 3. Topological Graph Streaming (Phase 3 Risk)
*   **The Assumption:** We can map a massive schema and selectively extract a 5% subset of records without loading the entire graph into memory.
*   **The Reality:** Relational transversal (fetching users, then fetching their orders, then the order lines) often requires recursive SQL queries that can lock production databases or cause infinite loops if circular dependencies exist.
*   **The Unknown:** Can `asyncpg` or `SQLAlchemy` safely execute a topological streaming yield on a 50-table schema without timing out?

### 4. Air-Gap Sneaker-Net Limits (Phase 1/4 Risk)
*   **The Assumption:** Our `make build-airgap-bundle` script will simply `tar` the Docker images and move them via USB.
*   **The Reality:** NVIDIA CUDA base images + PyTorch + SDV + the application code often result in Docker image artifacts exceeding **10GB**. Many restrictive environments have fat32 USB formatting limits (4GB per file) or strict malware scanning size limits.
*   **The Unknown:** Can we reliably chunk the deployment artifact?

---

## Action Plan: Injecting Phase 0.8 (Technical Spikes)

Before we authorize Phase 1 (Building the real app), we **must** create a new sandbox environment for isolated, throwaway prototypes. These spikes will be timeboxed (e.g., 4-8 hours) and require the agents to prove the concept in a simple Python script (`spike_dp_sgd.py`, `spike_fpe.py`) before integrating it into the master execution plan.

We need to inject **Phase 0.8: Technical Spikes (Fast-Fail Prototyping)** into the `BACKLOG.md` immediately following Phase 0.5.

**The Spikes:**
1.  **Spike A (ML Memory Physics):** Write a raw python script that attempts to train an open-source synthesizer on a local 500MB CSV file while Docker is hard-capped at 2GB of RAM. If it crashes, we must redesign Phase 4.
2.  **Spike B (Deterministic FPE):** Write a function that deterministically masks 10,000 SSNs and CC numbers using a static salt, verifying 0 collisions and 100% LUHN compliance. 
3.  **Spike C (Dependency Graphing):** Point a Python script at an open-source sample DB (like the Sakila DB) and prove we can programmatically extract the topological execution order and stream exactly 5% of the data without loading the DB entirely into RAM.

By explicitly forcing the agents to solve the hard math *first* in isolated scripts, we guarantee the monolith built in Phases 1-6 is architecturally sound.
