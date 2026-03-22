#!/usr/bin/env bash
# =============================================================================
# generate-mtls-certs.sh — Internal CA & mTLS Leaf Certificate Generation
#
# Generates a self-signed internal Certificate Authority (CA) and per-service
# leaf certificates for mTLS inter-container communication.
#
# Services receiving leaf certificates:
#   app         — Conclave Engine API server + Huey workers
#   postgres    — PostgreSQL database
#   pgbouncer   — PgBouncer connection pooler
#   redis       — Redis task queue
#
# Monitoring services EXEMPT from mTLS (per ADR-0029 Gap 7):
#   prometheus, alertmanager, grafana, minio
#
# Output structure:
#   secrets/mtls/ca.crt           — CA root certificate (trust anchor)
#   secrets/mtls/ca.key           — CA private key (0400, NEVER mounted in containers)
#   secrets/mtls/<service>.crt    — Leaf certificate (0644)
#   secrets/mtls/<service>.key    — Leaf private key (0600)
#
# Usage:
#   ./scripts/generate-mtls-certs.sh [OPTIONS]
#
# Options:
#   --force           Regenerate CA even if ca.key already exists (DANGER: invalidates
#                     all previously issued leaf certificates).
#   --ca-days N       CA certificate validity in days (default: 3650).
#   --leaf-days N     Leaf certificate validity in days (default: 90).
#   --output-dir DIR  Output directory (default: secrets/mtls).
#   -h, --help        Show this help message.
#
# Prerequisites:
#   openssl 1.1.1+ (checked at startup)
#
# Security notes:
#   - The CA private key (ca.key) must NEVER be mounted into any container.
#     Only leaf .crt and .key files are distributed to containers.
#   - This script is fully offline (air-gap compatible) — no network calls.
#   - All private key files are created with 0600 permissions; ca.key with 0400.
#   - The CA key is protected from overwrite unless --force is specified (idempotent).
#
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Constants — hardcoded service allowlist (no arbitrary input)
# ---------------------------------------------------------------------------

readonly SERVICES=("app" "postgres" "pgbouncer" "redis")

# SANs for each service: Docker Compose hostname + Kubernetes FQDN variants
declare -A SERVICE_SANS
SERVICE_SANS["app"]="DNS:app,DNS:app.synth-engine.svc.cluster.local,DNS:app.synth-engine"
SERVICE_SANS["postgres"]="DNS:postgres,DNS:postgres.synth-engine.svc.cluster.local,DNS:postgres.synth-engine"
SERVICE_SANS["pgbouncer"]="DNS:pgbouncer,DNS:pgbouncer.synth-engine.svc.cluster.local,DNS:pgbouncer.synth-engine"
SERVICE_SANS["redis"]="DNS:redis,DNS:redis.synth-engine.svc.cluster.local,DNS:redis.synth-engine"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

FORCE=false
CA_DAYS=3650
LEAF_DAYS=90
OUTPUT_DIR="secrets/mtls"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
    printf '[generate-mtls-certs] %s\n' "$*" >&2
}

die() {
    log "ERROR: $*"
    exit 1
}

