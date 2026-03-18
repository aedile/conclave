# Conclave API Reference

**Generated from**: `docs/api/openapi.json` (exported at Phase 32 completion, commit `3fa02cd`).

The Conclave Engine exposes a REST API over HTTP. All endpoints require the vault to be
unsealed (`POST /unseal`) before data operations will succeed.

For the machine-readable schema, see [`openapi.json`](openapi.json). For a live interactive
reference, run the application and visit `http://localhost:8000/docs` (Swagger UI) or
`http://localhost:8000/redoc` (ReDoc).

To re-export the schema from a running environment:

```python
from synth_engine.bootstrapper.main import create_app
import json

app = create_app()
schema = app.openapi()
with open("docs/api/openapi.json", "w") as f:
    json.dump(schema, f, indent=2)
```

---

## Authentication

All endpoints require a valid JWT bearer token (except `/health`). Tokens are issued by the
offline license activation flow. See [`docs/LICENSING.md`](../LICENSING.md) for details.

---

## Endpoints

### System

#### `GET /health`

Liveness probe for container orchestrators and load balancers.

**Returns**: `{"status": "ok"}`

---

#### `POST /unseal`

Unseal the vault by deriving the Key Encryption Key (KEK) from the operator passphrase.

The passphrase is never stored — the KEK is derived at runtime using HKDF-SHA256 from
the `VAULT_PASSPHRASE` environment variable (or request body). All ALE-encrypted columns
are inaccessible until the vault is unsealed.

**Request body**: `{"passphrase": "string"}`

**Returns**: `{"status": "unsealed"}`

---

### Jobs

#### `GET /jobs`

List synthesis jobs with cursor-based pagination.

**Query parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `after` | string (optional) | Cursor: return jobs after this job ID |
| `limit` | integer (optional, default 20) | Maximum results to return |

**Returns**: Array of job objects.

---

#### `POST /jobs`

Create a new synthesis job in `QUEUED` status.

**Request body**:

```json
{
  "connection_id": "string (UUID)",
  "table_name": "string",
  "num_rows": 1000,
  "epsilon": 1.0,
  "delta": 1e-5,
  "max_grad_norm": 1.0,
  "num_epochs": 10
}
```

**Returns**: The created job object with assigned `id`.

---

#### `GET /jobs/{job_id}`

Get a synthesis job by ID.

**Path parameters**: `job_id` (integer)

**Returns**: Job object with current status (`QUEUED`, `PROCESSING`, `GENERATING`, `COMPLETE`, `FAILED`, `SHREDDED`).

---

#### `POST /jobs/{job_id}/start`

Enqueue a synthesis job for background processing via Huey.

**Path parameters**: `job_id` (integer)

Transitions job from `QUEUED` → `PROCESSING`. The Huey worker picks up the task and
executes the synthesis pipeline: profiling → DP-CTGAN training → artifact generation.

**Returns**: `{"status": "enqueued", "job_id": 1}`

---

#### `POST /jobs/{job_id}/shred`

Shred all synthesis artifacts for a `COMPLETE` job (NIST SP 800-88).

Deletes the Parquet artifact from MinIO storage and transitions the job to `SHREDDED`.
Non-reversible. Implements cryptographic erasure per NIST SP 800-88 Rev 1 guidelines.

**Path parameters**: `job_id` (integer)

**Returns**: `{"status": "shredded", "job_id": 1}`

---

#### `GET /jobs/{job_id}/stream`

Stream real-time progress for a synthesis job via Server-Sent Events (SSE).

Polls the job status and emits events as the job transitions through pipeline stages.
The client should reconnect if the connection drops.

**Path parameters**: `job_id` (integer)

**Returns**: `text/event-stream` — SSE events with `data: {"status": "...", "progress": 0.5}`

---

#### `GET /jobs/{job_id}/download`

Stream the synthetic Parquet artifact for a completed job.

Verifies the HMAC artifact signature before serving. Returns `403` if the artifact has
been tampered with. Returns `404` if the job is not in `COMPLETE` status.

**Path parameters**: `job_id` (integer)

**Returns**: `application/octet-stream` — Parquet file download.

---

### Connections

#### `GET /connections`

List all stored database connection configurations.

**Returns**: Array of connection objects (credentials are ALE-encrypted at rest; decrypted
values are not returned by this endpoint).

---

#### `POST /connections`

Create a new database connection configuration.

