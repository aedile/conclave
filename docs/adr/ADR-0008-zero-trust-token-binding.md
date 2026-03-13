# ADR-0008: Zero-Trust JWT Token Binding

**Status:** Accepted
**Date:** 2026-03-13

## Context

JWT tokens can be stolen and replayed from any machine if they are not bound to the original
client's identity. This is a critical risk in air-gapped deployments where token theft via
network-level attacks is a realistic threat model.

## Decision

Embed a `bound_client_hash` claim in every access token. The claim is the SHA-256 hex digest
of the client identifier (mTLS SAN or IP address) present at token issuance. `verify_token()`
recomputes the hash from the current request and rejects mismatches using a constant-time
`hmac.compare_digest()` comparison to prevent timing side-channel attacks.

## Client identifier priority

1. `X-Client-Cert-SAN` header — set by the TLS-terminating reverse proxy after verifying the
   client certificate.

   **CRITICAL:** The reverse proxy MUST strip any incoming `X-Client-Cert-SAN` header from
   untrusted client requests before injecting its own verified value. Failure to do so allows
   clients to forge their identity.

2. First IP in `trusted_proxy_header` (default `X-Forwarded-For`) — only trusted when the
   service sits behind a known reverse proxy.

   **CRITICAL:** Direct-internet deployments MUST set `trusted_proxy_header=""` to disable
   header trust and fall back to the TCP peer address, preventing IP spoofing via forged headers.

3. `request.client.host` — direct TCP peer address used as the final fallback.

   When `request.client` is `None` (e.g. Unix socket transport or minimal ASGI scope),
   `extract_client_identifier()` raises `TokenVerificationError` with HTTP 400 rather than
   silently using a placeholder.

## Hash construction

`SHA-256(identifier.encode("utf-8"))` → 64-character lowercase hex digest. The comparison
in `verify_token()` uses `hmac.compare_digest()` to prevent timing side-channels. The raw
identifier is never stored in the token payload — only the hash.

## Framework boundary

All JWT logic lives in `synth_engine.shared.auth.jwt` (framework-agnostic). The FastAPI
`HTTPException` translation layer lives in
`synth_engine.bootstrapper.dependencies.auth.get_current_user()`. This separation ensures
`shared/` has no web-framework coupling and can be tested without a running ASGI application.

## Consequences

- Every token is cryptographically bound to the issuing client; stolen tokens cannot be
  replayed from a different origin.
- Proxy configuration is a hard security dependency — deployment runbooks must document the
  `X-Client-Cert-SAN` stripping requirement.
- Token rotation is required when the client's IP changes (e.g. mobile clients behind NAT).
  Applications should design token lifetimes and refresh flows accordingly.
- Future deployments using mutual TLS should prefer the `X-Client-Cert-SAN` path, as IP
  binding is weaker (NAT, CDN, shared egress).
