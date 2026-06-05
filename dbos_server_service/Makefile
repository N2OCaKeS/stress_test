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
ROOT    = docker compose -f tests/docker-compose.test.yml
E2E     = docker compose -f tests/integration/docker-compose.test.yml
DEV     = docker compose -f docker-compose.dev.yml
K8S     = kubectl -n dbos

# ── Dev-параметры (devcontainer postgres) ─────────────────────────────────────
_DEV_PG         = postgresql+psycopg://app_user:app_password@postgres:5432
_DEV_AUTH_DB    = $(_DEV_PG)/dev_auth
_DEV_LOG_DB     = $(_DEV_PG)/dev_logging
_DEV_SERVER_DB  = $(_DEV_PG)/dev_server
_DEV_AUTH_AUDIT_KEY   = audit_auth_service_dev_key
_DEV_SERVER_AUDIT_KEY = audit_server_service_dev_key
_DEV_WORKER_AUDIT_KEY = audit_server_worker_dev_key
_DEV_CONFIG_AUDIT_KEY = audit_config_service_dev_key
_DEV_INTROSPECT_KEY   = dev-introspect-api-key
# legacy single-key alias оставлен для обратной совместимости с целями, которые
# не дифференцируют caller'а (например, generic test-инструменты).
_DEV_LOG_KEY    = $(_DEV_AUTH_AUDIT_KEY)
_DEV_SECRET     = dev-secret-key-for-local-must-be-32-chars!
_DEV_SERVER_KEY = dev-server-encryption-key-do-not-use-in-prod
_DEV_ADMIN_USER = admin
_DEV_ADMIN_PASS = 1234
_DEV_ADMIN_EMAIL = admin@example.com

.PHONY: help \
        up up-build seed down down-v logs ps sh-auth sh-logging sh-server sh-worker \
        dev dev-stop dev-logs \
        run-auth run-logging run-server \
        test-dev test-dev-auth test-dev-logging test-dev-server test-dev-worker test-dev-integration test-dev-all \
        test-local test-local-auth test-local-logging test-local-server test-local-worker test-local-integration test-local-all \
        test-devcont-all install-registry \
        test-integration logs-test-integration up-test-integration down-test-integration \
        test-auth test-auth-e2e test-logging test-server test-worker test-all test-down test-build \
        k8s-install k8s-install-k3s k8s-install-cert-manager k8s-secrets k8s-build \
        k8s-deploy k8s-migrate k8s-smoke k8s-rollout k8s-rollback \
        k8s-logs k8s-status k8s-backup k8s-destroy \
        prod-deploy

##@ Dev: стек в Docker (без devcontainer)

up: ## Поднять dev-стек (postgres + redis + auth :8000 + logging :8001 + server :8002 + worker). Данные сохраняются.
	@$(DEV) up -d --wait postgres redis loging_service auth_service server_service server_worker
	@echo ""
	@echo "  auth_service    →  http://localhost:8000/docs    (admin / 1234)"
	@echo "  loging_service  →  http://localhost:8001/docs    (loging_admin / 1234 после make seed)"
	@echo "  server_service  →  http://localhost:8002/docs"
	@echo "  server_worker   →  taskiq worker (no HTTP), redis://localhost:6379/0"
	@echo ""
	@echo "  Первый запуск: сделай 'make seed' чтобы наполнить БД dev-данными."

up-build: ## Пересобрать образы и поднять dev-стек
	$(DEV) up -d --wait --build postgres redis loging_service auth_service server_service server_worker

seed: ## Чистая dev-БД, миграции всех сервисов, наполнение dev-данными (auth + logging + server: серверы/аккаунты/IPMI) через seed_dev.py и остановка контейнеров (volume сохраняется)
	$(DEV) down -v
	$(DEV) up -d --wait postgres redis loging_service auth_service server_service server_worker
	@echo "→ Миграции loging_service..."
	$(DEV) exec -T loging_service python -m alembic upgrade head
	@echo "→ Миграции auth_service..."
	$(DEV) exec -T auth_service python -m alembic upgrade head
	@echo "→ Миграции server_service..."
	$(DEV) exec -T server_service python -m alembic upgrade head
	@echo "→ Миграции server_worker..."
	$(DEV) exec -T server_worker python -m alembic upgrade head
	@echo "→ Наполнение dev-данными..."
	$(DEV) run --rm seeder
	$(DEV) down

down: ## Остановить dev-стек (данные в томе postgres сохраняются)
	$(DEV) down

down-v: ## Остановить и удалить том postgres (полная очистка БД)
	$(DEV) down -v

logs: ## tail -f логов всех сервисов (Ctrl+C для выхода)
	$(DEV) logs -f loging_service auth_service server_service server_worker

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

##@ Dev: внутри devcontainer

