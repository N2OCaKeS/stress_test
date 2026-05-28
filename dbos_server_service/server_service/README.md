# server_service

## Назначение

`server_service` — прикладной сервис управления физическими и виртуальными
серверами в рамках платформы DBOS Server Manager. Он хранит карточки
серверов, шифрованные пароли IPMI/локальных учёток, действия выполняет
от лица аутентифицированного пользователя через `auth_service`, аудит
сбрасывает в `loging_service`, тяжёлые операции (power-cycle, ротация
паролей, переустановка) перекладывает на фоновый `server_worker`.

## Что делает (реализовано)

- **CRUD карточек серверов** — list / get / create / patch / delete с
  department-isolation, проверкой прав через action-based матрицу,
  conflict detection по `hostname`/`ip_address`/`serial_number`.
- **IPMI power-операции** — `POST /servers/{id}/ipmi/power/{on,off,reboot}` →
  ставит таску в `server_worker` через taskiq broker и возвращает 202 +
  `task_id`. Synchronous-валидации до dispatch'а: VIEW + dept-isolation,
  `(server, *, power_*)`, `ServerStatus != DECOMMISSIONED`, запись в
  `ipmi_controllers` существует. `power.off` — всегда hard (worker шлёт
  `ForceOff` / `chassis power off`); graceful/ACPI shutdown через этот
  endpoint не поддерживается.
- **Action-matrix permissions** — `GET /permissions`, `GET /permissions/{entity_type}`,
  `PUT /permissions/{e}/{role}/{action}`, `DELETE /permissions/{e}/{role}/{action}`.
  Каждая пара `(entity_type, action)` валидируется против whitelist'а в
  `core/constants.py::ENTITY_ACTIONS`.
- **Service-роли** — реестр (`admin`/`reader`/`operator`/`guest` плюс кастомные)
  живёт в `auth_service`. `server_service` сам не редактирует каталог ролей —
  только наполняет матрицу actions для них через `/permissions/...` endpoints.
