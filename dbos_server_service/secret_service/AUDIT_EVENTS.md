# secret_service · audit events

Каталог событий, которые secret_service публикует в `loging_service` через `audit_service.emit(...)`. Источник истины — `src/services/audit_events.py` (`SERVICE_EVENTS` + `_DEFAULT_SEVERITY` таблица); этот документ дублирует таблицу для удобства ревью.

## Общие принципы

- Имя действия — `<object>.<verb>`. Для credentials используется префикс `tokens.*` (исторический выбор: одна сущность хранит `(login, secret)` пары для внешних токенов).
- Severity-дефолты задаются для пары `(action, status)` в `_DEFAULT_SEVERITY`. Failure-ось эскалируется вверх (CRUD → ERROR; reveal / transfer / dept-grant / cascade → CRITICAL).
- Severity в этой таблице — **дефолты сервиса**. `loging_service` может переопределить или подавить их своими rule'ами; финальный severity события определяет loging. Здесь зафиксирован срез из `_DEFAULT_SEVERITY` на момент регистрации событий.
- Поля envelope: `actor_id`, `actor_type` (`user` / `bot` / `oauth_client` / `service`), `actor_username`, `subject_id` (как правило `cred_id`), `subject_type`, `service`, `request_id`, `ip_address`, `user_agent`, `details` (action-specific, см. ниже), `status`, `severity`, `timestamp`.
- `details.secret` / `details.login` / `details.password` / `details.token` маскируются `redact_payload` перед отправкой (см. `src/services/redaction.py`).
- Health/ready НЕ логируются.

## Lifecycle

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `service.started` | success | INFO | `{ "version": "0.1.0" }`. Эмитится при старте lifespan'а. |

## HTTP middleware

`AuditAccessMiddleware` пишет outcome каждого HTTP-запроса. Применяется ко всем path'ам, кроме `/health` и `/ready`.

| Action | Status | Severity | Описание / payload |
|---|---|---|---|
| `http.client_error` | failure | WARNING | 4xx ответ (кроме 401/403). `details = { method, path, status_code, request_id }`. |
| `http.access_denied` | denied | CRITICAL | 401 или 403. `details = { method, path, status_code, error_code, request_id }`. Action и status выровнены с auth/server/loging — SIEM-запросы по `http.access_denied` ловят 401/403 и здесь. |
| `http.server_error` | failure | CRITICAL | 5xx ответ. `details = { method, path, status_code, request_id }`. |

## Credentials — CRUD

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `tokens.create` | success | INFO | `{ cred_id, name, service, scope, owner_user_id?, owner_dept_id? }`. `secret` маскируется. |
| `tokens.create` | failure | ERROR | `{ error_code, message, name, service, scope }`. |
| `tokens.update` | success | INFO | `{ cred_id, changed_fields }`. `changed_fields` — список из `name` / `login` / `secret`. |
| `tokens.update` | failure | ERROR | `{ error_code, message, cred_id }`. |
| `tokens.delete` | success | WARNING | `{ cred_id, scope, owner_user_id?, owner_dept_id? }`. |
| `tokens.delete` | failure | ERROR | `{ error_code, message, cred_id }`. |
| `tokens.admin_override_delete` | success | **CRITICAL** | `{ cred_id, scope, original_owner_user_id?, original_owner_dept_id?, reason }`. `reason` обязателен в request body. |
| `tokens.admin_override_delete` | failure | **CRITICAL** | `{ error_code, message, cred_id, reason }`. |

## Reveal + throttle

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `tokens.revealed` | success | **CRITICAL** | `{ cred_id, scope, throttle_window_seconds: 300 }`. Первый reveal в 5-мин окне per `(actor_id, cred_id)`. |
| `tokens.revealed` | failure | **CRITICAL** | `{ error_code, message, cred_id }`. Decrypt-провал тоже сюда. |
| `tokens.revealed_throttled` | success | INFO | `{ cred_id, count }`. Повторные reveals в окне. `count` — суммарное число reveals в текущем окне. |
| `tokens.revealed_blocked_by_validity` | failure | INFO | `{ cred_id, error_code: "SECRET_NOT_YET_VALID" \| "SECRET_EXPIRED", scope, service, valid_from?, valid_to? }`. Эмитится при reveal вне окна `[valid_from, valid_to]`. HTTP 410. Access-check (load_for_action) уже прошёл успешно — actor имел право на reveal, отбило именно окно валидности. |

## DeptGrant (cross_department flow)

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `tokens.dept_grant_added` | success | **CRITICAL** | `{ cred_id, recipient_dept_id, grant_id }`. |
| `tokens.dept_grant_added` | failure | **CRITICAL** | `{ error_code, message, cred_id, recipient_dept_id }`. |
| `tokens.dept_grant_revoked` | success | **CRITICAL** | `{ cred_id, recipient_dept_id, grant_id, cascaded_role_acls: N }`. |
| `tokens.dept_grant_revoked` | failure | **CRITICAL** | `{ error_code, message, cred_id, grant_id }`. |
| `tokens.dept_revoke_cascade` | success | **CRITICAL** | `{ dept_id, dept_grants_revoked: K, role_acls_revoked: M }`. Триггер — `revoke department_service_access`. |
| `tokens.dept_revoke_cascade` | failure | **CRITICAL** | `{ error_code, message, dept_id }`. |
| `tokens.dept_recipient_cascade` | success | **CRITICAL** | `{ dept_id, dept_grants_revoked: K, role_acls_revoked: M }`. Триггер — `delete_dept(recipient)`. |
| `tokens.dept_recipient_cascade` | failure | **CRITICAL** | `{ error_code, message, dept_id }`. |

