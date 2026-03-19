# Business Requirements — Conclave Engine

## Executive Summary

Enterprise engineering and data science teams need statistically faithful copies of production
databases for development, QA, and ML training. Regulations (GDPR, CCPA, HIPAA, and sector
controls such as FedRAMP) prohibit raw PII from leaving production boundaries, and SaaS data
platforms that require data egress are categorically disqualified in defense, healthcare, and
critical-infrastructure environments.

Conclave solves this by running entirely within the customer's perimeter on the customer's
hardware. It produces synthetic replicas that preserve the statistical distributions, referential
integrity, and schema topology of the source database — with no real PII in the output and no
network calls out. The core capabilities required to meet these needs are:

- **Differential Privacy (DP-SGD)** — CTGAN training with Opacus discriminator-level DP provides
  a mathematically rigorous epsilon/delta privacy guarantee over the generated dataset.
- **Deterministic Masking** — HMAC-SHA256–seeded Faker ensures a given real value always maps to
  the same fake value across all tables, preserving FK join integrity without exposing PII.
- **FK-Consistent Subsetting** — Topological traversal of the FK graph extracts a percentage
  slice of production with zero orphan rows, enabling right-sized test environments.
- **WORM Audit Trail** — Cryptographically signed, append-only audit records satisfy compliance
  mandates for demonstrating that every data operation was authorised and logged.
- **Air-Gap Deployment** — No license call-home, no model registry cloud pull, no telemetry.
  Offline license activation via RS256 JWT with hardware binding.

## Target Users

| Role | Primary Need |
|------|-------------|
| Data scientist / ML engineer | Statistically faithful training data without PII |
| QA / test engineer | Structurally intact subset of production schema |
| Compliance / DPO | Mathematical proof that no real PII left the perimeter |
| Platform / DevOps | Self-contained stack that runs on existing on-premises hardware |

## Compliance Drivers

GDPR Article 25 (data protection by design), HIPAA Safe Harbor de-identification, CCPA right to
deletion, and FedRAMP low/moderate baseline all require that PII not be used in non-production
environments without explicit controls. Conclave satisfies these requirements by generating
synthetic data that is not derived from any individual record, providing an audit trail of every
synthesis operation, and supporting cryptographic erasure of source-derived artefacts.
