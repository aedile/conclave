# =============================================================================
# Stage 1: Frontend builder (placeholder — no real frontend assets in Phase 1)
#   Uses node:20-alpine to establish the multi-stage pattern. Phase 5+ will
#   populate this stage with a real React/Vite build.
# =============================================================================
# Digest pinned 2026-03-16 via Docker Registry v2 API (ADV-014 resolved).
# To refresh: docker pull node:20-alpine && docker inspect --format='{{index .RepoDigests 0}}' node:20-alpine
# ADV-017 fix: comment moved above FROM to prevent BuildKit inline-comment parse error.
# node:20-alpine
FROM node:20-alpine@sha256:b88333c42c23fbd91596ebd7fd10de239cedab9617de04142dde7315e3bc0afa AS frontend-builder

WORKDIR /frontend

# Placeholder: create an empty dist/ so the COPY in the final stage succeeds.
# Remove this RUN line and replace with real build commands when frontend exists.
RUN mkdir -p dist

# =============================================================================
# Stage 2: Python dependency builder
#   Exports a requirements.txt from the Poetry lock file and installs into
#   a dedicated prefix so we can copy only the installed packages, not Poetry
#   itself, into the final image.
# =============================================================================
# TODO(P87): Pin to an immutable digest once Docker is available in CI.
# To pin: docker pull python:3.13-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.13-slim
# ADV-017 fix: comment moved above FROM to prevent BuildKit inline-comment parse error.
# python:3.13-slim (upgraded from 3.14-slim — 3.13 is the highest production-ready version)
FROM python:3.13-slim AS python-builder

WORKDIR /build

# Install build tools, Poetry, and export plugin
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir poetry poetry-plugin-export

# Copy dependency manifests only — invalidate this layer only when they change
# README.md is required by pyproject.toml (readme = "README.md") for package metadata
COPY pyproject.toml poetry.lock README.md ./

# Export production deps to requirements.txt (no hashes for air-gap compat).
# P28-F3: --with synthesizer includes sdv/torch/opacus in the production image.
# Without this flag these packages are absent from requirements.txt, causing
# ImportError when synthesis jobs execute inside the container.
RUN poetry export \
        --without dev \
        --with synthesizer \
        --without-hashes \
        -f requirements.txt \
        -o requirements.txt

# Install dependencies into an isolated prefix.
# --ignore-installed ensures packages that happen to be pre-installed in the
# python:3.13-slim base image (e.g. anyio, sniffio) are copied into /install
# rather than being silently skipped by pip's "Requirement already satisfied"
# shortcut.  Without this flag, those packages would be absent from the final
# stage (P28 finding F6 — anyio/sniffio missing from image).
RUN pip install --no-cache-dir --prefix=/install --ignore-installed -r requirements.txt

# Copy application source
COPY src/ ./src/

# Install the application package itself into the same prefix
RUN pip install --no-cache-dir --prefix=/install --no-deps .

# =============================================================================
# Stage 3: Final production image
#   Minimal python:3.13-slim surface; no dev tools, no build caches, no secrets.
#   Runs as non-root user appuser (UID 1000) via gosu + tini.
# =============================================================================
# TODO(P87): Pin to an immutable digest once Docker is available in CI.
# To pin: docker pull python:3.13-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.13-slim
# Same tag as python-builder stage — intentional for split-brain prevention.
# ADV-017 fix: comment moved above FROM to prevent BuildKit inline-comment parse error.
# python:3.13-slim (upgraded from 3.14-slim — 3.13 is the highest production-ready version)
FROM python:3.13-slim AS final

# ---- Security: install tini (PID-1 init) and gosu (privilege drop) ---------
# tini reaps zombie processes; gosu drops from root to appuser before exec.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        tini \
        gosu \
    && rm -rf /var/lib/apt/lists/*

# ---- Create a non-root user and group (UID/GID 1000) -----------------------
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --no-create-home --shell /bin/false appuser

WORKDIR /app

# ---- Copy installed Python packages from builder ---------------------------
COPY --from=python-builder /install /usr/local

# ---- Copy application source -----------------------------------------------
COPY --from=python-builder /build/src ./src

# ---- Copy frontend static assets (placeholder empty dir) -------------------
COPY --from=frontend-builder /frontend/dist ./static

# ---- Copy entrypoint script and make it executable -------------------------
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ---- Ownership: give appuser read access only (root owns files) ------------
# The app runs read-only on its own source; writes go to named volumes / tmpfs.
RUN chown -R root:appuser /app \
    && chmod -R 750 /app

# ---- Metadata --------------------------------------------------------------
LABEL org.opencontainers.image.title="conclave-engine" \
      org.opencontainers.image.description="Air-Gapped Synthetic Data Generation Engine" \
      org.opencontainers.image.vendor="Conclave" \
      org.opencontainers.image.licenses="Proprietary"

# NOTE: Trivy scan target — run `trivy image conclave-engine:latest` after
# building to confirm 0 CRITICAL/HIGH CVEs. The python:3.13-slim base and
# the pinned apt packages are chosen for minimal attack surface. Re-scan on
# every base-image bump.

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# tini as PID-1; entrypoint drops to appuser via gosu before executing CMD
# Poetry is not present in the final image — invoke uvicorn directly.
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["uvicorn", "synth_engine.bootstrapper.main:app", "--host", "0.0.0.0", "--port", "8000"]
