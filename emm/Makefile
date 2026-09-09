# DBOS Server Manager
#
# Два режима ручной отладки:
#
#   A) Dev-стек В КОНТЕЙНЕРАХ (без devcontainer, с хоста) — make up / make seed / make down
#   B) Dev-окружение внутри devcontainer (Python на хосте devcontainer'a) — make dev / make dev-stop
#
# Dev-стек в Docker (постгрес + оба сервиса в контейнерах, hot reload через mount src/):
#   make up                # поднять стек (данные сохраняются)
#   make up-build          # пересобрать образы и поднять
#   make seed              # ЧИСТАЯ БД + наполнить dev-данными (down -v + up + seed_dev.py)
#   make logs              # tail логи auth + logging + server + worker
#   make ps                # статус контейнеров
#   make sh-auth           # shell в контейнере auth_service
#   make sh-logging        # shell в контейнере loging_service
#   make sh-server         # shell в контейнере server_service
#   make sh-worker         # shell в контейнере server_worker
#   make down              # остановить (данные сохраняются)
#   make down-v            # остановить + удалить тома postgres+redis
#
# Devcontainer dev-окружение (общая БД devcontainer, данные сохраняются):
#   make dev               # применить миграции и запустить auth + logging в фоне
#   make dev-stop          # остановить оба сервиса (данные остаются)
#   make dev-logs          # следить за логами обоих сервисов
#
# Запуск отдельного сервиса (foreground, авто-перезагрузка, devcontainer):
#   make run-auth          # запустить auth_service на :8000
#   make run-logging       # запустить loging_service на :8001
#   make run-server        # запустить server_service на :8002
#
# Тесты — devcontainer (без Docker, postgres из devcontainer):
#   make test-dev-auth           # unit-тесты auth_service
#   make test-dev-logging        # unit-тесты loging_service
#   make test-dev-server         # unit-тесты server_service
#   make test-dev-integration    # кросс-сервисные интеграционные тесты
#   make test-dev-all            # всё выше разом
#   make test-dev TEST=path/to/test.py    # один конкретный тест/файл/директория
#   make test-devcont-all        # unit + integration + E2E docker registry
#   (test-local* — алиасы для test-dev* ради обратной совместимости.)
#
# Тесты — Docker-режим (CI / полная изоляция, работает с хоста без devcontainer):
#   make test-auth           # тесты auth_service в Docker (без E2E docker-registry)
#   make test-auth-e2e       # +E2E с docker-registry
#   make test-logging        # тесты loging_service в Docker
#   make test-server         # тесты server_service в Docker
#   make test-worker         # тесты server_worker в Docker
#   make test-integration    # кросс-сервисные тесты в Docker
#   make test-all            # всё в Docker
#   make test-build          # пересобрать все тестовые образы
#   make test-down           # удалить тестовые контейнеры и тома
#
# Полный список с группами: make help

AUTH    = docker compose -f auth_service/tests/docker-compose.test.yml
LOGGING = docker compose -f loging_service/tests/docker-compose.test.yml
SERVER  = docker compose -f server_service/tests/docker-compose.test.yml
WORKER  = docker compose -f server_worker/tests/docker-compose.test.yml
SECRET  = docker compose -f secret_service/tests/docker-compose.test.yml
TESTING = docker compose -f testing_service/tests/docker-compose.test.yml
TESTING_WORKER = docker compose -f testing_worker/tests/docker-compose.test.yml
ROOT    = docker compose -f tests/docker-compose.test.yml
E2E     = docker compose -f tests/integration/docker-compose.test.yml
DEV     = docker compose -f docker-compose.dev.yml
K8S     = kubectl -n dbos

# Prod-подобный стек в docker-compose (без k8s). Секреты — из .env.prod
# (--env-file), TLS — из ./certs. См. docker-compose.prod.yml.
PROD_ENV = .env.prod
PROD     = docker compose -f docker-compose.prod.yml --env-file $(PROD_ENV)

# Prod DNS-имя для будущего DNS-доступа. По умолчанию запекается в SAN
# self-signed сертификата вместе с auto-IP, чтобы тот же cert валидировался
# и сейчас (по IP), и позже (когда DNS пропишется), без перевыпуска.
# Override: make k8s-zero DNS=other.example.com   |  отключить: DNS=
DBOS_DNS ?= emm.devos.astralinux.ru

# ── Dev-параметры (devcontainer postgres) ─────────────────────────────────────
_DEV_PG         = postgresql+psycopg://app_user:app_password@postgres:5432
_DEV_AUTH_DB    = $(_DEV_PG)/dev_auth
_DEV_LOG_DB     = $(_DEV_PG)/dev_logging
_DEV_SERVER_DB  = $(_DEV_PG)/dev_server
_DEV_SECRET_DB  = $(_DEV_PG)/dev_secret
# make dev (devcontainer) — dev_testing БД в общем postgres, симметрично
# dev_secret выше. make up (docker-стек) использует отдельный контейнер
# testing_postgres — см. docker-compose.dev.yml.
_DEV_TESTING_DB = $(_DEV_PG)/dev_testing
_DEV_AUTH_AUDIT_KEY   = audit_auth_service_dev_key
_DEV_SERVER_AUDIT_KEY = audit_server_service_dev_key
_DEV_WORKER_AUDIT_KEY = audit_server_worker_dev_key
_DEV_SECRET_AUDIT_KEY = audit_secret_service_dev_key
_DEV_TESTING_AUDIT_KEY = audit_testing_service_dev_key
_DEV_CONFIG_AUDIT_KEY = audit_config_service_dev_key
_DEV_INTROSPECT_KEY   = dev-introspect-api-key
# legacy single-key alias оставлен для обратной совместимости с целями, которые
# не дифференцируют caller'а (например, generic test-инструменты).
_DEV_LOG_KEY    = $(_DEV_AUTH_AUDIT_KEY)
_DEV_SECRET     = dev-secret-key-for-local-must-be-32-chars!
_DEV_SERVER_KEY = dev-server-encryption-key-do-not-use-in-prod
_DEV_SECRET_KEY = dev-secret-service-encryption-key-do-not-use!
_DEV_ADMIN_USER = admin
_DEV_ADMIN_PASS = 1
_DEV_ADMIN_EMAIL = admin@example.com
# Токен бота воркера: auth на старте заводит под него бота server_worker
# в системном отделе. То же значение, что в docker-compose.dev.yml.
_DEV_WORKER_BOT_TOKEN = dbos_bot_dev_worker_local_token_do_not_use_in_prod

.PHONY: help \
        up up-build seed down down-v logs ps sh-auth sh-logging sh-server sh-worker sh-secret sh-testing logs-secret logs-testing \
        dev dev-stop dev-logs \
        run-auth run-logging run-server run-secret run-testing run-testing-worker \
        test-dev test-dev-auth test-dev-logging test-dev-server test-dev-worker test-dev-secret test-dev-testing test-dev-testing-worker test-dev-integration test-dev-all \
        test-local test-local-auth test-local-logging test-local-server test-local-worker test-local-secret test-local-testing test-local-testing-worker test-local-integration test-local-all \
        test-devcont-all install-registry \
        test-integration logs-test-integration up-test-integration down-test-integration \
        test-auth test-auth-e2e test-logging test-server test-worker test-secret test-testing test-testing-worker test-all test-down test-build \
        k8s-install k8s-install-k3s k8s-install-cert-manager k8s-secrets k8s-build \
        k8s-deploy k8s-migrate k8s-smoke k8s-rollout k8s-rollback \
        k8s-logs k8s-status k8s-backup k8s-destroy \
        k8s-zero k8s-install-docker k8s-install-cert-manager-images \
        k8s-rotate-server-master k8s-rotate-secret-master k8s-rotate-redis-stash-master \
        k8s-rotate-db-passwords k8s-rotate-redis-password k8s-rotate-s2s-keys \
        k8s-rotation-image k8s-rotation-trigger k8s-rotation-status k8s-rotation-logs \
        k8s-rotation-disable k8s-rotation-enable \
        k8s-backup-secret-full k8s-tls-renew \
        k8s-cronjob-list k8s-cronjob-history k8s-cronjob-suspend k8s-cronjob-resume \
        prod-deploy deploy prepare-docker prepare-k3s update k8s-update \
        prod-up prod-down prod-down-v prod-logs prod-ps prod-build