usage() {
    sed -n '/^# Usage:/,/^# =====/{ /^# =====/d; s/^# \{0,\}//; p }' "$0"
    exit 0
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)
            FORCE=true
            shift
            ;;
        --ca-days)
            [[ $# -ge 2 ]] || die "--ca-days requires an integer argument"
            CA_DAYS="$2"
            shift 2
            ;;
        --leaf-days)
            [[ $# -ge 2 ]] || die "--leaf-days requires an integer argument"
            LEAF_DAYS="$2"
            shift 2
            ;;
        --output-dir)
            [[ $# -ge 2 ]] || die "--output-dir requires a directory argument"
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            die "Unknown option: $1 (use --help for usage)"
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Validate numeric args
# ---------------------------------------------------------------------------

[[ "$CA_DAYS" =~ ^[1-9][0-9]*$ ]] || die "--ca-days must be a positive integer (got: $CA_DAYS)"
[[ "$LEAF_DAYS" =~ ^[1-9][0-9]*$ ]] || die "--leaf-days must be a positive integer (got: $LEAF_DAYS)"

# Enforce minimum key-strength requirements
if [[ "$CA_DAYS" -lt 365 ]]; then
    die "--ca-days must be at least 365 (got: $CA_DAYS)"
fi
if [[ "$LEAF_DAYS" -lt 1 ]]; then
    die "--leaf-days must be at least 1 (got: $LEAF_DAYS)"
fi
if [[ "$LEAF_DAYS" -gt 825 ]]; then
    log "WARNING: --leaf-days $LEAF_DAYS exceeds Apple/browser trust limit of 825 days"
fi

# ---------------------------------------------------------------------------
# Prerequisite: openssl version check
# ---------------------------------------------------------------------------

if ! command -v openssl >/dev/null 2>&1; then
    die "openssl is not installed or not on PATH"
fi

OPENSSL_VERSION="$(openssl version 2>/dev/null)"
log "Using: $OPENSSL_VERSION"

# Require at least OpenSSL 1.1.1 for ECDSA P-256 and modern digest support
OPENSSL_MAJOR="$(openssl version | awk '{print $2}' | cut -d. -f1)"
OPENSSL_MINOR="$(openssl version | awk '{print $2}' | cut -d. -f2)"

if [[ "$OPENSSL_MAJOR" -lt 1 ]] || \
   { [[ "$OPENSSL_MAJOR" -eq 1 ]] && [[ "$OPENSSL_MINOR" -lt 1 ]]; }; then
    die "openssl 1.1.1+ required (found: $OPENSSL_VERSION)"
fi

# ---------------------------------------------------------------------------
# Setup output directory
# ---------------------------------------------------------------------------

mkdir -p "$OUTPUT_DIR"

CA_KEY="$OUTPUT_DIR/ca.key"
CA_CERT="$OUTPUT_DIR/ca.crt"

# ---------------------------------------------------------------------------
# CA generation (idempotent — skip if ca.key exists unless --force)
# ---------------------------------------------------------------------------

if [[ -f "$CA_KEY" ]] && [[ "$FORCE" == "false" ]]; then
    log "CA key already exists at $CA_KEY — skipping CA generation."
    log "(Use --force to regenerate the CA. WARNING: this invalidates all leaf certs.)"
else
    if [[ -f "$CA_KEY" ]] && [[ "$FORCE" == "true" ]]; then
        log "WARNING: --force specified — regenerating CA and invalidating all leaf certs."
    fi

    log "Generating CA private key (ECDSA P-256)..."
    openssl ecparam -name prime256v1 -genkey -noout -out "$CA_KEY"
    chmod 0400 "$CA_KEY"

    log "Generating self-signed CA certificate (valid $CA_DAYS days)..."
    openssl req -new -x509 \
        -key "$CA_KEY" \
        -out "$CA_CERT" \
        -days "$CA_DAYS" \
        -subj "/CN=Conclave Internal CA/O=Conclave Engine/OU=mTLS CA" \
        -addext "basicConstraints=critical,CA:true,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -addext "subjectKeyIdentifier=hash"

    log "CA certificate written to $CA_CERT"
fi

# ---------------------------------------------------------------------------
# Leaf certificate generation for each service
# ---------------------------------------------------------------------------

TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

for SERVICE in "${SERVICES[@]}"; do
    LEAF_KEY="$OUTPUT_DIR/${SERVICE}.key"
    LEAF_CERT="$OUTPUT_DIR/${SERVICE}.crt"
    LEAF_CSR="$TMPDIR_WORK/${SERVICE}.csr"
    EXT_FILE="$TMPDIR_WORK/${SERVICE}.ext"
    SAN_VALUE="${SERVICE_SANS[$SERVICE]}"

    log "Generating leaf certificate for service: $SERVICE"

    # Private key
    openssl ecparam -name prime256v1 -genkey -noout -out "$LEAF_KEY"
    chmod 0600 "$LEAF_KEY"

    # CSR
    openssl req -new \
        -key "$LEAF_KEY" \
        -out "$LEAF_CSR" \
        -subj "/CN=${SERVICE}/O=Conclave Engine/OU=mTLS Leaf"

    # Extension file for SANs
    cat > "$EXT_FILE" <<EXTEOF
[v3_req]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth,clientAuth
subjectAltName = ${SAN_VALUE}
EXTEOF

    # Sign with CA
    openssl x509 -req \
        -in "$LEAF_CSR" \
        -CA "$CA_CERT" \
        -CAkey "$CA_KEY" \
        -CAcreateserial \
        -out "$LEAF_CERT" \
        -days "$LEAF_DAYS" \
        -extensions v3_req \
        -extfile "$EXT_FILE"

    log "  Certificate: $LEAF_CERT"
    log "  Private key: $LEAF_KEY (0600)"
done

# ---------------------------------------------------------------------------
# Verify generated certificates
# ---------------------------------------------------------------------------

log ""
log "Verifying certificate chain for all services..."

for SERVICE in "${SERVICES[@]}"; do
    LEAF_CERT="$OUTPUT_DIR/${SERVICE}.crt"

    # Verify leaf cert is signed by CA
    openssl verify -CAfile "$CA_CERT" "$LEAF_CERT" >/dev/null 2>&1 \
        || die "Chain verification FAILED for $SERVICE"

    EXPIRY="$(openssl x509 -noout -enddate -in "$LEAF_CERT" | cut -d= -f2)"
    log "  ✓ $SERVICE — expires $EXPIRY"
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log ""
log "mTLS certificate generation complete."
log "  Output directory : $OUTPUT_DIR"
log "  CA certificate   : $CA_CERT"
log "  CA private key   : $CA_KEY (0400 — NEVER mount this into containers)"
log "  Leaf services    : ${SERVICES[*]}"
log ""
log "Security reminders:"
log "  - The CA key ($CA_KEY) must remain on the operator host only."
log "  - Mount only <service>.crt + <service>.key + ca.crt into each container."
log "  - Monitoring services (prometheus, alertmanager, grafana, minio) are exempt"
log "    from mTLS per ADR-0029 Gap 7 and do NOT receive leaf certificates."
log "  - Rotate leaf certificates before expiry (--leaf-days default: $LEAF_DAYS)."
