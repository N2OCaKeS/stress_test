# auth_service · реестр тестов

**Всего тестов**: актуальное число — в `../STATUS.md` / `../TEST_COVERAGE.md`. На момент последней синхронизации Test.md: ~155 файлов под `auth_service/tests/`, ~1535 `def test_*` (после параметризации ~1539 passed). Быстрый прогон (`make test-auth`) — ~80–100 сек на свежем стенде. E2E (`make test-auth-e2e`) добавляет ~22 теста и ~2–3 мин (поднимает `auth-service-e2e` + `fetch-cert` + `docker-registry` через профиль compose `e2e`).

Файл фиксирует структуру каталогов и состав групп. Перечень тестовых функций ниже — снимок, точечные имена и счётчики могут эволюционировать вместе с фичами; для актуальной картины смотреть `STATUS.md` / `TEST_COVERAGE.md` и сам код тестов.

## Запуск

```bash
# Из корня dbos_server_service:
make test-auth        # быстрые (TestClient + postgres), без e2e
make test-auth-e2e    # всё, включая e2e (docker-registry, fetch-cert, реальный сервер)

# Точечно:
docker compose -f auth_service/tests/docker-compose.test.yml run --rm test-runner \
    pytest tests/auth/test_login.py::test_login_lockout_after_5_failed_attempts -v
```

## Раскладка по каталогам

| Каталог | Файлы | Тесты | Зона |
|---|---:|---:|---|
| `auth/` | 13 | 89 | login / logout / refresh / me / health / token form / concurrency / admin guard / account-admin без департамента |
| `authorization/` | 3 | 58 | introspect, service-access, edge-кейсы, service API key |
| `bots/` | 19 | 125 | боты, bot-токены, ролевая модель, lifecycle, edge |
| `core/` | 20 | 337 | юниты: security, redaction, audit context, schemas, dependencies, IDs, utils, config |
| `db/` | 2 | 23 | Alembic up/down, инварианты колонок |
| `departments/` | 1 | 15 | CRUD департамента и грантов сервисов |
| `docker/` | 7 | 87 | docker-config, docker-token, edge (PEM/JWKS) |
| `e2e/` | 1 | 22 | реальный registry + auth-service-e2e |
| `error_format/` | 1 | 7 | единый envelope ошибок + X-Request-ID |
| `groups/` | 4 | 52 | группы, members, грант сервисов и ролей на группу |
| `middleware/` | 1 | 13 | rate limit на login/refresh/docker/token |
| `oauth2/` | 18 | 124 | clients, authorization_code, PKCE, redirect_uri, code replay, user-context |
| `services/` | 37 | 305 | services, service-roles, bulk-roles, audit pool, hardening, cross-dept, lockout helpers |
| `tokens/` | 4 | 17 | Personal Access Tokens (CRUD, expires_at, recreate-after-revoke, integrity-race) |
| `users/` | 19 | 224 | CRUD, ban/unban, reset-password, roles, group attach, list, permissions endpoint, identity/ban cache, sessions management |
| **итого** | **~155** | **~1535 + параметризация ≈ 1539** | |

> Числа в строках выше — ориентировочные снимки (`def test_*` через grep, файлы через `find`). Точные актуальные значения — в `STATUS.md` / `TEST_COVERAGE.md`; pytest-параметризация (`@pytest.mark.parametrize`, `hypothesis`) разворачивает их до passed-числа.

---

## auth/

### `auth/test_login.py` — `POST /api/auth/v1/login`

Успех, ошибки авторизации, lockout, валидация формата, request_id. Включает кейсы:

