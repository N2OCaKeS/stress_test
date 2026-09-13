# server_service · реестр тестов

**Всего тестов**: **816 passed** в **45 файлах** под `server_service/tests/`. Тестовый стек: pytest + `httpx.AsyncClient(ASGITransport)` поверх FastAPI-приложения и **реальный PostgreSQL** в Docker (`server_db_test`). Никаких моков БД — каждый тест идёт внутри внешней транзакции + SAVEPOINT, который откатывается на teardown. `_introspect` подменяется monkeypatch'ем (см. `conftest.py`), чтобы Bearer-токены тестов отдавали predetermined identity без сети.

`def test_*` через grep — 750. Pytest с параметризацией (`@pytest.mark.parametrize`, `hypothesis`) разворачивает их до **816 passed**.

## Запуск

```bash
# Из корня dbos_server_service:
make test-server          # все тесты в Docker (с test-postgres сайдкаром)
make test-dev-server      # быстрый прогон внутри devcontainer (использует общий postgres)

# Точечно:
docker compose -f server_service/tests/docker-compose.test.yml run --rm test-runner \
    pytest tests/test_server_accounts_endpoints.py::TestCreateAccount -v
```

## Раскладка по каталогам

| Каталог | Файлы | Тесты | Зона |
|---|---:|---:|---|
| `tests/` (root) | 21 | 482 | HTTP-endpoint'ы: server / server_account / IPMI / inventory / roles / permissions / internal / worker-dispatch / acquire-release / audit / rate-limit / stub / health |
| `tests/unit/` | 22 | 233 | юниты: secrets / introspect / permission matrix / config / schemas / startup / shutdown / extract_client_ip / subject_type |
| `tests/integration/` | 2 | 35 | platform_admin_guard middleware + custom-role cross-dept isolation |
| **итого** | **45** | **750 + параметризация = 816** | |

---

## Root: HTTP-endpoint'ы

### `test_servers_endpoints.py` — `/api/server/v1/servers`

CRUD серверов. Классы: `TestListServers`, `TestCreateServer`, `TestGetServer`, `TestUpdateServer`, `TestDeleteServer`, `TestAuthDeniedPaths`.

- Листинг с пагинацией и фильтром по dept; dept_admin видит свой dept, reader — свой, regular cross-dept → 403.
- Создание: валидация (hostname, IP-форматы, ssh_port, non-negative counts, required fields), 409 на дубликат.
- PATCH: партиальные апдейты, cross-dept запрет, 404, валидация полей.
- DELETE: ownership, soft-delete семантика (если применимо), audit.
- Auth-denied paths: 401 без bearer, 403 без service-роли / без `server_service` в `allowed_services`.

### `test_server_accounts_endpoints.py` — `/api/server/v1/servers/{id}/accounts`

CRUD сервисных учёток на сервере: `TestCreateAccount`, `TestListAccounts`, `TestGetAccount`, `TestUpdateAccount`, `TestDeleteAccount`, `TestRotatePassword`. Шесть endpoints (`POST/GET list/GET one/PATCH/DELETE/POST rotate-password`), плотно покрыты:

- Создание учётки с шифрованием пароля (AES-256-GCM), идемпотентность, 409 на дубликат `(server_id, login)`.
- Листинг отдаёт `login`, `has_sudo` и метаданные **без plaintext-пароля**.
- GET — то же самое, отдельный `view_password` для plaintext (через `view_credentials_get`).
- PATCH — обновление флагов (`has_sudo`), повторное шифрование при смене пароля, audit.
- DELETE — cascade-чистка, audit.
- `rotate-password` — перевыпуск под бот воркера (`worker_bot` имеет `rotate_password`-action), audit `server_account.rotate_password`.
- Permission matrix per-action: `reader` блокируется на mutate-эндпоинтах, `operator` пускают/нет согласно матрице, `worker_bot` — только `view_password` / `rotate_password`.

### `test_ipmi_endpoints.py` — `/api/server/v1/servers/{id}/power/{on|off|reboot}`

Power-операции через IPMI/Redfish. Классы: `TestPowerOn`, `TestPowerOff`, `TestPowerReboot`, `TestDispatchedPayload`, `TestPowerVisibility`, `TestPowerNoIpmiController`, `TestPowerNoIpmiAudit`, `TestPowerIdempotency`, `TestPowerDispatchFailureAudit`.

