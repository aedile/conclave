# Infrastructure Security Model

This document describes the security posture of the Conclave Engine container
deployment and the host-level controls that the operator MUST enforce.  The
container runtime cannot provision host-level encryption; the responsibilities
are shared as described below.

---

## 1. Host-Level Disk Encryption (LUKS)

### Requirement

All directories that back Docker named volumes — in particular `chroma_data`
and any path used for the `data/` seed ingestion — MUST reside on a
LUKS-encrypted block device on the host.

### Rationale

The Conclave Engine processes sensitive, regulated data (PII, medical records,
financial data).  Encrypting the underlying block device ensures that a stolen
or decommissioned hard drive cannot be read without the LUKS passphrase, even
if an attacker bypasses OS-level access controls.

### Operator Steps (example — Debian/Ubuntu)

```bash
# 1. Identify the block device to encrypt (e.g. /dev/sdb)
# 2. Format as LUKS2
cryptsetup luksFormat --type luks2 /dev/sdb

# 3. Open the encrypted volume
cryptsetup open /dev/sdb conclave-data

# 4. Create a filesystem
mkfs.ext4 /var/lib/docker/volumes  # or a dedicated mountpoint

# 5. Add to /etc/crypttab and /etc/fstab for automatic unlock on boot
#    (use a keyfile stored in a hardware security module for unattended boots)
```

The Docker volume `chroma_data` automatically uses the path configured in the
Docker daemon's `data-root` (`/var/lib/docker` by default).  Ensure that path
is on the encrypted device before starting the engine.

---

## 2. IPC_LOCK Capability — Preventing Key Material from Swapping to Disk

### What IPC_LOCK Does

The Linux `IPC_LOCK` capability grants a process the right to call `mlock(2)`
and `mlockall(2)`, which pin memory pages and prevent the kernel from swapping
them to the swap partition or swap file.

### Why the Conclave Engine Needs It

The Synthesizer module performs DP-SGD computations that involve ephemeral
cryptographic keys and privacy-budget accounting state.  If those pages were
swapped to disk — even temporarily — they could be recovered from the swap
device after the container exits, violating the air-gap security model.

`cap_add: [IPC_LOCK]` in `docker-compose.yml` grants this capability to the
app service **without** granting any other elevated privilege.

All other Linux capabilities are explicitly dropped via `cap_drop: ALL`.

---

## 3. Non-Root Execution Model

### Container User

The Conclave Engine runs as `appuser` (UID 1000, GID 1000) inside the
container.  Root is never available to the application at runtime.

### How It Works

1. The Docker daemon starts the container as root (required to bind ports < 1024
   via tini).
2. `tini` (PID 1) calls `/entrypoint.sh`.
3. `/entrypoint.sh` calls `su-exec appuser <CMD>`, which performs a permanent
   `setuid`/`setgid` drop to UID/GID 1000 before `exec`-ing the application.
4. From this point forward the process cannot regain root.

### Read-Only Root Filesystem

The container rootfs is mounted read-only (`read_only: true`).  Writes are only
possible to:

| Path            | Mechanism | Contents                              |
|-----------------|-----------|---------------------------------------|
| `/tmp`          | tmpfs     | Temporary scratch files (max 64 MiB)  |
| `/run/secrets`  | tmpfs     | Docker secrets (max 4 MiB, mode 0700) |
| `/app/.chroma_data` | Named volume | ChromaDB vector store           |

This means that even if an attacker achieves RCE they cannot modify the
application binaries or configuration on the rootfs.

---

## 4. Secrets Management

Secrets (e.g. `app_secret_key`) are injected at runtime via Docker Secrets.
They appear inside the container as files under `/run/secrets/` and are never
baked into the image or environment variables.

Secret files live in `secrets/` on the host.  That directory is:

- Listed in `.gitignore` (`secrets/*.txt`) — files are never committed.
- Listed in `.dockerignore` — files are never copied into the image build context.
- Expected to have mode `0600` on the host.

Before the first `docker-compose up`:

```bash
mkdir -p secrets
openssl rand -hex 32 > secrets/app_secret_key.txt
chmod 600 secrets/app_secret_key.txt
```

---

## 5. Network Isolation

Services communicate exclusively over the Docker bridge network created by
Compose.  The only externally exposed port in the base configuration is
`8000` (the HTTP API).  Redis has no external port binding; it is only
reachable by the `app` service on the internal network.

---

## 6. Log Rotation

Container logs use the `json-file` driver with:

- `max-size: 50m` — each log file is capped at 50 MiB.
- `max-file: 3`   — a maximum of 3 rotated files are kept.

This bounds log disk usage to 150 MiB per service and prevents log-based
denial-of-service attacks on the host.  Logs do NOT contain PII; the
application's audit logger redacts sensitive fields before writing.
