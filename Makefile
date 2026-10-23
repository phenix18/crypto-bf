# AI-Trader — local development entry points.
# Run `make help` for the list of targets.

VENV    ?= .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
API_PORT ?= 8000
BASE_URL ?= http://127.0.0.1:$(API_PORT)

.DEFAULT_GOAL := help
.PHONY: help bootstrap install test test-frontend test-all api worker frontend build dev smoke clean \
        docker-up docker-down docker-logs require-venv require-npm require-npm-binary require-docker

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- guards ----
# Without these, a missing venv surfaces as `make: .venv/bin/python: No such
# file or directory`, which says nothing about the step that was skipped.

require-venv:
	@test -x "$(PY)" || { \
		echo ""; \
		echo "  No Python environment at $(VENV)/."; \
		echo "  Run this first:   ./scripts/bootstrap.sh   (or: make bootstrap)"; \
		echo ""; \
		exit 1; \
	}

require-npm-binary:
	@command -v npm >/dev/null 2>&1 || { \
		echo ""; \
		echo "  npm is not installed, and this target needs it to build the SPA."; \
		echo "  Install Node.js 20 or newer, then re-run."; \
		echo "  The backend alone works without it:   make api"; \
		echo ""; \
		exit 1; \
	}

require-npm: require-npm-binary
	@test -d service/frontend/node_modules || { \
		echo ""; \
		echo "  Frontend dependencies are not installed."; \
		echo "  Run:   cd service/frontend && npm ci     (or: ./scripts/bootstrap.sh)"; \
		echo ""; \
		exit 1; \
	}

require-docker:
	@docker compose version >/dev/null 2>&1 || { \
		echo ""; \
		echo "  Docker with the compose plugin is not installed."; \
		echo "  Install Docker Desktop, or the docker-compose-plugin package."; \
		echo "  To run without Docker:   ./scripts/bootstrap.sh && make dev"; \
		echo ""; \
		exit 1; \
	}
	@docker info >/dev/null 2>&1 || { \
		echo ""; \
		echo "  The Docker daemon is not reachable — the CLI is installed but nothing"; \
		echo "  is listening. Start Docker Desktop, or:   sudo systemctl start docker"; \
		echo "  To run without Docker:   ./scripts/bootstrap.sh && make dev"; \
		echo ""; \
		exit 1; \
	}

# --------------------------------------------------------------- targets ----

bootstrap: ## Create the venv, install backend + frontend deps, seed .env
	./scripts/bootstrap.sh

install: ## Install/refresh backend dependencies only
	$(PIP) install -r service/requirements.txt

test: require-venv ## Run the backend test suite
	$(PY) -m pytest

test-frontend: require-npm ## Run the frontend unit tests
	cd service/frontend && npm test

test-all: test test-frontend ## Run both suites

api: require-venv ## Start the FastAPI server (default http://127.0.0.1:8000)
	cd service/server && API_STDERR_LOG=true ../../$(PY) -m uvicorn main:app --host 0.0.0.0 --port $(API_PORT) --reload

worker: require-venv ## Start the background worker (prices, settlements, market intel)
	cd service/server && ../../$(PY) worker.py

frontend: require-npm ## Start the Vite dev server (http://127.0.0.1:3000)
	cd service/frontend && VITE_API_PROXY_TARGET=http://127.0.0.1:$(API_PORT) npm run dev

build: require-npm-binary ## Build the frontend into service/frontend/dist (served by the API at /)
	cd service/frontend && npm ci && npm run build

dev: require-venv ## Run API + worker + frontend together
	./scripts/dev.sh

smoke: ## Exercise a running instance end to end
	./scripts/smoke.sh $(BASE_URL)

docker-up: require-docker ## Start the full stack (Postgres, Redis, API, worker) with Docker
	docker compose up --build -d

docker-down: require-docker ## Stop the Docker stack
	docker compose down

docker-logs: require-docker ## Tail the Docker stack logs
	docker compose logs -f

clean: ## Remove build artefacts and caches
	rm -rf service/frontend/dist service/frontend/node_modules .pytest_cache
	find . -name __pycache__ -type d -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