- Happy path: dispatch таска в worker через taskiq, audit `server.power_on`.
- 404 `NO_IPMI_CONTROLLER` если у сервера нет `ipmi_controllers` row (проверяется **после** DECOMMISSIONED).
- 409 `SERVER_DECOMMISSIONED` — гард впереди IPMI-проверки.
- Идемпотентность: повторный dispatch с тем же idempotency-key не клонирует таску.
- Audit на success, denied и dispatch-failure (failure через monkeypatched worker client).
- Видимость: `reader` не видит power-эндпоинты в OpenAPI (если применимо), `operator` видит и может вызывать.

### `test_ipmi_controllers_crud.py` — `/api/server/v1/servers/{id}/ipmi_controllers`

CRUD IPMI-контроллеров (iDRAC/HPiLO/ipmitool). Классы: `TestCreateController`, `TestGetController`, `TestListControllers`, `TestUpdateController`, `TestDeleteController`, `TestRotateCredentials`, `TestAuditEmission`.

Создание/обновление шифруют password (AES-256-GCM), GET/list **никогда не отдают plaintext**, отдельный `rotate-credentials` пускает только тех, у кого `rotate_credentials`-action в матрице, dept-scope, dept_admin своего отдела, audit на каждую mutate-операцию.

### `test_view_credentials_get.py` — view-credentials read-only endpoints

`TestViewCredentialsMetadata`, `TestPowerStatusCached`, `TestGetBootOrderDispatch`. Чтение метаданных IPMI без plaintext, кешированный power-status, dispatch boot-order через worker.

### `test_internal_endpoints.py` — internal worker→server_service эндпоинты

