# server_worker test commands
#
# All test commands run inside Docker so no local Python setup is needed.
# Prerequisites: Docker + Docker Compose v2 installed on the host.
#
# Usage:
#   make test        # run all tests
#   make test-build  # rebuild the test image (after dependency changes)
#   make test-down   # stop and remove test containers + volumes

COMPOSE = docker compose -f tests/docker-compose.test.yml
RUNNER  = $(COMPOSE) run --rm test-runner

.PHONY: test test-down test-build test-logs

test:
	$(RUNNER) pytest tests/ -v --tb=short

test-build:
	$(COMPOSE) build

test-logs:
	$(COMPOSE) logs -f

test-down:
	$(COMPOSE) down -v --remove-orphans
