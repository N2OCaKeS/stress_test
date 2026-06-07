# secret_service · статус

Снимок состояния сервиса. Backend близок к production-ready, инфраструктура (k8s + dev-стек) и часть документации в работе.

## Общий статус

| Зона | Состояние | Примечание |
|---|---|---|
| Каркас и lifespan | готов | FastAPI + middleware + lifespan + custom OpenAPI с BearerAuth. |
| Модели + миграции | готовы | `Credential` / `RoleACL` / `DeptGrant` + CHECK-инварианты + UNIQUE. Alembic head. |
| Шифрование | готов | AES-256-GCM + HKDF-SHA256, AAD `cred:{cred_id}`, envelope `v<ver>$<nonce>$<ct>`, поддержка key-versioning. |
| Auth + audit | готов | introspect-клиент, lockout-сервис, redaction, audit-context (request_id / ip / ua), audit-emit fail-loud. |
| Access policy + repos | готов | scope-зависимые проверки (`personal` / `department` / `cross_department`), reader/operator/service_admin/account_admin, repos для credentials / role_acls / dept_grants. |
| HTTP endpoints | готов | CRUD credentials + reveal/transfer/recover, RoleACL CRUD, DeptGrant CRUD, internal lifecycle. Reveal-throttle 5-мин window. |
| Lifecycle + sweep | готов | `handle_user_deleted` / `handle_dept_deleted_as_owner/recipient` / `handle_dept_service_access_revoked`, фоновый sweep блокированных кред за `BLOCKED_RETENTION_DAYS`. |
| Integration-тесты | в работе | Реальный PostgreSQL + mocked introspect + cross-service smoke. |
| k8s манифесты | в работе | Deployment×2 + ClusterIP `:8003` + PDB + отдельный PostgreSQL-кластер + Secret для `SECRET_ENCRYPTION_KEY`. |
| Docs + Makefile + dev-стек seed | в работе | Регистрация сервиса в корневом `Makefile` (`make test-secret` / `run-secret` / `sh-secret` / `logs-secret`), включение в `docker-compose.dev.yml`, seed платформенного сервиса и системных ролей в `scripts/seed_dev.py`. |

## Закрытые фазы

- **Phase 1 — skeleton.** FastAPI app с lifespan, X-Request-ID middleware, SecurityHeaders, Settings со всеми production-валидаторами (HKDF salt, HTTPS, redis auth, introspect TLS), async-engine + sessionmaker, Alembic-каркас, `/api/secret/v1/health` и `/api/secret/v1/ready` (БД-пинг через `SELECT 1`), ErrorResponse / OkResponse envelope-схемы, Docker (multi-stage Dockerfile + dev compose).
- **Phase 2 — models + migration.** ORM-классы `Credential` / `RoleACL` / `DeptGrant` с CHECK-инвариантами и UNIQUE-индексами; Alembic-миграция; репо-слой для CRUD.
- **Phase 3 — encryption.** `services/secrets_service.py`: AES-256-GCM + HKDF-SHA256, AAD `cred:{cred_id}`, envelope `v<ver>$<nonce>$<ct>`, env-load `SECRET_ENCRYPTION_KEY` + `SECRET_ENCRYPTION_KEY__v<N>` для legacy-версий, в production требуется `SECRET_ENCRYPTION_KEY_VERSION >= 2`.
- **Phase 4 — auth + audit.** introspect-клиент в auth_service (pooled httpx, без in-memory кэша), `secret_service in allowed_services` проверка, `subject_type` (user/bot/oauth_client) пробрасывается в audit-context, audit-emit в loging_service с redaction и fail-loud-обёрткой, lockout-сервис per-actor на denied-failures, HTTPS-guard в prod, audit middleware для http.client_error/server_error/unauthorized.
- **Phase 5 — access policy + repos.** Полный набор scope-зависимых проверок (`personal` / `department` / `cross_department`), reader/operator vs service_admin vs account_admin, базовая последовательность `dep_service_access → status → scope check`. Репозитории credentials / role_acls / dept_grants с курсорной пагинацией.
- **Phase 6 — endpoints + reveal-throttle.** Endpoints `GET/POST/GET-one/PATCH/DELETE /credentials` + UNIQUE collision → `409 NAME_DUPLICATE`, `POST /credentials/{id}/reveal` с 5-минутным окном per `(actor_id, cred_id)` (первый reveal — CRITICAL, повторные — INFO с `count`), `POST /credentials/{id}/transfer`, `POST /credentials/{id}/recover`, RoleACL CRUD под `/credentials/{id}/acl` + DeptGrant CRUD под `/credentials/{id}/dept-grants`, cross-dep правило `DEPT_GRANT_REQUIRED`.
- **Phase 7 — lifecycle + sweep.** Внутренние lifecycle-эндпоинты под `/api/secret/v1/internal/lifecycle/{user-deleted,dept-deleted,dept-service-access-revoked}` (`include_in_schema=False`, shared bearer); транзитивные блокировки и cascade-revoke DeptGrant + RoleACL; transfer ownership для service_admin/account_admin; recover window 30 дней; фоновый sweep блокированных кред за `BLOCKED_RETENTION_DAYS` (default 30) с интервалом `SWEEP_INTERVAL_SECONDS` (default 3600).

