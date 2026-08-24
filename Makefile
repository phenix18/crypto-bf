# AI-Trader — local development entry points.
# Run `make help` for the list of targets.

VENV    ?= .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
API_PORT ?= 8000
BASE_URL ?= http://127.0.0.1:$(API_PORT)

.DEFAULT_GOAL := help
.PHONY: help bootstrap install test api worker frontend build dev smoke clean docker-up docker-down docker-logs

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

bootstrap: ## Create the venv, install backend + frontend deps, seed .env
	./scripts/bootstrap.sh

install: ## Install/refresh backend dependencies only
	$(PIP) install -r service/requirements.txt

test: ## Run the backend test suite
	$(PY) -m pytest

api: ## Start the FastAPI server (default http://127.0.0.1:8000)
	cd service/server && API_STDERR_LOG=true ../../$(PY) -m uvicorn main:app --host 0.0.0.0 --port $(API_PORT) --reload

worker: ## Start the background worker (prices, settlements, market intel)
	cd service/server && ../../$(PY) worker.py

frontend: ## Start the Vite dev server (http://127.0.0.1:3000)
	cd service/frontend && VITE_API_PROXY_TARGET=http://127.0.0.1:$(API_PORT) npm run dev

build: ## Build the frontend into service/frontend/dist (served by the API at /)
	cd service/frontend && npm ci && npm run build

dev: ## Run API + worker + frontend together
	./scripts/dev.sh

smoke: ## Exercise a running instance end to end
	./scripts/smoke.sh $(BASE_URL)

docker-up: ## Start the full stack (Postgres, Redis, API, worker) with Docker
	docker compose up --build -d

docker-down: ## Stop the Docker stack
	docker compose down

docker-logs: ## Tail the Docker stack logs
	docker compose logs -f

clean: ## Remove build artefacts and caches
	rm -rf service/frontend/dist service/frontend/node_modules .pytest_cache
	find . -name __pycache__ -type d -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
