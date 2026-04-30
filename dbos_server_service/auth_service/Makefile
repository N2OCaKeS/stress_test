# auth_service test commands
#
# All test commands run inside Docker so no local Python setup needed.
# Prerequisites: Docker + Docker Compose v2 installed on the host.
#
# Usage:
#   make test          # unit + integration tests only (fast, ~30s)
#   make test-e2e      # E2E registry tests only
#   make test-all      # everything
#   make test-ci       # what CI runs: all tests, exit on first failure
#   make test-down     # stop and remove test containers + volumes

COMPOSE = docker compose -f tests/docker-compose.test.yml
RUNNER  = $(COMPOSE) run --rm test-runner

.PHONY: test test-e2e test-all test-ci test-down test-build test-logs

## Fast: unit + integration (no docker registry needed)
test:
	$(RUNNER) pytest tests/ -m "not e2e" -v --tb=short

## E2E only: requires full stack (registry + auth-service-e2e)
test-e2e:
	$(RUNNER) pytest tests/e2e/ -m e2e -v --tb=short

## All tests
test-all:
	$(RUNNER) pytest tests/ -v --tb=short

## CI mode: fail fast, no colour
test-ci:
	$(RUNNER) pytest tests/ -x --tb=short --no-header -q

## Rebuild images (after code changes)
test-build:
	$(COMPOSE) build

## Tail logs from all services
test-logs:
	$(COMPOSE) logs -f

## Destroy containers + volumes (including test DB data)
test-down:
	$(COMPOSE) down -v --remove-orphans
