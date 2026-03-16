# =============================================================================
# Makefile — Conclave Engine build targets
#
# Usage:
#   make              → show this help message
#   make build        → build the conclave-engine Docker image
#   make build-airgap-bundle → create an offline deployable tar.gz bundle
#   make ci-local     → run all local CI gates (mirrors GitHub Actions)
# =============================================================================

.DEFAULT_GOAL := help

IMAGE_NAME ?= conclave-engine
IMAGE_TAG  ?= latest

# ---------------------------------------------------------------------------
# help — list available targets (default)
# ---------------------------------------------------------------------------
.PHONY: help
help: ## Show this help message
	@echo "Conclave Engine — available make targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ---------------------------------------------------------------------------
# build — build the production Docker image
# ---------------------------------------------------------------------------
.PHONY: build
build: ## Build the conclave-engine:latest Docker image
	docker build \
		--tag $(IMAGE_NAME):$(IMAGE_TAG) \
		--file Dockerfile \
		.

# ---------------------------------------------------------------------------
# build-airgap-bundle — create a self-contained offline deployment bundle
# ---------------------------------------------------------------------------
.PHONY: build-airgap-bundle
build-airgap-bundle: build ## Build image then create the air-gap tar.gz bundle
	bash scripts/build_airgap.sh

# ---------------------------------------------------------------------------
# ci-local — run all local CI gates (mirrors GitHub Actions)
# ---------------------------------------------------------------------------
.PHONY: ci-local
ci-local: ## Run all local CI gates — mirrors GitHub Actions pipeline
	bash scripts/ci-local.sh
