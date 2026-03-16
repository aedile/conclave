#!/usr/bin/env bash
# scripts/ci-local.sh
#
# Local CI runner — replicates all GitHub Actions CI gates.
#
# Usage:
#   ./scripts/ci-local.sh [--continue] [--help] [stage ...]
#
# Examples:
#   ./scripts/ci-local.sh                  # run all stages, stop on first failure
#   ./scripts/ci-local.sh --continue       # run all stages, collect all failures
#   ./scripts/ci-local.sh lint test        # run only lint and test stages
#   ./scripts/ci-local.sh --continue lint  # run only lint, ignore failures
#
# Stages (core — affect exit code):
#   security    gitleaks full-history scan
#   shell-lint  shell script linting (scripts/, frontend/scripts/, .claude/hooks/)
#   docs-gate   verify a docs: commit exists on branch relative to main
#   lint        ruff, ruff format, mypy, bandit, vulture, lint-imports, pip-audit
#   test        pytest unit tests with 90% coverage gate
#   frontend    npm audit, type-check, test:coverage, build
#
# Stages (optional — informational only, never affect exit code):
#   integration   pytest integration tests (requires pg_ctl on PATH)
#   synthesizer   pytest synthesizer integration tests
#   e2e           Playwright browser tests (requires node_modules)
#   trivy         Trivy container image scan (requires docker + trivy)
#   sbom          CycloneDX SBOM generation
#   zap           OWASP ZAP baseline scan (complex setup, skipped by default)

set -uo pipefail

# ---------------------------------------------------------------------------
# Colour helpers — honour NO_COLOR (https://no-color.org/)
# ---------------------------------------------------------------------------
if [[ -z "${NO_COLOR:-}" ]] && [[ -t 1 ]]; then
    _RED='\033[0;31m'
    _GREEN='\033[0;32m'
    _YELLOW='\033[1;33m'
    _BLUE='\033[0;34m'
    _BOLD='\033[1m'
    _RESET='\033[0m'
else
    _RED=''
    _GREEN=''
    _YELLOW=''
    _BLUE=''
    _BOLD=''
    _RESET=''
fi

print_info()  { printf "${_BLUE}[INFO]${_RESET}  %s\n" "$*"; }
print_pass()  { printf "${_GREEN}[PASS]${_RESET}  %s\n" "$*"; }
print_fail()  { printf "${_RED}[FAIL]${_RESET}  %s\n" "$*"; }
print_warn()  { printf "${_YELLOW}[SKIP]${_RESET}  %s\n" "$*"; }
print_bold()  { printf "${_BOLD}%s${_RESET}\n" "$*"; }

# ---------------------------------------------------------------------------
# Stage tracking helpers — bash-3.2-compatible (no declare -A)
# Stage names use hyphens; variable names mangle hyphens to underscores.
# ---------------------------------------------------------------------------

# set_stage_status <stage> <value>
set_stage_status() {
    local _var="STAGE_STATUS_${1//-/_}"
    eval "${_var}=\"\$2\""
}

# get_stage_status <stage>
get_stage_status() {
    local _var="STAGE_STATUS_${1//-/_}"
    eval "printf '%s' \"\${${_var}:-}\""
}

# set_stage_elapsed <stage> <value>
set_stage_elapsed() {
    local _var="STAGE_ELAPSED_${1//-/_}"
    eval "${_var}=\"\$2\""
}

# get_stage_elapsed <stage>
get_stage_elapsed() {
    local _var="STAGE_ELAPSED_${1//-/_}"
    eval "printf '%s' \"\${${_var}:-0}\""
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
CONTINUE_ON_FAILURE=false
REQUESTED_STAGES=()

show_help() {
    # Print the header comment block (lines 2 through the set -uo line),
    # stripping the leading "# " prefix.  Works on both GNU and BSD awk.
    awk '/^set -uo pipefail/{exit} NR>1{sub(/^# ?/,""); print}' "$0"
}

for _arg in "$@"; do
    case "$_arg" in
        --help|-h)  show_help; exit 0 ;;
        --continue) CONTINUE_ON_FAILURE=true ;;
        *)          REQUESTED_STAGES+=("$_arg") ;;
    esac
done

# ---------------------------------------------------------------------------
# Locate repo root — always run from there
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}" || exit 1

