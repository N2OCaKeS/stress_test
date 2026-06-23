# secret_service

## Назначение

`secret_service` — сервис безопасного хранения и выдачи секретов: токенов и учётных данных для внешних систем (Jira, Confluence, Git и т.д.), внутренних интеграций и автоматизаций. Платформенный аналог Vault для DBOS Server Manager.

## Что хранит

Один тип сущности — **credential**. Каждый credential несёт пару `(login, secret)` для одного внешнего сервиса. Возможные примеры:

- персональный токен сотрудника к корпоративному Jira;
- общий departments-токен бота, который публикует автоматические отчёты;
- кросс-департаментный токен общего git-mirror'а, к которому несколько отделов имеют read-access.

## Модель данных

### Credential

| Поле | Тип | NULL | Описание |
|---|---|---|---|
| `id` | `str` | NO | `cred_<32 hex>` |
| `name` | `str(64)` | NO | Человекочитаемое имя (`"jira_reports_bot"`) |
| `service` | `str(64)` | NO | Имя внешнего сервиса (`"jira"`, `"confluence"`, `"git"`) |
| `scope` | `enum` | NO | `personal` \| `department` \| `cross_department` |
| `owner_user_id` | `str` | YES | Заполнено когда `scope=personal`. Soft-FK на `auth.users.id`. |
| `owner_dept_id` | `str` | YES | Заполнено когда `scope ∈ {department, cross_department}`. Soft-FK на `auth.departments.id`. |
| `login` | `Text` | YES | Plaintext. Может быть `NULL` если внешний сервис принимает только токен. |
| `secret_encrypted` | `Text` | NO | `v<ver>$<nonce>$<ct>` envelope (AES-256-GCM + HKDF + master-key). |
| `status` | `enum` | NO | `active` \| `blocked`. `blocked` → `GET` возвращает `410 GONE`. |
| `created_by` | `str` | NO | `usr_…`/`bot_…` (immutable). |
| `created_at` / `updated_at` | `timestamptz` | NO | Стандарт. |
| `blocked_at` / `blocked_reason` | `timestamptz` / `str` | YES | Заполняются при автоблокировке (delete owner, dep deleted, dep_revoke cascade). |
| `valid_from` / `valid_to` | `timestamptz` | YES | Окно валидности секрета (UTC). Применяется только в `reveal` — вне окна → `410 SECRET_NOT_YET_VALID` / `SECRET_EXPIRED`. Метаданные (GET без reveal) отдаются независимо от срока. NULL = open-ended с этой стороны. |

**CHECK инварианты**:
- `scope='personal'` ⇒ `owner_user_id IS NOT NULL AND owner_dept_id IS NULL`
- `scope IN ('department','cross_department')` ⇒ `owner_dept_id IS NOT NULL AND owner_user_id IS NULL`
- `length(secret_encrypted) < 8192`
- `secret_encrypted ~ '^v\d+\$'` (envelope format)
- `valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from` (окно валидности; одиночные NULL разрешены)

**UNIQUE**: `(COALESCE(owner_user_id, owner_dept_id), service, name) WHERE status='active'`. Soft-delete-friendly: hard delete + recreate с тем же `(owner, service, name)` — OK.

### RoleACL (per-credential доступ внутри одного департамента)

| Поле | Тип | NULL | Описание |
|---|---|---|---|
| `id` | `str` | NO | `acl_…` |
| `cred_id` | `str` | NO | FK → `credentials.id` |
| `dept_id` | `str` | NO | В каком департаменте действует разрешение. |
| `role_name` | `str(64)` | NO | Имя роли (по смыслу — из per-department каталога `auth.service_role_definitions`). **На входе НЕ валидируется** против каталога: принимается любая строка 1..64. Несуществующая роль просто не сматчит actor'а при access-check (мёртвый ACL). |
| `can_read` | `bool` | NO | Право на reveal. |
| `can_write` | `bool` | NO | Право на update/delete (только для `scope=department` и `cross_department`). |
| `granted_by_user_id` | `str` | NO | `usr_…`/`bot_…` (immutable, не cascade'ится при удалении granter'а). |
| `granted_at` | `timestamptz` | NO | |

**UNIQUE**: `(cred_id, dept_id, role_name)`.

