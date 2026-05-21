# server_service developer commands
#
# All commands run inside Docker so no local Python setup is needed.
# Prerequisites: Docker + Docker Compose v2 installed on the host.
#
# Run the service:
#   make up          # start service + postgres (applies migrations automatically)
#   make up-build    # rebuild the image and start
#   make logs        # tail service logs (Ctrl+C to detach)
#   make ps          # status of dev containers
#   make sh          # shell inside the running server_service container
#   make down        # stop the stack (volume preserved)
#   make down-v      # stop and drop the postgres volume (full reset)
#
# Tests:
#   make test        # run all tests
#   make test-build  # rebuild the test image (after dependency changes)
#   make test-down   # stop and remove test containers + volumes

DEV     = docker compose -f docker/docker-compose.yml
COMPOSE = docker compose -f tests/docker-compose.test.yml
RUNNER  = $(COMPOSE) run --rm test-runner

.PHONY: up up-build down down-v logs ps sh \
        test test-down test-build test-logs

##@ Run

up: ## Start server_service + postgres (migrations applied on start)
	@$(DEV) up -d --wait server_postgres server_service
	@echo ""
	@echo "  server_service  →  http://localhost:8002/docs"
	@echo "  postgres        →  localhost:5434 (server_user / server_password / server_db)"

up-build: ## Rebuild image and start
	$(DEV) up -d --wait --build server_postgres server_service

down: ## Stop the stack (postgres volume preserved)
	$(DEV) down

down-v: ## Stop and drop the postgres volume
	$(DEV) down -v

logs: ## Tail server_service logs (Ctrl+C to detach)
	$(DEV) logs -f server_service

ps: ## Status of dev containers
	$(DEV) ps

sh: ## Shell inside the running server_service container
	$(DEV) exec server_service /bin/bash

##@ Tests

test:
	$(RUNNER) pytest tests/ -v --tb=short

test-build:
	$(COMPOSE) build

test-logs:
	$(COMPOSE) logs -f

test-down:
	$(COMPOSE) down -v --remove-orphans
