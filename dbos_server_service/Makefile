# DBOS Server Manager
#
# Dev-окружение (общая БД devcontainer, данные сохраняются):
#   make dev               # применить миграции и запустить auth + logging в фоне
#   make dev-stop          # остановить оба сервиса (данные остаются)
#   make dev-logs          # следить за логами обоих сервисов
#
# Запуск отдельного сервиса (foreground, авто-перезагрузка):
#   make run-auth          # запустить auth_service на :8000
#   make run-logging       # запустить loging_service на :8001
#
# Тесты — локальный режим (devcontainer, без Docker):
#   make test-local              # unit-тесты auth + logging
#   make test-local-integration  # кросс-сервисные интеграционные тесты
#   make test-devcont-all        # всё: unit + integration + E2E docker registry
#
# Тесты — Docker-режим (CI / полная изоляция):
#   make test-auth           # тесты auth_service в Docker
#   make test-logging        # тесты loging_service в Docker
#   make test-integration    # кросс-сервисные тесты в Docker
#   make test-all            # всё в Docker
#   make test-down           # удалить тестовые контейнеры и тома

AUTH    = docker compose -f auth_service/tests/docker-compose.test.yml
LOGGING = docker compose -f loging_service/tests/docker-compose.test.yml
ROOT    = docker compose -f tests/docker-compose.test.yml

# ── Dev-параметры (devcontainer postgres) ─────────────────────────────────────
_DEV_PG         = postgresql+psycopg://app_user:app_password@postgres:5432
_DEV_AUTH_DB    = $(_DEV_PG)/dev_auth
_DEV_LOG_DB     = $(_DEV_PG)/dev_logging
_DEV_LOG_KEY    = dev-logging-api-key
_DEV_SECRET     = dev-secret-key-for-local-must-be-32-chars!
_DEV_ADMIN_USER = admin
_DEV_ADMIN_PASS = 1234
_DEV_ADMIN_EMAIL = admin@example.com

.PHONY: help \
        dev dev-stop dev-logs \
        run-auth run-logging \
        test-dev test-dev-auth test-dev-logging test-dev-integration test-dev-all \
        test-local test-local-auth test-local-logging test-local-integration test-local-all \
        test-devcont-all install-registry \
        test-integration test-auth test-auth-e2e test-logging test-all test-down test-build

# ── Dev-окружение ─────────────────────────────────────────────────────────────

dev: ## Применить миграции и запустить auth + logging на общей dev-БД (данные сохраняются)
	@$(MAKE) dev-stop --no-print-directory 2>/dev/null; true
	@mkdir -p $(CURDIR)/.dev
	@echo "→ Пересоздаём dev-базы данных (чистая БД)..."
	@python3 -c "\
import psycopg; \
conn = psycopg.connect(host='postgres', user='app_user', password='app_password', dbname='postgres', autocommit=True); \
[conn.execute('DROP DATABASE IF EXISTS \"' + db + '\"') for db in ('dev_auth', 'dev_logging')]; \
[conn.execute('CREATE DATABASE \"' + db + '\"') for db in ('dev_auth', 'dev_logging')]; \
conn.close()"
	@echo "→ Миграции loging_service..."
	@cd loging_service && DATABASE_URL=$(_DEV_LOG_DB) PYTHONPATH=. alembic upgrade head
	@echo "→ Миграции auth_service..."
	@cd auth_service && DATABASE_URL=$(_DEV_AUTH_DB) PYTHONPATH=. alembic upgrade head
	@echo "→ Запуск loging_service :8001..."
	@cd loging_service && \
		DATABASE_URL=$(_DEV_LOG_DB) SERVICE_API_KEY=$(_DEV_LOG_KEY) \
		AUTH_SERVICE_URL=http://localhost:8000 \
		PYTHONPATH=. \
		uvicorn src.main:app --host 0.0.0.0 --port 8001 \
		> $(CURDIR)/.dev/logging.log 2>&1 & echo $$! > $(CURDIR)/.dev/logging.pid
	@echo "→ Ожидание готовности loging_service..."
	@until curl -sf http://localhost:8001/api/logging/v1/health > /dev/null 2>&1; do sleep 1; done
	@echo "  loging_service готов"
	@echo "→ Запуск auth_service :8000..."
	@cd auth_service && \
		DATABASE_URL=$(_DEV_AUTH_DB) SECRET_KEY=$(_DEV_SECRET) \
		ACCESS_TOKEN_TTL_MINUTES=60 REFRESH_TOKEN_TTL_DAYS=14 \
		LOGGING_SERVICE_URL=http://localhost:8001 LOGGING_SERVICE_API_KEY=$(_DEV_LOG_KEY) \
		INITIAL_ADMIN_USERNAME=$(_DEV_ADMIN_USER) INITIAL_ADMIN_PASSWORD=$(_DEV_ADMIN_PASS) INITIAL_ADMIN_EMAIL=$(_DEV_ADMIN_EMAIL) \
		PYTHONPATH=. uvicorn src.main:app --host 0.0.0.0 --port 8000 \
		> $(CURDIR)/.dev/auth.log 2>&1 & echo $$! > $(CURDIR)/.dev/auth.pid
	@echo "→ Ожидание готовности auth_service..."
	@until curl -sf http://localhost:8000/api/auth/v1/health > /dev/null 2>&1; do sleep 1; done
	@echo "  auth_service готов"
	@echo "→ Наполнение начальными данными..."
	@python3 $(CURDIR)/scripts/seed_dev.py