**Применимость по scope**:
- `personal` — `dept_id = owner.department_id` всегда; владелец выдаёт `role_acl` другим в **своём** департаменте.
- `department` — `dept_id = owner_dept_id`; dep_admin владеющего департамента выдаёт acl.
- `cross_department` — `dept_id = recipient_dept_id`; **сначала** owner dep_admin выдаёт `DeptGrant` для recipient_dept, **затем** dep_admin recipient'а выдаёт `role_acl` внутри своего dep'а.

### DeptGrant (только для `scope=cross_department`)

| Поле | Тип | NULL | Описание |
|---|---|---|---|
| `id` | `str` | NO | `dgr_…` |
| `cred_id` | `str` | NO | FK → `credentials.id` |
| `recipient_dept_id` | `str` | NO | Кому открыт доступ. |
| `granted_by_user_id` | `str` | NO | Должен быть dep_admin владеющего dep'а или admin `secret_service` того же dep'а. |
| `granted_at` | `timestamptz` | NO | |

**UNIQUE**: `(cred_id, recipient_dept_id)`.

**Семантика**: `DeptGrant` — это «можно начинать выдавать `role_acl` внутри recipient_dept». Без `DeptGrant` локальный dep_admin recipient'а не может выдавать `role_acl` на эту креду (даже если та `cross_department`).

## Зоны видимости (`scope`)

### `personal`
- Владелец — пользователь.
- Управление (update content, delete): только владелец.
- Выдача `RoleACL`: только владелец, только в своём департаменте.
- Reveal: владелец + grantees через `RoleACL.can_read`.

### `department`
- Владелец — департамент.
- Управление: dep_admin владеющего dep'а + admin `secret_service` того же dep'а.
- Выдача `RoleACL`: dep_admin владеющего dep'а; `dept_id = owner_dept_id`.
- Reveal: grantees через `RoleACL.can_read`.

