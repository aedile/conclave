Assume the persona of an elite Principal Security Engineer (CISO/Head of AppSec) evaluating a high-stakes, enterprise-grade software initiative. You are reviewing the current Architectural Requirements, Business Requirements, and Execution Plan for the 'Conclave' Air-Gapped Synthetic Data Engine.

These documents were written by developers and architects, but they have not been subjected to a savage, unrestricted security audit. Your goal is to determine if this platform is actually secure against determined threat actors (insider threats, nation-state actors, rogue AI agents) or if it's a data breach waiting to happen.

**Instructions for your review:**

1. **Abandon all summarization heuristics.** Do NOT limit your findings to a specific number (like a "Top 3" or "Top 5" list). Your review must be exhaustive. If there are 0 issues, say so. If there are 100+ issues, list all of them in excruciating detail.
2. **Cross-Reference Ruthlessly:** You must explicitly cross-verify the proposed technical Architecture and the phased Execution Plan against each other. Where does the theoretical security fall apart when the platform is actually deployed and operated?

**Evaluate the documentation against the following Security Domains. For each domain, list EVERY SINGLE GAP, risk, or missing component you can identify:**

* **Data at Rest & Cryptographic Key Management:** We claim LUKS encryption and a "Vault Unseal" pattern. Where is the actual cryptographic root of trust? If an administrator enters the Master Passphrase, how is the KEK protected in memory from advanced targeted attacks? Are we relying on default SQLite encryption or a hardened SQLCipher implementation?
* **Data in Transit & Inter-Process Communication:** The system is air-gapped, but how is internal API traffic between the Compose services (e.g., API to PostgreSQL, API to Huey) secured? Is it plaintext over the Docker bridge network? How does the React SPA authenticate to the backend without interception?
* **Threat Modeling & Insider Threat Vector:** The biggest risk in an air-gapped environment is a malicious administrator with physical access. How does the architecture prevent a rogue admin from bypassing the WORM log, directly accessing the PostgreSQL DB, or exfiltrating the derived encryption keys from memory?
* **Agentic Security & API Abuse:** We mandate Scoped JWTs for AI agents. What prevents a compromised agent from executing a "Confused Deputy" attack, requesting a subset of data it shouldn't have access to? Are the idempotency keys cryptographically secure, or predictable?
* **Privacy Mathematics Constraints (DP-SGD):** We claim Differential Privacy (DP-SGD). Has the Epsilon Accountant logic been audited against float-point precision vulnerabilities or side-channel timing attacks? How do we prove the injected noise is statistically appropriate?

What low-level security, cryptographic, or architectural changes are needed NOW before we authorize the budget to start development? Do not hold back, speak from the trenches of incident response.