- `test_login_returns_tokens_and_identity`, `test_login_identity_contains_required_fields`
- `test_login_account_admin_has_no_service_access`, `test_login_regular_user_has_service_roles`
- `test_login_wrong_password_returns_401`, `test_login_unknown_user_returns_401`, `test_login_unknown_user_does_not_reveal_existence`
- `test_login_blocked_user_returns_403`, `test_login_banned_user_returns_403`
- `test_login_lockout_after_5_failed_attempts`, `test_login_lockout_response_contains_retry_after`, `test_login_succeeds_after_lockout_expires`
- `test_login_response_has_request_id_header`, `test_login_propagates_custom_request_id`
- `test_login_error_contains_required_fields`, `test_login_missing_password_returns_422`

### `auth/test_logout.py` — `POST /api/auth/v1/logout`

Ревокация refresh-token, идемпотентность, изоляция сессий.

### `auth/test_me.py` — `GET /api/auth/v1/me`

Идентичность из БД (не payload), 401-кейсы, истёкший JWT.

### `auth/test_refresh.py` — `POST /api/auth/v1/refresh`

Ротация, инвалидация старого, reuse-детектор, истёкшая сессия.

### `auth/test_health.py` — `/health` и `/ready`

200 / тело / отсутствие требований авторизации / не публикуют аудит.

### `auth/test_token_form.py` — `POST /api/auth/v1/token` (OAuth2 password)

Form-only endpoint (не в openapi), валидные/невалидные креды, JSON-body отвергается.

### `auth/test_account_admin_no_dept.py`

Логин, `/me`, refresh для пользователя без `department_id` (платформенные роли).

### `auth/test_admin_guard_revalidate.py`

Проверка, что бан/деактивация/демотинг/удаление oauth-клиента закрывают доступ на следующем запросе (revalidate из БД).

### `auth/test_concurrency.py`

CAS refresh-токенов: rotate true/false, reuse → revoke all, параллельные refresh через `asyncio.gather`, race vs reuse аудит, дубль role/ban.

---

## authorization/

### `authorization/test_introspect.py` — `/api/auth/v1/authorization/introspect` + `/service-access`

JWT/PAT/bot active/inactive, subject_type, allowed_services, service-access по dept и сервису.

### `authorization/test_introspect_edge.py`

Подделки JWT (HS512, чужой секрет), garbage/empty, PAT удалённого пользователя, забаненный/блокированный/удалённый user, отзыв роли/доступа департамента/платформенной роли «после JWT», auth-схема и service API key (legacy и strict-режим, soft fallback).

---

## bots/

### `bots/test_bots.py` — CRUD ботов и bot-токенов

Создание / dept-scope / PATCH / outputs не содержат raw secret / введение токенов в introspect.

### `bots/test_bot_create_edge.py`

Глобально-неактивный сервис, сервис вне `bot.allowed_services`, странности `grant_types`, удалённый oauth-клиент.

### `bots/test_bot_lifecycle.py`

Полный жизненный цикл бота, propagation department_id, потеря доступа после revoke, актор `bot` в аудите.

### `bots/test_bot_roles.py`

Назначение/отзыв ролей боту, dept-scope, фильтр по `effective_services`, каскад при удалении ServiceRoleDefinition.

### `bots/test_bot_token_scope.py`

CRUD bot-токенов в пределах департамента (account_admin / dept_admin / regular user / cross-dept запрет).

---

## core/ — юниты

БД либо не требуется, либо подменена; FastAPI тоже не поднимается.

### `core/test_security.py` и `test_security_edge.py`

Argon2id (hash/verify/malformed), JWT (round-trip, ttl, expires_delta, expired/invalid/decode), refresh/PAT/bot token shape, `hash_opaque_token`. Edge — нулевые/отрицательные deltas, prefix-инварианты, distinct prefixes.

### `core/test_redaction.py` и `test_redaction_hypothesis.py`

Маскирование password/token/secret/hash/credential ключей, JWT/Argon2/bcrypt-shaped значений, opaque `dbos_pat_*`/`dbos_bot_*`, идемпотентность, обрезка >2048, no-mutate, hypothesis-инварианты.

### `core/test_audit_context.py` и `test_audit_integration.py`

