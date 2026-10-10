# ShutterStorm: common commands. Run `make help` for the list.
# Recipes run in bash regardless of your login shell (fish, zsh, ...).
SHELL := /usr/bin/env bash

IMMICH_DIR ?= immich
COMPOSE    := docker compose --project-directory $(IMMICH_DIR) \
              -f $(IMMICH_DIR)/docker-compose.yml \
              -f deploy/docker-compose.shutterstorm.yml
IMMICH_URL ?= http://127.0.0.1:2283
PYTHON     ?= python3

.PHONY: help install immich-fetch pull up down ps logs health bootstrap seed discover probe baseline drill evaluate run llm-check cleanup lint test

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-14s %s\n", $$1, $$2}'

install: ## Install ShutterStorm + dev tools into the active venv
	$(PYTHON) -m pip install -e ".[dev]"

immich-fetch: ## Download pinned Immich release files into immich/
	./scripts/fetch_immich.sh

pull: ## Pre-pull Immich images (large; do this on good Wi-Fi)
	$(COMPOSE) pull

up: ## Start Immich in the background
	@test -f $(IMMICH_DIR)/docker-compose.yml || { echo "Run 'make immich-fetch' first"; exit 1; }
	$(COMPOSE) up -d

down: ## Stop Immich (keeps data)
	$(COMPOSE) down

ps: ## Show Immich containers and health
	$(COMPOSE) ps

logs: ## Follow Immich logs (Ctrl-C to exit). One service: make logs s=immich-server
	$(COMPOSE) logs -f --tail=100 $(s)

health: ## Wait until all containers are healthy and the API answers ping
	./scripts/wait_healthy.sh "$(IMMICH_URL)"

bootstrap: ## Create admin (if missing) + API key, saved to .env (prompts for login)
	$(PYTHON) scripts/bootstrap.py --url $(IMMICH_URL)

seed: ## Upload the sample photos in data/sample_images/
	$(PYTHON) scripts/seed.py

discover: ## Map the app: topology.json, dependency-map.png, static audit
	shutterstorm discover

probe: ## 60 s of live probes (no fault)
	shutterstorm probe --seconds 60

baseline: ## 5 minutes of steady-state probes -> runs/baseline.json
	shutterstorm probe --seconds 300 --out runs/baseline-run
	$(PYTHON) -c "import json,pathlib;from shutterstorm.analyzer.metrics import compute_baseline as c;pathlib.Path('runs/baseline.json').write_text(json.dumps(c(pathlib.Path('runs/baseline-run')),indent=2));print('wrote runs/baseline.json')"

drill: ## PROVE the guard: must auto-abort and roll back before any real experiment
	shutterstorm drill

evaluate: ## Final evaluation flow: baseline -> ML fault -> restore -> verify -> report
	shutterstorm evaluate

run: ## The agent loop: plan -> inject -> observe -> analyze -> report
	shutterstorm run

llm-check: ## Check the LLM key, models, quota and JSON/tool support
	shutterstorm llm-check

cleanup: ## Delete the photos the probes uploaded (ss-probe-*)
	shutterstorm cleanup

lint: ## Lint Python code
	$(PYTHON) -m ruff check .

test: ## Run unit tests
	$(PYTHON) -m pytest -q
