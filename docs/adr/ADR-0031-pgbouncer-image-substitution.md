# ADR-0031 — PgBouncer Docker Image Substitution: edoburu/pgbouncer

**Date:** 2026-03-16
**Status:** Accepted
**Deciders:** PM + DevOps Reviewer
**Task:** P18-T18.2
**Resolves:** ADV-015 (BLOCKER — phantom pgbouncer tag in docker-compose.yml)

---

## Context

`docker-compose.yml` has referenced `pgbouncer/pgbouncer:1.23.1` since Task 2.2 (Phase 2).
On 2026-03-16, during P17-T17.1 (Docker Base Image SHA-256 Pinning), the Docker Registry v2 API
confirmed that this tag **does not exist** in Docker Hub:

```
GET https://registry-1.docker.io/v2/pgbouncer/pgbouncer/manifests/1.23.1
→ 404 Not Found (tag unknown)
```

The `pgbouncer/pgbouncer` image on Docker Hub only has versions up to `1.15.0`. The `1.23.1` tag
is a phantom reference that was silently present for at least 17 phases. This constituted a supply
chain integrity failure: a non-existent image that could not be SHA-256 pinned and would silently
fail on production deployment.

The finding was logged as **ADV-015 (BLOCKER)** in `docs/RETRO_LOG.md` with the following note:

> `pgbouncer/pgbouncer:1.23.1` does not exist in Docker Hub. Cannot be SHA-256 pinned until
> the image reference is replaced with a valid image. Candidate: `edoburu/pgbouncer:v1.23.1-p3`
> (verified available). Requires ADR per Rule 6 (technology substitution). Blocks supply chain
> security completeness for the pgbouncer service.

This ADR documents the image substitution decision per CLAUDE.md Rule 6.

---

## Decision

Replace `pgbouncer/pgbouncer:1.23.1` with `edoburu/pgbouncer:v1.23.1-p3`, pinned to its
SHA-256 digest:

```yaml
image: edoburu/pgbouncer:v1.23.1-p3@sha256:377dec3c0e4a66a1077ec043e16a26ed5702a6d954011a7983a1457c2e070b1d
```

Digest was obtained on 2026-03-16 via the Docker Registry v2 API (no Docker daemon required):

```bash
# Step 1: Obtain auth token
TOKEN=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:edoburu/pgbouncer:pull" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Step 2: Fetch manifest with digest header
curl -s -I -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
  "https://registry-1.docker.io/v2/edoburu/pgbouncer/manifests/v1.23.1-p3"
# → docker-content-digest: sha256:377dec3c0e4a66a1077ec043e16a26ed5702a6d954011a7983a1457c2e070b1d
```

---

## Rationale

### Why `edoburu/pgbouncer`?

`edoburu/pgbouncer` is the most widely-used community-maintained Docker image for PgBouncer.
It is available on Docker Hub under the `edoburu` organisation. The `v1.23.1-p3` tag:

- Provides PgBouncer 1.23.1 (the intended version from the original spec).
- Is actively maintained by the Docker Hub user `edoburu` with a history of regular releases.
- Has `v1.23.1-p3` available and confirmed via Registry v2 API on 2026-03-16.
- The `-p3` suffix denotes a patch to the Docker image (not to PgBouncer itself), typically
  addressing base image security updates.

### Why not `bitnami/pgbouncer`?

Bitnami's pgbouncer image runs as a non-root user by default and uses a different environment
variable naming convention (e.g., `POSTGRESQL_HOST` instead of `DATABASES_HOST`). Switching to
Bitnami would require additional configuration changes. `edoburu/pgbouncer` uses the same
environment variable names as the original spec, making this a minimal substitution.

### Why not the official `pgbouncer/pgbouncer`?

The official `pgbouncer/pgbouncer` image only publishes versions up to `1.15.0` on Docker Hub
(confirmed 2026-03-16). There is no `1.23.1` tag and no expected future tag — the project has
not maintained Docker Hub pushes for recent versions. Using the official image would require
downgrading PgBouncer to `1.15.0` or awaiting official releases.

