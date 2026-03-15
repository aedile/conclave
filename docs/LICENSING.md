# Conclave Engine — Licensing

## Overview

The Conclave Engine uses an **offline activation protocol** designed for
air-gapped deployments where the server has no internet access. A license is
issued as a signed JWT (RS256) that is generated on an internet-connected device
and transferred manually to the air-gapped machine.

This document describes:

1. The hardware binding model
2. The challenge/response activation flow
3. The JWT structure and claims
4. The `LicenseGateMiddleware` enforcement behavior
5. Key rotation and re-activation procedures

---

## 1. Hardware Binding

Each license is bound to a specific machine. The binding is based on a
**hardware ID** — a SHA-256 digest derived from the machine's MAC address and a
static application seed.

### How the Hardware ID Is Computed

The hardware ID is computed by `get_hardware_id()` in
`src/synth_engine/shared/security/licensing.py`:

```text
hardware_id = SHA-256( MAC_hex_bytes || b"conclave-license-v1" )
```

Where:

- `MAC_hex_bytes` is the machine's primary MAC address as a 12-character
  lowercase hexadecimal ASCII string (e.g., `"aabbccddeeff"`), encoded as
  UTF-8 bytes.
- `b"conclave-license-v1"` is a fixed application seed that prevents a bare
  MAC address from being usable as a hardware ID in another application.
- The result is the lowercase hexadecimal representation of the 32-byte
  SHA-256 digest (64 characters).

### Container Deployment Warning

In Docker containers, `uuid.getnode()` (the Python function used to retrieve
the MAC address) may return a randomly generated value if the container's
network interface does not expose a fixed MAC. This causes the hardware ID to
differ across container restarts, invalidating the license.

**Production containers MUST either:**

- Assign a fixed MAC address to the container's network interface:

  ```yaml
  # docker-compose.override.yml
  services:
    app:
      mac_address: "02:42:ac:11:00:02"
  ```

- Or inject a stable machine identifier via the `LICENSE_PUBLIC_KEY` flow and
  coordinate with the licensing server to use a non-MAC-based hardware ID in
  a future key issuance.

---

## 2. Challenge/Response Activation Flow

Activation uses a three-step protocol:

```text
[Air-gapped machine]                    [Internet-connected device]
        |                                          |
  1. GET /license/challenge                        |
        | ← { hardware_id, app_version,            |
        |      timestamp, qr_code_data_url }        |
        |                                          |
  2. Operator copies hardware_id ────────────────► |
     (or scans QR code)                            |
                                                   | → submit to licensing server
                                                   | ← signed JWT
                                                   |
  3. Operator copies JWT back ◄───────────────────|
        |                                          |
  4. POST /license/activate                        |
     { "token": "<JWT>" }                          |
        | ← { "status": "licensed",                |
        |      "hardware_id": "...",               |
        |      "expires_at": "..." }               |
```

### Step 1: Generate a Challenge

```bash
curl http://<host>:8000/license/challenge
```

Response (HTTP 200):

```json
{
  "hardware_id": "a1b2c3d4e5f6...",
  "app_version": "0.1.0",
  "timestamp": "2026-03-15T10:00:00+00:00",
  "qr_code_data_url": "data:image/png;base64,...",
  "alt_text": "QR code encoding hardware ID a1b2c3..."
}
```

The `qr_code_data_url` field is a Base64-encoded PNG data URL containing a QR
code that encodes the `hardware_id`. The operator can scan this code with a
mobile device to avoid manually transcribing the 64-character hex string.
The `alt_text` field is provided for screen-reader accessibility (WCAG 1.1.1).

### Step 2: Request a Signed JWT

On an internet-connected device, submit the `hardware_id` to the Conclave
licensing server (the URL and protocol for contacting the licensing server are
provided separately by your Conclave representative). The licensing server
verifies the hardware ID and returns a signed JWT.

### Step 3: Activate the License

Transfer the signed JWT to the air-gapped machine and submit it:

```bash
curl -X POST http://<host>:8000/license/activate \
  -H "Content-Type: application/json" \
  -d '{"token": "<signed-JWT-from-licensing-server>"}'
```

Successful response (HTTP 200):

```json
{
  "status": "licensed",
  "hardware_id": "a1b2c3d4...",
  "expires_at": "2027-03-15T00:00:00+00:00"
}
```

After successful activation, `LicenseState._is_licensed` is set to `True` and
all API routes become available (subject to the vault also being unsealed).

### Error Responses