##@ Dev: стек в Docker (без devcontainer)

up: ## Поднять dev-стек С НУЛЯ: стереть тома + поднять + наполнить seed'ом. Каждый запуск — чистая БД с корректными dev-данными.
	@echo "→ Очистка томов (полный сброс данных)..."
	@$(DEV) down -v --remove-orphans 2>/dev/null || true
	@echo "→ Поднятие стека..."
	@$(DEV) up -d --wait postgres secret_postgres testing_postgres redis loging_service auth_service server_service server_worker secret_service testing_service testing_worker test_server web_ui
	@echo "→ Наполнение dev-данными (seed)..."
	@$(MAKE) --no-print-directory seed
	@echo ""
	@echo "  auth_service    →  http://localhost:8000/docs    (admin / 1)"
	@echo "  loging_service  →  http://localhost:8001/docs    (loging_admin1 / 1)"
	@echo "  server_service  →  http://localhost:8002/docs"
	@echo "  secret_service  →  http://localhost:8003/docs"
	@echo "  testing_service →  http://localhost:8004/docs"
	@echo "  server_worker   →  taskiq worker (no HTTP), redis://localhost:6379/0"
	@echo "  testing_worker  →  taskiq worker (no HTTP), redis://localhost:6379/3"
	@echo "  web_ui          →  http://localhost:5173            (vite dev, proxy → backend)"
	@echo ""
	@echo "  Стек поднят с чистой БД + dev-данными. test-server-01 / tester / tester1234."

up-keep: ## Поднять dev-стек БЕЗ сброса (данные в томах сохраняются; seed вручную при необходимости).
	@$(DEV) up -d --wait postgres secret_postgres testing_postgres redis loging_service auth_service server_service server_worker secret_service testing_service testing_worker test_server web_ui
	@echo ""
	@echo "  Стек поднят, данные сохранены. Наполнить/обновить: make seed"

up-build: ## Пересобрать образы и поднять dev-стек
	$(DEV) up -d --wait --build postgres secret_postgres testing_postgres redis loging_service auth_service server_service server_worker secret_service testing_service testing_worker test_server

seed: ## Наполнить уже поднятый dev-стек данными напрямую через SQL (admin/loging_admin1/dep_admin1/loging_reader1/user1, пароль 1)
	@cd scripts && python3 seed_dev.py

down: ## Остановить dev-стек (данные в томе postgres сохраняются)
	$(DEV) down

down-v: ## Остановить и удалить том postgres (полная очистка БД)
	$(DEV) down -v

logs: ## tail -f логов всех сервисов (Ctrl+C для выхода)
	$(DEV) logs -f loging_service auth_service server_service server_worker secret_service testing_service testing_worker

logs-secret: ## tail -f логов secret_service
	$(DEV) logs -f secret_service

logs-testing: ## tail -f логов testing_service + testing_worker
	$(DEV) logs -f testing_service testing_worker

ps: ## Статус контейнеров dev-стека
	$(DEV) ps

sh-auth: ## Shell внутри контейнера auth_service
	$(DEV) exec auth_service /bin/bash

sh-logging: ## Shell внутри контейнера loging_service
	$(DEV) exec loging_service /bin/bash

sh-server: ## Shell внутри контейнера server_service
	$(DEV) exec server_service /bin/bash

sh-worker: ## Shell внутри контейнера server_worker
	$(DEV) exec server_worker /bin/bash

sh-secret: ## Shell внутри контейнера secret_service
	$(DEV) exec secret_service /bin/bash

sh-testing: ## Shell внутри контейнера testing_service
	$(DEV) exec testing_service /bin/bash

##@ Dev: внутри devcontainer

dev: ## Применить миграции и запустить auth + logging + server на общей dev-БД (данные сохраняются)
	@$(MAKE) dev-stop --no-print-directory 2>/dev/null; true
	@mkdir -p $(CURDIR)/.dev
	@echo "→ Пересоздаём dev-базы данных (чистая БД)..."
	@python3 -c "\
import psycopg; \
conn = psycopg.connect(host='postgres', user='app_user', password='app_password', dbname='postgres', autocommit=True); \
[conn.execute('DROP DATABASE IF EXISTS \"' + db + '\"') for db in ('dev_auth', 'dev_logging', 'dev_server', 'dev_secret', 'dev_testing')]; \
[conn.execute('CREATE DATABASE \"' + db + '\"') for db in ('dev_auth', 'dev_logging', 'dev_server', 'dev_secret', 'dev_testing')]; \
conn.close()"
	@echo "→ Миграции loging_service..."
	@cd loging_service && DATABASE_URL=$(_DEV_LOG_DB) PYTHONPATH=. alembic upgrade head
	@echo "→ Миграции auth_service..."
	@cd auth_service && DATABASE_URL=$(_DEV_AUTH_DB) PYTHONPATH=. alembic upgrade head
	@echo "→ Миграции server_service..."
	@cd server_service && DATABASE_URL=$(_DEV_SERVER_DB) SERVER_ENCRYPTION_KEY=$(_DEV_SERVER_KEY) PYTHONPATH=. alembic upgrade head
	@echo "→ Миграции secret_service..."
	@cd secret_service && DATABASE_URL=$(_DEV_SECRET_DB) SECRET_ENCRYPTION_KEY=$(_DEV_SECRET_KEY) SECRET_ENCRYPTION_KEY_VERSION=2 AUTH_SERVICE_URL=http://localhost:8000 PYTHONPATH=. alembic upgrade head
	@echo "→ Миграции testing_service (0 миграций этой волной — no-op)..."
	@cd testing_service && DATABASE_URL=$(_DEV_TESTING_DB) AUTH_SERVICE_URL=http://localhost:8000 PYTHONPATH=. alembic upgrade head
	@echo "→ Запуск loging_service :8001..."
	@cd loging_service && \
		DATABASE_URL=$(_DEV_LOG_DB) \
		SERVICE_API_KEYS='{"auth_service":"$(_DEV_AUTH_AUDIT_KEY)","server_service":"$(_DEV_SERVER_AUDIT_KEY)","config_service":"$(_DEV_CONFIG_AUDIT_KEY)","server_worker":"$(_DEV_WORKER_AUDIT_KEY)","secret_service":"$(_DEV_SECRET_AUDIT_KEY)"}' \
		INTROSPECT_SERVICE_API_KEY=$(_DEV_INTROSPECT_KEY)-introspect \
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
		LOGGING_SERVICE_URL=http://localhost:8001 LOGGING_SERVICE_API_KEY=$(_DEV_AUTH_AUDIT_KEY) \
		INITIAL_ADMIN_USERNAME=$(_DEV_ADMIN_USER) INITIAL_ADMIN_PASSWORD=$(_DEV_ADMIN_PASS) INITIAL_ADMIN_EMAIL=$(_DEV_ADMIN_EMAIL) \
		WORKER_BOT_TOKEN=$(_DEV_WORKER_BOT_TOKEN) \
		PYTHONPATH=. uvicorn src.main:app --host 0.0.0.0 --port 8000 \
		> $(CURDIR)/.dev/auth.log 2>&1 & echo $$! > $(CURDIR)/.dev/auth.pid
	@echo "→ Ожидание готовности auth_service..."
	@until curl -sf http://localhost:8000/api/auth/v1/health > /dev/null 2>&1; do sleep 1; done
	@echo "  auth_service готов"
	@echo "→ Запуск server_service :8002..."
	@cd server_service && \
		DATABASE_URL=$(_DEV_SERVER_DB) SERVER_ENCRYPTION_KEY=$(_DEV_SERVER_KEY) \
		PYTHONPATH=. \
		uvicorn src.main:app --host 0.0.0.0 --port 8002 \
		> $(CURDIR)/.dev/server.log 2>&1 & echo $$! > $(CURDIR)/.dev/server.pid
	@echo "→ Ожидание готовности server_service..."
	@until curl -sf http://localhost:8002/api/server/v1/health > /dev/null 2>&1; do sleep 1; done
	@echo "  server_service готов"
	@echo "→ Запуск secret_service :8003..."
	@cd secret_service && \
		DATABASE_URL=$(_DEV_SECRET_DB) \
		SECRET_ENCRYPTION_KEY=$(_DEV_SECRET_KEY) \
		SECRET_ENCRYPTION_KEY_VERSION=2 \
		AUTH_SERVICE_URL=http://localhost:8000 \
		INTROSPECT_SERVICE_API_KEY=$(_DEV_INTROSPECT_KEY) \
		LOGGING_SERVICE_URL=http://localhost:8001 \
		LOGGING_SERVICE_API_KEY=$(_DEV_SECRET_AUDIT_KEY) \
		SWEEP_ENABLED=false \
		APP_ENV=local \
		PYTHONPATH=. \
		uvicorn src.main:app --host 0.0.0.0 --port 8003 \
		> $(CURDIR)/.dev/secret.log 2>&1 & echo $$! > $(CURDIR)/.dev/secret.pid
	@echo "→ Ожидание готовности secret_service..."
	@until curl -sf http://localhost:8003/api/secret/v1/health > /dev/null 2>&1; do sleep 1; done
	@echo "  secret_service готов"
	@echo "→ Запуск testing_service :8004..."
	@cd testing_service && \
		DATABASE_URL=$(_DEV_TESTING_DB) \
		AUTH_SERVICE_URL=http://localhost:8000 \
		INTROSPECT_SERVICE_API_KEY=$(_DEV_INTROSPECT_KEY) \
		LOGGING_SERVICE_URL=http://localhost:8001 \
		LOGGING_SERVICE_API_KEY=$(_DEV_TESTING_AUDIT_KEY) \
		APP_ENV=local \
		PYTHONPATH=. \
		uvicorn src.main:app --host 0.0.0.0 --port 8004 \
		> $(CURDIR)/.dev/testing.log 2>&1 & echo $$! > $(CURDIR)/.dev/testing.pid
	@echo "→ Ожидание готовности testing_service..."
	@until curl -sf http://localhost:8004/api/testing/v1/health > /dev/null 2>&1; do sleep 1; done
	@echo "  testing_service готов"
	@echo "→ Наполнение начальными данными..."
	@python3 $(CURDIR)/scripts/seed_dev.py

