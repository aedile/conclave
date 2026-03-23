# Conclave Engine — Licensing

## Overview

Conclave uses an **offline activation protocol** for air-gapped deployments. A license is a signed JWT (RS256) generated on an internet-connected device and transferred manually to the air-gapped machine.

Topics covered:
1. Hardware binding model
2. Challenge/response activation flow
3. JWT structure and claims
4. `LicenseGateMiddleware` enforcement
5. Key rotation and re-activation

---

## 1. Hardware Binding

Each license is bound to a specific machine via a **hardware ID** — a SHA-256 digest of the machine's MAC address and a static application seed.

### How the Hardware ID Is Computed

`get_hardware_id()` in `src/synth_engine/shared/security/licensing.py`:

```text
hardware_id = SHA-256( MAC_hex_bytes || b"conclave-license-v1" )
```

- `MAC_hex_bytes`: primary MAC address as 12-character lowercase hex ASCII string (e.g., `"aabbccddeeff"`), UTF-8 encoded.
- `b"conclave-license-v1"`: fixed application seed preventing bare MAC addresses from being valid hardware IDs in other applications.
- Result: lowercase hex representation of the 32-byte SHA-256 digest (64 characters).

### Container Deployment Warning

`uuid.getnode()` may return a randomly generated value in containers without a fixed MAC, causing the hardware ID to change across restarts.

**Production containers MUST either:**

- Assign a fixed MAC address:
  ```yaml
  # docker-compose.override.yml
  services:
    app:
      mac_address: "02:42:ac:11:00:02"
  ```
- Or coordinate with the licensing server to use a non-MAC-based hardware ID.

---

## 2. Challenge/Response Activation Flow

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
  "app_version": "1.0.0rc1",
  "timestamp": "2026-03-15T10:00:00+00:00",
  "qr_code_data_url": "data:image/png;base64,...",
  "alt_text": "QR code encoding hardware ID a1b2c3..."
}
```

The `qr_code_data_url` is a Base64-encoded PNG encoding the `hardware_id` — scan with a mobile device to avoid transcribing the 64-character hex string. The `alt_text` field satisfies WCAG 1.1.1.

### Step 2: Request a Signed JWT

On an internet-connected device, submit the `hardware_id` to the Conclave licensing server (contact your Conclave representative for the server URL and protocol).

### Step 3: Activate the License

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

After activation, `LicenseState._is_licensed` is set to `True` and all API routes become available (subject to vault also being unsealed).

### Error Responses

| HTTP Status | Cause |
|-------------|-------|
| 403 Forbidden | Invalid JWT signature |
| 403 Forbidden | JWT expired |
| 403 Forbidden | `hardware_id` claim does not match this machine |
| 422 Unprocessable Entity | Request body missing `token` field |

---

## 3. JWT Structure

License JWTs use RS256 (RSA + SHA-256). The private key never leaves the licensing server. The air-gapped machine holds only the public key for signature verification.

### Standard Claims

| Claim | Type | Description |
|-------|------|-------------|
| `iss` | string | Issuer — identifies the licensing server |
| `sub` | string | Subject — identifies the licensee |
| `iat` | integer (Unix timestamp) | Issued At |
| `exp` | integer (Unix timestamp) | Expiry |

### Custom Claims

| Claim | Type | Description |
|-------|------|-------------|
| `hardware_id` | string | SHA-256 hardware ID of the licensed machine (64-character hex) |

### Validation Logic

`verify_license_jwt()` in `shared/security/licensing.py` performs:

1. Signature verification against the configured RSA public key (RS256).
2. `exp` claim validation — `PyJWT` rejects expired tokens automatically.
3. `hardware_id` claim presence check.
4. `hardware_id` binding check — must equal local `get_hardware_id()` result.

Any failure raises `LicenseError` (HTTP 403).

### Public Key Resolution Order

1. `LICENSE_PUBLIC_KEY` environment variable (PEM-encoded RSA public key).
2. Key embedded in the application binary at build time.

Always set `LICENSE_PUBLIC_KEY` via Docker secrets in production. Never rely on the embedded placeholder key.

---

## 4. LicenseGateMiddleware Behavior

`LicenseGateMiddleware` in `src/synth_engine/bootstrapper/dependencies/licensing.py` runs on every HTTP request.

While `LicenseState._is_licensed` is `False`: non-exempt paths receive **HTTP 402 Payment Required** (RFC 7807 Problem Details body).

### Exempt Paths

| Path | Purpose |
|------|---------|
| `/health` | Service health check |
| `/unseal` | Vault unseal |
| `/metrics` | Prometheus metrics |
| `/docs` | API documentation |
| `/redoc` | API documentation (alternate) |
| `/openapi.json` | OpenAPI schema |
| `/license/challenge` | Generate activation challenge |
| `/license/activate` | Submit signed JWT |
| `/security/shred` | Emergency cryptographic erasure |
| `/security/keys/rotate` | Key rotation |

### Middleware Ordering

`SealGateMiddleware` runs outside (first), `LicenseGateMiddleware` inside (second). A sealed vault returns 423 before the license check fires:

```text
Request → SealGateMiddleware (423?) → LicenseGateMiddleware (402?) → Route Handler
```

---

## 5. Key Rotation and Re-Activation

### 5.1 When the License Public Key Changes

1. The licensing server issues a new JWT signed with the new private key.
2. Set the new RSA public key in `LICENSE_PUBLIC_KEY`.
3. Restart the `app` service:
   ```bash
   docker compose up -d --no-deps app
   ```
4. Unseal the vault (see Operator Manual Section 4).
5. Re-activate via the challenge/response flow (Section 2).

### 5.2 When the License JWT Expires

1. All non-exempt routes return HTTP 402.
2. Follow the challenge/response flow (Section 2) to obtain a new JWT.
3. `POST /license/activate` with the new token — no service restart required.

### 5.3 Changing the Licensed Machine

1. On the new machine, `GET /license/challenge` for the new `hardware_id`.
2. Submit the new `hardware_id` to the licensing server for a new JWT.
3. `POST /license/activate` on the new machine.

The original license is rejected on the new machine — the `hardware_id` binding check will fail.