Internal API для воркера: `TestOpenApiHidden` (internal-пути скрыты из public OpenAPI), `TestIpmiCredentials` (worker fetch'ит credentials), `TestAccountPassword` (fetch для PXE/SSH), `TestRotatePassword`, `TestTargetDeptHeaderScoping`, `TestTargetDeptHeaderStrictMode`, `TestWorkerBotDeptAgnostic`.

`X-Target-Department-Id` — единственный cross-dept гард и enforce'ится безусловно (глобальный worker-бот обслуживает серверы всех отделов, его собственный отдел не участвует): нет заголовка → 403 `TARGET_DEPARTMENT_HEADER_REQUIRED`, не совпал с `server.department_id` → 404 (маска not-found). Совпал — проходит, даже если бот из другого отдела.

### `test_internal_callbacks.py` — worker→server_service callbacks

Воркер пушит результаты обратно: `TestInventoryCallback` (inventory.sync пишет CPU/disks/packages/os), `TestIpmiCredentialsRotatedCallback`, `TestReinstallStatusCallback`, `TestCallbackDeptHeaderStrict`, `TestNewCallbacksHiddenFromOpenAPI`.

### `test_worker_task_dispatch_endpoints.py` — dispatch worker-задач

Классы: `TestPowerStatusDispatch`, `TestInventorySyncDispatch`, `TestReinstallDispatch`, `TestAccountRotateDispatch`, `TestIpmiControllerRotateDispatch`, `TestDispatchAuditOnSuccess`, `TestDispatchAuditOnWorkerFailure`.

Front-controller над taskiq: PATH→kind mapping, валидация payload, rollback transaction при kiq-ошибке (см. `unit/test_worker_client_dispatch.py`), audit на успех и на failure отдельной парой.

### CRUD-словари инвентаря

- `test_disks_crud.py` — пятёрка create/list/get/update/delete для дисков.
- `test_installed_packages_live.py` — live-probe пакетов через worker (без БД-таблицы; раньше `test_installed_packages_crud.py` тестировал CRUD).
- `test_os_versions_crud.py` — каталог версий ОС.

CPU-данные (brand/model/cores/threads/frequency_ghz) хранятся inline в `servers` — отдельной таблицы-каталога нет, тесты на эти поля живут в `test_servers_endpoints.py::TestServerCpuFields`.

Везде проверяется dept-scope, unique-инварианты, валидация полей, audit.

### `test_acquire_release.py` — захват сервера под тест-прогон

`TestAcquireServer` (POST acquire), `TestReleaseServer` (POST release), `TestUpdateOsVersion` (смена OS как часть acquire-flow), `TestBusyAuditEmission`. Sit-on-top концепция: сервер может быть «занят» арендатором; повторный acquire → 409 BUSY, audit.

### `test_audit_emission.py` — what gets audited and what doesn't

`TestServerCrudAudit`, `TestPowerAudit`, `TestInternalSensitiveAudit`, `TestPermissionMatrixAudit`, `TestHealthNotAudited`, `TestStub501NotAudited`.

Каталог `(action, status)` → severity синхронизирован с `AUDIT_EVENTS.md`. Health и stub-501 явно исключены из аудита.

### `test_permissions_endpoints.py` — `/api/server/v1/permissions`

Action-based матрица прав. `TestListAll`, `TestListForEntity`, `TestGrant`, `TestRevoke`, `TestDepartmentAdminFullCycle`. dept_admin может грантить роли на свои объекты в своём dept, account_admin **не** имеет доступа к бизнес-матрице (заблокирован `platform_admin_guard`). Управление каталогом service-ролей живёт только в auth_service — у server_service нет endpoint'а `/roles`.

### `test_worker_bot_least_privilege.py` — least-privilege контракт воркер-бота

`TestWorkerBotAllowedActions` (только 4 разрешённых: `view_password`, `rotate_password`, `view_credentials`, `rotate_credentials`), `TestWorkerBotForbiddenServerCrud`, `TestWorkerBotForbiddenPower`, `TestWorkerBotForbiddenPermissionMatrix`, `TestWorkerBotGrantsLandedInDb`.

Гранты роли сидятся миграцией `43cf9cfef9e1_seed_worker_bot_entity_permissions.py`. Тест проверяет, что миграция реально кладёт 4 row'а в `role_entity_permissions`.

### `test_rate_limit.py` — глобальный rate-limit

`TestWithinLimit` / `TestRateLimitExceeded` / `TestHealthNotRateLimited` / `TestRateLimitNoAuditAmplification` / `TestRateLimitConfig`.

slowapi per-IP, default 500/min, health bypass, 429 не публикует http-audit (защита от amplification).

### `test_health.py` — `/health` и `/ready`

3 теста: `test_health_ok`, `test_ready_pings_database`, `test_unknown_endpoint_returns_404`.

### `test_stub_envelope_and_openapi.py` — заглушки 501 и production-OpenAPI

5 тестов: количество stub-эндпоинтов соответствует реестру; anonymous → 401-envelope; authenticated → 501-envelope с правильным `error_code`; OpenAPI закрыт в production (`APP_ENV=production`), открыт в local/dev.

---

## `unit/` — юниты

БД и FastAPI обычно не поднимаются (исключение — endpoint-юниты для migration и introspect).

### `unit/test_secrets_service.py`, `unit/test_secrets_hypothesis.py`, `unit/test_secrets_migration_endpoints.py`

Шифрование секретов (`secrets_service.py`):

- `TestRoundTrip` — encrypt → decrypt идентичность.
- `TestEncryptInput` / `TestDecryptInputValidation` — формы входа, тип/длина.
- `TestNoPlaintextLeak` — никакой подстроки plaintext в ciphertext.
- `TestKeyVersioning` — формат `v{N}$base64...`, версия 1 vs версия 2 одновременно расшифровываются.
- `TestBase64Helpers`, `TestKDFDispatch` (HKDF-SHA256), `TestKeyMinLengthValidator`.
- `hypothesis`-инварианты на ASCII / full-Unicode / случайные байты: round-trip, nonce unique across encrypts, любой bit-flip ciphertext → InvalidTag.
- Migration endpoints (`POST /secrets/reencrypt-batch`): `TestMigrationStatusAuth/Shape`, `TestReencryptBatchAuth/Contract/AuditStatus` — батч пере-шифровывает old-version secrets под новый ключ, идемпотентность, аудит.

### `unit/test_introspect_no_cache.py`, `unit/test_introspect_client_pool.py`, `unit/test_introspect_pool_config.py`, `unit/test_introspect_service_key.py`

Introspect без кэша ответов (свежий вызов на каждый запрос):

- `test_get_current_identity_introspects_every_request` — N запросов → N introspect'ов (нет кэш-hit'ов).
- `test_revoked_token_rejected_immediately` — токен, ставший `active=False`, отбивается на следующем же запросе (нет 5s-окна).
- `test_platform_admin_guard_introspects_every_request` — middleware тоже зовёт свежий introspect.

Pool: pooled `AsyncClient` для introspect вместо per-call (закрывает slowloris-подобную нагрузку на auth_service), shape-валидация токена до round-trip (отбивает мусор без сетевого запроса), service-API-key header даже при пустом ключе.

### `unit/test_audit_and_auth_client_pool.py`, `unit/test_audit_middleware_pool.py`, `unit/test_audit_shutdown_drain.py`

Pool для audit-emitter (HTTP-клиент в `loging_service`):