### `cross_department`
- Владелец — департамент (как и `department`).
- Cross-dep двухуровневый flow:
  1. Owner dep_admin создаёт `DeptGrant(cred_id, recipient_dept_id)`.
  2. Recipient dep_admin (в своём dep'е) создаёт `RoleACL(cred_id, recipient_dept_id, role, can_read)`.
- Управление и удаление: dep_admin владеющего dep'а + admin `secret_service` того же dep'а. Recipient dep_admin может только выдавать/отзывать собственные `RoleACL` внутри своего dep'а.

## Модель доступа

### Базовые проверки (в порядке исполнения)

1. **Department service-access**: `actor.department_id` имеет доступ к `secret_service` (`auth.department_service_access`). Без этого — `403`.
2. **Cred status**: `status='active'`. Если `blocked` — `410 GONE`.
3. **Scope-зависимая проверка**:
   - `personal`: `actor.user_id == cred.owner_user_id` ИЛИ существует `RoleACL(cred_id, actor.department_id, actor_role)` с нужным правом.
   - `department`: `actor.department_id == cred.owner_dept_id` AND существует `RoleACL(cred_id, actor.department_id, actor_role)` с нужным правом.
   - `cross_department`: существует `DeptGrant(cred_id, actor.department_id)` AND `RoleACL(cred_id, actor.department_id, actor_role)` с нужным правом. Если `actor.department_id == owner_dept_id` — `DeptGrant` не требуется (свой dep).

### Bot и PAT

Боты и PAT'ы проходят через стандартный `auth.introspect` (W11+). Видят те же ресурсы, к которым `introspect.allowed_services` + `service_roles` дают доступ:

- **PAT user_a**: identity actor_type=user, user_id=user_a → видит личные креды user_a + всё что доступно user_a по `RoleACL` в его dep'е.
- **Bot dep_a**: identity actor_type=bot, dept_id=dep_a → видит креды dep_a + cross_dep creds, к которым его dep имеет `DeptGrant` + `RoleACL`. **Не видит** ничьи personal creds (у бота нет user-identity).

### Service admin (per-(dept, service))

`admin` в `secret_service` — это per-(dept, service) роль из `service_role_definitions`, а **не** платформенный флаг. Она сидится автоматически при `grant_service_access(dep_X, secret_service)`. Носитель такой роли в dep_X может действовать **только над cred'ами своего dept'а** — cross-dept привилегий у роли нет.

Что разрешено `admin secret_service` в его dept'е:

- **читать** (без reveal) любую креду dept'а — personal владельцев из своего dept'а, department, и cross_department с `owner_dept_id` равным своему;
- **удалять** не-свою креду в своём dept'е с обязательным `reason: str` в body. Без `reason` → `422`. Эмитит `tokens.admin_override_delete` CRITICAL;
- **recover** blocked cred'у своего dept'а в окне 30 дней;
- **transfer ownership** blocked cred'ы своего dept'а (например, personal с удалённым owner-user → передать одному из grantees);
- **управлять каталогом ролей** своего сервиса в своём dept'е (`POST/PATCH/DELETE /api/auth/v1/departments/{dep}/services/secret_service/roles`). Системные роли `admin`/`guest` неизменяемы.
- **НЕ может изменять** content (`login`/`secret`) personal-кред живого владельца.
- **НЕ может** ничего делать с cred'ами других dept'ов — для cross-dept-аудита нужен отдельный design (например, кастомная роль `audit_officer`); пока такого механизма нет.

Cross-dep transfer удалённого owner_dept'а (когда `cred.owner_dept_id` больше не указывает на живой dep) — отдельный случай: см. §«Account admin».

### Account admin (платформенный)

`account_admin` (account-level admin в auth_service) — платформенный админ. По текущей политике (см. Memory `project-dbos-secrets-scope`) он **не имеет доступа к содержимому секретов** в штатном flow: gated от всех user-facing `/credentials/...` (list/get/create/update/delete/reveal) через `require_user_context` (`SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT`, т.к. у него `department_id=null` и нет secret-service-access), и в `access_service` не получает ни read, ни delete.

**Исключение — emergency transfer/recover.** Ровно две операции, `/credentials/{id}/transfer` и `/credentials/{id}/recover`, доступны account_admin'у как аварийный override. Без него blocked-кред'а с удалённым владеющим отделом (у которого больше нет живого service-admin'а) была бы невосстановима и ушла бы в hard-delete по 30-дневному sweep'у. Эти эндпоинты идут через `require_transfer_recover_context` (пропускает account_admin несмотря на отсутствие dep-service-access), а в `credential_service.transfer`/`.recover` есть отдельная ветка `_is_account_admin`. Узость гарантируется тем, что остальные `/credentials/...` остаются за `require_user_context` — emergency-путь НЕ открывает account_admin'у обычный CRUD/reveal. Audit таких операций несёт `actor_role: "account_admin"` + `emergency_override: true`.

## Lifecycle

### Owner deleted

#### `personal` cred, владелец = user

| Сценарий | Действие |
|---|---|
| `ban_user(user_a)` | Cred продолжает работать. Sole owner просто заблокирован в auth, но cred ему «принадлежит». |
| `delete_user(user_a)`, `RoleACL.count > 0` | Cred → `status=blocked`. admin `secret_service` того же dept'а может **transfer ownership** одному из grantees (в течение 30 дней). После 30 дней — hard delete по retention sweep. |
| `delete_user(user_a)`, `RoleACL.count == 0` | Hard delete сразу (некому передавать). |

#### `department` / `cross_department` cred, владелец = dep

| Сценарий | Действие |
|---|---|
| `revoke department_service_access(secret_service, dep_a)` | Cascade: все `DeptGrant` выданные dep_a И все `RoleACL` для recipient=dep_a — сразу `deleted`. Аудит `tokens.dept_revoke_cascade` CRITICAL. |
| `delete_dept(dep_a)`, где dep_a — owner | Cred → `status=blocked`, UI: `"contact platform admin"`. account_admin в окне 30 дней переназначает владельца через `POST /credentials/{id}/transfer` (emergency-override, см. §«Account admin») либо снимает блокировку через `/recover`. По истечении окна — hard delete по sweep'у. |
| `delete_dept(dep_b)`, где dep_b — recipient cross_dep | Cascade: `DeptGrant(cred, dep_b)` + все `RoleACL(cred, dep_b)` — сразу `deleted`. Аудит `tokens.dept_recipient_cascade` CRITICAL. |

#### Granter deleted

`RoleACL.granted_by_user_id` и `DeptGrant.granted_by_user_id` НЕ cascade'ятся. Решение остаётся в силе как принятое легитимным админом на тот момент. (Симметрия с auth W31 owner-decision.)

## API

URL prefix: `/api/secret/v1/`.

### Credentials CRUD

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `GET` | `/credentials` | reader (per scope) | Список с пагинацией (`limit`/`cursor`). `secret_encrypted` НЕ отдаётся, только метаданные. |
| `POST` | `/credentials` | operator+ | Создать. Body: `{name, service, scope, login?, secret_b64, owner_dept_id?}`. `secret_b64` — base64(plaintext), клиент кодирует `base64.b64encode(plaintext)` (декод → plaintext 1..8192 chars). Header `Idempotency-Key` поддерживается. UNIQUE `(owner, service, name)` → `409 NAME_DUPLICATE`. |
| `GET` | `/credentials/{id}` | reader (per scope) | Метаданные. Без secret. |
| `PATCH` | `/credentials/{id}` | owner / dep_admin / admin secret_service (per scope, own dept) | Изменить `name`, `login`, `secret_b64`. `secret_b64` — base64(plaintext); декод → повторно шифрует. |
| `DELETE` | `/credentials/{id}` | owner / dep_admin / admin secret_service (own dept) | Удалить. Если `scope=personal` и удаляет НЕ owner → требуется `reason` (admin override). Ответ — `200 OkResponse = { ok: true }`. |
| `POST` | `/credentials/{id}/reveal` | reader+can_read | Возвращает `{login, secret_b64}`. Эмитит audit CRITICAL/INFO (throttle). |
| `POST` | `/credentials/{id}/transfer` | admin secret_service владеющего dept'а / account_admin (emergency) | `{new_owner_user_id? \| new_owner_dept_id?, reason}` (ровно один owner + обязательный `reason`). Только для blocked creds. account_admin допущен как аварийный override (в т.ч. для кред'ы с удалённым владеющим отделом). |
| `POST` | `/credentials/{id}/recover` | admin secret_service владеющего dept'а / account_admin (emergency) | Снять `status=blocked`. Только в окне 30 дней. account_admin допущен как аварийный override. |

### RoleACL CRUD

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `POST` | `/credentials/{id}/acl` | owner (personal) / dep_admin (department/cross_dep recipient) | `{dept_id, role_name, can_read, can_write}`. Для cross_dep требует существующего `DeptGrant`. |
| `DELETE` | `/credentials/{id}/acl/{acl_id}` | owner / dep_admin | Revoke. Ответ — `200 OkResponse = { ok: true }`. |
| `GET` | `/credentials/{id}/acl` | reader+can_manage_acl | Список ACL. |

### UserACL CRUD (per-user доступ, только personal)

Поимённый доступ конкретному пользователю **разрешён только для личных секретов** (`scope=personal`): владелец personal-кред'ы пускает конкретный `user_id` на reveal/read (опц. write), не заводя для этого service-роль. Для `department`/`cross_department` доступ раздаётся только ролями (RoleACL) — user-ACL на них запрещён. Управляется тем же правом, что и RoleACL.

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `POST` | `/credentials/{id}/user-acl` | owner (personal) | `{user_id, can_read=true, can_write=false}`. Только `scope=personal` — иначе `422 USER_ACL_SCOPE_NOT_PERSONAL`. Выдача владельцу/себе → `422`. Дубль `(cred_id, user_id)` → `409 USER_ACL_DUPLICATE`. |
| `GET` | `/credentials/{id}/user-acl` | reader (как read) | Список user-ACL. |
| `DELETE` | `/credentials/{id}/user-acl/{acl_id}` | owner | Revoke. Ответ — `200 OkResponse = { ok: true }`. |

### DeptGrant CRUD (только `cross_department`)

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `POST` | `/credentials/{id}/dept-grants` | `department_admin` владеющего dep'а | `{recipient_dept_id}`. Гейт `grant_dept` — только `department_admin`, service-роль `admin` НЕ проходит (даёт лишь read-override). |
| `DELETE` | `/credentials/{id}/dept-grants/{grant_id}` | `department_admin` владеющего dep'а | Revoke. Cascade'ит `RoleACL(cred_id, dept_id=recipient_dept_id)`. Ответ — `200 OkResponse = { ok: true }`. |
| `GET` | `/credentials/{id}/dept-grants` | owner/recipient `department_admin` / admin secret_service владеющего dep'а (read-override) | Список. |

### Error codes

Стабильный каталог:

- `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT` — у dep'а нет access к secret_service.
- `404 CREDENTIAL_NOT_FOUND` — не существует ИЛИ cross-dep visibility miss.
- `410 CREDENTIAL_BLOCKED` — `status=blocked` с `blocked_reason`.
- `410 SECRET_NOT_YET_VALID` — reveal до `valid_from`. `details = { valid_from }`. Metadata GET по-прежнему 200.
- `410 SECRET_EXPIRED` — reveal после `valid_to`. `details = { valid_to }`. Metadata GET по-прежнему 200. Sweep автоматически НЕ удаляет expired-кред'ы — юзер сам решает.
- `409 NAME_DUPLICATE` — UNIQUE collision.
- `403 CREDENTIAL_ACCESS_DENIED` — actor не имеет прав (нет ACL).
- `422 ADMIN_OVERRIDE_REASON_REQUIRED` — admin override без `reason`.
- `422 DEPT_GRANT_REQUIRED` — попытка создать ACL для cross_dep без существующего `DeptGrant`.
- `429 RATE_LIMIT_EXCEEDED` — на `/reveal` и других чувствительных путях.

## Reveal + throttle

`POST /credentials/{id}/reveal` возвращает `{login, secret_b64}` (login plaintext, secret base64-кодированный plaintext).

**Throttle**: 5-минутное окно per `(actor_id, cred_id)`:

- Первый reveal в окне → audit `tokens.revealed` **CRITICAL**.
- Каждый последующий reveal в том же окне → audit `tokens.revealed_throttled` **INFO** с `count` в details.
- Окно скользящее, сбрасывается на новый reveal вне окна.

Симметрия с `server_service.server_account` (паттерн W26 + W39).

## Шифрование

- **Алгоритм**: AES-256-GCM + HKDF-SHA256.
- **Master key**: env-вар `SECRET_ENCRYPTION_KEY` (Kubernetes Secret при deploy). Симметрия с server_service. Старые версии ключа подгружаются через `SECRET_ENCRYPTION_KEY__v<N>` (legacy v1 — SHA-256 без HKDF; в production обязательно `SECRET_ENCRYPTION_KEY_VERSION >= 2`).
- **AAD**: `f"cred:{cred_id}"` — credential id входит в AAD, защита от swap-атак.
- **Envelope**: `v<ver>$<nonce>$<ciphertext>` — версия ключа в envelope, для будущей rotation. `<ver>` начинается с `1`.
- **Reveal**: secret base64-кодируется при выдаче из API. Это не дополнительное шифрование — это просто транспортный encoding, чтобы клиент не парсил Unicode.

## Audit catalog

Все события идут в `loging_service`. Severity (default):

| Action | Severity | Описание |
|---|---|---|
| `tokens.create` | INFO | |
| `tokens.update` | INFO | |
| `tokens.delete` | WARNING | Кроме admin override (CRITICAL). |
| `tokens.admin_override_delete` | **CRITICAL** | Обязательный `reason` в details. |
| `tokens.revealed` | **CRITICAL** | Первый reveal в окне throttle. |
| `tokens.revealed_throttled` | INFO | Повторные reveals в окне. `count` в details. |
| `tokens.dept_grant_added` | **CRITICAL** | |
| `tokens.dept_grant_revoked` | **CRITICAL** | |
| `tokens.dept_revoke_cascade` | **CRITICAL** | Cascade при revoke department_service_access. |
| `tokens.dept_recipient_cascade` | **CRITICAL** | Cascade при delete_dept(recipient). |
| `tokens.role_acl_added` | INFO | |
| `tokens.role_acl_revoked` | INFO | |
| `tokens.owner_user_deleted_block` | WARNING | Auto-блок при delete_user. |
| `tokens.owner_dept_deleted_block` | WARNING | Auto-блок при delete_dept. |
| `tokens.transfer_ownership` | **CRITICAL** | Transfer после блокировки. |
| `tokens.recover` | WARNING | Снятие блокировки. |
| `tokens.access_denied` | INFO | Reader-попытка без прав. |

## Безопасность

- Шифрование secret'ов в покое (AES-256-GCM + master key).
- Аудит каждого reveal, изменения, создания, удаления и cascade-операции.
- Маскирование `secret`/`login` в логах (`redact_payload` помечает поля).
- 5-минутный reveal-throttle защищает от UI-polling-флуда и затрудняет brute-force.
- Block + recover окно 30 дней — defense-in-depth для compliance / incident response.
- HSM/KMS — owner-decision на будущее (см. `project-dbos-org-and-deployment.md`).

## Интеграции

- `auth_service` — introspect identity, проверка `department_service_access`, каталог ролей (`ServiceRoleDefinition`).
- `loging_service` — аудит-события.
- `server_service` — может ходить за техническими токенами через свой `bot_token` (если bot имеет `RoleACL.can_read`).

## Технологии

- `Python 3.12`
- `FastAPI`
- `SQLAlchemy 2.0 async`
- `Pydantic v2`
- `PostgreSQL` (отдельный кластер)
- `Docker`
- `Kubernetes`

## Healthcheck

Два публичных endpoint'а под k8s probe'ы (без auth, без rate-limit).

### `GET /health` — liveness

Сервис жив, процесс не повис. Не трогает БД, не зовёт зависимости.

```json
{ "status": "ok", "timestamp": "2026-06-08T12:34:56.000+00:00" }
```

### `GET /ready` — readiness + counters

БД обязательна для зелёного `status=ok`; иначе `status=degraded` (HTTP всё
равно 200 — payload едет оператору, k8s reacts на тег). Redis и audit-counter
best-effort: их фейл не валит ready.

```json
{
  "status": "ok",                       // "ok" | "degraded"
  "timestamp": "2026-06-08T12:34:56.000+00:00",
  "db": true,                           // SELECT 1 + COUNT прошли
  "redis_connected": true,              // PING на reveal_throttle redis-клиент (timeout 0.5s)
  "secrets_total": 42,                  // SELECT COUNT(*) FROM credentials
  "blocked_total": 3,                   // SELECT COUNT(*) WHERE status='blocked'
  "audit_dropped_429_total": 0          // per-process; сколько событий отброшено после исчерпания retry на 429
}
```

`secrets_total` / `blocked_total` — общие по БД, одинаковы между pod'ами.
`audit_dropped_429_total` — per-process; при нескольких репликах суммирует
внешний агрегатор. На любой ошибке counter'а — `status=degraded` + WARNING в
лог, payload отдаётся с нулями.

## Развёртывание

- 2 реплики API в Kubernetes (`k8s/13-secret-service.yaml`).
- Отдельный PostgreSQL-кластер (`k8s/13-postgres-secret.yaml`).
- `SECRET_ENCRYPTION_KEY` (+ опциональные `SECRET_ENCRYPTION_KEY__v<N>` для legacy-версий) и `HKDF_SALT_HEX` берутся из k8s Secret'а `dbos-secrets`, монтируются в env.
- Внутренний доступ через сервисную сеть Kubernetes (ClusterIP `:8003`); наружу — через Ingress `https://<host>/api/secret/...`.

## Переменные окружения

Все алиасы соответствуют `src/core/config.py`. Default'ы безопасны для dev; в prod значения шифрования/auth обязательны и валидируются на старте.

### App

| Variable | Default | Что |
|---|---|---|
| `APP_ENV` | `local` | `local` / `dev` / `prod`. В `prod` включаются доп. инварианты (`SECRET_ENCRYPTION_KEY_VERSION >= 2`, `INTROSPECT_TLS_VERIFY=true`, HTTPS-guard). |
| `APP_HOST` | `0.0.0.0` | uvicorn bind host. |
| `APP_PORT` | `8003` | TCP-порт. |
| `APP_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |
| `APP_DEBUG` | `false` | `true` включает stack trace в HTTP-ответах. В prod — `false`. |

### Database

| Variable | Default | Что |
|---|---|---|
| `DATABASE_URL` | — (обязательно) | DSN `postgresql+asyncpg://user:pass@host:5432/secret_db`. |
| `DB_POOL_SIZE` | `10` | Размер базового пула asyncpg. |
| `DB_MAX_OVERFLOW` | `20` | Дополнительные соединения сверх пула. |

### Auth + introspect

| Variable | Default | Что |
|---|---|---|
| `AUTH_SERVICE_URL` | — (обязательно) | URL auth_service для `POST /api/auth/v1/introspect`. |
| `INTROSPECT_SERVICE_API_KEY` | — (обязательно) | Bearer для intra-cluster introspect. |
| `INTROSPECT_TLS_VERIFY` | `true` | В prod должно быть `true`. |
| `INTROSPECT_TLS_CA_BUNDLE` | `""` | Путь к CA-bundle для introspect, если internal CA. |
| `SERVICE_API_KEY` | — (исходящий) | Outbound API-key для походов в loging_service / server_service. |
| `SERVICE_API_KEYS` | — (входящий, JSON) | Карта `{identity: api_key}` входящих S2S-запросов. |

### Audit (loging_service)

| Variable | Default | Что |
|---|---|---|
| `LOGGING_SERVICE_URL` | — (обязательно) | URL loging_service. |
| `LOGGING_SERVICE_API_KEY` | — (обязательно) | API-key для записи audit-событий. |

### Encryption

| Variable | Default | Что |
|---|---|---|
| `SECRET_ENCRYPTION_KEY` | — (обязательно) | base64 32-байтный master-key AES-256-GCM. В prod — из k8s Secret. Legacy-версии: `SECRET_ENCRYPTION_KEY__v<N>`. |
| `SECRET_ENCRYPTION_KEY_VERSION` | `2` | Активная версия ключа; в prod должна быть >= 2 (v1 — legacy SHA-256 без HKDF). |
| `HKDF_SALT_HEX` | — (обязательно) | Hex-строка salt'а для HKDF-SHA256. Симметрия с server_service. |

### Rate-limit (slowapi)

| Variable | Default | Что |
|---|---|---|
| `SLOWAPI_RATE_LIMIT` | `500/minute` | Общий cap per-IP. |
| `RATE_LIMIT_STORAGE_URI` | `memory://` | `redis://...` в prod для шейринга между репликами. |
| `REDIS_PASSWORD` | `""` | Если задан, добавляется в DSN при `redis://` storage. |
| `RATE_LIMIT_REVEAL` | `5/minute` | Per-actor лимит на `POST /credentials/{id}/reveal`. |
| `RATE_LIMIT_TRANSFER` | `10/minute` | На `/transfer`. |
| `RATE_LIMIT_RECOVER` | `10/minute` | На `/recover`. |
| `RATE_LIMIT_DELETE` | `30/minute` | На `DELETE /credentials/{id}`. |
| `RATE_LIMIT_CREATE` | `60/minute` | На `POST /credentials`. |
| `RATE_LIMIT_DEPT_GRANT` | `20/minute` | На `/dept-grants` CRUD. |
| `RATE_LIMIT_ACL` | `30/minute` | На `/acl` CRUD. |

### Lockout

| Variable | Default | Что |
|---|---|---|
| `LOCKOUT_THRESHOLD` | `10` | Сколько denied-failures до lock'а. |
| `LOCKOUT_WINDOW_SECONDS` | `300` | Окно, в котором считаются failure'ы. |
| `LOCKOUT_DURATION_SECONDS` | `900` | Продолжительность lock'а. |

### Sweep + retention

| Variable | Default | Что |
|---|---|---|
| `BLOCKED_RETENTION_DAYS` | `30` | Сколько blocked-cred живёт до hard-delete sweep'ом. |
| `SWEEP_INTERVAL_SECONDS` | `3600` | Период фонового sweep'а. |
| `SWEEP_ENABLED` | `true` | Можно выключить sweep на dev/test. |

### Security

| Variable | Default | Что |
|---|---|---|
| `SECURITY_HSTS_ENABLED` | `false` | `true` в prod (за Ingress'ом с TLS). |

## Что НЕ делает сервис

- Не аутентифицирует пользователей (это `auth_service`).
- Не управляет ролями (это `auth_service.ServiceRoleDefinition`).
- Не хранит технические креды серверов (это `server_service.server_account`).
- Не публикует креды наружу — все reveals только по явному запросу с правами.