# ---------------------------------------------------------------------------
# Stage registry
# ---------------------------------------------------------------------------
ALL_CORE_STAGES=(security shell-lint docs-gate lint test frontend)
ALL_OPTIONAL_STAGES=(integration synthesizer e2e trivy sbom zap)
ALL_STAGES=("${ALL_CORE_STAGES[@]}" "${ALL_OPTIONAL_STAGES[@]}")

# Determine which stages to run
STAGES_TO_RUN=()
if [[ ${#REQUESTED_STAGES[@]} -eq 0 ]]; then
    STAGES_TO_RUN=("${ALL_STAGES[@]}")
else
    for _s in "${REQUESTED_STAGES[@]}"; do
        _valid=false
        for _known in "${ALL_STAGES[@]}"; do
            if [[ "$_s" == "$_known" ]]; then
                _valid=true
                break
            fi
        done
        if ! $_valid; then
            print_fail "Unknown stage: '$_s'"
            print_info "Valid stages: ${ALL_STAGES[*]}"
            exit 1
        fi
        STAGES_TO_RUN+=("$_s")
    done
fi

FAILED_CORE_STAGES=()

# Helper: is a stage in the core set?
is_core_stage() {
    local _stage="$1"
    local _s
    for _s in "${ALL_CORE_STAGES[@]}"; do
        if [[ "$_s" == "$_stage" ]]; then
            return 0
        fi
    done
    return 1
}

# Helper: is a stage in the requested run set?
stage_requested() {
    local _stage="$1"
    local _s
    for _s in "${STAGES_TO_RUN[@]}"; do
        if [[ "$_s" == "$_stage" ]]; then
            return 0
        fi
    done
    return 1
}

# Run a stage function.  Usage: run_stage <name> <function>
run_stage() {
    local name="$1"
    local func="$2"

    if ! stage_requested "$name"; then
        return 0
    fi

    print_bold ""
    print_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    print_bold "  Stage: ${name}"
    print_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    local _start_time
    _start_time=$(date +%s)

    local _exit_code=0
    "$func" || _exit_code=$?

    local _end_time
    _end_time=$(date +%s)
    local _elapsed=$(( _end_time - _start_time ))
    set_stage_elapsed "$name" "$_elapsed"

    if [[ $_exit_code -eq 0 ]]; then
        _current="$(get_stage_status "$name")"
        if [[ "$_current" != "SKIP" ]]; then
            set_stage_status "$name" "PASS"
        fi
        print_pass "${name} completed in ${_elapsed}s"
    else
        set_stage_status "$name" "FAIL"
        print_fail "${name} FAILED (exit ${_exit_code}) after ${_elapsed}s"
        if is_core_stage "$name"; then
            FAILED_CORE_STAGES+=("$name")
            if ! $CONTINUE_ON_FAILURE; then
                print_summary
                exit 1
            fi
        fi
    fi
}

mark_skip() {
    local name="$1"
    local reason="$2"
    if stage_requested "$name"; then
        set_stage_status "$name" "SKIP"
        set_stage_elapsed "$name" "0"
        print_warn "${name}: ${reason}"
    fi
}

# ---------------------------------------------------------------------------
# Stage implementations
# ---------------------------------------------------------------------------

stage_security() {
    if ! command -v gitleaks > /dev/null 2>&1; then
        print_warn "security: gitleaks not installed — install via https://github.com/gitleaks/gitleaks/releases"
        mark_skip "security" "gitleaks not installed"
        return 0
    fi
    print_info "Running gitleaks full-history scan..."
    gitleaks detect --source . --log-opts="HEAD"
}

stage_shell_lint() {
    if ! command -v shellcheck > /dev/null 2>&1; then
        print_warn "shell-lint: shellcheck not installed — brew install shellcheck"
        mark_skip "shell-lint" "shellcheck not installed"
        return 0
    fi
    print_info "Running shellcheck on scripts/, frontend/scripts/, .claude/hooks/ ..."

    local _exit_code=0
    local _sh_files=()

    # Collect .sh files from each directory that exists
    local _dir
    for _dir in scripts/ frontend/scripts/ .claude/hooks/; do
        if [[ -d "${_dir}" ]]; then
            while IFS= read -r -d '' _f; do
                _sh_files+=("$_f")
            done < <(find "${_dir}" -name "*.sh" -print0)
        fi
    done

    if [[ ${#_sh_files[@]} -eq 0 ]]; then
        print_warn "shell-lint: no .sh files found in target directories"
        return 0
    fi

    shellcheck --severity=warning "${_sh_files[@]}" || _exit_code=$?
    return "$_exit_code"
}

stage_docs_gate() {
    print_info "Checking for docs: commit on current branch relative to main..."

    local _current_branch
    _current_branch=$(git rev-parse --abbrev-ref HEAD)

    if [[ "$_current_branch" == "main" ]]; then
        print_warn "docs-gate: on main branch — skipping PR-only check"
        mark_skip "docs-gate" "on main branch (PR-only check)"
        return 0
    fi

    local _docs_count
    _docs_count=$(git log main..HEAD --format="%s" 2>/dev/null | { grep -c "^docs:" || true; })

    if [[ "$_docs_count" -eq 0 ]]; then
        print_fail "No 'docs:' commit found on branch '${_current_branch}' relative to main."
        print_fail "Constitution Priority 6: every PR must contain at least one docs: commit."
        print_fail "If no documentation changed, add:"
        print_fail "  docs: no documentation changes required — <one-sentence justification>"
        return 1
    fi
    print_pass "Found ${_docs_count} docs: commit(s). Constitution Priority 6: satisfied."
}

stage_lint() {
    local _exit_code=0

    print_info "poetry check --lock ..."
    poetry check --lock || _exit_code=$?

    print_info "ruff check src/ tests/ scripts/ ..."
    poetry run ruff check src/ tests/ scripts/ || _exit_code=$?

    print_info "ruff format --check src/ tests/ scripts/ ..."
    poetry run ruff format --check src/ tests/ scripts/ || _exit_code=$?

    print_info "mypy src/ ..."
    poetry run mypy src/ || _exit_code=$?

    print_info "bandit -c pyproject.toml -r src/ scripts/ ..."
    poetry run bandit -c pyproject.toml -r src/ scripts/ || _exit_code=$?

    print_info "vulture src/ vulture_whitelist.py ..."
    poetry run vulture src/ vulture_whitelist.py || _exit_code=$?

    print_info "lint-imports boundary check ..."
    poetry run lint-imports || _exit_code=$?

    print_info "pip-audit dependency vulnerability scan ..."
    poetry run pip-audit || _exit_code=$?

    return "$_exit_code"
}

stage_test() {
    print_info "Running unit tests with 90% coverage gate..."
    # ADV-066: Zero-warning policy is enforced via pyproject.toml filterwarnings=["error", ...].
    # Adding bare -W error here would override the ignore suppressor rules that follow
    # "error" in the config. Policy is already active via ini config.
    poetry run pytest tests/unit/ \
        --cov=src/synth_engine \
        --cov-report=term-missing \
        --cov-fail-under=90
}

stage_frontend() {
    if [[ ! -d "frontend/node_modules" ]]; then
        print_warn "frontend: node_modules not found — run: cd frontend && npm ci"
        mark_skip "frontend" "frontend/node_modules not found (run: cd frontend && npm ci)"
        return 0
    fi

    local _exit_code=0

    print_info "npm audit --audit-level=high ..."
    (cd frontend && npm audit --audit-level=high) || _exit_code=$?

    print_info "npm run type-check ..."
    (cd frontend && npm run type-check) || _exit_code=$?

    print_info "npm run test:coverage ..."
    (cd frontend && npm run test:coverage) || _exit_code=$?

    print_info "npm run build ..."
    (cd frontend && npm run build) || _exit_code=$?

    return "$_exit_code"
}

stage_integration() {
    if ! command -v pg_ctl > /dev/null 2>&1; then
        print_warn "integration: pg_ctl not on PATH — install PostgreSQL 16 to run integration tests"
        mark_skip "integration" "pg_ctl not on PATH"
        return 0
    fi
    print_info "Running integration tests (pytest-postgresql)..."
    poetry run pytest tests/integration/ -v --tb=short --no-cov -p pytest_postgresql
}

stage_synthesizer() {
    print_info "Running synthesizer integration tests..."
    # ADV-069: Use marker-based routing; new synthesizer tests are auto-included.
    poetry run pytest tests/integration/ -m synthesizer -v --no-cov
}

stage_e2e() {
    if [[ ! -d "frontend/node_modules" ]]; then
        print_warn "e2e: frontend/node_modules not found — run: cd frontend && npm ci"
        mark_skip "e2e" "frontend/node_modules not found"
        return 0
    fi

    print_info "Installing Playwright Chromium binaries..."
    local _install_exit=0
    (cd frontend && npx playwright install --with-deps chromium) || _install_exit=$?
    if [[ $_install_exit -ne 0 ]]; then
        print_warn "e2e: Playwright install failed — skipping"
        mark_skip "e2e" "Playwright browser install failed"
        return 0
    fi

    print_info "Running Playwright E2E tests..."
    (cd frontend && npx playwright test)
}

stage_trivy() {
    if ! command -v docker > /dev/null 2>&1; then
        print_warn "trivy: docker not available — skipping container scan"
        mark_skip "trivy" "docker not available"
        return 0
    fi

    if ! command -v trivy > /dev/null 2>&1; then
        print_warn "trivy: trivy not installed — install via https://aquasecurity.github.io/trivy"
        mark_skip "trivy" "trivy not installed"
        return 0
    fi

    print_info "Building Docker image for scanning..."
    docker build -t conclave-engine:ci-scan .

    print_info "Running Trivy vulnerability scan (HIGH,CRITICAL)..."
    trivy image \
        --exit-code 1 \
        --ignore-unfixed \
        --severity HIGH,CRITICAL \
        conclave-engine:ci-scan
}

stage_sbom() {
    print_info "Generating CycloneDX SBOM..."
    local _sbom_exit=0
    poetry run cyclonedx-py poetry -o sbom.json || _sbom_exit=$?
    if [[ $_sbom_exit -ne 0 ]]; then
        print_warn "sbom: cyclonedx-py failed — ensure it is installed: poetry install --with dev"
        mark_skip "sbom" "cyclonedx-py not available"
        return 0
    fi
    print_info "SBOM written to sbom.json"
}

stage_zap() {
    print_warn "zap: OWASP ZAP requires a live server and Docker — skipping in local CI"
    print_warn "zap: To run manually, see .github/workflows/ci.yml (zap-baseline job)"
    mark_skip "zap" "complex setup — skipped by default in local CI"
    return 0
}

# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------
print_summary() {
    local _total_start="${CI_LOCAL_START_TIME:-0}"
    local _total_end
    _total_end=$(date +%s)
    local _total_elapsed=$(( _total_end - _total_start ))

    print_bold ""
    print_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    print_bold "  CI Local — Summary"
    print_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    local _stage _status _elapsed
    for _stage in "${ALL_STAGES[@]}"; do
        if ! stage_requested "$_stage"; then
            continue
        fi

        _status="$(get_stage_status "$_stage")"
        _elapsed="$(get_stage_elapsed "$_stage")"

        case "$_status" in
            PASS) printf "  ${_GREEN}%-14s PASS${_RESET}  %ss\n" "$_stage" "$_elapsed" ;;
            FAIL) printf "  ${_RED}%-14s FAIL${_RESET}  %ss\n" "$_stage" "$_elapsed" ;;
            *)    printf "  ${_YELLOW}%-14s SKIP${_RESET}  %ss\n" "$_stage" "$_elapsed" ;;
        esac
    done

    print_bold ""
    print_bold "  Total elapsed: ${_total_elapsed}s"
    print_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [[ ${#FAILED_CORE_STAGES[@]} -gt 0 ]]; then
        print_fail "Failed core stage(s): ${FAILED_CORE_STAGES[*]}"
    else
        print_pass "All required stages passed."
    fi
    print_bold ""
}

# ---------------------------------------------------------------------------
# Main — run stages in CI order
# ---------------------------------------------------------------------------
CI_LOCAL_START_TIME=$(date +%s)
export CI_LOCAL_START_TIME

print_bold ""
print_bold "  Conclave Engine — Local CI Runner"
print_bold "  Repo: ${REPO_ROOT}"
print_bold ""

# Core stages (in CI dependency order: security -> lint -> test)
run_stage "security"   stage_security
run_stage "shell-lint" stage_shell_lint
run_stage "docs-gate"  stage_docs_gate
run_stage "lint"       stage_lint
run_stage "test"       stage_test
run_stage "frontend"   stage_frontend

# Optional stages (informational — failures never affect exit code)
run_stage "integration"  stage_integration
run_stage "synthesizer"  stage_synthesizer
run_stage "e2e"          stage_e2e
run_stage "trivy"        stage_trivy
run_stage "sbom"         stage_sbom
run_stage "zap"          stage_zap

print_summary

if [[ ${#FAILED_CORE_STAGES[@]} -gt 0 ]]; then
    exit 1
fi
exit 0