- `test_audit_emit_reuses_pooled_client`, `test_audit_send_falls_back_to_per_call_when_pool_uninitialised` (lifespan не успел / упал).
- Lifespan init / shutdown для audit + auth pools.
- Drain audit-tasks на shutdown: ждёт pending фоновых задач до timeout, не блокируется на unrelated, закрывает audit-client после drain.

### `unit/test_entity_actions.py`, `unit/test_permissions.py`

Permission matrix:

- `TestIsValidAction`, `TestConstantsMigrationSync` (action-каталог в коде = в миграциях seed'а), `TestDefaultGrants` (built-in роли seed'ятся правильно), `TestSensitiveActionsClosed` (никакой роли по умолчанию нет на `view_password`).
- `TestAccountAdminNoLongerBypassesMatrix` — platform-admin больше не имеет глобального bypass через матрицу (после разделения admin-плоскостей).
- `TestHasAction`, `TestRequireAction`, `TestEffectiveActions` (объединение direct + role-source грантов), `TestServiceRoleScoping` (роль на сервис ≠ роль на конкретный entity).

### `unit/test_worker_bot_grants.py`, `unit/test_worker_client_dispatch.py`, `unit/test_worker_client_broker_lock.py`

Воркер-сторона из server_service:

- `TestWorkerBotGrants`, `TestWorkerBotForbiddenActions`, `TestWorkerBotIsolation`, `TestWorkerBotMigrationShape` — состав seed'а воркер-бота.
- `TestDispatchTaskHappyPath`, `TestDispatchTaskKiqFailureRollback`, `TestDispatchTaskBrokerStartupFailureRollback`, `TestDispatchTaskUnknownKindRollback`, `TestDispatchTaskRollbackDoesNotBreakIdempotency`, `TestDeleteTaskRowBestEffort` — атомарность: если `kiq()` упал, task-row в БД должен откатиться.
- `TestEnsureBrokerStartedRaceProtection`, `TestEnsureBrokerStartedFailureBehavior`, `TestEnsureBrokerStartedBuildBrokerFailure`, `TestDoubleCheckPattern` — lazy-init taskiq-брокера с lock'ом и double-check.

### `unit/test_extract_client_ip.py`

`TestExtractClientIPAllowList`, `TestTrustedProxyIPsSetting` — доверенные прокси (XFF leftmost-non-trusted, X-Real-IP fallback, CIDR allow-list).

### `unit/test_schemas_server.py`

Pydantic-валидация: `TestServerCreateHostname/IpParsing/SshPort/NonNegativeCounts/RequiredFields`, `TestServerUpdate`.

### `unit/test_subject_type_propagation.py`, `unit/test_platform_role_enum.py`

`subject_type` (`user` / `bot` / `oauth_client` / `service` / `system`) пропагируется из introspect-payload в audit-context. `platform_role` приходит как строка от auth_service, в `models/enums.py` сужается до enum'а, unknown → reject, None → allowed.

### `unit/test_startup_audit.py`