Контекст (set/reset/update, username/IP/UA/details), доверенные прокси (XFF leftmost-non-trusted, X-Real-IP fallback, CIDR allow-list), маскирование password в реальных эмитах (login/me/user.create/pat.create/http.access_denied).

### `core/test_schemas.py`

Pydantic-валидация: `UserCreate` / `UserUpdate` / `BanRequest` / `PasswordReset` / `RoleAssign` / `BotCreate` / `OAuthClientCreate` / `OAuthTokenRequest` / `LoginRequest`.

### `core/test_config_validation.py`

Production-валидация настроек: запрет placeholder SECRET_KEY, короткий service_api_key, дефолтный admin password, app_debug=True, HTTP logging URL и т.д.

### `core/test_dependencies_auth.py`

`get_bearer_token`, `get_current_identity`, `require_account_admin`, `require_admin_roles` — happy/error пути.

### `core/test_docker_jwt.py`

Production без `docker_rsa_private_key` → ValidationError; non-prod — эфемерный ключ + warning.

### `core/test_merge_permissions.py`

Объединение direct и group-source ролей, dedup, отсев при revoke, no-mutate.

### `core/test_cascades.py`

Каскады delete: department↔users/groups, user↔sessions/PATs/bans/service_roles, bot↔tokens/roles, service↔role_definitions.

### `core/test_ids_hypothesis.py`

`new_id(prefix)`: формат, уникальность (5000 итераций), не пересекается между prefix'ами; `hash_opaque_token` детерминирован и 64 hex.

### `core/test_main_extract_client_ip_wiring.py`

Wiring `extract_client_ip` в FastAPI middleware: проверка trusted/untrusted XFF в реальном HTTP-вызове.

### `core/test_utils.py`

`new_id`, пагинация (clamp offset/limit), `utcnow`, `expires_at` (minutes/days/combined), `is_expired` (naive/unix epoch/far future).

---

## db/

### `db/test_migrations.py`

Alembic upgrade head — успех / идемпотентность / содержит ожидаемые таблицы и колонки. Уникальность `(name, department_id)` для ServiceRoleDefinition. Downgrade один шаг и до base, полный цикл up→down→up.

---

## departments/

### `departments/test_departments.py` — `/api/auth/v1/departments`

CRUD департаментов, grant/revoke сервисов, dept_admin запрет, активный фильтр в листинге.

---

## docker/

### `docker/test_docker_config.py` — конфигурация registry на отдел

`enable` / `disable` / `pull_policy` / `restricted` + push_user_ids / dept-scope / get_config.

### `docker/test_docker_token.py` — `/api/auth/v1/docker/token`

Базовая auth (basic), отсутствие конфига → 403, pull_policy=all/restricted, push policy (actions из JWT), PAT и bot-токены как Docker password (включая ревокацию), `/certs` и `/jwks`.

### `docker/test_docker_edge.py`

PATCH partial / 422 / 404, basic-auth ошибки (нет header, bearer вместо basic, malformed base64, без двоеточия), `/certs` PEM, `/jwks` ключи.

---

## e2e/

### `e2e/test_docker_e2e.py` — реальный registry + auth-service-e2e

Запускается только профилем `e2e` (`@pytest.mark.e2e`). 22 теста: RegistryAvailability, TokenIssuance, FullAuthFlow, ScopeAccess, DisabledRegistry, PATAuth, BotTokenAuth, JWKS, RestrictedPullPolicy.

---

## error_format/

### `error_format/test_error_envelope.py`

Поля envelope: `error`, `error_code`, `message`, `details`, `request_id`, `timestamp`. 401/403/422, echo и автогенерация `X-Request-ID`, заголовок в успешном ответе.

---

## groups/

### `groups/test_groups.py` — группы пользователей и групповые роли

Создание/листинг/PATCH/delete, members, dept-scope, грант/revoke сервиса на группу, назначение/отзыв групповых ролей, видимость в листинге.

---

## middleware/

