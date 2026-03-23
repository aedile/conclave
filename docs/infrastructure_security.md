# Infrastructure Security Model

Security posture of the Conclave Engine container deployment and host-level controls that the operator MUST enforce. The container runtime cannot provision host-level encryption; responsibilities are shared as described below.

---

## 1. Host-Level Disk Encryption (LUKS)

### Requirement

All directories backing Docker named volumes — in particular `chroma_data` and any path used for `data/` seed ingestion — MUST reside on a LUKS-encrypted block device.

### Rationale

A stolen or decommissioned drive cannot be read without the LUKS passphrase, even if an attacker bypasses OS-level access controls.

### Operator Steps (Debian/Ubuntu)

```bash
# 1. Identify the block device (e.g. /dev/sdb)
# 2. Format as LUKS2
cryptsetup luksFormat --type luks2 /dev/sdb

# 3. Open the encrypted volume
cryptsetup open /dev/sdb conclave-data

# 4. Create a filesystem
mkfs.ext4 /var/lib/docker/volumes  # or a dedicated mountpoint

# 5. Add to /etc/crypttab and /etc/fstab for automatic unlock on boot
#    (use a keyfile in a hardware security module for unattended boots)
```

The Docker volume `chroma_data` uses the Docker daemon's `data-root` (`/var/lib/docker` by default). Ensure that path is on the encrypted device before starting the engine.

---

## 2. IPC_LOCK Capability — Preventing Key Material from Swapping to Disk

`IPC_LOCK` grants a process the right to call `mlock(2)` / `mlockall(2)`, pinning memory pages to prevent kernel swapping to disk.

The Synthesizer module performs DP-SGD computations involving ephemeral cryptographic keys and privacy-budget state. If those pages swapped to disk, they could be recovered from the swap device after the container exits, violating the air-gap security model.

`cap_add: [IPC_LOCK]` grants this capability to the app service only. All other Linux capabilities are explicitly dropped via `cap_drop: ALL`.

---

## 3. Non-Root Execution Model

### Container User

The engine runs as `appuser` (UID 1000, GID 1000). Root is never available to the application at runtime.

### Privilege Drop Flow

1. Docker daemon starts the container's init process (`tini`). The application port (8000) is above 1024 — root is not needed for port binding.
2. `tini` (PID 1) calls `/entrypoint.sh`.
3. `/entrypoint.sh` calls `gosu appuser <CMD>`, performing a permanent `setuid`/`setgid` drop to UID/GID 1000 before `exec`-ing the application.
4. The process cannot regain root from this point.

### Read-Only Root Filesystem

The container rootfs is mounted read-only (`read_only: true`). Writes are only possible to:

| Path            | Mechanism | Contents                              |
|-----------------|-----------|---------------------------------------|
| `/tmp`          | tmpfs     | Temporary scratch files (max 64 MiB)  |
| `/run/secrets`  | tmpfs     | Docker secrets (max 4 MiB, mode 0700) |
| `/app/.chroma_data` | Named volume | ChromaDB vector store           |

An attacker achieving RCE cannot modify application binaries or configuration on the rootfs.

---

## 4. Secrets Management

Secrets (e.g. `app_secret_key`) are injected at runtime via Docker Secrets — they appear as files under `/run/secrets/` and are never baked into the image or environment variables.

Secret files live in `secrets/` on the host:

- Listed in `.gitignore` (`secrets/*.txt`) — never committed.
- Listed in `.dockerignore` — never copied into the image build context.
- Expected to have mode `0600` on the host.

Before the first `docker-compose up`:

```bash
mkdir -p secrets
openssl rand -hex 32 > secrets/app_secret_key.txt
chmod 600 secrets/app_secret_key.txt
```

---

## 5. Network Isolation

Services communicate exclusively over the Docker bridge network created by Compose. The only externally exposed port in the base configuration is `8000` (HTTP API). Redis has no external port binding — reachable only by the `app` service on the internal network.

---

## 6. Log Rotation

Container logs use the `json-file` driver with:

- `max-size: 50m` — each log file capped at 50 MiB.
- `max-file: 3` — maximum 3 rotated files kept.

This bounds log disk usage to 150 MiB per service and prevents log-based denial-of-service. Logs do NOT contain PII; the audit logger redacts sensitive fields before writing.

---

## 7. mTLS Inter-Container Communication

All inter-container data-plane connections can be secured with mutual TLS by enabling the opt-in overlay. When `MTLS_ENABLED=true`, every connection uses ECDSA P-256 certificates issued by an internal CA.

| Connection | Protocol | mTLS Mode |
|------------|----------|-----------|
| app → pgbouncer | TCP 6432 | `verify-full` (client cert required) |
| pgbouncer → postgres | TCP 5432 | mutual (both sides verify) |
| app → redis | TCP 6379 (TLS) | mutual |
| huey worker → redis | TCP 6379 (TLS) | mutual |

Monitoring services (Prometheus, AlertManager, Grafana, MinIO) are exempt — they are read-only observability consumers with no write path to sensitive data.

### Activation

```bash
# 1. Generate certificates (one-time setup)
bash scripts/generate-mtls-certs.sh

# 2. Start with the mTLS overlay
docker-compose -f docker-compose.yml -f docker-compose.mtls.yml up -d
```

The `docker-compose.mtls.yml` overlay mounts `secrets/mtls/` into each service and sets `MTLS_ENABLED=true`, activating SSL context builders in `shared/db.py`, `bootstrapper/dependencies/redis.py`, and `shared/task_queue.py`.

### Certificate Management

`scripts/generate-mtls-certs.sh` uses `openssl` with ECDSA P-256 keys. Created artifacts:

- `secrets/mtls/ca.crt` / `secrets/mtls/ca.key` — Root CA (keep offline after issuance)
- Per-service leaf certificates: `app`, `pgbouncer`, `postgres`, `redis`

Certificate expiry is monitored via `conclave_cert_expiry_days` (Prometheus gauge with `service` label) emitted by `shared/cert_metrics.py`. Set an AlertManager rule to fire when expiry falls below 30 days.

### Kubernetes NetworkPolicy

For Kubernetes deployments, L3/L4 network segmentation is enforced by manifests in `k8s/network-policies/`. These restrict which pods may initiate connections, complementing mTLS.

**Prerequisite**: A CNI plugin that enforces NetworkPolicy (Calico, Cilium, or Weave Net). Kubenet and Flannel silently ignore NetworkPolicy manifests.

See `k8s/network-policies/README.md` for application instructions.

### Design Decisions and Threat Model

See ADR-0045 for the full architecture, including: ECDSA P-256 rationale, why monitoring is exempt, the single-hop certificate chain rationale, and the complete threat model.