`service.started` audit на startup, `register_events` (регистрация AUDIT_EVENTS catalog'а в `loging_service`), graceful fallback если loging_service недоступен (не блокировать startup).

### `unit/test_internal_soft_mode_warning_audit.py`

Header-scoping на service-уровне: отсутствие `X-Target-Department-Id` → 403 `TARGET_DEPARTMENT_HEADER_REQUIRED`, несовпадение → 404 (маска) с denied-audit `reason=target_department_mismatch`, совпадение → успех. Отдельный кейс: бот из чужого отдела с корректным заголовком проходит.

### `unit/test_config_redis_prod_guard.py`

`TestProductionRequiresRedisAuth` (production-сборка отказывается без пароля Redis), `TestNonProductionEnvsAllowAnonymousRedis`, `TestProductionRequiresHttpsAuthUrl`.

---

## `integration/` — middleware и cross-dept

### `integration/test_platform_admin_block.py` — `platform_admin_guard`

22 теста, проверяют middleware который блокирует `account_admin` / `loging_admin` от business-эндпоинтов:

- `TestAccountAdminBlocked` — `account_admin` получает 403 `PLATFORM_ADMIN_BUSINESS_DATA_DENIED` на любой `/api/server/v1/*` business path.
- `TestLogingAdminBlocked` — то же для `loging_admin`.
- `TestLogingReaderNotBlocked` — `loging_reader` пропускается (он имеет легитимный read-доступ к audit).
- `TestAllowListPaths` — `/health`, `/ready`, `/openapi.json`, `/docs` всегда пропускаются.
- `TestDepartmentAdminNotBlocked`, `TestServiceRoleUserNotBlocked`, `TestWorkerBotNotBlocked` — legitimate акторы проходят.
- `TestAuditEmittedOnBlock` — на блокировку публикуется audit `platform_admin_business_data_denied`.
- `TestAnonymousNotIntrospected` — без bearer гард не делает introspect (auth-слой отбивает раньше).
- `TestAccountAdminPureBlocking` — `account_admin` даже с сервис-ролью на бумаге не получает доступ (роль не должна быть выдана, но если выдана — гард всё равно блокирует).

### `integration/test_custom_role_cross_dept_isolation.py`

13 тестов:

- `TestCustomRoleGrantScopedToActorDept` — кастомная роль создаётся в dept актёра, не в чужом.
- `TestCrossDeptIsolation` — невозможно гранитить роль user'у из чужого dept.
- `TestBuiltinRolesGlobal` — системные роли (`admin`, `operator`, `reader`, `guest`) видны во всех dept (но привязка к dept'у при выдаче пользователю).
- `TestDeptAdminCannotGrantCrossDept` — dept_admin отбит 403.
- `TestAccountAdminBlockedFromPermissionsApi` — `account_admin` не имеет доступа к `/permissions` (бизнес-данные).
- `TestWorkerBotGrantsGlobalSeedPreserved` — seed воркер-бота не ломается при cross-dept операциях.
- `TestDeptIsolationAuditEmitted` — нарушение публикуется в audit.

---

## Инфраструктура

`tests/conftest.py` обеспечивает:

- **Тестовая БД** — `server_db_test` в контейнере `server-tests-test-postgres-1`. Схема пересоздаётся раз на сессию (`DROP SCHEMA public CASCADE` → `alembic upgrade head` через subprocess).
- **Изоляция тестов** — внешняя транзакция + SAVEPOINT, откатывается на teardown каждого теста (как в `auth_service`). `await db.commit()` внутри сервиса не сохраняется между тестами.
- **`AsyncClient`** — `httpx.AsyncClient(transport=ASGITransport(app=app))` поверх свежего FastAPI-приложения с подменённой зависимостью `get_db` → тестовая сессия.
- **Mock introspect** — `monkeypatch` на `src.dependencies.auth._introspect`. Кэша ответов нет — каждый запрос идёт через свежий (замоканный) introspect.
- **Reset rate-limiter** — `app.state.limiter.reset()` between tests, чтобы 168+ запросов в подряд не отбивались 429.
- **Token factory** — `make_token(platform_role=..., department_id=..., service_roles=..., allowed_services=...)` собирает identity-body и регистрирует его под уникальным `tok_<uuid>`.
- **Готовые токены**:
  - `admin_token` / `admin_token_b` — `department_admin` с сервисной ролью `admin` в `dep_a` / `dep_b`.
  - `account_admin_token`, `loging_admin_token`, `loging_reader_token` — platform-роли для проверок `platform_admin_guard`.
  - `reader_token_a` / `operator_token_a` / `admin_role_token_a` / `guest_token_a` / `no_role_token_a` — все варианты сервис-роли в `dep_a`.
  - `reader_token_b` / `operator_token_b` — симметрично для `dep_b`.
  - `worker_pat_token` (legacy admin-role) / `worker_bot_token_a` (least-privilege `worker_bot`-role).
- **Factories для сущностей**: `make_server(department_id, with_ipmi=...)`, `make_ipmi(server_id, ...)`, `make_account(server_id, login, password)` — создают server / ipmi_controller / server_account, шифруют секреты через `secrets_service.encrypt`.
- **`soft_dept_mode`** — фикстура, форсящая `INTERNAL_REQUIRE_DEPT_HEADER=false` через monkeypatch + `get_settings.cache_clear()`.

## Что НЕ покрыто

- Прямой E2E с реальным `auth_service` и `loging_service` — лежит в `dbos_server_service/tests/` (cross-service integration через `make test-integration`).
- Прямой E2E с реальным `server_worker` через taskiq+redis — воркер тестируется отдельно (`make test-worker`); здесь dispatch-сторона проверена через mocked `kiq()`.
- Реальная hardware-сторона IPMI (iDRAC / HPiLO / ipmitool) — все power/inventory-операции в server_service диспатчатся в worker, который сам мокает hardware-клиенты.
- Производительность / нагрузочные — не входят в этот реестр.

## Обнаружение ядер ОС

`tests/test_os_kernel_discovery.py`: 4 проверки — разбор Packages и repo-строк, gzip/plain загрузка, сохранение каталога и отсутствие потери старых ядер при сбое. Внешняя сеть заменена MockTransport/подменой резолвера.