dev-stop: ## Остановить все dev-сервисы (данные в БД сохраняются)
	@if test -f $(CURDIR)/.dev/auth.pid; then \
		kill $$(cat $(CURDIR)/.dev/auth.pid) 2>/dev/null || true; rm -f $(CURDIR)/.dev/auth.pid; \
	fi
	@if test -f $(CURDIR)/.dev/logging.pid; then \
		kill $$(cat $(CURDIR)/.dev/logging.pid) 2>/dev/null || true; rm -f $(CURDIR)/.dev/logging.pid; \
	fi
	@if test -f $(CURDIR)/.dev/server.pid; then \
		kill $$(cat $(CURDIR)/.dev/server.pid) 2>/dev/null || true; rm -f $(CURDIR)/.dev/server.pid; \
	fi
	@if test -f $(CURDIR)/.dev/secret.pid; then \
		kill $$(cat $(CURDIR)/.dev/secret.pid) 2>/dev/null || true; rm -f $(CURDIR)/.dev/secret.pid; \
	fi
	@if test -f $(CURDIR)/.dev/testing.pid; then \
		kill $$(cat $(CURDIR)/.dev/testing.pid) 2>/dev/null || true; rm -f $(CURDIR)/.dev/testing.pid; \
	fi
	@fuser -k 8000/tcp 2>/dev/null || true
	@fuser -k 8001/tcp 2>/dev/null || true
	@fuser -k 8002/tcp 2>/dev/null || true
	@fuser -k 8003/tcp 2>/dev/null || true
	@fuser -k 8004/tcp 2>/dev/null || true
	@echo "  сервисы остановлены"

dev-logs: ## Следить за логами всех dev-сервисов (Ctrl+C для выхода)
	@tail -f $(CURDIR)/.dev/auth.log $(CURDIR)/.dev/logging.log $(CURDIR)/.dev/server.log $(CURDIR)/.dev/secret.log $(CURDIR)/.dev/testing.log

run-auth: ## Запустить auth_service локально (devcontainer, порт 8000, авто-перезагрузка)
	cd auth_service && PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

run-logging: ## Запустить loging_service локально (devcontainer, порт 8001, авто-перезагрузка)
	cd loging_service && PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8001

run-server: ## Запустить server_service локально (devcontainer, порт 8002, авто-перезагрузка)
	cd server_service && \
		DATABASE_URL=$(_DEV_SERVER_DB) SERVER_ENCRYPTION_KEY=$(_DEV_SERVER_KEY) \
		PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8002

run-secret: ## Запустить secret_service локально (devcontainer, порт 8003, авто-перезагрузка)
	cd secret_service && \
		DATABASE_URL=$(_DEV_SECRET_DB) \
		SECRET_ENCRYPTION_KEY=$(_DEV_SECRET_KEY) \
		SECRET_ENCRYPTION_KEY_VERSION=2 \
		AUTH_SERVICE_URL=http://localhost:8000 \
		INTROSPECT_SERVICE_API_KEY=$(_DEV_INTROSPECT_KEY) \
		LOGGING_SERVICE_URL=http://localhost:8001 \
		LOGGING_SERVICE_API_KEY=$(_DEV_SECRET_AUDIT_KEY) \
		SWEEP_ENABLED=false \
		APP_ENV=local \
		PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8003

run-testing: ## Запустить testing_service локально (devcontainer, порт 8004, авто-перезагрузка)
	cd testing_service && \
		DATABASE_URL=$(_DEV_TESTING_DB) \
		AUTH_SERVICE_URL=http://localhost:8000 \
		INTROSPECT_SERVICE_API_KEY=$(_DEV_INTROSPECT_KEY) \
		LOGGING_SERVICE_URL=http://localhost:8001 \
		LOGGING_SERVICE_API_KEY=$(_DEV_TESTING_AUDIT_KEY) \
		APP_ENV=local \
		PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8004

run-testing-worker: ## Запустить testing_worker локально (devcontainer, foreground). Нужен Redis на localhost:6379 (в devcontainer'е его нет по умолчанию — см. docker-compose.dev.yml `redis`).
	cd testing_worker && \
		REDIS_URL=redis://localhost:6379/3 \
		PYTHONPATH=. taskiq worker src.main:broker

##@ Тесты: в devcontainer
# Все команды работают только внутри devcontainer (нет Docker).
#
# Запуск конкретного теста/файла/директории:
#   make test-dev TEST=loging_service/tests/test_rules.py
#   make test-dev TEST=loging_service/tests/test_rules.py::TestRulesCRUD
#   make test-dev TEST=auth_service/tests/auth/test_login.py
#   make test-dev TEST=server_worker/tests/test_smoke.py
#   make test-dev TEST=tests/integration/test_auth_logging.py
#
# Запуск всех тестов:
#   make test-dev-all

_LOGGING_TEST_URL = postgresql+psycopg://app_user:app_password@postgres:5432/test_logging_unit
_AUTH_TEST_URL    = postgresql+psycopg://app_user:app_password@postgres:5432/test_auth_unit
_SERVER_TEST_URL  = postgresql+psycopg://app_user:app_password@postgres:5432/test_server_unit
_WORKER_TEST_URL  = postgresql+psycopg://app_user:app_password@postgres:5432/test_worker_unit
_SECRET_TEST_URL  = postgresql+psycopg://app_user:app_password@postgres:5432/test_secret_unit
_TESTING_TEST_URL = postgresql+psycopg://app_user:app_password@postgres:5432/test_testing_unit