**Request body**:

```json
{
  "name": "string",
  "host": "string",
  "port": 5432,
  "database": "string",
  "username": "string",
  "password": "string"
}
```

Credentials are ALE-encrypted before storage. The plaintext password is never persisted.

**Returns**: The created connection object with assigned `id`.

---

#### `GET /connections/{connection_id}`

Get a database connection by ID.

**Path parameters**: `connection_id` (string UUID)

**Returns**: Connection object (password field is omitted).

---

#### `DELETE /connections/{connection_id}`

Delete a database connection by ID.

**Path parameters**: `connection_id` (string UUID)

**Returns**: `204 No Content`

---

### Settings

#### `GET /settings`

List all application settings.

**Returns**: Array of `{"key": "string", "value": "string"}` objects.

---

#### `PUT /settings/{key}`

Create or update a setting by key (upsert semantics).

**Path parameters**: `key` (string)

**Request body**: `{"value": "string"}`

**Returns**: The upserted setting object.

---

#### `GET /settings/{key}`

Get a setting by key.

**Path parameters**: `key` (string)

**Returns**: `{"key": "string", "value": "string"}`

---

#### `DELETE /settings/{key}`

Delete a setting by key.

**Path parameters**: `key` (string)

**Returns**: `204 No Content`

---

### License

#### `GET /license/challenge`

Generate a hardware-bound challenge payload for offline license activation.

Returns a signed challenge containing a hardware fingerprint. The operator sends this
challenge to the license issuer, who signs a JWT and returns it. See
[`docs/LICENSING.md`](../LICENSING.md) for the full activation flow.

**Returns**: `{"challenge": "string (base64)"}`

---

#### `POST /license/activate`

Activate the software license using a signed JWT.

Validates the RS256 signature and the hardware binding claim. Stores the validated
license token. No network call is made — all validation is local.

**Request body**: `{"token": "string (JWT)"}`

**Returns**: `{"status": "activated", "expires_at": "ISO 8601"}`

---

### Security

#### `POST /security/shred`

Zeroize the master wrapping key, rendering all ALE ciphertext unrecoverable.

This operation permanently destroys the ability to decrypt any ALE-encrypted column in
the database. Use only for NIST SP 800-88 media sanitization scenarios. Emits a WORM
audit event before execution.

**Request body**: `{"confirm": true}`

**Returns**: `{"status": "key_zeroized"}`

---

#### `POST /security/keys/rotate`

Enqueue a Huey background task to re-encrypt all ALE-encrypted columns.

Derives a new KEK from the current vault passphrase and re-encrypts all ciphertext.
The old KEK is zeroized after successful re-encryption. Emits a WORM audit event.

**Returns**: `{"status": "rotation_enqueued", "task_id": "string"}`

---

### Privacy Budget

#### `GET /privacy/budget`

Return the current privacy budget ledger state.

Reads the active `PrivacyBudget` record and returns total allocated epsilon, total
spent epsilon, remaining epsilon, and per-table consumption breakdown.

**Returns**:

```json
{
  "total_epsilon": 100.0,
  "spent_epsilon": 28.33,
  "remaining_epsilon": 71.67,
  "delta": 1e-5,
  "per_table": {
    "customers": 10.5,
    "orders": 8.2
  }
}
```

---

#### `POST /privacy/budget/refresh`

Reset the privacy budget and emit a WORM audit event.

Resets `total_spent_epsilon` to zero. Requires operator authorization. The WORM audit
log records the reset with timestamp and actor. Budget history is not deleted — only the
running total is reset.

**Returns**: `{"status": "budget_reset", "new_spent": 0.0}`

---

## Error Responses

All error responses follow RFC 7807 Problem Details format:

```json
{
  "type": "https://conclave.local/errors/budget-exhausted",
  "title": "Privacy Budget Exhausted",
  "status": 422,
  "detail": "Requested epsilon 5.0 would exceed remaining budget 2.3",
  "instance": "/jobs/42/start"
}
```

Common status codes:

| Code | Meaning |
|------|---------|
| 400 | Bad request — invalid input |
| 401 | Unauthorized — missing or invalid JWT |
| 403 | Forbidden — vault sealed, artifact tampered, or insufficient scope |
| 404 | Not found |
| 409 | Conflict — invalid state transition |
| 422 | Unprocessable — budget exhausted, OOM pre-flight rejection |
| 500 | Internal server error |