dev-stop: ## Остановить оба dev-сервиса (данные в БД сохраняются)
	@if test -f $(CURDIR)/.dev/auth.pid; then \
		kill $$(cat $(CURDIR)/.dev/auth.pid) 2>/dev/null || true; rm -f $(CURDIR)/.dev/auth.pid; \
	fi
	@if test -f $(CURDIR)/.dev/logging.pid; then \
		kill $$(cat $(CURDIR)/.dev/logging.pid) 2>/dev/null || true; rm -f $(CURDIR)/.dev/logging.pid; \
	fi
	@fuser -k 8000/tcp 2>/dev/null || true
	@fuser -k 8001/tcp 2>/dev/null || true
	@echo "  сервисы остановлены"

dev-logs: ## Следить за логами обоих dev-сервисов (Ctrl+C для выхода)
	@tail -f $(CURDIR)/.dev/auth.log $(CURDIR)/.dev/logging.log

# ── Справка ───────────────────────────────────────────────────────────────────

help: ## Показать список всех команд
	@echo ""
	@echo "  DBOS Server Manager — доступные команды:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ── Запуск сервисов ───────────────────────────────────────────────────────────

run-auth: ## Запустить auth_service локально (порт 8000, авто-перезагрузка)
	cd auth_service && PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

run-logging: ## Запустить loging_service локально (порт 8001, авто-перезагрузка)
	cd loging_service && PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8001

# ── Тесты в devcontainer ──────────────────────────────────────────────────────
# Все команды работают только внутри devcontainer (нет Docker).
#
# Запуск конкретного теста/файла/директории:
#   make test-dev TEST=loging_service/tests/test_rules.py
#   make test-dev TEST=loging_service/tests/test_rules.py::TestRulesCRUD
#   make test-dev TEST=auth_service/tests/auth/test_login.py
#   make test-dev TEST=tests/integration/test_auth_logging.py
#
# Запуск всех тестов:
#   make test-dev-all

_LOGGING_TEST_URL = postgresql+psycopg://app_user:app_password@postgres:5432/test_logging_unit
_AUTH_TEST_URL    = postgresql+psycopg://app_user:app_password@postgres:5432/test_auth_unit

test-dev: ## Запустить конкретный тест. Пример: make test-dev TEST=loging_service/tests/test_rules.py
	@if [ -z "$(TEST)" ]; then \
		echo "Использование: make test-dev TEST=<путь>"; \
		echo "Примеры:"; \
		echo "  make test-dev TEST=loging_service/tests/test_rules.py"; \
		echo "  make test-dev TEST=auth_service/tests/auth/test_login.py"; \
		echo "  make test-dev TEST=tests/integration/test_auth_logging.py"; \
		exit 1; \
	fi
	@if echo "$(TEST)" | grep -q "^loging_service/"; then \
		cd loging_service && TEST_DATABASE_URL=$(_LOGGING_TEST_URL) \
			PYTHONPATH=. pytest ../$(TEST) -v --tb=short; \
	elif echo "$(TEST)" | grep -q "^auth_service/"; then \
		cd auth_service && TEST_DATABASE_URL=$(_AUTH_TEST_URL) \
			PYTHONPATH=. pytest ../$(TEST) -v --tb=short; \
	else \
		pytest $(TEST) -v --tb=short; \
	fi

test-dev-auth: ## Unit-тесты auth_service (devcontainer)
	cd auth_service && TEST_DATABASE_URL=$(_AUTH_TEST_URL) \
		PYTHONPATH=. pytest tests/ -m "not e2e" -v --tb=short

test-dev-logging: ## Unit-тесты loging_service (devcontainer)
	cd loging_service && TEST_DATABASE_URL=$(_LOGGING_TEST_URL) \
		PYTHONPATH=. pytest tests/ -v --tb=short

test-dev-integration: ## Кросс-сервисные интеграционные тесты (devcontainer, порты :8010/:8011)
	pytest tests/ -v --tb=short

test-dev-all: test-dev-auth test-dev-logging test-dev-integration ## Все тесты в devcontainer

# ── Тесты: локальный режим (алиасы для обратной совместимости) ────────────────

test-local-auth: test-dev-auth ## = test-dev-auth
test-local-logging: test-dev-logging ## = test-dev-logging
test-local: test-dev-auth test-dev-logging ## = test-dev-auth + test-dev-logging
test-local-integration: test-dev-integration ## = test-dev-integration
test-local-all: test-dev-all ## = test-dev-all

test-devcont-all: ## Полный набор в devcontainer: unit + integration + E2E docker registry
	python tests/scripts/run_devcont_tests.py

install-registry: ## Установить бинарник registry v2.8.3 (нужно выполнить один раз)
	bash tests/scripts/install_registry.sh

# ── Тесты: Docker-режим ───────────────────────────────────────────────────────

test-auth: ## Тесты auth_service в Docker (без E2E docker-registry)
	$(AUTH) run --rm test-runner pytest tests/ -m "not e2e" -v --tb=short

test-auth-e2e: ## Тесты auth_service в Docker включая E2E docker-registry
	$(AUTH) run --rm test-runner pytest tests/ -v --tb=short

test-logging: ## Тесты loging_service в Docker
	$(LOGGING) run --rm test-runner pytest tests/ -v --tb=short

test-integration: ## Кросс-сервисные интеграционные тесты в Docker
	$(ROOT) run --rm test-runner

test-all: test-auth test-logging test-integration ## Все тесты в Docker

test-build: ## Пересобрать все тестовые образы
	$(AUTH) build
	$(LOGGING) build
	$(ROOT) build

test-down: ## Удалить тестовые контейнеры и тома
	$(AUTH) down -v --remove-orphans
	$(LOGGING) down -v --remove-orphans
	$(ROOT) down -v --remove-orphans