test-dev: ## Запустить конкретный тест. Пример: make test-dev TEST=loging_service/tests/test_rules.py
	@if [ -z "$(TEST)" ]; then \
		echo "Использование: make test-dev TEST=<путь>"; \
		echo "Примеры:"; \
		echo "  make test-dev TEST=loging_service/tests/test_rules.py"; \
		echo "  make test-dev TEST=auth_service/tests/auth/test_login.py"; \
		echo "  make test-dev TEST=server_service/tests/test_health.py"; \
		echo "  make test-dev TEST=server_worker/tests/test_smoke.py"; \
		echo "  make test-dev TEST=tests/integration/test_auth_logging.py"; \
		exit 1; \
	fi
	@if echo "$(TEST)" | grep -q "^loging_service/"; then \
		cd loging_service && TEST_DATABASE_URL=$(_LOGGING_TEST_URL) \
			PYTHONPATH=. pytest ../$(TEST) -v --tb=short; \
	elif echo "$(TEST)" | grep -q "^auth_service/"; then \
		cd auth_service && TEST_DATABASE_URL=$(_AUTH_TEST_URL) \
			PYTHONPATH=. pytest ../$(TEST) -v --tb=short; \
	elif echo "$(TEST)" | grep -q "^server_service/"; then \
		cd server_service && DATABASE_URL=$(_SERVER_TEST_URL) TEST_DATABASE_URL=$(_SERVER_TEST_URL) \
			SERVER_ENCRYPTION_KEY=$(_DEV_SERVER_KEY) \
			PYTHONPATH=. pytest ../$(TEST) -v --tb=short; \
	elif echo "$(TEST)" | grep -q "^server_worker/"; then \
		cd server_worker && DATABASE_URL=$(_WORKER_TEST_URL) TEST_DATABASE_URL=$(_WORKER_TEST_URL) \
			REDIS_URL=redis://localhost:6379/0 \
			SERVER_SERVICE_URL=http://not-used \
			AUTH_SERVICE_URL=http://not-used \
			LOGGING_SERVICE_URL=http://not-used \
			WORKER_BOT_TOKEN=dummy-test-token \
			PYTHONPATH=. pytest ../$(TEST) -v --tb=short; \
	elif echo "$(TEST)" | grep -q "^secret_service/"; then \
		cd secret_service && DATABASE_URL=$(_SECRET_TEST_URL) TEST_DATABASE_URL=$(_SECRET_TEST_URL) \
			SECRET_ENCRYPTION_KEY=$(_DEV_SECRET_KEY) \
			SECRET_ENCRYPTION_KEY_VERSION=2 \
			AUTH_SERVICE_URL=http://not-used \
			APP_ENV=test SWEEP_ENABLED=false \
			PYTHONPATH=. pytest ../$(TEST) -v --tb=short; \
	elif echo "$(TEST)" | grep -q "^testing_service/"; then \
		cd testing_service && DATABASE_URL=$(_TESTING_TEST_URL) TEST_DATABASE_URL=$(_TESTING_TEST_URL) \
			REDIS_URL=redis://localhost:6379/3 \
			AUTH_SERVICE_URL=http://not-used \
			APP_ENV=test \
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

test-dev-server: ## Unit-тесты server_service (devcontainer)
	cd server_service && DATABASE_URL=$(_SERVER_TEST_URL) TEST_DATABASE_URL=$(_SERVER_TEST_URL) \
		SERVER_ENCRYPTION_KEY=$(_DEV_SERVER_KEY) \
		PYTHONPATH=. pytest tests/ -v --tb=short

test-dev-worker: ## Unit-тесты server_worker (devcontainer; Redis не поднимается, broker monkeypatch'ится)
	cd server_worker && DATABASE_URL=$(_WORKER_TEST_URL) TEST_DATABASE_URL=$(_WORKER_TEST_URL) \
		REDIS_URL=redis://localhost:6379/0 \
		SERVER_SERVICE_URL=http://not-used \
		AUTH_SERVICE_URL=http://not-used \
		LOGGING_SERVICE_URL=http://not-used \
		WORKER_BOT_TOKEN=dummy-test-token \
		PYTHONPATH=. pytest tests/ -v --tb=short

test-dev-secret: ## Unit-тесты secret_service (devcontainer)
	cd secret_service && DATABASE_URL=$(_SECRET_TEST_URL) TEST_DATABASE_URL=$(_SECRET_TEST_URL) \
		SECRET_ENCRYPTION_KEY=$(_DEV_SECRET_KEY) \
		SECRET_ENCRYPTION_KEY_VERSION=2 \
		AUTH_SERVICE_URL=http://not-used \
		APP_ENV=test SWEEP_ENABLED=false \
		PYTHONPATH=. pytest tests/ -v --tb=short

test-dev-testing: ## Unit-тесты testing_service (devcontainer; Redis не поднимается по умолчанию — см. docker-compose.dev.yml `redis`)
	cd testing_service && DATABASE_URL=$(_TESTING_TEST_URL) TEST_DATABASE_URL=$(_TESTING_TEST_URL) \
		REDIS_URL=redis://localhost:6379/3 \
		AUTH_SERVICE_URL=http://not-used \
		APP_ENV=test \
		PYTHONPATH=. pytest tests/ -v --tb=short

test-dev-testing-worker: ## Unit-тесты testing_worker (devcontainer; структурная проверка, без реального Redis)
	cd testing_worker && \
		REDIS_URL=redis://localhost:6379/3 \
		APP_ENV=test \
		PYTHONPATH=. pytest tests/ -v --tb=short

test-dev-integration: ## Кросс-сервисные интеграционные тесты (devcontainer, порты :8010/:8011)
	pytest tests/ -v --tb=short

test-dev-all: test-dev-auth test-dev-logging test-dev-server test-dev-worker test-dev-secret test-dev-testing test-dev-testing-worker test-dev-integration ## Все тесты в devcontainer

test-devcont-all: ## Полный набор в devcontainer: unit + integration + E2E docker registry
	python tests/scripts/run_devcont_tests.py

install-registry: ## Установить бинарник registry v2.8.3 (нужно выполнить один раз)
	bash tests/scripts/install_registry.sh

##@ Тесты: алиасы (обратная совместимость)

test-local-auth: test-dev-auth ## = test-dev-auth
test-local-logging: test-dev-logging ## = test-dev-logging
test-local-server: test-dev-server ## = test-dev-server
test-local-worker: test-dev-worker ## = test-dev-worker
test-local-secret: test-dev-secret ## = test-dev-secret
test-local-testing: test-dev-testing ## = test-dev-testing
test-local-testing-worker: test-dev-testing-worker ## = test-dev-testing-worker
test-local: test-dev-auth test-dev-logging test-dev-server test-dev-worker test-dev-secret test-dev-testing test-dev-testing-worker ## = test-dev-auth + test-dev-logging + test-dev-server + test-dev-worker + test-dev-secret + test-dev-testing + test-dev-testing-worker
test-local-integration: test-dev-integration ## = test-dev-integration
test-local-all: test-dev-all ## = test-dev-all

##@ Тесты: Docker-режим (CI / с хоста)

test-auth: ## Тесты auth_service в Docker (без E2E docker-registry — быстрый прогон)
	$(AUTH) run --rm test-runner pytest tests/ -m "not e2e" -v --tb=short

test-auth-e2e: ## Тесты auth_service в Docker включая E2E docker-registry (поднимает auth-service-e2e + registry)
	$(AUTH) --profile e2e run --rm test-runner pytest tests/ -v --tb=short

test-logging: ## Тесты loging_service в Docker
	$(LOGGING) run --rm test-runner pytest tests/ -v --tb=short

test-server: ## Тесты server_service в Docker
	$(SERVER) run --rm test-runner pytest tests/ -v --tb=short

test-worker: ## Тесты server_worker в Docker
	$(WORKER) run --rm test-runner pytest tests/ -v --tb=short