dev: ## Применить миграции и запустить auth + logging + server на общей dev-БД (данные сохраняются)
	@$(MAKE) dev-stop --no-print-directory 2>/dev/null; true
	@mkdir -p $(CURDIR)/.dev
	@echo "→ Пересоздаём dev-базы данных (чистая БД)..."
	@python3 -c "\
import psycopg; \
conn = psycopg.connect(host='postgres', user='app_user', password='app_password', dbname='postgres', autocommit=True); \
[conn.execute('DROP DATABASE IF EXISTS \"' + db + '\"') for db in ('dev_auth', 'dev_logging', 'dev_server')]; \
[conn.execute('CREATE DATABASE \"' + db + '\"') for db in ('dev_auth', 'dev_logging', 'dev_server')]; \
conn.close()"
	@echo "→ Миграции loging_service..."
	@cd loging_service && DATABASE_URL=$(_DEV_LOG_DB) PYTHONPATH=. alembic upgrade head
	@echo "→ Миграции auth_service..."
	@cd auth_service && DATABASE_URL=$(_DEV_AUTH_DB) PYTHONPATH=. alembic upgrade head
	@echo "→ Миграции server_service..."
	@cd server_service && DATABASE_URL=$(_DEV_SERVER_DB) SERVER_ENCRYPTION_KEY=$(_DEV_SERVER_KEY) PYTHONPATH=. alembic upgrade head
	@echo "→ Запуск loging_service :8001..."
	@cd loging_service && \
		DATABASE_URL=$(_DEV_LOG_DB) \
		SERVICE_API_KEYS='{"auth_service":"$(_DEV_AUTH_AUDIT_KEY)","server_service":"$(_DEV_SERVER_AUDIT_KEY)","config_service":"$(_DEV_CONFIG_AUDIT_KEY)","server_worker":"$(_DEV_WORKER_AUDIT_KEY)"}' \
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
	@fuser -k 8000/tcp 2>/dev/null || true
	@fuser -k 8001/tcp 2>/dev/null || true
	@fuser -k 8002/tcp 2>/dev/null || true
	@echo "  сервисы остановлены"

dev-logs: ## Следить за логами всех dev-сервисов (Ctrl+C для выхода)
	@tail -f $(CURDIR)/.dev/auth.log $(CURDIR)/.dev/logging.log $(CURDIR)/.dev/server.log

run-auth: ## Запустить auth_service локально (devcontainer, порт 8000, авто-перезагрузка)
	cd auth_service && PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

run-logging: ## Запустить loging_service локально (devcontainer, порт 8001, авто-перезагрузка)
	cd loging_service && PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8001

run-server: ## Запустить server_service локально (devcontainer, порт 8002, авто-перезагрузка)
	cd server_service && \
		DATABASE_URL=$(_DEV_SERVER_DB) SERVER_ENCRYPTION_KEY=$(_DEV_SERVER_KEY) \
		PYTHONPATH=. uvicorn src.main:app --reload --host 0.0.0.0 --port 8002

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

test-dev-integration: ## Кросс-сервисные интеграционные тесты (devcontainer, порты :8010/:8011)
	pytest tests/ -v --tb=short

test-dev-all: test-dev-auth test-dev-logging test-dev-server test-dev-worker test-dev-integration ## Все тесты в devcontainer

test-devcont-all: ## Полный набор в devcontainer: unit + integration + E2E docker registry
	python tests/scripts/run_devcont_tests.py

install-registry: ## Установить бинарник registry v2.8.3 (нужно выполнить один раз)
	bash tests/scripts/install_registry.sh

##@ Тесты: алиасы (обратная совместимость)

test-local-auth: test-dev-auth ## = test-dev-auth
test-local-logging: test-dev-logging ## = test-dev-logging
test-local-server: test-dev-server ## = test-dev-server
test-local-worker: test-dev-worker ## = test-dev-worker
test-local: test-dev-auth test-dev-logging test-dev-server test-dev-worker ## = test-dev-auth + test-dev-logging + test-dev-server + test-dev-worker
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

test-all: test-auth test-logging test-server test-worker test-integration ## Все тесты в Docker

test-build: ## Пересобрать все тестовые образы
	$(AUTH) build
	$(LOGGING) build
	$(SERVER) build
	$(WORKER) build
	$(ROOT) build
	$(E2E) build

test-down: ## Удалить тестовые контейнеры и тома
	$(AUTH) down -v --remove-orphans
	$(LOGGING) down -v --remove-orphans
	$(SERVER) down -v --remove-orphans
	$(WORKER) down -v --remove-orphans
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

k8s-install-cert-manager: ## [VM] Установить cert-manager в кластер (один раз). Идемпотентно.
	@if [ -x scripts/k8s/install_cert_manager.sh ]; then \
		bash scripts/k8s/install_cert_manager.sh; \
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

k8s-deploy: ## kubectl apply -k k8s/ + ожидание готовности всех deployments
	bash scripts/k8s/deploy.sh

k8s-migrate: ## Прогнать alembic-миграции внутри pod'ов всех сервисов (идемпотентно)
	@echo "→ Миграции auth_service..."
	@$(K8S) exec deploy/auth-service -- alembic upgrade head
	@echo "→ Миграции logging_service..."
	@$(K8S) exec deploy/logging-service -- alembic upgrade head
	@echo "→ Миграции server_service..."
	@$(K8S) exec deploy/server-service -- alembic upgrade head
	@echo "→ Миграции server_worker..."
	@$(K8S) exec deploy/server-worker -- alembic upgrade head
	@echo "✓ Миграции прогнаны."

k8s-smoke: ## Smoke-тесты после деплоя (health/ready/login для всех сервисов)
	@if [ -x scripts/k8s/smoke_test.sh ]; then \
		bash scripts/k8s/smoke_test.sh; \
	else \
		echo "scripts/k8s/smoke_test.sh не найден — см. obsidian/infra/Production checklist.md."; \
		exit 1; \
	fi

k8s-rollout: ## Пересобрать образы и сделать rolling update. SVC=auth|logging|all (default: all)
	bash scripts/k8s/rollout.sh $(SVC)

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

prod-deploy: ## Meta: build → check secrets → deploy → migrate → smoke. Требует k3s и cert-manager уже установленными.
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
	@echo "→ [3/5] Применение манифестов..."
	@$(MAKE) k8s-deploy --no-print-directory
	@echo ""
	@echo "→ [4/5] Миграции..."
	@$(MAKE) k8s-migrate --no-print-directory
	@echo ""
	@echo "→ [5/5] Smoke-тесты..."
	@$(MAKE) k8s-smoke --no-print-directory
	@echo ""
	@echo "✓ prod-deploy завершён."

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
