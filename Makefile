# --- Configuration ---

YANG_MODELS_REPO ?= https://github.com/nokia/srlinux-yang-models
YANG_MODELS_TAG  ?= v24.10.1

TELEMETRY_LAB_REPO ?= https://github.com/srl-labs/srl-telemetry-lab
TELEMETRY_LAB_DIR  ?= srl-telemetry-lab

# --- Development ---

.PHONY: install-deps
install-deps:     ## Install uv, containerlab (+ Docker), and gnmic
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "Installing uv..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
	else \
		echo "uv already installed"; \
	fi
	@if ! command -v containerlab >/dev/null 2>&1; then \
		echo "Installing containerlab (includes Docker)..."; \
		bash -c "$$(curl -sL https://get.containerlab.dev)"; \
	else \
		echo "containerlab already installed"; \
	fi
	@if ! command -v gnmic >/dev/null 2>&1; then \
		echo "Installing gnmic..."; \
		bash -c "$$(curl -sL https://get-gnmic.openconfig.net)"; \
	else \
		echo "gnmic already installed"; \
	fi
	@echo ""
	@echo "All dependencies installed. Run 'make setup' next."

.PHONY: check-prereqs
check-prereqs:    ## Check that required tools are installed
	@missing=0; \
	if ! command -v docker >/dev/null 2>&1; then \
		echo "  docker not found — install from https://docs.docker.com/engine/install/"; \
		missing=1; \
	fi; \
	if ! command -v containerlab >/dev/null 2>&1; then \
		echo "  containerlab not found — install from https://containerlab.dev/install/"; \
		missing=1; \
	fi; \
	if ! command -v gnmic >/dev/null 2>&1; then \
		echo "  gnmic not found — install from https://gnmic.openconfig.net/install/"; \
		missing=1; \
	fi; \
	if [ $$missing -eq 1 ]; then \
		echo ""; \
		echo "Install the missing tools above, then re-run make setup."; \
		exit 1; \
	fi; \
	echo "All prerequisites found."

.PHONY: setup
setup: check-prereqs yang-models lab ## Set up local dev environment (install deps + YANG models + lab)
	@uv sync

.PHONY: yang-models
yang-models:      ## Clone SR Linux YANG models (if not present)
	@if [ ! -d "srlinux-yang-models" ]; then \
		echo "Cloning YANG models ($(YANG_MODELS_TAG))..."; \
		git clone -b $(YANG_MODELS_TAG) --depth 1 $(YANG_MODELS_REPO); \
	else \
		echo "YANG models already present"; \
	fi

.PHONY: lab
lab:              ## Clone srl-telemetry-lab (if not present)
	@if [ ! -d "$(TELEMETRY_LAB_DIR)" ]; then \
		echo "Cloning srl-telemetry-lab..."; \
		git clone $(TELEMETRY_LAB_REPO); \
	else \
		echo "srl-telemetry-lab already present"; \
	fi

.PHONY: lab-up
lab-up: setup lab ## Start the telemetry lab (requires containerlab)
	cd $(TELEMETRY_LAB_DIR) && containerlab deploy --reconfigure

.PHONY: lab-down
lab-down:         ## Stop the telemetry lab
	cd $(TELEMETRY_LAB_DIR) && containerlab destroy

.PHONY: lab-traffic
lab-traffic:      ## Generate traffic between lab nodes
	docker exec -d client1 bash /config/traffic.sh

.PHONY: run
run:              ## Run srl-explorer locally
	@uv run srl-explorer

.PHONY: audit
audit:            ## Check dependencies for known vulnerabilities
	@uv run pip-audit --skip-editable

.PHONY: lint
lint:             ## Run linter (ruff check)
	@uv run ruff check src/

.PHONY: format
format:           ## Format code (ruff format)
	@uv run ruff format src/

.PHONY: test
test:             ## Run tests
	@uv run pytest tests/ -v

.PHONY: clean
clean:            ## Remove caches, logs, build artifacts
	@rm -rf .cache/ logs/ dist/ build/
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true

# --- Container ---

IMAGE_NAME ?= srl-explorer
IMAGE_TAG  ?= latest

.PHONY: docker-build
docker-build:     ## Build the Docker container
	@docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

.PHONY: docker-run
docker-run:       ## Run srl-explorer in a container (--network host, --env-file .env)
	@mkdir -p logs
	@docker run -it --rm \
		--user $$(id -u):$$(id -g) \
		--network host \
		--env-file .env \
		-v $(PWD)/logs:/app/logs \
		$(IMAGE_NAME):$(IMAGE_TAG)

.PHONY: docker-shell
docker-shell:     ## Shell into the container for debugging
	@docker run -it --rm \
		--user $$(id -u):$$(id -g) \
		--network host \
		--env-file .env \
		-v $(PWD)/logs:/app/logs \
		$(IMAGE_NAME):$(IMAGE_TAG) /bin/bash

# --- Help ---

.PHONY: help
help:             ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