test-secret: ## Тесты secret_service в Docker
	$(SECRET) run --rm test-runner pytest tests/ -v --tb=short

test-testing: ## Тесты testing_service в Docker
	$(TESTING) run --rm test-runner pytest tests/ -v --tb=short

test-testing-worker: ## Тесты testing_worker в Docker
	$(TESTING_WORKER) run --rm test-runner pytest tests/ -v --tb=short

test-console: ## Тесты console_proxy (VNC/SPICE-прокси) в одноразовом python-контейнере
	docker run --rm -v "$(CURDIR)/console_proxy:/app" -w /app python:3.12-slim bash -lc \
		"printf '%s\\n' '[global]' 'index-url = http://allta.devos.astralinux.ru:3141/root/release' 'trusted-host = allta.devos.astralinux.ru' > /etc/pip.conf && \
			 chmod 0644 /etc/pip.conf && \
		 pip install -q poetry==2.3.4 && poetry install --only main,dev --no-root && PYTHONPATH=. poetry run pytest -q"

test-integration: ## Полный E2E-стек (4 сервиса + openssh-server + worker) и pytest tests/integration/
	$(E2E) up -d --wait test-postgres test-redis ssh-target logging-service auth-service server-service
	$(E2E) up --abort-on-container-exit --exit-code-from init-ssh-keys init-ssh-keys
	$(E2E) up --abort-on-container-exit --exit-code-from seeder seeder
	$(E2E) up -d server-worker
	$(E2E) run --rm test-runner

up-test-integration: ## Поднять E2E-стек без запуска тестов (для ручной отладки сценариев)
	$(E2E) up -d --wait test-postgres test-redis ssh-target logging-service auth-service server-service
	$(E2E) up --abort-on-container-exit --exit-code-from init-ssh-keys init-ssh-keys
	$(E2E) up --abort-on-container-exit --exit-code-from seeder seeder
	$(E2E) up -d server-worker
	@echo ""
	@echo "  E2E stack ready. Logs: make logs-test-integration"
	@echo "  Run tests:           $(E2E) run --rm test-runner"
	@echo "  SSH target:          ssh -p 2222 dbosroot@127.0.0.1 (password: test-root-pw)"

logs-test-integration: ## tail логов всех сервисов E2E-стека (Ctrl+C для выхода)
	$(E2E) logs -f auth-service logging-service server-service server-worker ssh-target

down-test-integration: ## Остановить E2E-стек и удалить тома (mgmt-keys, bootstrap)
	$(E2E) down -v --remove-orphans

test-all: test-auth test-logging test-server test-worker test-secret test-testing test-testing-worker test-integration ## Все тесты в Docker

test-build: ## Пересобрать все тестовые образы
	$(AUTH) build
	$(LOGGING) build
	$(SERVER) build
	$(WORKER) build
	$(SECRET) build
	$(TESTING) build
	$(TESTING_WORKER) build
	$(ROOT) build
	$(E2E) build

test-down: ## Удалить тестовые контейнеры и тома
	$(AUTH) down -v --remove-orphans
	$(LOGGING) down -v --remove-orphans
	$(SERVER) down -v --remove-orphans
	$(WORKER) down -v --remove-orphans
	$(SECRET) down -v --remove-orphans
	$(TESTING) down -v --remove-orphans
	$(TESTING_WORKER) down -v --remove-orphans
	$(ROOT) down -v --remove-orphans
	$(E2E) down -v --remove-orphans

scan: ## [ручной] Полное локальное сканирование (тесты + lint + audit). CI нет — это единственный регулярный прогон.
	@scripts/full-scan.sh

##@ k8s: production деплой в k3s на VM
# Полный цикл: k8s-install-k3s → k8s-install-cert-manager → k8s-build →
# k8s-secrets → k8s-deploy → k8s-migrate → k8s-smoke.
# Подробный runbook — obsidian/infra/Production deploy runbook.md и k8s/README.md.

k8s-install-k3s: ## [VM, sudo] Установить k3s на текущий хост (Astra Linux SE). Идемпотентно.
	sudo bash scripts/k8s/install_k3s.sh

k8s-install: k8s-install-k3s ## Алиас k8s-install-k3s (обратная совместимость)

k8s-install-cert-manager: ## [VM, sudo] Установить cert-manager в кластер (один раз). Идемпотентно.
	@if [ -x scripts/k8s/install_cert_manager.sh ]; then \
		sudo -E env KUBECONFIG=/etc/rancher/k3s/k3s.yaml bash scripts/k8s/install_cert_manager.sh; \
	else \
		echo "scripts/k8s/install_cert_manager.sh не найден — см. obsidian/infra/Production deploy runbook.md."; \
		exit 1; \
	fi

k8s-build: ## [VM] Собрать Docker-образы сервисов и импортировать в k3s containerd (ctr)
	bash scripts/k8s/build_and_import.sh $(if $(TAG),$(TAG),)

k8s-secrets: ## Сгенерировать k8s/20-secrets.yaml + 50-ingress.yaml. Спросит домен, если не задан ранее.
	@bash scripts/k8s/gen_secrets.sh $(if $(DOMAIN),$(DOMAIN),)
	@echo ""
	@echo "  Дальше: проверь k8s/20-secrets.yaml и k8s/50-ingress.yaml,"
	@echo "  затем 'make k8s-deploy' применит их через kustomize."

prepare-k3s: ## [k3s] Сгенерировать секреты+ingress (DOMAIN=/сохранённый INGRESS_HOST) и напечатать, что править руками.
	@echo "→ [1/2] secrets + ingress (gen_secrets.sh)..."
	@bash scripts/k8s/gen_secrets.sh $(if $(DOMAIN),$(DOMAIN),)
	@echo "→ [2/2] Готово. Проверь и при необходимости поправь руками:"
	@echo "═══════════════════════════════════════════════════════════════════"
	@echo "  Домен деплоя:  k8s/.env.k8s → INGRESS_HOST=<домен-или-IP>"
	@echo "                 (сменил домен → перезапусти: make prepare-k3s DOMAIN=<new>)"
	@echo "  Секреты:       k8s/20-secrets.yaml (VM_CONSOLE_PROXY_WS_BASE=wss://<host> проставлен авто)"
	@echo "  Ingress:       k8s/50-ingress.yaml — только websecure (443); порт 80 (redirect + CA-http) убран."
	@echo ""
	@echo "  Порт 80 у traefik в k3s открыт на уровне entryPoint. Чтобы закрыть наружу полностью:"
	@echo "    отредактируй HelmChartConfig traefik — убери ports.web (:80) или ограничь loopback."
	@echo "    Файл: /var/lib/rancher/k3s/server/manifests/traefik-config.yaml (см. obsidian/infra/Kubernetes.md)."
	@echo "  CA (до импорта): curl -k https://<INGRESS_HOST>/emm-ca.crt -o emm-ca.crt   (порт 80 закрыт)"
	@echo ""
	@echo "  Затем: make k8s-deploy"
	@echo "═══════════════════════════════════════════════════════════════════"

k8s-deploy: ## kubectl apply -k k8s/ + миграции (Job'ы) + ожидание готовности всех deployments
	bash scripts/k8s/deploy.sh

k8s-migrate: ## Ручной fallback: alembic-миграции внутри pod'ов (штатно их гонит deploy.sh через Job'ы)
	@echo "→ Миграции auth_service..."
	@$(K8S) exec deploy/auth-service -- alembic upgrade head
	@echo "→ Миграции logging_service..."
	@$(K8S) exec deploy/logging-service -- alembic upgrade head
	@echo "→ Миграции server_service..."
	@$(K8S) exec deploy/server-service -- alembic upgrade head
	@echo "→ Миграции server_worker..."
	@$(K8S) exec deploy/server-worker -- alembic upgrade head
	@echo "→ Миграции secret_service..."
	@$(K8S) exec deploy/secret-service -- alembic upgrade head
	@echo "✓ Миграции прогнаны."

