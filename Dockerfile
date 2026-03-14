# =============================================================================
# Stage 1: Frontend builder (placeholder — no real frontend assets in Phase 1)
#   Uses node:20-alpine to establish the multi-stage pattern. Phase 5+ will
#   populate this stage with a real React/Vite build.
# =============================================================================
FROM node:20-alpine AS frontend-builder

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
FROM python:3.14-slim AS python-builder

WORKDIR /build

# Install build tools, Poetry, and export plugin
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir poetry poetry-plugin-export

# Copy dependency manifests only — invalidate this layer only when they change
COPY pyproject.toml poetry.lock ./

# Export production deps to requirements.txt (no hashes for air-gap compat)
RUN poetry export \
        --without dev \
        --without-hashes \
        -f requirements.txt \
        -o requirements.txt

# Install dependencies into an isolated prefix
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Copy application source
COPY src/ ./src/

# Install the application package itself into the same prefix
RUN pip install --no-cache-dir --prefix=/install --no-deps .

# =============================================================================
# Stage 3: Final production image
#   Minimal python:3.14-slim surface; no dev tools, no build caches, no secrets.
#   Runs as non-root user appuser (UID 1000) via gosu + tini.
# =============================================================================
FROM python:3.14-slim AS final

# ---- Security: install tini (PID-1 init) and gosu (privilege drop) ---------
# tini reaps zombie processes; gosu drops from root to appuser before exec.
RUN apt-get update \
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
# building to confirm 0 CRITICAL/HIGH CVEs. The python:3.14-slim base and
# the pinned apt packages are chosen for minimal attack surface. Re-scan on
# every base-image bump.

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# tini as PID-1; entrypoint drops to appuser via gosu before executing CMD
# Poetry is not present in the final image — invoke uvicorn directly.
ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]
CMD ["uvicorn", "synth_engine.bootstrapper.main:app", "--host", "0.0.0.0", "--port", "8000"]