### SHA-256 Pinning

The `edoburu/pgbouncer:v1.23.1-p3` image is pinned to its SHA-256 digest:
`sha256:377dec3c0e4a66a1077ec043e16a26ed5702a6d954011a7983a1457c2e070b1d`

This resolves the supply chain security gap that ADV-015 documented. The digest was obtained via
the Docker Registry v2 API and is not fabricated.

---

## Configuration Compatibility

The `edoburu/pgbouncer` image uses the same environment variable interface as the original spec:

| Environment Variable | Value | Purpose |
|---------------------|-------|---------|
| `DATABASES_HOST` | `postgres` | PostgreSQL host |
| `DATABASES_PORT` | `5432` | PostgreSQL port |
| `DATABASES_USER` | `conclave` | PostgreSQL user |
| `DATABASES_DBNAME` | `conclave` | Target database |
| `PGBOUNCER_POOL_MODE` | `transaction` | Transaction pooling mode |
| `PGBOUNCER_MAX_CLIENT_CONN` | `100` | Max client connections |
| `PGBOUNCER_DEFAULT_POOL_SIZE` | `10` | Pool size per database/user |
| `PGBOUNCER_AUTH_TYPE` | `scram-sha-256` | Authentication type (updated in T19.2 — see Amendment below) |
| `PGBOUNCER_AUTH_FILE` | `/etc/pgbouncer/userlist.txt` | Auth file path |

No changes to `pgbouncer/userlist.txt` or downstream configuration are required.

### Amendment — T19.2 (2026-03-16)

`PGBOUNCER_AUTH_TYPE` was updated from `md5` to `scram-sha-256` as part of T19.2 security
hardening. PostgreSQL 14+ deprecates md5 authentication in favour of SCRAM-SHA-256. The value
shown in the compatibility table above reflects the post-amendment state. This resolves ADV-016.

---

## Alternatives Considered

### 1. Wait for official `pgbouncer/pgbouncer:1.23.1` publication

**Rejected.** The official image has not been published for versions > 1.15.0. There is no
known timeline for publication. The phantom tag has been in the codebase for 17+ phases without
resolution.

### 2. Downgrade to `pgbouncer/pgbouncer:1.15.0`

**Rejected.** PgBouncer 1.15.0 is significantly older (2022) and lacks security patches and
features present in 1.23.1. ADR-0031 would be replacing a newer phantom with a working older
version — a functional downgrade.

### 3. `bitnami/pgbouncer`

**Rejected for this task.** Configuration interface differs (different env var names), requiring
changes to docker-compose.yml beyond the image reference. Scope-limited to image reference fix;
bitnami migration is a separate ADR.

---

## Consequences

### Positive

- ADV-015 BLOCKER is resolved. The pgbouncer service can now be SHA-256 pinned.
- All 9 external service images in docker-compose.yml are now SHA-256 pinned (supply chain
  security complete).
- `tests/unit/test_docker_image_pinning.py` can now include pgbouncer in blanket pinning checks.
- The WARNING(P17-T17.1) comment is removed; no phantom tag references remain.

### Negative / Accepted

- `edoburu/pgbouncer` is a community image, not an official PgBouncer project image. This is
  a known and accepted trade-off given the official image's publishing gap. The image is widely
  used in the community and the SHA-256 pin eliminates supply chain risk.
- The `-p3` patch suffix may not be present in future releases; the digest pin means future
  updates require explicit digest refresh.

---

## References

- ADV-015: Phantom pgbouncer tag blocking SHA-256 pinning (Phase 17, RETRO_LOG)
- ADV-016: PGBOUNCER_AUTH_TYPE md5 → scram-sha-256 upgrade (resolved T19.2)
- `docker-compose.yml` — updated image reference
- `tests/unit/test_docker_image_pinning.py` — updated to verify edoburu image is pinned
- CLAUDE.md Rule 6 — Technology substitution requires an ADR
- P17-T17.1 — Docker Base Image SHA-256 Pinning (origin of ADV-015)