k8s-smoke: ## Smoke-тесты после деплоя (health/ready/login для всех сервисов)
	@if [ -x scripts/k8s/smoke_test.sh ]; then \
		bash scripts/k8s/smoke_test.sh; \
	else \
		echo "scripts/k8s/smoke_test.sh не найден — см. obsidian/infra/Production checklist.md."; \
		exit 1; \
	fi

k8s-install-docker: ## [VM, sudo] Установить docker engine + jq + python3, если их нет. Идемпотентно.
	@if command -v docker >/dev/null 2>&1; then \
		echo "✓ docker уже установлен: $$(docker --version)"; \
	else \
		echo "→ Устанавливаем docker через apt..."; \
		sudo apt-get update -qq && sudo apt-get install -y -qq docker.io; \
		sudo systemctl enable --now docker; \
		echo "✓ docker установлен: $$(docker --version)"; \
	fi
	@sudo usermod -aG docker "$$(id -un)" 2>/dev/null || true
	@# jq нужен для kubectl-patch'ей в rotation-скриптах; python3 — для регенерации
	@# Ingress в IP-режиме (gen_secrets.sh) и инлайн-скриптов в smoke. Оба обычно
	@# уже стоят на Astra SE, но проверяем — без них k8s-zero/ротация падают.
	@for pkg in jq python3; do \
		if ! command -v $$pkg >/dev/null 2>&1; then \
			echo "→ Устанавливаем $$pkg..."; \
			sudo apt-get install -y -qq $$pkg; \
		fi; \
	done

k8s-install-cert-manager-images: ## [VM, sudo] Спулить cert-manager образы и импортировать в k3s containerd. CM_VERSION=v1.15.3.
	@CM_VERSION="$${CM_VERSION:-v1.15.3}"; \
	for img in controller webhook cainjector startupapicheck; do \
		ref="quay.io/jetstack/cert-manager-$$img:$$CM_VERSION"; \
		if sudo k3s ctr images ls -q | grep -qx "$$ref"; then \
			echo "  • $$ref уже в containerd"; \
		else \
			echo "  • docker pull $$ref"; \
			sudo docker pull "$$ref" && sudo docker save "$$ref" | sudo k3s ctr images import -; \
		fi; \
	done

k8s-zero: ## [VM, sudo] Полный deploy с нуля. Без аргументов: IP хоста авто + cert SAN под IP и DBOS_DNS ($(DBOS_DNS)). Override: DOMAIN=<host>, DBOS_DNS=<dns>, DBOS_DNS= (отключить DNS), CM_VERSION=v1.15.3.
	@echo "═══════════════════════════════════════════════════════════════════"
	@echo "  DBOS zero-deploy на $$(hostname)"
	@echo "═══════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "▶ pre-check: свободное место на /"
	@df -BG / | awk 'NR==2 {gsub("G","",$$4); if($$4<10) {print "ОШИБКА: <10G свободно на /, для билда 5 образов нужно ~5G"; exit 1} else {print "  • " $$4 "G свободно — ок"}}'
	@echo ""
	@echo "▶ [1/9] docker engine"
	@$(MAKE) k8s-install-docker --no-print-directory
	@echo ""
	@echo "▶ [2/9] k3s"
	@$(MAKE) k8s-install-k3s --no-print-directory
	@echo ""
	@echo "▶ [3/9] Сборка и импорт 5 образов сервисов"
	@$(MAKE) k8s-build --no-print-directory
	@echo ""
	@echo "▶ [4/9] cert-manager образы (pull + import)"
	@$(MAKE) k8s-install-cert-manager-images --no-print-directory
	@echo ""
	@echo "▶ [5/9] cert-manager apply + CA + leaf-сертификаты"
	@$(MAKE) k8s-install-cert-manager --no-print-directory
	@echo ""
	@echo "▶ [6/9] secrets + ingress (host=$(if $(DOMAIN),$(DOMAIN),<auto-IP>)$(if $(DBOS_DNS), + SAN DNS:$(DBOS_DNS),))"
	@if [ -f k8s/20-secrets.yaml ] && [ -f k8s/50-ingress.yaml ]; then \
		echo "  • k8s/20-secrets.yaml и k8s/50-ingress.yaml уже сгенерированы — пропускаем"; \
	else \
		$(if $(DBOS_DNS),SAN_EXTRA="DNS:$(DBOS_DNS)" ,)bash scripts/k8s/gen_secrets.sh $(if $(DOMAIN),$(DOMAIN),) </dev/null; \
	fi
	@echo ""
	@echo "▶ [7/9] kubectl apply -k k8s/ + миграции (Job'ы) + ожидание rollout"
	@$(MAKE) k8s-deploy --no-print-directory
	@echo ""
	@echo "▶ [8/9] dump openapi + restart swagger-ui"
	@sudo -E env KUBECONFIG=/etc/rancher/k3s/k3s.yaml bash scripts/k8s/dump_openapi.sh
	@echo ""
	@echo "▶ [9/9] smoke"
	@$(MAKE) k8s-smoke --no-print-directory
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════"
	@echo "  ✓ zero-deploy завершён"
	@echo "═══════════════════════════════════════════════════════════════════"
	@$(K8S) get pods

k8s-rollout: ## Пересобрать образы и сделать rolling update. SVC=auth|logging|server|worker|secret|web|all (default: all)
	bash scripts/k8s/rollout.sh $(SVC)

k8s-update: ## [k3s] Обновить стек из git: git pull → пересборка+импорт образов → rolling update → миграции. Нужен git-checkout на хосте.
	@echo "→ [1/3] git pull (--ff-only)..."
	@git pull --ff-only
	@echo "→ [2/3] Пересборка образов + rolling update (SVC=$(if $(SVC),$(SVC),all))..."
	@bash scripts/k8s/rollout.sh $(SVC)
	@echo "→ [3/3] Миграции (alembic upgrade head в pod'ах)..."
	@$(MAKE) k8s-migrate
	@echo "✓ k8s-update завершён."

k8s-rollback: ## Откатить deployment. SVC=auth|logging, REV=<revision> (по умолчанию предыдущая)
	bash scripts/k8s/rollback.sh $(SVC) $(REV)

k8s-logs: ## Логи pods. SERVICE=auth|loging|server|worker (default: auth). Также принимает SVC.
	@svc="$(if $(SERVICE),$(SERVICE),$(if $(SVC),$(SVC),auth))"; \
	case "$$svc" in \
		auth)    label="auth-service" ;; \
		loging|logging) label="logging-service" ;; \
		server)  label="server-service" ;; \
		worker)  label="server-worker" ;; \
		*)       label="$${svc}-service" ;; \
	esac; \
	$(K8S) logs -f -l app=$$label --tail=100

k8s-status: ## Статус всех ресурсов в namespace dbos
	$(K8S) get all -o wide
	@echo ""
	$(K8S) get pvc,configmap,secret,ingress -o wide

k8s-backup: ## pg_dump обеих БД через kubectl exec. DEST=path (default: ./k8s/backups)
	bash scripts/k8s/backup_pg.sh $(if $(DEST),$(DEST),./k8s/backups)

k8s-destroy: ## ОПАСНО: удалить весь namespace dbos (включая PVC и данные)
	@echo "Это удалит ВСЕ данные. Подтвердить: 'y' для продолжения"
	@read confirm && [ "$$confirm" = "y" ] && $(K8S) delete namespace dbos

##@ Ротация секретов
# Все скрипты в scripts/k8s/rotate_*.sh — интерактивные, поддерживают --status / --finalize
# где применимо. Подробный runbook — obsidian/infra/runbooks/Rotation.md.

k8s-rotate-server-master: ## Ротация SERVER_ENCRYPTION_KEY (server_service). FLAG=--status|--finalize|--auto-finalize|пусто.
	@if [ ! -f scripts/k8s/rotate_master_key.sh ]; then \
		echo "ОШИБКА: scripts/k8s/rotate_master_key.sh не найден."; exit 1; \
	fi
	bash scripts/k8s/rotate_master_key.sh $(if $(FLAG),$(FLAG),)