## Фазы в работе

- **Phase 8 — integration tests.** Реальный PostgreSQL через `tests/docker-compose.test.yml`, фабрики токенов/identity (mocked introspect), фабрики кред/ACL, end-to-end сценарии CRUD + reveal-throttle + lifecycle + cross-dep. Цель — паритет по стилю с `auth_service/server_service`.
- **Phase 9 — k8s.** Манифесты `k8s/70-secret-service.yaml` (Deployment×2 + ClusterIP `:8003` + PDB), `k8s/12-postgres-secret.yaml` (отдельный кластер), ConfigMap + Secret для `SECRET_ENCRYPTION_KEY` / `HKDF_SALT_HEX`, ingress-маршрут.
- **Phase 10 — docs + Makefile + dev-стек + seed.** API_ENDPOINTS / AUDIT_EVENTS / Test каталоги, регистрация сервиса в корневом `Makefile` (`make test-secret` / `run-secret` / `sh-secret` / `logs-secret`), включение в `docker-compose.dev.yml` (`secret-postgres` + `secret_service` :8003), seed платформенного сервиса `secret_service` и системных ролей (`guest`/`reader`/`operator`/`admin`) в `scripts/seed_dev.py`.

## Тесты

Unit (`tests/unit/`): около **269** тестов:

| Модуль | Файл | Кол-во |
|---|---|---|
| Models | `test_models_credential.py` / `test_models_role_acl.py` / `test_models_dept_grant.py` | ~33 |
| Encryption | `test_secrets_service.py` | ~35 |
| Auth + audit | `test_auth_identity.py` / `test_audit_service.py` / `test_audit_events.py` / `test_middleware_audit.py` / `test_middleware_https_guard.py` / `test_lockout_service.py` / `test_reveal_throttle.py` | ~50 |
| Access + repos | `test_access_service.py` / `test_repositories_credentials.py` / `test_repositories_role_acls.py` / `test_repositories_dept_grants.py` | ~66 |
| Endpoints | `test_credentials_endpoints.py` / `test_role_acl_endpoints.py` / `test_dept_grant_endpoints.py` / `test_internal_endpoints.py` | ~57 |
| Lifecycle + sweep | `test_lifecycle_handle_user_deleted.py` / `test_lifecycle_handle_dept_deleted.py` / `test_lifecycle_handle_dept_revoke.py` / `test_sweep_expired_blocked.py` | ~23 |
| Smoke | `test_health.py` | ~5 |

Integration (`tests/integration/`): добавится в Phase 8.

Запуск: `make test-secret` (Docker, после регистрации в корневом Makefile) или `make test-dev-secret` (внутри devcontainer).
