# ADR-0022 — Offline License Activation Protocol

**Date:** 2026-03-15
**Status:** Accepted
**Deciders:** PM + Architect
**Task:** P5-T5.2 — Offline License Activation Protocol

---

## Context

The Conclave Engine is deployed in air-gapped environments with no outbound network
access. It requires a software license mechanism that:

1. Does not require the deployed machine to call a remote license server at runtime.
2. Binds the license to the specific machine to prevent license sharing.
3. Allows license issuance from an internet-connected server without any direct
   connection to the air-gapped machine.
4. Provides a usable operator workflow for transferring the license across the
   air gap (physical media, QR code scanning).

The design must adhere to the project's security-first posture: the private key
used to sign license tokens must never be embedded in the application binary.

---

## Decision

### 1. Hardware Binding via `uuid.getnode()` + SHA-256 + App Seed

The machine identity (`hardware_id`) is computed as:

```
hardware_id = SHA-256( hex(uuid.getnode()) + b"conclave-license-v1" )
```

`uuid.getnode()` returns the primary MAC address as a 48-bit integer. The static
application seed (`b"conclave-license-v1"`) prevents the bare MAC from being
submitted to a different application's licensing server and accepted.

**Container/VM Limitation:** In Docker or Kubernetes environments where the network
interface is virtual and the MAC is not explicitly assigned, `uuid.getnode()` may
return a randomly generated value per process invocation. This causes the
`hardware_id` to change across container restarts, invalidating the license.

Production containerized deployments MUST either:
- Assign a static MAC address to the container network interface (`--mac-address`
  in Docker, `spec.template.spec.containers[].securityContext.allowPrivilegeEscalation`
  + network policy in Kubernetes), or
- Use an alternative stable machine identifier (e.g., mounted from a Kubernetes
  Secret, or derived from a persistent volume UUID) injected via environment variable.

This limitation is accepted for the initial implementation. A future ADR may
introduce an environment-variable override for `hardware_id` when container
stability requirements are confirmed.

### 2. RS256 Asymmetric JWT Trust Model

License tokens are RS256-signed JWTs. The trust model:

- **Private key:** Lives exclusively on the central licensing server. Never
  distributed, never embedded in the application binary.
- **Public key:** Embedded in the application binary as `_EMBEDDED_PUBLIC_KEY`.
  Can be overridden at runtime via the `LICENSE_PUBLIC_KEY` environment variable
  (for key rotation without redeployment).
- **JWT claims:** `hardware_id` (required), `exp` (required — enforced by PyJWT),
  `licensee` and `tier` (optional, surfaced in the activation response).

RS256 was chosen over HS256 because asymmetric signing prevents a compromised
application binary from being used to forge its own license: an attacker with
access to the binary has only the public key, which cannot sign new tokens.

PyJWT (already a project dependency via T2.4 vault work) was used rather than
introducing a new library (CLAUDE.md: "Justify every dependency; prefer stdlib").

### 3. LicenseState Class-Level Singleton

`LicenseState` uses class-level attributes (not instance state) to hold the
activation status and JWT claims, mirroring the `VaultState` pattern established
in T2.4. This approach:

- Requires no dependency injection: the middleware and route handlers can call
  `LicenseState.is_licensed()` directly.
- Is thread-safe via `threading.Lock` on mutations (`activate()`, `deactivate()`).
- Is reset per-test via the `reset_license_state` autouse fixture (calls
  `LicenseState.deactivate()` in teardown).

The singleton pattern is appropriate because license state is process-global: the
entire application is either licensed or it is not.

### 4. `qrcode[pil]` Dependency for Air-Gap Activation

The `/license/challenge` endpoint renders the challenge payload as a base64-encoded
PNG QR code (`qr_code` field). This allows the operator to:

1. Display the QR code on the air-gapped machine's screen or a connected monitor.
2. Scan it with a mobile device that has internet access.
3. Submit the challenge to the licensing server via the mobile device.
4. Receive the signed JWT and copy it back to the air-gapped machine.

`qrcode[pil]` (version `>=8.0.0,<9.0.0`) is the canonical Python QR code library
with Pillow integration for PNG rendering. Pillow is pinned directly
(`pillow = ">=12.0.0,<13.0.0"`) to ensure reproducibility and enable security
audits via `pip-audit`.

A text fallback (base64-encoded JSON) is returned if the `qrcode`/Pillow import
fails (e.g., in minimal environments). This ensures the endpoint remains functional
even if image rendering is unavailable.

### 5. Middleware Evaluation Order

The application adds two security gate middlewares in LIFO order:

```
app.add_middleware(LicenseGateMiddleware)  # added first → evaluated second (inner)
app.add_middleware(SealGateMiddleware)      # added second → evaluated first (outer)
```

Evaluation order: `SealGateMiddleware` (423 Locked) → `LicenseGateMiddleware` (402
Payment Required) → route handler.

This ordering ensures that a sealed vault returns 423 before the license gate fires.
A sealed vault means the KEK has not been derived — no database access is possible —
so checking the license state in that condition is moot.

Both middleware classes share a common exempt path set structure (frozenset of
strings) to ensure consistency. The license-exempt paths are a superset of the
seal-exempt paths, adding `/license/challenge` and `/license/activate`.

### 6. Production Key Deployment via `LICENSE_PUBLIC_KEY`

The `LICENSE_PUBLIC_KEY` environment variable overrides the embedded placeholder key
at runtime. This supports:

- **Key rotation** without redeployment: update the env var and restart the service.
- **Multi-environment deployments:** different keys for staging vs. production.
- **Container deployments:** inject the key via a Kubernetes Secret or Docker secret
  mounted as an env var.

The embedded key (`_EMBEDDED_PUBLIC_KEY`) is a placeholder with a corresponding
private key that is not distributed. It is present solely to satisfy the module's
type contract (a non-empty string). Any JWT signed with the placeholder's private
key would technically validate against it, but since the private key is never
released, this is not a practical attack vector.

The `.env.example` file documents `LICENSE_PUBLIC_KEY` under a "License Activation"
section with instructions for obtaining the production key.

---

## Consequences

### Positive
- Air-gap activation is possible with no outbound network access from the deployed
  machine.
- Asymmetric cryptography prevents license forgery even if the binary is extracted.
- QR code workflow minimises operator error during the physical key transfer.
- Thread-safe singleton matches the established `VaultState` pattern — no new
  architectural patterns introduced.

### Negative / Risks
- `uuid.getnode()` is unstable in containerized environments (documented above).
- The embedded placeholder key could confuse developers who expect the application
  to be "ready to use" without configuration — mitigated by the prominent
  `PLACEHOLDER_KEY_NOT_FOR_PRODUCTION_USE` comment.
- Key rotation requires an environment variable update and service restart — not
  zero-downtime. Accepted for the air-gapped deployment model.

### Neutral
- PyJWT `exp` claim validation has a default 0-second leeway. Operators issuing
  tokens should account for clock skew between the licensing server and the
  air-gapped machine (recommend ±5 minute leeway on the server-side `exp` value).