k8s-rotate-secret-master: ## Ротация SECRET_ENCRYPTION_KEY (secret_service). FLAG=--status|--finalize|--auto-finalize|пусто.
	@if [ ! -f scripts/k8s/rotate_secret_master_key.sh ]; then \
		echo "ОШИБКА: scripts/k8s/rotate_secret_master_key.sh ещё не создан."; exit 1; \
	fi
	bash scripts/k8s/rotate_secret_master_key.sh $(if $(FLAG),$(FLAG),)

k8s-rotate-redis-stash-master: ## Ротация master-ключа redis-stash (provision creds). FLAG=--status|--finalize|--auto-finalize|пусто.
	@if [ ! -f scripts/k8s/rotate_redis_stash_master_key.sh ]; then \
		echo "ОШИБКА: scripts/k8s/rotate_redis_stash_master_key.sh ещё не создан."; exit 1; \
	fi
	bash scripts/k8s/rotate_redis_stash_master_key.sh $(if $(FLAG),$(FLAG),)

k8s-rotate-db-passwords: ## Ротация паролей PostgreSQL для одного или всех сервисов. SERVICE=auth|logging|server|worker|secret (default: all).
	@if [ ! -f scripts/k8s/rotate_db_passwords.sh ]; then \
		echo "ОШИБКА: scripts/k8s/rotate_db_passwords.sh ещё не создан."; exit 1; \
	fi
	bash scripts/k8s/rotate_db_passwords.sh $(if $(SERVICE),--service $(SERVICE),)

k8s-rotate-redis-password: ## Ротация пароля Redis (broker + rate-limit storage).
	@if [ ! -f scripts/k8s/rotate_redis_password.sh ]; then \
		echo "ОШИБКА: scripts/k8s/rotate_redis_password.sh ещё не создан."; exit 1; \
	fi
	bash scripts/k8s/rotate_redis_password.sh

k8s-rotate-s2s-keys: ## Ротация service-to-service API-ключей (SERVICE_API_KEYS, INTROSPECT_SERVICE_API_KEY).
	@if [ ! -f scripts/k8s/rotate_s2s_keys.sh ]; then \
		echo "ОШИБКА: scripts/k8s/rotate_s2s_keys.sh ещё не создан."; exit 1; \
	fi
	bash scripts/k8s/rotate_s2s_keys.sh

##@ Автоматическая ротация (CronJob)
# CronJob rotation-scheduler гоняет scripts/k8s/test_rotation_safety.sh
# 1 января и 1 июля в 03:00 UTC. См. k8s/121-rotation-cronjob.yaml и
# runbook obsidian/infra/runbooks/Rotation.md §«Автоматическая ротация».

