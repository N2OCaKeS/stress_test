# loging_service test commands
#
# Usage:
#   make test        # all tests in Docker
#   make test-down   # remove containers and volumes
#   make test-build  # rebuild test image

COMPOSE = docker compose -f tests/docker-compose.test.yml
RUNNER  = $(COMPOSE) run --rm test-runner

.PHONY: test test-down test-build

test:
	$(RUNNER) pytest tests/ -v --tb=short

test-down:
	$(COMPOSE) down -v --remove-orphans

test-build:
	$(COMPOSE) build
