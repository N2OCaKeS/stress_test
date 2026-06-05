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

**CHECK инварианты**:
- `scope='personal'` ⇒ `owner_user_id IS NOT NULL AND owner_dept_id IS NULL`
- `scope IN ('department','cross_department')` ⇒ `owner_dept_id IS NOT NULL AND owner_user_id IS NULL`
- `length(secret_encrypted) < 8192`
- `secret_encrypted ~ '^v\d+\$'` (envelope format)

**UNIQUE**: `(COALESCE(owner_user_id, owner_dept_id), service, name) WHERE status='active'`. Soft-delete-friendly: hard delete + recreate с тем же `(owner, service, name)` — OK.

### RoleACL (per-credential доступ внутри одного департамента)

| Поле | Тип | NULL | Описание |
|---|---|---|---|
| `id` | `str` | NO | `acl_…` |
| `cred_id` | `str` | NO | FK → `credentials.id` |
| `dept_id` | `str` | NO | В каком департаменте действует разрешение. |
| `role_name` | `str(64)` | NO | Имя роли из `auth.service_role_definitions` (per-department каталог). |
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
| `granted_by_user_id` | `str` | NO | Должен быть dep_admin владеющего dep'а (или service_admin). |
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
- Управление: dep_admin владеющего dep'а + service_admin.
- Выдача `RoleACL`: dep_admin владеющего dep'а; `dept_id = owner_dept_id`.
- Reveal: grantees через `RoleACL.can_read`.