k8s-rotation-image: ## [VM] Собрать dbos/tools:latest (kubectl+bash+jq+openssl) и импортировать в k3s
	@TAG="$${TAG:-latest}"; \
	echo "→ Сборка dbos/tools:$$TAG из scripts/k8s/dbos-tools/Dockerfile..."; \
	DOCKER="docker"; \
	docker info >/dev/null 2>&1 || DOCKER="sudo docker"; \
	$$DOCKER build -t "dbos/tools:$$TAG" scripts/k8s/dbos-tools/; \
	echo "→ Экспортируем в k3s containerd..."; \
	TMP=$$(mktemp -d); \
	trap "rm -rf $$TMP" EXIT; \
	$$DOCKER save "dbos/tools:$$TAG" -o "$$TMP/tools.tar"; \
	sudo chmod 644 "$$TMP"/*.tar 2>/dev/null || true; \
	K3S_BIN="$$(command -v k3s || echo /usr/local/bin/k3s)"; \
	sudo "$$K3S_BIN" ctr images import "$$TMP/tools.tar"; \
	echo "✓ dbos/tools:$$TAG готов и доступен k3s."

k8s-rotation-trigger: ## Запустить rotation-scheduler вручную (создаст Job из CronJob). Параметр SUFFIX=<метка>.
	@SUFFIX="$${SUFFIX:-manual-$$(date +%s)}"; \
	echo "→ Создаём Job rotation-scheduler-$$SUFFIX из CronJob rotation-scheduler..."; \
	$(K8S) create job --from=cronjob/rotation-scheduler "rotation-scheduler-$$SUFFIX"; \
	echo ""; \
	echo "  Логи:    $(K8S) logs -f job/rotation-scheduler-$$SUFFIX"; \
	echo "  Статус:  make k8s-rotation-status"

k8s-rotation-status: ## Статус CronJob'а и последних Job'ов ротации
	@echo "→ CronJob rotation-scheduler:"
	@$(K8S) get cronjob rotation-scheduler
	@echo ""
	@echo "→ Последние Job'ы ротации (история):"
	@$(K8S) get jobs -l app=rotation-scheduler --sort-by=.metadata.creationTimestamp
	@echo ""
	@echo "→ Pod'ы текущего ротирующего Job'а (если идёт):"
	@$(K8S) get pods -l app=rotation-scheduler --field-selector=status.phase!=Succeeded 2>/dev/null || true

k8s-rotation-logs: ## Логи последнего ротирующего Job'а (tail=200)
	@$(K8S) logs -l app=rotation-scheduler --tail=200 --all-containers=true 2>/dev/null \
		|| echo "Job'ов с label app=rotation-scheduler не найдено."

k8s-rotation-disable: ## Приостановить автоматическую ротацию (suspend=true). Ручной запуск k8s-rotation-trigger остаётся доступен.
	$(K8S) patch cronjob rotation-scheduler -p '{"spec":{"suspend":true}}'
	@echo "✓ CronJob rotation-scheduler приостановлен."

k8s-rotation-enable: ## Возобновить автоматическую ротацию (suspend=false).
	$(K8S) patch cronjob rotation-scheduler -p '{"spec":{"suspend":false}}'
	@echo "✓ CronJob rotation-scheduler возобновлён."

##@ Backup / TLS / CronJob управление

k8s-backup-secret-full: ## Полный snapshot Secret'а dbos-secrets (master-keys + DB + s2s + Redis). DEST=path (default: ./k8s/backups)
	@if [ ! -f scripts/k8s/backup_secret_full.sh ]; then \
		echo "ОШИБКА: scripts/k8s/backup_secret_full.sh не найден."; exit 1; \
	fi
	bash scripts/k8s/backup_secret_full.sh $(if $(DEST),$(DEST),./k8s/backups)

k8s-tls-renew: ## Перевыпустить self-signed TLS-cert (365 дней) без regen остальных секретов. Использует .env.k8s или DOMAIN=<host>.
	bash scripts/k8s/gen_secrets.sh --only-tls $(if $(DOMAIN),$(DOMAIN),)

k8s-cronjob-list: ## Список всех CronJob'ов в namespace dbos
	$(K8S) get cronjobs

k8s-cronjob-history: ## История запусков всех CronJob'ов (Job'ы по metadata.creationTimestamp)
	$(K8S) get jobs --sort-by=.metadata.creationTimestamp

k8s-cronjob-suspend: ## Приостановить CronJob. SVC=<name> (например: SVC=rotation-scheduler).
	@if [ -z "$(SVC)" ]; then \
		echo "ОШИБКА: SVC=<cronjob-name> обязателен."; \
		echo "  Список:  make k8s-cronjob-list"; exit 1; \
	fi
	$(K8S) patch cronjob $(SVC) -p '{"spec":{"suspend":true}}'
	@echo "✓ CronJob $(SVC) приостановлен."

k8s-cronjob-resume: ## Возобновить CronJob. SVC=<name>.
	@if [ -z "$(SVC)" ]; then \
		echo "ОШИБКА: SVC=<cronjob-name> обязателен."; \
		echo "  Список:  make k8s-cronjob-list"; exit 1; \
	fi
	$(K8S) patch cronjob $(SVC) -p '{"spec":{"suspend":false}}'
	@echo "✓ CronJob $(SVC) возобновлён."

prod-deploy: ## Meta: build → check secrets → deploy (вкл. миграции) → openapi → smoke. Требует k3s и cert-manager уже установленными.
	@echo "→ [1/5] Сборка образов и импорт в k3s..."
	@$(MAKE) k8s-build --no-print-directory
	@echo ""
	@echo "→ [2/5] Проверка секретов..."
	@if [ ! -f k8s/20-secrets.yaml ]; then \
		echo "ОШИБКА: k8s/20-secrets.yaml отсутствует. Запусти 'make k8s-secrets' и заполни значения."; \
		exit 1; \
	fi
	@if [ ! -f k8s/50-ingress.yaml ]; then \
		echo "ОШИБКА: k8s/50-ingress.yaml отсутствует. Запусти 'make k8s-secrets'."; \
		exit 1; \
	fi
	@echo "  k8s/20-secrets.yaml и k8s/50-ingress.yaml на месте."
	@echo ""
	@echo "→ [3/5] Применение манифестов + миграции (Job'ы) + rollout..."
	@$(MAKE) k8s-deploy --no-print-directory
	@echo ""
	@echo "→ [4/5] OpenAPI dump + restart swagger-ui (иначе swagger-ui в CrashLoop)..."
	@sudo -E env KUBECONFIG=/etc/rancher/k3s/k3s.yaml bash scripts/k8s/dump_openapi.sh
	@echo ""
	@echo "→ [5/5] Smoke-тесты..."
	@$(MAKE) k8s-smoke --no-print-directory
	@echo ""
	@echo "✓ prod-deploy завершён."

deploy: prod-deploy ## Алиас prod-deploy: одна команда поднимает всё (build → secrets-check → deploy+keystore+migrate → openapi → smoke).

##@ Prod-стек в docker-compose (без k8s)

prepare-docker: ## [docker] Подготовить .env.prod + TLS-сертификаты и напечатать, что править руками. PROD_HOST=/PROD_IP= — домен/IP.
	@echo "→ [1/3] .env.prod (секреты, идемпотентно — существующий не трогаю)..."
	@DBOS_PUBLIC_HOST="$(PROD_HOST)" PROD_HOST="$(PROD_HOST)" PROD_IP="$(PROD_IP)" bash scripts/compose/gen_prod_env.sh
	@# Дозаливаем URL-ключи в уже существующий .env.prod, если их там ещё нет.
	@grep -q '^DBOS_PUBLIC_HOST=' $(PROD_ENV) 2>/dev/null || printf 'DBOS_PUBLIC_HOST=%s\n' "$(if $(PROD_HOST),$(PROD_HOST),$(PROD_IP))" >> $(PROD_ENV)
	@grep -q '^DBOS_HTTPS_PORT=' $(PROD_ENV) 2>/dev/null || printf 'DBOS_HTTPS_PORT=443\n' >> $(PROD_ENV)
	@echo "→ [2/3] TLS-сертификаты под публичный хост из .env.prod..."
	@HOST=$$(grep -E '^DBOS_PUBLIC_HOST=' $(PROD_ENV) | cut -d= -f2-); \
	 PROD_HOST="$$HOST" PROD_IP="$(PROD_IP)" bash scripts/compose/gen_certs.sh
	@echo "→ [3/3] Готово. Проверь и при необходимости поправь руками:"
	@echo "═══════════════════════════════════════════════════════════════════"
	@echo "  Файл $(PROD_ENV):"
	@echo "    DBOS_PUBLIC_HOST=<домен-или-IP>   # адрес снаружи; идёт в SAN TLS-сертификата"
	@echo "    DBOS_HTTPS_PORT=443               # внешний порт reverse-proxy (внутри всегда 443)"
	@echo "    INITIAL_ADMIN_PASSWORD=<...>      # пароль первого админа (сменить при входе)"
	@echo "  Сменил DBOS_PUBLIC_HOST → перевыпусти cert: make prepare-docker PROD_HOST=<host>"
	@echo "  Поднять стек:  make prod-up"
	@echo "  Доступ:        https://<DBOS_PUBLIC_HOST>:<DBOS_HTTPS_PORT>/   (порт 80 наружу закрыт)"
	@echo "  CA (до импорта): curl -k https://<host>:<port>/emm-ca.crt -o emm-ca.crt"
	@echo "═══════════════════════════════════════════════════════════════════"

prod-up: ## Поднять prod-подобный стек: gen секретов+сертов → build → up → миграции → показать доступ. PROD_HOST=/PROD_IP= для SAN сертификата.
	@echo "→ [1/5] Секреты (.env.prod)..."
	@bash scripts/compose/gen_prod_env.sh
	@echo ""
	@echo "→ [2/5] TLS-сертификаты (внутренний CA + leaf)..."
	@PROD_HOST="$(PROD_HOST)" PROD_IP="$(PROD_IP)" bash scripts/compose/gen_certs.sh
	@echo ""
	@echo "→ [3/5] Сборка образов..."
	@$(PROD) build
	@echo ""
	@echo "→ [4/5] Подъём стека (миграции — one-shot, ждём готовности сервисов)..."
	@$(PROD) up -d --wait
	@echo ""
	@echo "→ [5/5] Готово."
	@echo "═══════════════════════════════════════════════════════════════════"
	@echo "  DBOS Server Manager — prod-стек поднят"
	@echo "═══════════════════════════════════════════════════════════════════"
	@printf "  URL:      https://%s/\n" "$(if $(PROD_HOST),$(PROD_HOST),$(if $(PROD_IP),$(PROD_IP),localhost))"
	@printf "  Логин:    %s\n" "$$(grep -E '^INITIAL_ADMIN_USERNAME=' $(PROD_ENV) | cut -d= -f2-)"
	@printf "  Пароль:   %s   (СМЕНИТЬ при первом входе)\n" "$$(grep -E '^INITIAL_ADMIN_PASSWORD=' $(PROD_ENV) | cut -d= -f2-)"
	@echo ""
	@echo "  TLS — самоподписанный внутренний CA. Импортируй в доверенные корневые:"
	@printf "    curl -k https://%s/emm-ca.crt -o emm-ca.crt   (порт 80 закрыт, CA только по HTTPS)\n" "$(if $(PROD_HOST),$(PROD_HOST),$(if $(PROD_IP),$(PROD_IP),localhost))"
	@echo "  Пока CA не импортирован — обращайся с curl -k / verify=False."
	@echo "═══════════════════════════════════════════════════════════════════"

update: ## [docker] Обновить стек из git: git pull → пересборка → рестарт (миграции — one-shot на up). Нужен git-checkout на хосте.
	@echo "→ [1/3] git pull (--ff-only)..."
	@git pull --ff-only
	@echo "→ [2/3] Пересборка образов..."
	@$(PROD) build
	@echo "→ [3/3] Рестарт стека + миграции (up -d --wait)..."
	@$(PROD) up -d --wait
	@echo "✓ update завершён."

prod-build: ## Пересобрать образы prod-стека (без подъёма)
	@$(PROD) build

prod-down: ## Остановить prod-стек (данные в volume'ах сохраняются)
	@$(PROD) down

prod-down-v: ## Остановить prod-стек И удалить volume'ы (БД, keystore, redis — полная очистка)
	@$(PROD) down -v

prod-logs: ## tail -f логов всех сервисов prod-стека (Ctrl+C для выхода)
	@$(PROD) logs -f

prod-ps: ## Статус контейнеров prod-стека
	@$(PROD) ps

##@ Справка

help: ## Показать список всех команд (сгруппировано)
	@awk 'BEGIN { \
	  FS = ":.*?##"; \
	  printf "\n  \033[1mDBOS Server Manager\033[0m — доступные команды:\n"; \
	} \
	/^##@/ { printf "\n  \033[1;33m%s\033[0m\n", substr($$0, 5); next } \
	/^[a-zA-Z0-9_-]+:.*?##/ { printf "    \033[36m%-26s\033[0m %s\n", $$1, $$2 } \
	' $(MAKEFILE_LIST)
	@echo ""
