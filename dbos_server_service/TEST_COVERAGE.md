# DBOS Server Manager — карта тестового покрытия

Источники: фактическое содержимое `*/tests/` и per-service `STATUS.md` (например, [secret_service/STATUS.md](secret_service/STATUS.md)). Корневого `STATUS.md` нет — этот файл выполняет его роль.

| Сервис | Состояние кода | Тесты | Прогон |
|---|---|---|---|
| [auth_service](#auth_service) | production-ready | **1539** | `make test-auth`, `make test-auth-e2e` |
| [loging_service](#loging_service) | production-ready | **1794** | `make test-logging` |
| [server_service](#server_service) | production-ready, 0 stubs | **1671** | `make test-server` |
| [server_worker](#server_worker) | production-ready, 3 hw-клиента (Redfish/ipmitool/SSH) | **2214** | `make test-worker` |
| [tests/integration](#cross-service-integration) | cross-service auth↔logging↔server | **495** | `make test-integration` |
| `config_service` | не начат | — | — |
| `web_settings` | не начат | — | — |
| `cli` | не начат | — | — |

Суммарно: **7218 passing тестов** (1539 + 1794 + 1671 + 2214) + **495 integration**. Критичных открытых задач нет.

### Недавно добавленное покрытие

- `server_worker/tests/test_retention_cleanup_tasks.py` — покрытие periodic-задач `tasks.cleanup_completed_old` и `audit_outbox.cleanup_published_old`: cutoff по `TASKS_RETENTION_DAYS` / `AUDIT_OUTBOX_RETENTION_DAYS`, удаление только терминальных task'ов, публикованных outbox-row, идемпотентность повторного запуска.
- `auth_service/tests/auth/test_login.py` расширен сценариями lockout вокруг `services/_lockout.py` (счётчик неудач, окно `LOCKOUT_MINUTES`, сброс при успешном входе, `MAX_FAILED_LOGIN_ATTEMPTS` как порог) — login-flow интеграционно. Generic-хелперы того же модуля (`release_principal_if_expired`, `assert_principal_not_locked`, `register_principal_failure` — используются OAuth client_credentials / bot-token lockout'ом) покрыты отдельным unit-тестом `auth_service/tests/unit/test_lockout_generic_helpers.py`.
- `server_service/tests/test_server_accounts_endpoints.py` — фактический CRUD `server_account` (6 endpoints), link/unlink M2M (`POST`/`DELETE /server-accounts/{id}/servers`) + ротация пароля под action-based permissions.

> Разделы ниже отражают более ранний снимок и могут отставать. Актуальный test-count — в таблице выше, открытые задачи — в `obsidian/TODO.md` и per-service `STATUS.md`.

---

## auth_service

Полный реестр старых тестов: [auth_service/Test.md](auth_service/Test.md).
Стек: pytest + `httpx.AsyncClient(ASGITransport)` + реальный Postgres в Docker
(`auth_db_test`). Изоляция — внешняя транзакция + SAVEPOINT на тест.

### Unit-тесты (без HTTP/без БД)

- [`core/test_security.py`](auth_service/tests/core/test_security.py) — Argon2id, JWT,
  refresh tokens, PAT, bot tokens, `hash_opaque_token` (**26**).
- [`core/test_security_edge.py`](auth_service/tests/core/test_security_edge.py) — JWT
  exp edge cases (negative delta, overflow), PAT/Bot token формат, Argon2id, decode
  без `sub`/`exp` (**18**).
- [`core/test_audit_context.py`](auth_service/tests/core/test_audit_context.py) (**17**),
  [`core/test_audit_integration.py`](auth_service/tests/core/test_audit_integration.py) (**6**),
  [`core/test_redaction.py`](auth_service/tests/core/test_redaction.py) (**29**).
- [`core/test_redaction_hypothesis.py`](auth_service/tests/core/test_redaction_hypothesis.py) —
  property-based: идемпотентность, замена по ключам (PASSWORD/TOKEN/SECRET/HASH/
  CREDENTIAL), JWT detection, immutability (**13**).
- [`core/test_ids_hypothesis.py`](auth_service/tests/core/test_ids_hypothesis.py) —
  property-based: `prefix+32hex` формат, uniqueness, hash determinism (**9**).
- [`core/test_merge_permissions.py`](auth_service/tests/core/test_merge_permissions.py) —
  `_merge_permissions()`: union services без дублей, merge ролей с дедупом,
  immutability (**10**).
- [`core/test_schemas.py`](auth_service/tests/core/test_schemas.py) — Pydantic
  validation: UserCreate/Update, BanRequest, ResetPasswordRequest,
  ServiceRole/GroupCreate, BotCreate, OAuthClient, IdentityContext, OkResponse (**29**).
- [`core/test_utils.py`](auth_service/tests/core/test_utils.py) — `utils/ids.py`,
  `pagination.py`, `time.py`: prefix correctness, uniqueness, clamping, utcnow,
  expires_at, is_expired (**19**).
- [`core/test_dependencies_auth.py`](auth_service/tests/core/test_dependencies_auth.py) —
  unit для `dependencies/auth.py`: `_extract_bearer` (case, пробелы),
  payload→identity mapping, JWT validation, `require_account_admin`,
  `require_any_admin` (**18**).
- [`core/test_cascades.py`](auth_service/tests/core/test_cascades.py) — FK + каскады
  на ORM: Department RESTRICT user / CASCADE groups, User → session/PAT/ban/roles/
  memberships, Bot → tokens/roles, Service → role_definitions (**7**).

### Integration-тесты (TestClient + Postgres)

| Блок | Файл | Тестов |
|---|---|---|
| Логин / lockout | [`auth/test_login.py`](auth_service/tests/auth/test_login.py) | 16 |
| Logout | [`auth/test_logout.py`](auth_service/tests/auth/test_logout.py) | 5 |
| `/me` | [`auth/test_me.py`](auth_service/tests/auth/test_me.py) | 7 |
| Refresh + reuse-детектор | [`auth/test_refresh.py`](auth_service/tests/auth/test_refresh.py) | 5 |
| **Health/Ready публичные** | [`auth/test_health.py`](auth_service/tests/auth/test_health.py) | 8 |
| **OAuth2 password (Swagger /token)** | [`auth/test_token_form.py`](auth_service/tests/auth/test_token_form.py) | 6 |
| **account_admin без dept + dept move через refresh** | [`auth/test_account_admin_no_dept.py`](auth_service/tests/auth/test_account_admin_no_dept.py) | 4 |
| **Race: refresh reuse, double ban/logout, unique role** | [`auth/test_concurrency.py`](auth_service/tests/auth/test_concurrency.py) | 6 |
| Introspect / service-access | [`authorization/test_introspect.py`](auth_service/tests/authorization/test_introspect.py) | 19 |
| **Introspect edge (JWT alg confusion, orphan PAT)** | [`authorization/test_introspect_edge.py`](auth_service/tests/authorization/test_introspect_edge.py) | 9 |
| Users — create / update / ban / reset / roles | [`users/`](auth_service/tests/users/) (5 файлов) | 40 |
| **Users — list & dept-filter** | [`users/test_list_users.py`](auth_service/tests/users/test_list_users.py) | 11 |
| **Users — ban edge (temp ban, cycle, reset)** | [`users/test_ban_edge.py`](auth_service/tests/users/test_ban_edge.py) | 7 |
| **Users — attach/detach к группам** | [`users/test_user_groups_attach.py`](auth_service/tests/users/test_user_groups_attach.py) | 14 |
| Services + service_roles + bulk (3 файла) | [`services/`](auth_service/tests/services/) | 39 |
| **Bulk-roles edge (empty/partial/cross-dept)** | [`services/test_bulk_roles_edge.py`](auth_service/tests/services/test_bulk_roles_edge.py) | 6 |
| Departments | [`departments/test_departments.py`](auth_service/tests/departments/test_departments.py) | 10 |
| PAT | [`tokens/test_pat.py`](auth_service/tests/tokens/test_pat.py) | 11 |
| Боты + tokens + roles + lifecycle (3 файла) | [`bots/`](auth_service/tests/bots/) | 36 |
| **Bot create edge (inactive svc, empty grant)** | [`bots/test_bot_create_edge.py`](auth_service/tests/bots/test_bot_create_edge.py) | 5 |
| OAuth2 (client_credentials + authz_code) | [`oauth2/test_clients.py`](auth_service/tests/oauth2/test_clients.py) | 29 |
| **OAuth2 authorize edge (state escape, expired code)** | [`oauth2/test_authorize_edge.py`](auth_service/tests/oauth2/test_authorize_edge.py) | 6 |
| Группы + групповые роли | [`groups/test_groups.py`](auth_service/tests/groups/test_groups.py) | 23 |
| Docker registry config + token | [`docker/`](auth_service/tests/docker/) (2 файла) | 31 |
| **Docker edge (PATCH partial, Basic auth, /certs, /jwks)** | [`docker/test_docker_edge.py`](auth_service/tests/docker/test_docker_edge.py) | 14 |
| Error envelope | [`error_format/test_error_envelope.py`](auth_service/tests/error_format/test_error_envelope.py) | 7 |

### Миграции

- **[`db/test_migrations.py`](auth_service/tests/db/test_migrations.py)** — Alembic
  upgrade/downgrade: создание таблиц, constraints, индексов; per-department role
  definitions backfill; `bot_service_roles` table; idempotency; full cycle (**16**).

### E2E с реальным registry

- [`e2e/test_docker_e2e.py`](auth_service/tests/e2e/test_docker_e2e.py) (**22**),
  `@pytest.mark.e2e`, только `make test-auth-e2e`.

### Не покрыто (остатки после обновления)

- OAuth2 `refresh_token` grant — не реализован в коде, тестов нет намеренно.
- Performance / нагрузочные — вне реестра.
- Per-resource scoping в `UserServiceRole` (план B, когда появится модель).
- Lifespan migration с `@app.on_event("startup")` (если ещё используется).
- Index usage performance (`EXPLAIN ANALYZE` на больших таблицах).

### TODO — auth_service

- [ ] OAuth2 `refresh_token` grant — добавить, когда будет реализован в коде.
- [ ] Per-resource scoping в `UserServiceRole` — когда появится модель.
- [ ] Performance / нагрузочные ingest (отдельный профиль).
- [ ] Index usage / EXPLAIN ANALYZE на больших таблицах.

---

## loging_service

Полный реестр старых тестов: [loging_service/Test.md](loging_service/Test.md).
Стек: pytest + `fastapi.TestClient` + реальный Postgres в Docker (`logging_db_test`).
Изоляция — `TRUNCATE` перед каждым тестом.

### Unit-тесты

- [`test_rule_service.py`](loging_service/tests/test_rule_service.py) — движок правил:
  глоб-матчинг, severity resolution, apply logic (**42**).
- **[`test_rule_engine_edge.py`](loging_service/tests/test_rule_engine_edge.py)** —
  pure unit rule engine edge cases: `action_matches_pattern` (`*`, `.*`, escape
  спецсимволов), `_resolve_default_severity` (default INFO/WARNING/CRITICAL),
  `_matches` фильтры (service/action/status/severity/allowed), `apply_rules` с кешем
  (SUPPRESS/ALLOW/OVERRIDE_SEVERITY, цепочки, non-mutating) (**29**).
- [`test_rule_cache.py`](loging_service/tests/test_rule_cache.py) — TTL, stale-fallback,
  concurrent (**13**).
- **[`test_rule_cache_stress.py`](loging_service/tests/test_rule_cache_stress.py)** —
  TTL=1сек перезагрузка, 100 параллельных `get()` под локом → одна загрузка БД,
  `get()||invalidate()` без deadlock, leak protection, stale fallback на сбое БД (**8**).
- [`test_ids.py`](loging_service/tests/test_ids.py) — префиксы (**13**).
- [`test_redaction.py`](loging_service/tests/test_redaction.py) — маскировка `details`
  (key-based, value-based, рекурсия, truncate) + `argon2id` hash + `credentials`
  dict целиком → `<CREDENTIAL>` (**20**).
- [`test_admin_audit_severity.py`](loging_service/tests/test_admin_audit_severity.py) —
  `record_admin_action`, дефолтные severity, обход правил (**8**).
- **[`test_core_exceptions.py`](loging_service/tests/test_core_exceptions.py)** — HTTP
  статусы подклассов (401/403/404/422/503), `require_service_token` с constant-time
  compare, network errors `_fetch_identity` (timeout/connect/invalid-token/banned),
  identity → `request.state` (**18**).
- **[`test_schema_validators.py`](loging_service/tests/test_schema_validators.py)** —
  Pydantic-валидация: `EventCreate` (required, actor_type whitelist, lengths,
  details size), `RuleCreate` (`effect ↔ effect_severity` invariant, priority bounds
  [1,1000]), `RegisterEventsRequest`, `EventDefinition`, `RetentionPolicyCreate`
  bounds [30, 3650] (**37**).

### Integration-тесты (TestClient + Postgres)

| Блок | Файл | Тестов |
|---|---|---|
| Ingest + health | [`test_ingest.py`](loging_service/tests/test_ingest.py) | 12 |
| Query / фильтры / пагинация | [`test_query.py`](loging_service/tests/test_query.py) | 19 |
| **Query edge (комбо фильтров, from>to, границы)** | [`test_query_edge.py`](loging_service/tests/test_query_edge.py) | 14 |
| Rules CRUD + применение + admin-аудит | [`test_rules.py`](loging_service/tests/test_rules.py) | 28 |
| Services registry + match_action validation | [`test_services.py`](loging_service/tests/test_services.py) | 26 |
| Retention policy | [`test_retention.py`](loging_service/tests/test_retention.py) | 16 |
| **Retention apply edge (cutoff, rowcount, inactive)** | [`test_retention_apply.py`](loging_service/tests/test_retention_apply.py) | 10 |
| Защита loging_service от retention | [`test_retention_protection.py`](loging_service/tests/test_retention_protection.py) | 10 |
| `require_admin` + `/token` proxy | [`test_admin_auth.py`](loging_service/tests/test_admin_auth.py) | 19 |
| `require_reader` (dept-scoping, service-roles) | [`test_reader_auth.py`](loging_service/tests/test_reader_auth.py) | 8 |
| Payload validation | [`test_payload_validation.py`](loging_service/tests/test_payload_validation.py) | 21 |
| PATCH /rules edge cases | [`test_rule_patch.py`](loging_service/tests/test_rule_patch.py) | 13 |
| Middleware + AppException | [`test_middleware.py`](loging_service/tests/test_middleware.py) | 19 |
| Таймзоны (UTC store / MSK display) | [`test_timezones.py`](loging_service/tests/test_timezones.py) | 6 |
| Гонки upsert (1 xfail остался) | [`test_concurrency.py`](loging_service/tests/test_concurrency.py) | 3 |

### Не покрыто (остатки)

- **`xfail` всё ещё активен**:
  `test_concurrent_upsert_same_action_raises_integrity_error` — `upsert_events`
  использует SELECT+INSERT без `ON CONFLICT`, при параллельных регистрациях падает
  на unique constraint. Требуется переход на `pg_insert(...).on_conflict_do_update`.
- Фоновый `_retention_loop` (таймер MSK 00:00) — функция `apply_active()` покрыта,
  сам цикл с тайминг-логикой нет.
- Rate-limit ingest per-service — не реализован, тестов нет.
- Lifespan migration с `@app.on_event("startup")`.
- Index usage / EXPLAIN ANALYZE на 1M+ events.

### TODO — loging_service

- [ ] `_retention_loop` — таймер MSK 00:00, daemon thread, идемпотентность,
      lock между replicas (k8s).
- [ ] Rate-limit ingest per-service — когда появится.
- [ ] Index usage / EXPLAIN ANALYZE на 1M+ events.

---

## server_service

Прорыв в этой итерации: **181 тест** (было 4). Покрыты все реализованные слои —
endpoints, services, repositories, миграции, dependencies, схемы, секреты, permission
matrix. `server_accounts`, `os_versions`, `installed_packages`, `inventory` реализованы
и покрыты живым CRUD; чистых 501-заглушек на момент актуализации не осталось.

### Unit-тесты (78)

- [`unit/test_secrets_service.py`](server_service/tests/unit/test_secrets_service.py) —
  round-trip (текст/юникод/emoji/пусто/4KB); wire-формат
  `v<n>$<nonce_b64u>$<ciphertext_b64u>`; fresh nonce; malformed token (no version,
  wrong split, `vXYZ`, bad base64); **tampered ciphertext → `DECRYPT_FAILED`**;
  **plaintext не утекает** в `exc.message`/`exc.details`; key versioning
  (`SERVER_ENCRYPTION_KEY__v1` legacy fallback при bump); `encrypt(None) →
  ENCRYPT_INPUT_INVALID` (**16**).
- [`unit/test_secrets_hypothesis.py`](server_service/tests/unit/test_secrets_hypothesis.py) —
  property-based: round-trip 0–512 chars + full Unicode (surrogates filtered);
  token shape (2 `$` + `v`-prefix); nonce uniqueness (AES-GCM); bit-flip →
  `DECRYPT_FAILED`; base64url round-trip без padding; malformed → typed error;
  plaintext never echoes даже при совпадающих подстроках (**8**).
- [`unit/test_entity_actions.py`](server_service/tests/unit/test_entity_actions.py) —
  `is_valid_action` (valid pair, unknown entity/action, empty); sync
  `constants.ENTITY_ACTIONS` ↔ seed-миграция `_ALL_ACTIONS`; 90 default grants
  (admin 50 / reader 8 / operator 32 / guest 0, no duplicates); sensitive actions
  closure (`view_password`/`view_credentials`/`grant_sudo` НЕ у operator); role
  management — admin only (**20**).
- [`unit/test_permissions.py`](server_service/tests/unit/test_permissions.py) —
  `has_action`/`require_action`/`effective_actions`; account_admin bypass;
  no roles → false/empty; union ролей; service-role isolation (роли для
  другого сервиса игнорируются) (**14**).
- [`unit/test_schemas_server.py`](server_service/tests/unit/test_schemas_server.py) —
  `ServerCreate/Update`: hostname max=255, display_name max=256, IPv4/IPv6 parsing,
  CIDR rejected, mgmt_ip отдельно, ssh_port [1, 65535], cpu_count/ram_total_mb
  non-negative + zero, required fields, partial Update (**20**).

### Integration-тесты (99)

- [`test_servers_endpoints.py`](server_service/tests/test_servers_endpoints.py) —
  все 5 CRUD × 4 роли (`admin`/`operator`/`reader`/`guest`) + `account_admin`:
  GET list (dept filter, пагинация), POST (DEPARTMENT_ISOLATION, дубли
  hostname/ip → 409), GET /{id} (404 hidden для чужого dept, account_admin видит
  всё), PATCH (admin/operator OK, reader → 403, дубль → 409), DELETE (admin OK,
  operator → 403, cascade через ORM), 501 stubs (busy/os-sync), banned user,
  inactive token (**39**).
- [`test_internal_endpoints.py`](server_service/tests/test_internal_endpoints.py) —
  OpenAPI exposure check (`include_in_schema=False` в `/openapi.json`); IPMI
  credentials (worker PAT, decrypt); roles guard (reader/operator → 403); account
  password (`ACCOUNT_HAS_NO_PASSWORD` 404); rotate (operator имеет grant);
  plaintext не светится в response (**16**).
- [`test_permissions_endpoints.py`](server_service/tests/test_permissions_endpoints.py) —
  все 4 endpoints: GET / (90 default grants, распределение по ролям), GET
  /{entity_type} (фильтр, unknown → 422), PUT (grant, unknown action → 422,
  идемпотентность), DELETE (revoke, 404 на несуществующий); ролевая защита
  (admin OK, остальные по матрице); account_admin bypass (**21**).
- [`test_ipmi_endpoints.py`](server_service/tests/test_ipmi_endpoints.py) —
  3 power endpoints (on/off/reboot): 202 + `tsk_*` task_id, dispatch check,
  payload + created_by, permission (reader → 403, operator → 200) (**11**).
  Boot-order/reinstall endpoint'ов нет — pipeline вырезан миграцией
  `b8d4e3f9a712`.
- [`test_roles_endpoints.py`](server_service/tests/test_roles_endpoints.py) —
  GET (reader OK, guest → 403, bearer forwarding), POST (admin_role OK, reader
  → 403), DELETE (admin_role OK, reader → 403); auth_service unreachable → 503 (**12**).

### Smoke

- [`test_health.py`](server_service/tests/test_health.py) — health/ready/404/501
  (**4**).

### Не покрыто

- `cpu_models`, `disks` — отдельных user-facing CRUD endpoints нет (поля живут
  внутри карточки сервера; ingest идёт через worker inventory). Полный отдельный
  CRUD не запланирован.
- Field-level gate `has_sudo=True → Action.GRANT_SUDO` — поведение запланировано в
  `obsidian/TODO.md`, не реализовано.
- Кэш introspect в server_service — намеренно отсутствует (свежий introspect на каждом запросе, чтобы revoke/ban действовали мгновенно). В auth_service есть identity-cache `_identity_cache` с TTL `IDENTITY_CACHE_TTL_SECONDS` (default 5.0s), не плановая, а штатная фича.
- FK RESTRICT на `os_version_id`/`cpu_id` (попытка удалить → 409) — миграция
  есть, но интеграционного теста нет.
- Полное CRUD для `ipmi_controller`, `server_account` repositories (косвенно
  затронуто в integration, нет отдельных репо-тестов).

### TODO — server_service

- [ ] `audit_service.py` (новый модуль) — публикация значимых действий в
      loging_service по образцу auth_service.
- [ ] Реализация и тесты `server_accounts` CRUD (включая field-level `has_sudo →
      GRANT_SUDO`).
- [ ] Реализация и тесты `ipmi_controller` CRUD (`PUT/DELETE /servers/{id}/ipmi`,
      `view_credentials`, `rotate_credentials`).
- [ ] Реализация и тесты `cpu_models`, `os_versions`, `disks`,
      `installed_packages`, `inventory` (CRUD + ingest от worker'а).
- [ ] FK RESTRICT integrity test на `os_version_id`/`cpu_id`.
- [ ] Кэш introspect (TTL) — когда реализуется.
- [ ] Cross-service integration test `server_service → loging_service` (после
      audit_service).
- [ ] ГОСТ-Кузнечик MGM вместо AES-256-GCM в `secrets_service` (swap без
      перешифрования благодаря self-contained формату).

---

## server_worker

Прорыв в этой итерации: **79 тестов** (было 3 smoke). Покрыт `_runner`, все 8 task
handlers, модель Task, HTTP-клиенты, mock'и iDRAC/SSH, enum'ы.

### Unit-тесты (43)

- [`unit/test_audit_client.py`](server_worker/tests/unit/test_audit_client.py) —
  `emit()` payload (required + optional поля), Auth header
  (`logging_service_api_key` приоритет, fallback `worker_bot_token`, без ключа →
  no Auth), URL composition (`/api/logging/v1/events`, strip trailing slash),
  error swallowing (4xx/5xx logged, `httpx.TimeoutException`/`ConnectError`
  swallowed) (**13**).
- [`unit/test_server_service_client.py`](server_worker/tests/unit/test_server_service_client.py) —
  3 метода: `fetch_ipmi_credentials` (200 → payload, 404/401/500 →
  `CredentialFetchError`, network → `SERVER_SERVICE_UNREACHABLE`);
  `fetch_account_password` (404 → `ACCOUNT_PASSWORD_UNAVAILABLE` с details);
  `submit_rotated_password` (200, 400 → `PASSWORD_ROTATE_REJECTED`, 500/network);
  PAT в Authorization, пустой → no header; **пароль не в exc.message/details** (**11**).
- [`unit/test_mock_clients.py`](server_worker/tests/unit/test_mock_clients.py) —
  iDRAC mock (power_on/off/reboot/status, set_password, credentials не echoed
  в result); SSH mock (collect_inventory с faked facts, package/disk fields,
  set_account_password — password не leaks) (**12**).
- [`unit/test_constants.py`](server_worker/tests/unit/test_constants.py) — `TaskKind`
  (8 значений StrEnum, dot-namespaced), `TaskStatus` (5 значений), terminal vs
  non-terminal (**7**).

### Runner + handlers + модель (33)

- [`test_runner.py`](server_worker/tests/test_runner.py) — `run_task()` happy path
  (load → mark_running → impl → mark_succeeded → audit success); exception →
  mark_failed (`Type: message` в `last_error`) + audit failure; `task_not_found`
  (audit без mark_running); fallback target_id → task_id; attempt 0→1 (**8**).
- [`test_task_handlers.py`](server_worker/tests/test_task_handlers.py) — хендлеры:
  `power.{on,off,reboot,status}` (+ credentials_fetch_failure + KeyError),
  `inventory.sync` (default `ssh_login=root`, account override), `account.rotate_password`
  (full flow fetch → ssh → submit, **password secrecy в audit**),
  `ipmi.rotate_password` (idrac), `_generate_password()` (uniqueness, length ≥ 24)
  (**17**). `reinstall.start` вырезан вместе с PXE-pipeline'ом.
- [`test_task_model.py`](server_worker/tests/test_task_model.py) — ORM Task: defaults
  (status=queued, attempt=0, max_attempts=3, payload={}, enqueued_at=now()); UNIQUE
  `idempotency_key` → IntegrityError; NULL не нарушает UNIQUE (PostgreSQL); JSONB
  nested access; индексы (`ix_tasks_kind_status`, `ix_tasks_status_enqueued`,
  `ix_tasks_target_server_id`), `ix_tasks_task_kind` дропнут как дубликат
  prefix композита; NOT NULL columns (**8**).

### Smoke

- [`test_smoke.py`](server_worker/tests/test_smoke.py) — модель импортируется,
  broker регистрирует все 8 task names, `TaskKind ↔ broker` (**3**).

### Не покрыто

- **Retry logic не реализован** — `attempt`/`max_attempts` в модели есть, но
  `run_task()` не содержит цикла повторов: exception → mark_failed сразу.
  Тестов на back-off / max_attempts exhaustion нет.
- **Идемпотентность через `idempotency_key` не используется в runtime** — UNIQUE
  constraint есть, `get_by_idempotency_key()` в repo есть, но handler'ы не делают
  dedupe-проверку перед запуском impl.
- **Graceful shutdown не реализован** — SIGTERM handler / context manager для
  drain'а running задач не найден.
- **Periodic scheduler** (пока не реализован): `taskiq.scheduler` для `power.status` /
  `inventory.sync` per-department — не реализован.
- **End-to-end через все 3 сервиса** в формальных тестах нет (только живой
  happy path через swagger). Mock-стек server_service + loging_service в test stack
  не собран.
- Concurrent worker'ы (race в pick task из Redis) — нет тестов.
- Реальные iDRAC/SSH-клиенты (Redfish через `sushy`/прямой httpx; `asyncssh`)
  не реализованы; тесты против mock'ов.

### TODO — server_worker

- [ ] Retry logic: back-off + повторный enqueue после failure, `attempt <
      max_attempts`, jitter.
- [ ] Идемпотентность runtime: dedupe-проверка по `idempotency_key` перед запуском
      impl + тесты на duplicate rejection.
- [ ] Graceful shutdown: SIGTERM handler, tracking running task_id, timeout на drain.
- [ ] Periodic scheduler (`taskiq.scheduler`): power.status per-department,
      inventory.sync раз в N часов.
- [ ] End-to-end test stack: mock server_service (respx) + mock loging_service
      + fakeredis или реальный Redis в compose; полный цикл power.on, inventory,
      account/ipmi rotate.
- [ ] Concurrency: 2 worker'а на одну task — один выигрывает; race в `mark_running`.
- [ ] Реальные iDRAC/iLO (Redfish), raw IPMI (`pyghmi`), SSH (`asyncssh`) — когда
      будут.
- [ ] `inventory.sync` отправка facts в `POST /servers/{id}/inventory/sync` (endpoint
      ещё не реализован в server_service).
- [ ] `ipmi.rotate_password` — сохранение нового ciphertext в server_service.

---

## Cross-service integration

Реальные процессы (не TestClient), коммуникация по сети. Запуск: `make test-integration`.

- [`tests/integration/test_auth_logging.py`](tests/integration/test_auth_logging.py) —
  поток аудита `auth_service` → `loging_service`: `TestServiceStartup`,
  `TestLoginAudit` (success/failure severity), `TestUnauthorizedAccessAudit` (CRITICAL
  denied), `TestTokenAudit` (pat.create/list), `TestClientErrorAudit` (404/422),
  `TestMeAudit`, `TestLoggingAdminWorkflow` (rule CRUD + apply + bypass SUPPRESS),
  `TestServiceRegistry` (**19**).
- **[`tests/integration/test_bot_lifecycle_audit.py`](tests/integration/test_bot_lifecycle_audit.py)** —
  bot.create (WARNING, target_id=bot_id), bot.token_create (plaintext redacted в
  details), introspect bot-token (actor_type=bot, actor_id=bot_id), bot.roles_assign
  (target_type=bot, roles в details) (**4**).
- **[`tests/integration/test_groups_and_roles_audit.py`](tests/integration/test_groups_and_roles_audit.py)** —
  иерархический audit (dept → service → role/group → member): department.service_grant
  (CRITICAL), service_role.create (INFO), service_role.delete (CRITICAL,
  auto_revoked flags), group.create (INFO), group.member_add (WARNING),
  group.roles_assign (CRITICAL) (**6**).

### Не покрыто

- `server_service` → `loging_service` — `audit_service.py` ещё не написан.
- `server_worker` → `loging_service` end-to-end — `audit_client.py` покрыт unit,
  но в реальном стенде не проверен.
- `server_worker` → `server_service /internal/...` — `server_service_client.py`
  покрыт unit, end-to-end нет.
- Отказ introspect (timeout / 5xx → 503) cross-service — покрыт в unit (loging),
  но не во всех сервисах cross-service.
- Полный happy-path `POST /ipmi/power/reboot` через все 3 сервиса — есть только
  ручной запуск через swagger.
- PAT lifecycle cross-service: создание → использование в worker → ротация →
  отказ старого.

### TODO — cross-service

- [ ] `server_service → loging_service` — после `audit_service.py`.
- [ ] `server_worker → loging_service` end-to-end в compose-стенде.
- [ ] `server_worker → server_service /internal/...` end-to-end.
- [ ] `auth_service introspect ↔ server_service/loging_service` — отказ → 503.
- [ ] Полный happy-path `POST /ipmi/power/reboot` через 3 сервиса (формальный тест).
- [ ] PAT lifecycle cross-service.

---

## Инфраструктура тестов

- [x] Property-based testing (Hypothesis) — `secrets_hypothesis`,
      `ids_hypothesis`, `redaction_hypothesis`.
- [x] Migration tests (Alembic upgrade/downgrade) — auth_service.
- [x] Полный локальный скан вручную: `make scan` → `scripts/full-scan.sh`
      (тесты всех сервисов + ruff + опционально pip-audit/safety). CI/CD
      в репозитории намеренно отсутствует, регулярный прогон делает
      владелец этой командой.
- [ ] Coverage-репорт (pytest-cov) с порогом на сервис (например, ≥80%).
- [ ] Mutation testing (mutmut / cosmic-ray) на security-critical модулях
      (`secrets_service.py`, `core/security.py`, `redaction.py`).
- [ ] Performance benchmark suite: ingest throughput, query latency, introspect
      QPS — baseline + regression detection.
- [ ] Security scan: bandit / safety в составе ручного `make scan`
      (сейчас только pip-audit/safety опционально).
- [ ] Migration tests для loging_service и server_service (Alembic upgrade/downgrade
      на свежей БД).
- [ ] End-to-end test stack для server_worker (respx + fakeredis или реальные
      сервисы в compose).