### `middleware/test_rate_limit.py`

Дефолтные квоты: login=10/min, refresh=30/min, docker/token=30/min. 429-envelope, health bypass, login quota не сжирает refresh. 429 не публикует http audit.

---

## oauth2/

### `oauth2/test_clients.py` — `/api/auth/v1/oauth2/clients`

Создание (`cs_`-prefix секрет), dept-scope, листинг, delete, client_credentials grant, authorization_code flow (302 → code → exchange), reuse code, redirect_uri mismatch, unsupported grant.

### `oauth2/test_authorization_code.py`

JWT после `authorization_code` exchange содержит `oauth_scopes`, login-JWT — нет. Introspect с узким scope возвращает только scoped service, после ревока live permission — пустое пересечение. Service-access denied для out-of-scope.

### `oauth2/test_authorization_oauth.py`

`actor_type` = `oauth_client` для cc-JWT, `user` для login-JWT. Introspect для cc-JWT: scopes ∩ dept-services, после delete client — inactive.

### `oauth2/test_authorize_edge.py`

`/authorize` без bearer / с битым bearer, percent-encoding state, лимит state=2048, `client.grant_types` без authorization_code → 403, истёкший code, `response_type=code|default|other`, валидация перед issuance.

### `oauth2/test_code_replay_race.py`

Атомарность обмена code: первый успех / второй invalid_grant, параллельные exchange ровно один success, PKCE c CAS skip → invalid_grant, `mark_used` true→false, CAS skip при изменённом `used_at`.

### `oauth2/test_pkce_and_redirect_uri.py`

PKCE S256/plain (full flow / mismatched verifier / missing verifier), unknown method, legacy без challenge, верификация redirect_uri (http не на localhost → 422, fragment → 422, ftp → 422, https / 127.0.0.1 / mixed list).

### `oauth2/test_user_context_reject.py`

cc-JWT не пускает в user-эндпоинты: `/authorize`, `/me`, create user, create PAT. Introspect принимает cc-JWT, штатные user-JWT по-прежнему 200/201.

> `refresh_token` grant в `oauth2/token` намеренно не реализован — тестов нет.

---

## services/

### `services/test_services.py` — `/api/auth/v1/services`

CRUD платформенных сервисов, каскады: при delete сервиса отделы теряют доступ, user_service_roles деактивируются.

### `services/test_service_roles.py` — ролевая модель сервисов

CRUD ролей, дубликаты, `service_admin` может создать роль, кастомные роли, `admin` сидится при выдаче сервиса департаменту, валидация `RoleAssign`.

### `services/test_bulk_roles.py` и `test_bulk_roles_edge.py`

Bulk-assign/revoke, dept-acccess, инициальные роли при create user, атомарность при cross-dept user в батче.

### `services/test_audit_pool.py`

HTTP-pool для audit-emitter переиспользуется (одна `AsyncClient`), fallback на per-call, lifespan init/shutdown/skip.

### `services/test_cross_dept_retention.py`

Сохранение/сброс ролей при переносе пользователя между департаментами и при revoke dept-service.

### `services/test_security_hardening.py`

Кросс-tenant audit `denied`, redaction в `AppException.details`, timing-выравнивание logon неизвестного user (argon2-verify), валидация PAT (scope/past expiry), security headers и HSTS, rate-limit на introspect, OAuth token bad/missing grant_type, dept-transfer roles purge audit, `SERVICE_API_KEYS` (dual/strict/legacy fallback).

---

## tokens/

### `tokens/test_pat.py` — Personal Access Tokens

Создание (`dbos_pat_`-prefix), показ ровно один раз, листинг без секретов, scope «свои», double-revoke 409, PAT в introspect (active/inactive после revoke).

---

## users/

### `users/test_create.py` — `POST /api/auth/v1/users`

Admin/dept_admin создают user в своём dept, cross-dept запрет, dept_admin не может создать в чужом отделе, дубликат → 409, несуществующий dept → 404, regular user без dept → 422, regular user не может создавать, без auth → 401.