## RoleACL

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `tokens.role_acl_added` | success | INFO | `{ cred_id, acl_id, dept_id, role_name, can_read, can_write }`. |
| `tokens.role_acl_added` | failure | ERROR | `{ error_code, message, cred_id, dept_id, role_name }`. |
| `tokens.role_acl_revoked` | success | INFO | `{ cred_id, acl_id, dept_id, role_name }`. |
| `tokens.role_acl_revoked` | failure | ERROR | `{ error_code, message, cred_id, acl_id }`. |
| `tokens.user_acl_added` | success | INFO | `{ cred_id, user_id, can_read, can_write }` (target_id = acl_id). |
| `tokens.user_acl_added` | failure | ERROR | `{ error_code, error_class, user_id }` (target_id = cred_id). |
| `tokens.user_acl_removed` | success | INFO | `{ cred_id, user_id }` (target_id = acl_id). |
| `tokens.user_acl_removed` | failure | ERROR | `{ error_code, error_class, cred_id }` (target_id = acl_id). |

## Owner deleted lifecycle

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `tokens.owner_user_deleted_block` | success | WARNING | `{ cred_id, owner_user_id, role_acls_count }`. Эмитится handler'ом `/internal/lifecycle/user-deleted` для personal cred с grantees. Orphan creds (no grants) идут как `tokens.delete`. Trigger: `auth_service.user_service.hard_delete_user` → `secret_service_client.notify_user_deleted` (best-effort POST). |
| `tokens.owner_dept_deleted_block` | success | WARNING | `{ cred_id, owner_dept_id, scope }`. Эмитится handler'ом `/internal/lifecycle/dept-deleted` для cred'ы, где dep — owner. Trigger: `auth_service.department_service.hard_delete_department` → `secret_service_client.notify_dept_deleted` (best-effort POST). |

## Ownership recovery

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `tokens.transfer_ownership` | success | **CRITICAL** | `{ cred_id, scope, old_owner_user_id?, old_owner_dept_id?, new_owner_user_id?, new_owner_dept_id?, override_actor_id }`. |
| `tokens.transfer_ownership` | failure | **CRITICAL** | `{ error_code, message, cred_id }`. |
| `tokens.recover` | success | WARNING | `{ cred_id, blocked_at, recovered_at }`. |
| `tokens.recover` | failure | ERROR | `{ error_code, message, cred_id }`. |

## Authorization

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `tokens.access_denied` | failure | INFO | `{ cred_id?, error_code, scope, attempted_action }`. Эмитится при `403 CREDENTIAL_ACCESS_DENIED` / `SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT` / scope-mismatch. Используется lockout-сервисом для счёта denied-попыток. |
| `tokens.lockout_triggered` | success | WARNING | `{ actor_id, denied_count, lockout_until }`. Per-actor блокировка после серии denied-попыток (защита от перебора). Срабатывает, когда счётчик `tokens.access_denied` превышает порог. |

## Re-encrypt outbox + ротация ключа

Проактивная ротация мастер-ключа: `secrets.reencrypt_*` — служебный поток ре-шифрации (outbox), `secrets.encryption_*` — действия ops-runner'а, `secrets.admin_encryption_*` — те же действия, но инициированные `account_admin` из UI (отдельные имена, чтобы SIEM различал s2s-runner и человека). Failure-ось процесса и ротации эскалируется до ERROR/CRITICAL.

| Action | Status | Severity | Payload (`details`) |
|---|---|---|---|
| `secrets.reencrypt_seed` | success | INFO | `{ pending_rows, from_key_version, to_key_version }`. Reencrypt-outbox засиден pending-строками после ротации мастер-ключа. |
| `secrets.reencrypt_process` | success | INFO | `{ batch_size, processed, remaining }`. Обработан батч outbox'а (decrypt → encrypt под активным ключом). |
| `secrets.reencrypt_process` | failure | ERROR | `{ error_code, message, batch_size }`. |
| `secrets.encryption_rotate` | success | **CRITICAL** | `{ new_key_version, seeded_rows }`. Rotation-runner ввёл новую версию мастер-ключа активной через keystore и засидил reencrypt-outbox (рантайм-ротация без простоя). |
| `secrets.encryption_rotate` | failure | **CRITICAL** | `{ error_code, message }`. |
| `secrets.encryption_retire` | success | **CRITICAL** | `{ retired_key_version }`. Rotation-runner убрал старую версию мастер-ключа из keystore после полной ре-шифрации (0 строк на версии). |
| `secrets.encryption_retire` | failure | **CRITICAL** | `{ error_code, message, key_version }`. |
| `secrets.admin_encryption_rotate` | success | **CRITICAL** | `{ new_key_version, seeded_rows }`. `account_admin` ввёл новую версию мастер-ключа активной через UI (`/admin/encryption/rotate`); keystore-bump + reencrypt-outbox seed. |
| `secrets.admin_encryption_rotate` | failure | **CRITICAL** | `{ error_code, message }`. |
| `secrets.admin_encryption_retire` | success | **CRITICAL** | `{ retired_key_version }`. `account_admin` убрал старую версию мастер-ключа из keystore через UI (`/admin/encryption/retire`) после полной ре-шифрации. |
| `secrets.admin_encryption_retire` | failure | **CRITICAL** | `{ error_code, message, key_version }`. |