### `cross_department`
- Владелец — департамент (как и `department`).
- Cross-dep двухуровневый flow:
  1. Owner dep_admin создаёт `DeptGrant(cred_id, recipient_dept_id)`.
  2. Recipient dep_admin (в своём dep'е) создаёт `RoleACL(cred_id, recipient_dept_id, role, can_read)`.
- Управление и удаление: dep_admin владеющего dep'а + service_admin. Recipient dep_admin может только выдавать/отзывать собственные `RoleACL` внутри своего dep'а.

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

### Service admin

`service_admin` (роль с правом `*` в `secret_service`) — может:

- читать (без reveal) любую креду в любом scope для аудита;
- **удалять** любую креду — с обязательным `reason: str` в body. Без `reason` → `422`. Эмитит `tokens.admin_override_delete` CRITICAL;
- **transfer ownership** (только эти случаи):
  - personal cred, владелец удалён, `RoleACL.count > 0` → передать одному из grantees;
  - cross_dep cred, owner_dept удалён → передать другому dep (любому). Берёт на себя `account_admin` (см. ниже).
- **НЕ может изменять** content (`login`/`secret`) personal-кред живого владельца. Только `delete` + `transfer`.

### Account admin (платформенный)

`account_admin` (account-level admin в auth_service) — нужен только для случая **удалённого владеющего dep'а** в cross_department-credах. Он переназначает владельца на другой dep. До переназначения cred находится в `status=blocked` и `GET` возвращает `410 GONE` с message `"contact platform admin"`.

## Lifecycle

### Owner deleted

#### `personal` cred, владелец = user

| Сценарий | Действие |
|---|---|
| `ban_user(user_a)` | Cred продолжает работать. Sole owner просто заблокирован в auth, но cred ему «принадлежит». |
| `delete_user(user_a)`, `RoleACL.count > 0` | Cred → `status=blocked`. service_admin может **transfer ownership** одному из grantees (в течение 30 дней). После 30 дней — hard delete по retention sweep. |
| `delete_user(user_a)`, `RoleACL.count == 0` | Hard delete сразу (некому передавать). |

#### `department` / `cross_department` cred, владелец = dep

| Сценарий | Действие |
|---|---|
| `revoke department_service_access(secret_service, dep_a)` | Cascade: все `DeptGrant` выданные dep_a И все `RoleACL` для recipient=dep_a — сразу `deleted`. Аудит `tokens.dept_revoke_cascade` CRITICAL. |
| `delete_dept(dep_a)`, где dep_a — owner | Cred → `status=blocked`. `account_admin` в окне 30 дней может **transfer ownership** другому dep. После 30 дней — hard delete. UI: `"contact platform admin"`. |
| `delete_dept(dep_b)`, где dep_b — recipient cross_dep | Cascade: `DeptGrant(cred, dep_b)` + все `RoleACL(cred, dep_b)` — сразу `deleted`. Аудит `tokens.dept_recipient_cascade` CRITICAL. |

#### Granter deleted

`RoleACL.granted_by_user_id` и `DeptGrant.granted_by_user_id` НЕ cascade'ятся. Решение остаётся в силе как принятое легитимным админом на тот момент. (Симметрия с auth W31 owner-decision.)

## API

URL prefix: `/api/secret/v1/`.

### Credentials CRUD

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `GET` | `/credentials` | reader (per scope) | Список с пагинацией (`limit`/`cursor`). `secret_encrypted` НЕ отдаётся, только метаданные. |
| `POST` | `/credentials` | operator+ | Создать. Body: `{name, service, scope, login?, secret, owner_dept_id?}`. Header `Idempotency-Key` поддерживается. UNIQUE `(owner, service, name)` → `409 NAME_DUPLICATE`. |
| `GET` | `/credentials/{id}` | reader (per scope) | Метаданные. Без secret. |
| `PATCH` | `/credentials/{id}` | owner / dep_admin / service_admin (per scope) | Изменить `name`, `login`, `secret`. На `secret` — повторно шифрует. |
| `DELETE` | `/credentials/{id}` | owner / dep_admin / service_admin | Удалить. Если `scope=personal` и удаляет НЕ owner → требуется `reason` (admin override). |
| `POST` | `/credentials/{id}/reveal` | reader+can_read | Возвращает `{login, secret_b64}`. Эмитит audit CRITICAL/INFO (throttle). |
| `POST` | `/credentials/{id}/transfer` | service_admin / account_admin | `{new_owner_user_id?, new_owner_dept_id?}`. Только для blocked creds с grants. |
| `POST` | `/credentials/{id}/recover` | service_admin / account_admin | Снять `status=blocked`. Только в окне 30 дней. |

### RoleACL CRUD

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `POST` | `/credentials/{id}/acl` | owner (personal) / dep_admin (department/cross_dep recipient) | `{dept_id, role_name, can_read, can_write}`. Для cross_dep требует существующего `DeptGrant`. |
| `DELETE` | `/credentials/{id}/acl/{acl_id}` | owner / dep_admin | Revoke. |
| `GET` | `/credentials/{id}/acl` | reader+can_manage_acl | Список ACL. |

### DeptGrant CRUD (только `cross_department`)

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `POST` | `/credentials/{id}/dept-grants` | owner dep_admin / service_admin | `{recipient_dept_id}`. |
| `DELETE` | `/credentials/{id}/dept-grants/{grant_id}` | owner dep_admin / service_admin | Revoke. Cascade'ит `RoleACL(cred_id, dept_id=recipient_dept_id)`. |
| `GET` | `/credentials/{id}/dept-grants` | owner dep_admin / recipient dep_admin / service_admin | Список. |

### Error codes

Стабильный каталог:

- `403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT` — у dep'а нет access к secret_service.
- `404 CREDENTIAL_NOT_FOUND` — не существует ИЛИ cross-dep visibility miss.
- `410 CREDENTIAL_BLOCKED` — `status=blocked` с `blocked_reason`.
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
- **Master key**: env-вар `SECRETS_MASTER_KEY_B64` (Kubernetes Secret при deploy). Симметрия с server_service.
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

## Развёртывание

- 2 реплики API в Kubernetes.
- Отдельный PostgreSQL-кластер (2 реплики).
- `SECRETS_MASTER_KEY_B64` через Kubernetes Secret, монтируется в env.
- Внутренний доступ через сервисную сеть Kubernetes.

## Что НЕ делает сервис

- Не аутентифицирует пользователей (это `auth_service`).
- Не управляет ролями (это `auth_service.ServiceRoleDefinition`).
- Не хранит технические креды серверов (это `server_service.server_account`).
- Не публикует креды наружу — все reveals только по явному запросу с правами.