| HTTP Status | Cause |
|-------------|-------|
| 403 Forbidden | Invalid JWT signature (wrong key or tampered token) |
| 403 Forbidden | JWT has expired (`exp` claim in the past) |
| 403 Forbidden | `hardware_id` claim in the JWT does not match this machine |
| 422 Unprocessable Entity | Request body missing `token` field |

---

## 3. JWT Structure

License JWTs use the RS256 algorithm (RSA + SHA-256). The private key for
signing **never leaves the licensing server**. The air-gapped machine holds
only the corresponding public key, used for signature verification.

### Standard Claims

| Claim | Type | Description |
|-------|------|-------------|
| `iss` | string | Issuer — identifies the licensing server |
| `sub` | string | Subject — identifies the licensee |
| `iat` | integer (Unix timestamp) | Issued At |
| `exp` | integer (Unix timestamp) | Expiry — token is invalid after this time |

### Custom Claims

| Claim | Type | Description |
|-------|------|-------------|
| `hardware_id` | string | SHA-256 hardware ID of the licensed machine (64-character hex) |

### Validation Logic

`verify_license_jwt()` in `shared/security/licensing.py` performs:

1. Signature verification against the configured RSA public key (RS256).
2. `exp` claim validation — `PyJWT` rejects expired tokens automatically.
3. `hardware_id` claim presence check — the claim must be present.
4. `hardware_id` binding check — the claim must equal the local
   `get_hardware_id()` result.

Any failure raises `LicenseError` (HTTP 403).

### Public Key Resolution Order

The public key used for JWT verification is resolved in this order:

1. The `LICENSE_PUBLIC_KEY` environment variable (PEM-encoded RSA public key).
2. The key embedded in the application binary at build time.

In production, always set `LICENSE_PUBLIC_KEY` via Docker secrets or a
secrets manager. Never rely on the embedded placeholder key in production.

---

## 4. LicenseGateMiddleware Behavior

`LicenseGateMiddleware` in
`src/synth_engine/bootstrapper/dependencies/licensing.py` is a Starlette
middleware that runs on every HTTP request.

### Enforcement Rule

While `LicenseState._is_licensed` is `False`:

- **Non-exempt paths** receive HTTP **402 Payment Required** with an RFC 7807
  Problem Details response body.
- **Exempt paths** pass through normally.

### Exempt Paths

The following paths are accessible without a license:

| Path | Purpose |
|------|---------|
| `/health` | Service health check |
| `/unseal` | Vault unseal (required before activation) |
| `/metrics` | Prometheus metrics |
| `/docs` | API documentation |
| `/redoc` | API documentation (alternate) |
| `/openapi.json` | OpenAPI schema |
| `/license/challenge` | Generate activation challenge |
| `/license/activate` | Submit signed JWT |
| `/security/shred` | Emergency cryptographic erasure |
| `/security/keys/rotate` | Key rotation |

### Middleware Ordering

`SealGateMiddleware` runs **outside** (first) and `LicenseGateMiddleware` runs
**inside** (second). A sealed vault returns HTTP 423 before the license check
fires. The order of checks is:

```text
Request → SealGateMiddleware (423?) → LicenseGateMiddleware (402?) → Route Handler
```

---

## 5. Key Rotation and Re-Activation

### 5.1 When the License Public Key Changes

If the licensing server rotates its RSA key pair:

1. The licensing server issues a new JWT signed with the new private key.
2. The operator sets the new RSA public key in the `LICENSE_PUBLIC_KEY`
   environment variable (or as a Docker secret).
3. Restart the `app` service to pick up the new key:

   ```bash
   docker compose up -d --no-deps app
   ```

4. Unseal the vault (see Operator Manual Section 4).
5. Re-activate by following the challenge/response flow (Section 2).

### 5.2 When the License JWT Expires

JWTs include an `exp` claim. When the license expires:

1. All non-exempt routes return HTTP 402 Payment Required.
2. Follow the challenge/response flow (Section 2) to obtain a new JWT from
   the licensing server.
3. `POST /license/activate` with the new token — no service restart is required.

### 5.3 Changing the Licensed Machine

A license is hardware-bound. To move the license to a different machine:

1. On the new machine, `GET /license/challenge` to obtain the new `hardware_id`.
2. Submit the new `hardware_id` to the licensing server to obtain a new JWT
   bound to the new machine.
3. `POST /license/activate` on the new machine with the new JWT.

The original license (bound to the old machine's hardware ID) becomes
effectively unusable on the new machine — the `hardware_id` binding check will
reject it.