- **Internal endpoints для worker'а** (скрыты из swagger):
  `GET /internal/servers/{id}/ipmi/credentials`,
  `GET /internal/servers/{id}/accounts/{aid}/password`,
  `POST /internal/servers/{id}/accounts/{aid}/password/rotate`,
  плюс write-direction callback'и:
  `POST /internal/servers/{id}/inventory` (hardware-facts от worker'а),
  `POST /internal/servers/{id}/users/inventory` (список OS-пользователей от worker'а → reconcile),
  `POST /internal/servers/{id}/accounts/{aid}/provision_status` (результат useradd/usermod/userdel на боксе),
  `POST /internal/servers/{id}/prepared` (worker подтвердил бутстрап управления),
  `POST /internal/ipmi-controllers/{id}/credentials_rotated`
  (worker подтверждает ротацию BMC),
  `GET /internal/secrets/migration_status`,
  `POST /internal/secrets/reencrypt_batch` (фоновая ротация мастер-ключа).
- **Audit к loging_service** — pooled `_audit_client`,
  redaction по key+value эвристикам, severity-default per `(action, status)`.
  Полный каталог событий и их число — `AUDIT_EVENTS.md`.
- **Шифрование секретов** — `secrets_service.py`, AES-256-GCM + HKDF-SHA256,
  формат `v<key>$<nonce>$<ciphertext>`, поддержка legacy-версий ключа
  через `SERVER_ENCRYPTION_KEY__vN`.

URL-префикс: `/api/server/v1/`. Порт по умолчанию: `8002`. Swagger:
`http://localhost:8002/docs` (отключён в `APP_ENV=production`).

## Архитектура

```
HTTP запрос
  → middleware: security_headers → rate_limit → platform_admin_guard → attach_request_id → audit_access
  → src/api/v1/endpoints/*.py   (FastAPI роутеры; только request/response)
  → src/services/*.py           (бизнес-логика: server, secrets, permissions, audit, worker_client, …)
  → src/repositories/*.py       (SQLAlchemy запросы)
  → src/models/*.py             (ORM-таблицы)
                              + cross-DB INSERT → dev_server_worker.tasks (worker_client)
                              + taskiq Redis publish (worker_client)
                              + HTTP → auth_service (introspect only)
                              + HTTP → loging_service (audit emit)
```

| Пакет | Назначение |
|---|---|
| `api/v1/endpoints/` | servers, ipmi, accounts, installed_packages (live SSH-probe, без БД), inventory (hardware + OS-users), os-versions, permissions, health, internal, worker_dispatch, secrets_migration |
| `core/` | `config.py` (pydantic-settings), `constants.py` (`ENTITY_ACTIONS`, `Action`, `EntityType`, `ServerStatus`), `exceptions.py` (`AppException` + envelope handler) |
| `db/` | `session.py`, `base.py`, миграции Alembic в `migrations/versions/` |
| `dependencies/` | `auth.py` (introspect на каждый запрос, без кэша; пул только под TCP/TLS), `db.py` (AsyncSession) |
| `middleware/` | `platform_admin_guard.py` — блокирует platform-админов от business endpoint'ов |
| `models/` | 7 ORM-таблиц (см. ниже) |
| `repositories/` | server, server_account (+ server_account_servers join), ipmi_controller, server_disk, entity_permission |
| `schemas/` | Pydantic-схемы запросов/ответов |
| `services/` | `server.py`, `secrets_service.py`, `worker_client.py`, `permissions.py`, `permission_service.py`, `audit_service.py`, `audit_events.py`, `audit_context.py`, `internal_service.py` |
| `utils/` | генераторы id (`srv_*`, `acc_*`, `tsk_*`, …) |

### Таблицы БД (7)

1. **servers** — hostname/ip/mgmt_ip/ssh_port unique, FK `os_version_id`, `department_id`, `status`, `power_state`, busy-state, `decommissioned_at`. CPU-данные хранятся inline: `cpu_brand` / `cpu_model` / `cpu_cores` / `cpu_threads` / `cpu_frequency_ghz` (обновляются `inventory.sync` callback'ом или вручную через `PATCH /servers/{id}`). Управление: `is_managed`, `management_user`, `prepared_at` — заполняются после `prepare`-бутстрапа.
2. **server_accounts** — учётка-«личность» с общим `password_encrypted` (nullable), `has_sudo`, `unix_groups[]`, `linked_user_id`, `shell`, `home_dir`, `department_id` (владелец) и `source` (`managed` / `discovered`). Привязка к серверам — через join `server_account_servers`. Пароль один на все привязанные серверы.
3. **server_account_servers** — join учётка↔сервер (M2M). FK CASCADE на оба, денормализованный `login`, `present_on_server`, `last_inventory_at`. UNIQUE `(account_id, server_id)` и `(server_id, login)` (один логин на сервер).
4. **ipmi_controllers** — 1:1 с servers, `kind` (idrac/ilo/ipmi/redfish — он же определяет BMC-путь), `endpoint_url`, `username`, `password_encrypted`, `last_probed_at`/`last_status`.
5. **server_disks** — FK CASCADE, `device_name` (слот: `system`/`disk1`/`diskN`), `size_gb`, `model`, `is_system`. Partial idx: один system-диск на сервер. Управляются только через вложенный `storage` сервера — отдельного disks-endpoint'а нет.
6. **os_versions** — `name` unique, `description`, `repositories` (массив URL-строк).
7. **entity_permissions** — `(entity_type, role, action, target_department_id)`, scope per-department или system-wide. (Бывшая таблица `server_installed_packages` удалена миграцией `c8e4f6a9b1d2` — теперь live SSH-probe через worker. Бывшая таблица-каталог `cpu_models` удалена миграцией `b6f3a91d27e8` — CPU-данные плоско в `servers`.)

Миграции (7 alembic ревизий):

- `d3ad4aac49cc_initial_schema_action_based.py` — 8 таблиц + 13 индексов.
- `831ba55543e9_seed_default_entity_permissions.py` — 90 строк дефолтных grants (admin 50 + reader 8 + operator 32 + guest 0).
- `abd8298d5349_entity_permissions_department_scope.py` — `target_department_id` колонка + UNIQUE с учётом scope. Закрывает cross-dept grant'ы (раньше grant был system-wide, чем пользовались для эскалации привилегий).
- `43cf9cfef9e1_seed_worker_bot_entity_permissions.py` — least-privilege role `worker_bot`: 4 grants (`server_account.view_password`, `server_account.rotate_password`, `ipmi_controller.view_credentials`, `ipmi_controller.rotate_credentials`). Никаких power/CRUD/permission-grant.
- `e9a7c2814d33_worker_bot_callback_grants.py` — два callback-гранта для worker_bot: `(server, inventory_submit)` и `(server, reinstall_start)` (последний далее переехал в `f1234abc56e7`, а потом удалён вместе с reinstall в `b8d4e3f9a712`).
- `f1234abc56e7_split_reinstall_status_action.py` — отделение worker callback'а: вводит `(server, reinstall_status_submit)`. Полностью обнуляется миграцией `b8d4e3f9a712` (вместе с удалением reinstall-pipeline'а).
- `a1b2c3d4e5f6_remove_service_role_entity.py` — выпил `service_role` entity из матрицы. Управление каталогом service-ролей (create/delete role-names) переехало в `auth_service` полностью, `server_service` сам реестр не редактирует. Самоуправление матрицей привязано к новой entity `permission` (view / permission_grant / permission_revoke).
- `b6f3a91d27e8_drop_cpu_models_inline_cpu_fields.py` — выпил каталог-таблицу `cpu_models` и FK `servers.cpu_id`. CPU-данные хранятся плоско в `servers`: `cpu_brand` / `cpu_model` / `cpu_cores` / `cpu_threads` / `cpu_frequency_ghz`. Обновляются `inventory.sync` callback'ом или вручную через `PATCH /servers/{id}`. Гранты на `cpu_model`-entity тоже подчищаются.
- `b8d4e3f9a712_ipmi_reveal_and_drop_bootorder_reinstall.py` — удаляет все гранты на boot_order/pxe_boot/reinstall_start/reinstall_status_submit (соответствующие endpoint'ы и worker-task'и сняты целиком). Reveal-гранты, которые эта миграция тоже добавляла, позже сняты в `c3f9b1a8d420`.
- `c3f9b1a8d420_merge_reveal_into_view_secrets.py` — снимает осиротевшие `(server_account, reveal_password)` / `(ipmi_controller, reveal_credentials)`. Отдельные reveal-endpoint'ы убраны; пароль теперь приходит прямо в GET-карточке аккаунта/IPMI, если вызывающий держит `view_password` / `view_credentials`.
- `a7d2c4e8f0b1_disks_storage_size_gb_drop_kind.py` — `server_disks`: `size_bytes` → `size_gb`, удалена колонка `kind`, снят грант `(disk, *)` из матрицы. Диски управляются только через вложенный `storage` сервера.
- `c1a9f2b7e4d8_os_version_repositories_public_read.py` — добавлена колонка `os_versions.repositories` (ARRAY of URL-строк); снят грант `(os_version, view)` (чтение каталога стало публичным).
- `d5e8a1c3f960_ipmi_controllers_drop_bmc_vendor.py` — удалена колонка `bmc_vendor`; выбор BMC-пути в worker'е теперь по `kind`.
- `d7e1a4c93b62_account_servers_m2m.py` — учётка на нескольких серверах: join-таблица `server_account_servers`, колонка `server_accounts.department_id`, снят одиночный `server_id`.
- `a3f1b8c2d495_account_source_inventory_fields.py` — `server_accounts.source` (`managed`/`discovered`), `server_account_servers.last_inventory_at` / `present_on_server` под инвентаризацию OS-пользователей.
- `b7e2c9a14f63_worker_bot_account_inventory_submit.py` — грант `(server_account, inventory_submit)` для `worker_bot` (callback инвентаризации OS-пользователей).
- `c4f7d9b2a1e8_worker_bot_provision_on_host.py` — грант `(server_account, provision_on_host)` для `worker_bot` (callback provision/deprovision на боксе).
- `d1f4a8c7b3e9_server_prepared_and_worker_bot_grant.py` — поля `servers.is_managed` / `management_user` / `prepared_at` + грант `(server, prepare_callback)` для `worker_bot`.

## Что в stub'ах (501 NOT_IMPLEMENTED)

**0 заглушек.** После фаз CRUD + worker integration + secrets migration +
закрытия busy/os-sync/boot/inventory/credentials-meta пути все ранее
501-ные endpoint'ы реализованы. `test_stub_envelope_and_openapi.py`
остаётся как guard — не вернуться ли случайно в stub при рефакторинге.

Реализованы (раньше — stub):

- `server_accounts` CRUD + `rotate_password` (учётка-«личность» на нескольких серверах через M2M-привязку, общий пароль). Создание принимает `server_ids` (список); link/unlink — `POST` / `DELETE /server-accounts/{id}/servers`. GET карточки отдаёт `password_b64` (base64 plaintext), если вызывающий держит `view_password` — отдельной reveal-ручки нет. Тот же `view_password` использует worker через internal endpoint. Ротация — точечная (`?server_id=`) или массовая (без него); ручной ввод пароля под политикой ≥8 символов с буквами и цифрами.
- `ipmi_controllers` CRUD + `rotate_credentials`. IPMI можно создать вместе с сервером (вложенный `ipmi`-блок в `POST /servers`) или отдельным `POST /servers/{id}/ipmi`. GET карточки отдаёт `password_b64`, если вызывающий держит `view_credentials`. Пароль на create/rotate — под той же политикой.
- `server_disks` — только через вложенный `storage` сервера (слоты `system`/`disk1`/`diskN`, `size_gb`, `is_system`). Отдельного disks-endpoint'а нет.
- `os_versions` — write-CRUD под матрицей; чтение каталога (list / get по id / get по имени) — публичное, без auth. Поле `repositories` (список URL).
- OS-пользователи на боксе — инвентаризация (`POST /servers/{id}/users/inventory` → worker `getent` → reconcile, поля `source` / `last_inventory_at` / `present_on_server`) и реальный CRUD на ОС (`provision` / `update_on_host` / `deprovision` → worker `useradd`/`usermod`/`userdel`). Reconcile работает **warn-on-drift**: БД — источник истины по атрибутам аккаунта (`has_sudo`/`unix_groups`/`shell`/`home_dir`), расхождение с боксом не перетирает поля, а пишет audit `server_account.drift_detected` (WARNING); обновляется только presence связки (`present_on_server`). Версия ОС сервера наоборот синкается box→DB. PATCH управляемых атрибутов аккаунта (`has_sudo`/`unix_groups`/`shell`) делает fan-out `account.update_on_host` на **все** привязанные серверы (best-effort, неблокирующее, пропускает `present_on_server=False` и списанные).
- `prepare` — бутстрап управления: `POST /servers/{id}/prepare` (bootstrap-креды в base64) → worker заводит управляющего пользователя `dbos` + кладёт management SSH-ключ; на сервере выставляются `is_managed` / `management_user` / `prepared_at`.
- `installed_packages` live-listing через SSH worker (без БД-таблицы — каждый запрос идёт `dpkg-query`/`rpm -qa` на сервере)

Worker-task'и, зарегистрированные в брокере, с клиентскими endpoint'ами:

- `inventory.sync` — `POST /servers/{id}/inventory/sync` + write-back
  через `POST /internal/servers/{id}/inventory`.
- `account.rotate_password` — `POST /server-accounts/{id}/rotate` +
  write-back через `POST /internal/servers/{id}/accounts/{aid}/password/rotate`.
- `ipmi.rotate_password` — `POST /ipmi-controllers/{id}/rotate` +
  write-back через `POST /internal/ipmi-controllers/{id}/credentials_rotated`.
- `power.status` — `POST /servers/{id}/power/status` (live BMC-probe через worker).
- `users.inventory` — `POST /servers/{id}/users/inventory` + write-back
  через `POST /internal/servers/{id}/users/inventory`.
- `account.provision` / `account.update_on_host` / `account.deprovision` —
  `POST /server-accounts/{id}/provision|update_on_host|deprovision?server_id=…` +
  write-back через `POST /internal/servers/{id}/accounts/{aid}/provision_status`.
- `server.prepare` — `POST /servers/{id}/prepare` + write-back через
  `POST /internal/servers/{id}/prepared`.

## Модель безопасности

Документ-источник — `1.txt` §`server_service`, 12 пунктов. Текущая
реализация:

1. **Хранение чувствительных данных.** Пароли IPMI и server_accounts —
   AES-256-GCM (AEAD: confidentiality + integrity). Ключ выводится из
   master через HKDF-SHA256, версия закодирована в префиксе `v<N>$nonce$ct`.
   Master-ключ — Kubernetes Secret, отдельно от БД. `SERVER_ENCRYPTION_KEY`
   валидируется на старте: `min_length=32`.

2. **Доступ к сервису — только через JWT от auth_service.** Сам JWT
   сервис не выпускает. Каждый запрос валидируется свежим introspect'ом —
   кэша ответов нет, поэтому отозванный токен / бан / смена роли перестают
   действовать немедленно, без окна ожидания. HTTP-пул переиспользуется,
   но только под TCP/TLS, не под ответы. `AUTH_SERVICE_URL` валидируется в
   config: в `APP_ENV in {production, staging}` обязан быть `https://`
   (исключение — localhost для dev).

3. **Action-based матрица.** Права не "роль → набор", а тройка
   `(entity_type, role, action)` в `entity_permissions`. Whitelist
   допустимых actions per entity_type — в `core/constants.py::ENTITY_ACTIONS`.
   Пользователь может иметь несколько ролей в сервисе — права складываются.

4. **Управление матрицей.** PUT/DELETE `/permissions/{e}/{r}/{a}` требует
   `(permission, *, permission_grant)` / `permission_revoke`. Scope —
   только свой department (`target_department_id`); попытка передать чужой
   department → 403 DEPARTMENT_ISOLATION. Каждое изменение пишется в
   audit как CRITICAL.

5. **Изоляция департаментов.** Пользователь видит только ресурсы своего
   department. Чужие → 404 (не 403), чтобы не утечь сам факт существования.
   Создание/изменение в чужом department запрещено даже при наличии
   нужной роли. Platform-роли (`account_admin`/`loging_admin`) блокируются
   `platform_admin_guard` middleware ДО endpoint-логики: 403
   `PLATFORM_ADMIN_BUSINESS_DATA_DENIED`. `loging_reader` middleware'ом не
   блокируется (у него есть `department_id`, но в server_service у него
   нет сервисных ролей → 403 PERMISSION_DENIED от матрицы).

6. **Раскрытие plaintext-кредов** — для worker'а через `/internal/*` (audit
   WARNING `server_account.view_password` / `ipmi_controller.view_credentials`),
   для пользователя — прямо в GET-карточке аккаунта/IPMI при наличии
   `view_password` / `view_credentials` (audit CRITICAL
   `server_account.password_revealed` / `ipmi_controller.credentials_revealed`).
   Вызывающий с одним `view` получает карточку без `password_b64`.

7. **Worker — least-privilege.** Роль `worker_bot` имеет ровно 8 grants:
   4 secret-access (`view_password`/`rotate_password` на server_account,
   `view_credentials`/`rotate_credentials` на ipmi_controller) плюс 4
   узких callback-гранта — `(server, inventory_submit)`,
   `(server_account, inventory_submit)`, `(server_account, provision_on_host)`,
   `(server, prepare_callback)`. Никаких power-операций, delete,
   permission-grant'ов. `internal_service._check_target_department`
   делает **двухуровневый** dept-cross-check:
   * **Actor vs server** — `identity.department_id` обязан совпасть с
     `server.department_id`, **всегда**, не зависит от
     `INTERNAL_REQUIRE_DEPT_HEADER`. Mismatch (включая `None` для
     platform-роли) → 403 `TARGET_DEPARTMENT_MISMATCH` с
     `reason=actor_department_mismatch`. Это закрывает leak, при котором
     soft-mode пропускал worker'а из чужого отдела по глобальной
     admin-роли.
   * **`X-Target-Department-Id` header** — worker форвардит target dept из
     task payload, defense-in-depth поверх actor-check'а. Default mode —
     strict (`INTERNAL_REQUIRE_DEPT_HEADER=true`): отсутствие header'а → 403
     `TARGET_DEPARTMENT_HEADER_REQUIRED`, mismatch → 403
     `TARGET_DEPARTMENT_MISMATCH`. Soft mode (`false`) оставлен только для
     dev/test — отсутствие/mismatch только пишет audit warning.

8. **Защита транспортного слоя.** Rate-limit slowapi global 500/min,
   slowloris-защита. `X-Forwarded-For` принимается только от
   `trusted_proxy_ips` allow-list (default `[]`). 429 не порождает audit
   (anti-amplification). Health/Ready исключены из audit и rate-limit'а.
   `SecurityHeadersMiddleware` ставит на каждый ответ (включая 429/422/401)
   `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
   `Referrer-Policy: strict-origin-when-cross-origin` и CSP
   `default-src 'none'; frame-ancestors 'none'`; HSTS добавляется только при
   `SECURITY_HSTS_ENABLED=true` (за https-фронтом). Регистрируется outermost.

9. **Ротация мастер-ключа шифрования.** Версии ключа живут одновременно
   (`SERVER_ENCRYPTION_KEY__vN`): новый шифрует, старый — расшифровывает
   исторические. `server_worker` фоном перешифровывает старые записи
   только в окнах низкой нагрузки. Старый ключ удаляется только после
   подтверждения worker'ом «миграция завершена».

10. **Ротация хранимых паролей.** `rotate_password` / `rotate_credentials`
    запускают ротацию через worker'а, новый ciphertext сохраняется через
    `/internal/*/rotate`, event пишется как CRITICAL.

11. **Аудит.** Все значимые действия (CRUD по серверам, power, раскрытие
    секретов, grant/revoke прав, отказы в доступе) идут в `loging_service`.
    Чувствительные поля фильтруются per-handler whitelist'ом. Каталог —
    `AUDIT_EVENTS.md`.

12. **Контракт ошибок.** Единый JSON-envelope:
    `{error, error_code, message, details, request_id, timestamp}`.
    Стабильные коды: `PERMISSION_DENIED`, `DEPARTMENT_ISOLATION`,
    `PLATFORM_ADMIN_BUSINESS_DATA_DENIED`, `RATE_LIMIT_EXCEEDED`,
    `ENCRYPTION_KEY_MISSING`, `DECRYPT_FAILED`, `SERVER_DECOMMISSIONED`,
    `SERVER_NO_IPMI`, `TASK_IDEMPOTENT_CONFLICT`, `WORKER_UNREACHABLE`,
    `SERVICE_ACCESS_DENIED`.

## Конфиг

| ENV | Назначение |
|---|---|
| `DATABASE_URL` | PostgreSQL async DSN |
| `AUTH_SERVICE_URL` | базовый URL для introspect; в prod/staging обязан быть https |
| `AUTH_REQUEST_TIMEOUT_SECONDS` | default 3.0 |
| `SERVER_ENCRYPTION_KEY` | master-ключ, обязательный, `min_length=32` |
| `SERVER_ENCRYPTION_KEY_VERSION` | активная версия для новой записи; default 2 (HKDF), минимум 2 — v1 (legacy SHA-256) только для расшифровки старых ciphertext'ов |
| `SERVER_ENCRYPTION_KEY__vN` | legacy ключи под версию N |
| `SERVER_WORKER_DATABASE_URL` | cross-DB INSERT в `dev_server_worker.tasks` |
| `SERVER_WORKER_REDIS_URL` | taskiq publish (он же хранит ephemeral bootstrap-креды prepare) |
| `PREPARE_CREDS_TTL_SECONDS` | TTL bootstrap-кред prepare в Redis (ключ `dbos:prepare_creds:<task_id>`); default 900 |
| `SECURITY_HSTS_ENABLED` | включает `Strict-Transport-Security` на всех ответах; default `False` (только за https-фронтом) |
| `LOGGING_SERVICE_URL` | endpoint loging_service'а |
| `LOGGING_SERVICE_API_KEY` | ingest-ключ для аудита |
| `INTERNAL_REQUIRE_DEPT_HEADER` | default `True`; soft mode (`False`) — только для dev/test |
| `APP_ENV` | `development` / `staging` / `production` (влияет на https-guard'ы и swagger) |
| `TRUSTED_PROXY_IPS` | CIDR allow-list для XFF; default `[]` |

## Тестирование

**~805 тестов (passed)** после закрытия stub'ов + расширения visibility/audit
покрытия (точная цифра — в `STATUS.md` / `TEST_COVERAGE.md`). Запуск:

```bash
make test-server         # в Docker (рекомендуемо — реальный postgres)
make test-dev-server     # в devcontainer
```

Структура:

- `tests/test_*.py` (integration) — `test_servers_endpoints.py`,
  `test_ipmi_endpoints.py`, `test_permissions_endpoints.py`,
  `test_roles_endpoints.py`, `test_internal_endpoints.py`,
  `test_audit_emission.py`, `test_worker_bot_least_privilege.py`,
  `test_worker_bot_grants.py`, `test_rate_limit.py`,
  `test_introspect_*.py`, `test_stub_envelope_and_openapi.py`,
  `test_platform_admin_block.py`, `test_security_headers.py` и др.
- `tests/unit/` — `test_secrets_service.py`, `test_secrets_hypothesis.py`,
  `test_entity_actions.py`, `test_permissions.py`, `test_schemas_server.py`,
  `test_worker_client_dispatch.py`, `test_worker_client_broker_lock.py`,
  `test_startup_audit.py`, `test_config_redis_prod_guard.py`,
  `test_internal_soft_mode_warning_audit.py`.

Что покрыто: audit emission на всех ~222 точках, worker_bot least-privilege,
rate-limit, introspect без кэша (свежий вызов на каждый запрос, revoked-токен
отбивается немедленно), security headers, stub envelope+OpenAPI, dispatch_task
idempotency + zombie rollback, broker_lock concurrency, platform_admin_guard
middleware, https-guard для AUTH_SERVICE_URL в prod, startup audit
lifecycle, массовая ротация с per-server-tolerance.

Что НЕ покрыто реальной интеграцией: cross-DB INSERT в
`dev_server_worker.tasks` (mocked), httpx-вызовы к auth_service (introspect
mocked в `conftest.py`), worker-PAT путь (mocked identity).

## Развёртывание

- 2 реплики API в Kubernetes за ingress (TLS-терминация на ingress, внутри
  кластера bearer-токены сервисных аккаунтов).
- Собственный PostgreSQL-кластер, отдельная БД (cross-DB только для INSERT'а
  в `dev_server_worker.tasks`).
- Liveness: `/api/server/v1/health`. Readiness: `/api/server/v1/ready` (SELECT 1).
- Секреты (`SERVER_ENCRYPTION_KEY`, `LOGGING_SERVICE_API_KEY`) —
  Kubernetes `Secret`, конфиг — `ConfigMap`.

## Технологии

- Python 3.12, FastAPI, SQLAlchemy async, asyncpg, Alembic
- httpx (pooled клиенты для introspect / auth / audit)
- taskiq + Redis (publish задач worker'у)
- slowapi (rate-limit)
- Docker, Kubernetes

Каталог audit-событий: `AUDIT_EVENTS.md`.