### `users/test_update.py` — `PATCH /api/auth/v1/users/{id}`

PATCH email/status/department, cross-dept запрет, 404, 422 на email.

### `users/test_ban.py` и `test_ban_edge.py`

Ban/unban, лог-кейсы (banned не залогинится, refresh fails, duplicate 409, unban-перезалогинивание, ban revoke sessions, dept-scope, 404). Edge: temporary с future expires, past expires → auto-unban на логине, CAS skip при уже снятом бане, audit `user.unban` system actor, реактивация PAT при unban, валидация `BanRequest` (past/now/missing expires_at).

### `users/test_reset_password.py`

Сброс админом, новый пароль работает / старый нет, revoke sessions, regular user не может, 404.

### `users/test_roles.py`

Назначение ролей admin / dept_admin, cross-dept, `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT`, regular user, 404.

### `users/test_list_users.py` — `GET /api/auth/v1/users[?department_id=]`

Account_admin листает любой dept; dept_admin — только свой; regular user → 403; unauth → 401; пустой dept → `[]`.

### `users/test_permissions_endpoint.py` — `GET /users/{id}/permissions`

Account_admin / dept_admin / self / 404, response содержит direct + group роли, account_admin имеет пустой effective layer, audit emit на success/denied.

### `users/test_user_groups_attach.py`

Account_admin/dept_admin/regular user добавляют/удаляют участников группы в пределах dept, cross-dept запрет, 404 на удаление не-члена, удаление последнего члена не убивает группу.

### `users/test_identity_ban_cache.py`

`actor_type=user` в audit, валидация `platform_role`, переход banned→blocked (деактивирует ban, audit status_change), unban реактивирует system-revoked PAT (не user-revoked), introspect inactive для забаненного, identity-cache TTL и invalidate на ban.

---

## Инфраструктура

`tests/conftest.py` обеспечивает:

- **Тестовая БД** — `auth_db_test` в контейнере `tests-test-postgres-1`. Схема пересоздаётся раз на сессию (`DROP SCHEMA public CASCADE` → `alembic upgrade head`).
- **Изоляция тестов** — внешняя транзакция + SAVEPOINT, откатывается на teardown каждого теста. `await db.commit()` внутри сервиса не сохраняется между тестами.
- **`AsyncClient`** — `httpx.AsyncClient(transport=ASGITransport(app=app))` поверх свежего FastAPI-приложения с подменённой зависимостью `get_db` → тестовая сессия.
- **Готовые сущности**:
  - `account_admin` / `admin_token` — глобальный администратор (`t_admin / Admin1234!`)
  - `dept_a`, `dept_b` — отделы
  - `service_x` — платформенный сервис с дефолтными ролями `admin/operator/reader/guest`
  - `dept_a_with_service` — dept_a с грантом на service_x
  - `user_a` / `user_a_token` — пользователь dept_a с ролью `reader` на service_x
  - `dept_admin_a` / `dept_admin_a_token` — администратор dept_a
  - `user_b` / `user_b_token` — пользователь dept_b (без доступа к service_x)
  - `dept_admin_b` / `dept_admin_b_token`
  - `docker_registry_enabled` — DepartmentDockerRegistry для dept_a с `pull_policy=all`

## Что НЕ покрыто

- `refresh_token` grant в `oauth2/token` — не реализован в `src/services/oauth_service.py`.
- E2E с docker-registry требует профиля `e2e` в `docker-compose.test.yml`, поэтому пропускается в обычном `make test-auth`.
- Производительность / нагрузочные — не входят в этот реестр.
- Точечные coverage-gap'ы из аудитных волн фиксируются в `obsidian/TODO.md` (секции `## 🔎 Аудит кода …`), Test.md их пофайлово не дублирует — `STATUS.md` / `TEST_COVERAGE.md` остаются единственным актуальным срезом покрытия.
