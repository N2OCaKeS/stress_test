# AUDIT_EVENTS — server_service

Канонический список audit-событий, которые эмитит `server_service` в
`loging_service`. Источник истины — `src/services/audit_events.py:SERVICE_EVENTS`.
На startup сервис POST'ит этот список в
`loging_service POST /api/logging/v1/services/server_service/events`,
чтобы loging_service знал severity-defaults для пар `(action, status)`.

**Severity:** `default_severity` относится к `status=success`. Для
`status=denied`/`failure` правила в loging_service дефолтят severity
самостоятельно (обычно WARNING / CRITICAL). Сам severity может быть
override'нут на стороне loging_service через rule-движок.

Все события используют схему naming: `<entity>.<verb>` (compound entity
имена остаются как один сегмент: `server_account.view_password`,
`ipmi_controller.credentials_rotated_callback`). HTTP middleware —
`http.<outcome>`. Lifecycle — `service.<verb>`. Internal degradation —
`internal.<reason>`.

Source-of-truth — `src/services/audit_events.py::SERVICE_EVENTS`.

---

## Lifecycle

| action | default_severity | эмитится при | target_type | детали (`details`) |
|---|---|---|---|---|
| `service.started` | INFO | старт приложения (`_run_startup_audit_sequence` в `main.py`) | `service` | `actor_type=system`, `actor_id=server_service`, `request_id=startup-<pid>`. Не пользовательское — lifecycle |

---

## HTTP middleware

Эмитятся в `main.py::audit_access` middleware на основе HTTP-кода ответа.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `http.access_denied` | CRITICAL | response 401 / 403 | `http_endpoint` (path) | `method`, `path`, `status_code` |
| `http.client_error` | WARNING | response 4xx (кроме 401/403, 429 исключён) | `http_endpoint` | `method`, `path`, `status_code` |
| `http.server_error` | CRITICAL | response 5xx | `http_endpoint` | `method`, `path`, `status_code` |
| `http.platform_admin_blocked` | WARNING | `platform_admin_guard` middleware отбил `account_admin`/`loging_admin` 403 (loging_reader **не** блокируется) | `http_endpoint` | `platform_role`, `method`, `path`, `reason=platform_admin_business_data_blocked` |

> **`429` (rate-limit) в audit не пишется** — by design, anti-amplification.
> Брутфорс оставляет только HTTP access-log.

---

## Server (CRUD + power)

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `server.create` | INFO | INSERT в `servers` | `server` | `hostname`, `ip_address`, `department_id` (whitelist в audit_service) |
| `server.view` | INFO | denied на GET /{id} (cross-dept / nonexistent) — success на read не аудитим (шум) | `server` | `reason in {not_found_or_cross_dept, cross_department}` |
| `server.view_drift` | INFO | GET `/servers/{id}/drift` — агрегированная сводка drift-событий | `server` | `department_id`, `since`, `count`, `truncated`. denied: `reason in {permission_denied, not_found_or_cross_dept}` |
| `server.update` | INFO | UPDATE через PATCH | `server` | поля diff'а (whitelist), `department_id` |
| `server.delete` | CRITICAL | hard-delete + CASCADE | `server` | `department_id` |
| `server.power_on` | WARNING | dispatch `power.on` в worker | `server` | `task_id`, `task_kind=power.on`, `department_id`. denied/failure: `reason in {not_found_or_cross_dept, no_view_permission, permission_denied, decommissioned, no_ipmi, idempotent_conflict, worker_unreachable}` |
| `server.power_off` | WARNING | dispatch `power.off` — всегда hard ForceOff (никакого graceful/ACPI shutdown'а) | `server` | как `power_on` |
| `server.power_reboot` | WARNING | dispatch `power.reboot` | `server` | как `power_on` |
| `server.power_status` | INFO | POST `/servers/{id}/power/status` — dispatch live BMC-probe `power.status` через worker'а (Redfish/ipmitool); парный к `server.power_status_cached` (GET-вариант, без worker'а) | `server` | `task_id`, `task_kind=power.status`, `department_id`. denied/failure: как у других power-операций |
| `server.inventory_sync` | INFO | dispatch `inventory.sync` (SSH-probe) | `server` | `task_id`, `task_kind=inventory.sync` |
| `server.prepare` | CRITICAL | POST `/servers/{id}/prepare` — dispatch бутстрапа управления (`server.prepare`: useradd management-user + authorized_keys) | `server` | `task_id`, `task_kind=server.prepare`, `department_id` |
| `server.power_status_cached` | INFO | GET `/servers/{id}/ipmi/power` — чтение поля `servers.power_state` из БД без BMC-probe (cached-view для dashboard'ов); парный к `server.power_status` (POST-вариант, дёргает worker'а) | `server` | `department_id`, `power_state`. denied: `reason in {permission_denied, not_found_or_cross_dept}` |
| `server.acquire` | INFO | POST `/servers/{id}/busy` — успех захвата (busy_state → busy) | `server` | `department_id`, `purpose`, `lease_until`. denied/failure: `reason in {not_found_or_cross_dept, permission_denied, decommissioned, already_busy}` |
| `server.release` | INFO | DELETE `/servers/{id}/busy` — успех освобождения (busy_state → free) | `server` | `department_id`, `previous_user_id`. denied/failure: `reason in {not_found_or_cross_dept, permission_denied, not_busy, race_already_free}` |
| `server.update_os_version` | INFO | POST `/servers/{id}/os-sync` — ручной апдейт `os_version_id` без inventory sync | `server` | `department_id`, `previous_os_version_id`, `new_os_version_id`. denied/failure: `reason in {not_found_or_cross_dept, permission_denied, invalid_os_version}` |

---

## Server / IPMI worker callbacks (write-direction internal API)

Worker → server_service эмиты от `/internal/*` endpoint'ов, которые
worker дёргает после реальной работы.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `server.inventory_received` | INFO | POST `/internal/servers/{id}/inventory` — worker отдал hardware-facts после `inventory.sync` | `server` | `server_id`, `department_id`, `cpu_brand`, `cpu_model`, `os_version`, `disks`. denied: `reason=permission_denied` |
| `server.prepared` | CRITICAL | POST `/internal/servers/{id}/prepared` — worker подтвердил завершение бутстрапа управления (callback помечает `is_managed`) | `server` | `server_id`, `department_id`. denied/failure: `reason in {permission_denied, server_not_found, actor_department_mismatch}` |
| `server_account.users_inventory_received` | INFO | POST `/internal/servers/{id}/users-inventory` — worker отдал OS-user-инвентаризацию, reconcile против `server_accounts` (callback) | `server_account` | `server_id`, `department_id`, `created` (новые discovered), `present` (подтверждены на боксе), `drifted` (attribute-drift + discovered + missing), `found` (всего на боксе) |
| `server_account.drift_detected` | WARNING | reconcile инвентаризации обнаружил расхождение состояния на боксе с БД. БД — источник истины, поля аккаунта НЕ перетираются; обновляется только presence связки | `server_account` | `server_id`, `login`, `department_id`, `drift in {attributes, missing_on_box, unknown_login}`; для `attributes` — `fields` (список разошедшихся) и `expected`/`found` по каждому полю |
| `server_account.provision_status` | INFO | POST `/internal/servers/{id}/accounts/{aid}/provision-status` — worker отдал результат useradd/usermod/userdel, обновлён `present_on_server` (callback) | `server_account` | `server_id`, `account_id`, `department_id`, `operation`, `present_on_server` |
| `ipmi_controller.credentials_rotated_callback` | WARNING | POST `/internal/ipmi-controllers/{id}/credentials_rotated` — worker подтвердил ротацию (отдал plaintext, server_service зашифровал) | `ipmi_controller` | success: `server_id`, `controller_id`, `department_id`. denied/failure: `reason in {permission_denied, server_not_found, actor_department_mismatch, rotated_at_too_old}` |
| `secrets.reencrypt_batch` | INFO | POST `/internal/secrets/reencrypt_batch` — фоновая ротация мастер-ключа (`processed=0 ∧ errors>0` → status=failure для эскалации severity) | `secret` | `limit`, `processed`, `errors` |
| `secrets.reencrypt_seed` | INFO | POST `/internal/secrets/reencrypt_outbox/seed` — скан owner-таблиц, вставка pending outbox-row для row'ов с устаревшей версией ciphertext | `secret` | `inserted`, `scanned`, `active_version`, `limit` |
| `secrets.reencrypt_done` | INFO | POST `/internal/secrets/reencrypt_outbox/{id}/done` — worker подтвердил, outbox-row закрыт, owner-row перешифрован активной версией ключа. `skipped=true` если owner-row пропал / уже перешифровался параллельно (в этом случае service-layer дополнительно эмитит парный `secrets.migration.skipped` с `status="warning"` и точным `reason`) | `secret` | `outbox_id`, `status`, `skipped` |
| `secrets.reencrypt_failed` | WARNING | POST `/internal/secrets/reencrypt_outbox/{id}/failed` — worker не смог закрыть outbox-row (decrypt/encrypt error), row помечен `failed` | `secret` | `outbox_id`, `status` |
| `secrets.reencrypt_outbox_cleanup` | INFO | POST `/internal/secrets/reencrypt_outbox/cleanup` — удаление `done`-row'ов старше `older_than_hours` (bounded growth таблицы) | `secret` | `deleted`, `older_than_hours` |
| `secrets.migration.skipped` | WARNING | `secrets_migration_service.finalize_done` — outbox-row уже закрыт другой ветвью (`status_not_processing`), либо owner-row пропал/перетёрся параллельно (`owner_vanished`, `owner_ciphertext_changed`). Идемпотентность сохранена, но факт требует видимости в SIEM | `secrets_reencrypt_outbox` | `reason in {status_not_processing, owner_vanished, owner_ciphertext_changed}`, `entity_type`, `entity_id`, `current_status` (для `status_not_processing`) |

---

## Server account / IPMI controller — секреты (raw decrypt)

Эмитятся при показе расшифрованных секретов через `/internal/*` endpoints
для worker'а, а также при user-initiated ротации пароля аккаунта.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `server_account.view_password` | WARNING | GET `/internal/servers/{id}/accounts/{aid}/password` — расшифрован и отдан plaintext | `server_account` | `server_id`, `account_id`, `department_id` |
| `server_account.password_revealed` | CRITICAL | GET `/api/server/v1/server-accounts/{id}` вызывающим с `view_password` — пароль расшифрован и доставлен в `password_b64` (services/server_account.get_account → _reveal_account_password) | `server_account` | `server_id`, `login`, `department_id`. failure: `reason in {no_password_stored, decrypt_failed}` |
| `server_account.rotate_password` | CRITICAL | (a) POST `/api/server/v1/server-accounts/{id}/rotate_password` — user-initiated ротация записи в БД без SSH apply (services/server_account.py); (b) POST `/internal/servers/{id}/accounts/{aid}/password/rotate` — worker положил новый ciphertext после SSH apply (internal_service) | `server_account` | `server_id`, `login`, `rotated_at`; для (a) дополнительно `department_id`, `reason=user_initiated`. denied/failure: `reason in {permission_denied, not_found_or_cross_dept, account_not_found}` |
| `ipmi_controller.view_credentials` | WARNING | GET `/internal/servers/{id}/ipmi/credentials` — расшифрованы IPMI-creds | `ipmi_controller` | `server_id`, `department_id` |
| `ipmi_controller.credentials_revealed` | CRITICAL | GET `/api/server/v1/servers/{id}/ipmi` вызывающим с `view_credentials` — BMC-пароль расшифрован и доставлен в `password_b64` (services/ipmi_controller.get_controller → _reveal_controller_password) | `ipmi_controller` | `server_id`, `username`, `department_id`. failure: `reason in {no_password_stored, decrypt_failed}` |

---

## Worker-dispatch (admin-initiated rotation)

Public endpoint'ы, через которые user (обычно admin) запускает ротацию
паролей. Сам rewrite в БД делает worker через callback'и выше.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `server_account.rotate_password_dispatch` | CRITICAL | POST `/api/server/v1/server-accounts/{id}/rotate` — dispatch SSH-rotation task. Эмитится один агрегированный success на запрос (даже при частичных пропусках в массовом режиме); per-server-фейлы (idempotent-конфликт) идут отдельными failure-эмитами, worker-unreachable отбивает весь батч | `server_account` | success: `mode`, `task_kind=account.rotate_password`, `task_ids`, `server_ids` (только реально поставленные), `dispatched`, `skipped` (список `{server_id, reason}`), `skipped_count`, `login`, `department_id` |
| `server_account.provision` | WARNING | POST `/api/server/v1/server-accounts/{id}/provision` — dispatch useradd на боксе | `server_account` | `task_id`, `task_kind=account.provision` |
| `server_account.update_on_host` | INFO | POST `/api/server/v1/server-accounts/{id}/update-on-host` — dispatch usermod (синк атрибутов) | `server_account` | `task_id`, `task_kind=account.update_on_host` |
| `server_account.deprovision` | WARNING | POST `/api/server/v1/server-accounts/{id}/deprovision` — dispatch userdel | `server_account` | `task_id`, `task_kind=account.deprovision` |
| `server.users_inventory_triggered` | INFO | POST `/api/server/v1/servers/{id}/users-inventory` — dispatch инвентаризации OS-пользователей (SSH getent). target=server: kick'ается со стороны сервера, аккаунты — только результат | `server` | `task_id`, `task_kind=account.users_inventory` |
| `ipmi_controller.rotate_dispatch` | CRITICAL | POST `/api/server/v1/ipmi-controllers/{id}/rotate` — dispatch BMC-rotation (currently safety-guarded; worker fails fast пока storage round-trip не реализован) | `ipmi_controller` | `task_id`, `task_kind=ipmi.rotate_password` |

---

## Internal degradation

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `internal.dept_header_missing` | WARNING | `/internal/*` дёрнули без `X-Target-Department-Id` в soft mode (`INTERNAL_REQUIRE_DEPT_HEADER=false`) | `http_endpoint` | `path`, `caller_username` |

> **Default (prod):** `INTERNAL_REQUIRE_DEPT_HEADER=true` → отсутствие
> header'а отбивается 403 ДО этого audit-события. Soft mode оставлен
> только для dev/test.

> **Actor-vs-server department check** — отдельный always-on guard. Если
> `identity.department_id != server.department_id` (или `None` для
> platform-роли), endpoint отдаёт 403 `TARGET_DEPARTMENT_MISMATCH` с
> denied-audit `<action>` (например `ipmi_controller.view_credentials`,
> `server_account.view_password`, `server_account.rotate_password`,
> `server.inventory_received` и т.д.) и `details.reason=actor_department_mismatch`,
> `details.actor_department_id`, `details.server_department_id`.
> Не зависит от `INTERNAL_REQUIRE_DEPT_HEADER` — soft mode **не открывает**
> cross-department leak.

---

## Permission matrix (entity_permissions)

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `permission.grant` | CRITICAL | INSERT в `entity_permissions` через PUT `/permissions/{e}/{r}/{a}` | `entity_permission` | `entity_type`, `role`, `action`, `target_department_id` |
| `permission.revoke` | CRITICAL | DELETE из `entity_permissions` через DELETE `/permissions/{e}/{r}/{a}` | `entity_permission` | `entity_type`, `role`, `action`, `target_department_id` |

---

## Server accounts — CRUD (user-facing)

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `server_account.create` | CRITICAL | INSERT в `server_accounts` (содержит шифр-пароль) | `server_account` | `server_id`, `login`, `has_sudo`, `department_id` |
| `server_account.view` | INFO | denied на GET (cross-dept / nonexistent) — success на read не аудитим (шум) | `server_account` | `reason in {permission_denied, cross_department}` |
| `server_account.list` | INFO | denied на GET list (success — by design не аудитится) | `server_account` | `reason=permission_denied` |
| `server_account.update` | INFO | PATCH — изменение метаданных (login/unix_groups/sudo) | `server_account` | поля diff'а (whitelist) |
| `server_account.link_servers` | INFO | POST `/server-accounts/{id}/servers` — привязка аккаунта к дополнительным серверам | `server_account` | `server_ids`, `department_id`. denied: `reason in {permission_denied, not_found_or_cross_dept}` |
| `server_account.unlink_servers` | INFO | DELETE `/server-accounts/{id}/servers` — отвязка от серверов | `server_account` | `server_ids`, `department_id`. denied: `reason in {permission_denied, not_found_or_cross_dept}` |
| `server_account.delete` | CRITICAL | hard-delete | `server_account` | `server_id`, `login` |

---

## IPMI controllers — CRUD (user-facing)

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `ipmi_controller.create` | CRITICAL | INSERT в `ipmi_controllers` (шифр-пароль BMC) | `ipmi_controller` | `server_id`, `kind`, `bmc_vendor`, `endpoint_url`, `username` |
| `ipmi_controller.view` | INFO | denied на GET (cross-dept / nonexistent / not registered) | `ipmi_controller` | `reason in {permission_denied, cross_department, not_registered}` |
| `ipmi_controller.list` | INFO | denied на GET list | `ipmi_controller` | `reason=permission_denied` |
| `ipmi_controller.update` | INFO | PATCH (kind/bmc_vendor/endpoint/username — не password) | `ipmi_controller` | поля diff'а; при смене `bmc_vendor` — новое значение в `details.bmc_vendor` |
| `ipmi_controller.delete` | CRITICAL | hard-delete | `ipmi_controller` | `server_id`, `kind` |
| `ipmi_controller.rotate_credentials` | CRITICAL | direct PATCH (legacy) — currently не используется в пользу dispatch+callback | `ipmi_controller` | `server_id` |
| `ipmi_controller.view_credentials_meta` | INFO | GET `/ipmi/credentials` — метаданные controller'а без plaintext-пароля (kind/endpoint_url/username/last_probed_at) | `ipmi_controller` | `server_id`, `department_id`. denied: `reason in {permission_denied, not_found_or_cross_dept, not_registered}` |

---

## OS versions — глобальный каталог

Чтение каталога (`list` / `get` по id / по имени) — публичное (без auth).
Authenticated read'ы не аудитятся (шум на rendering UI), но anonymous read'ы
эмитят `os_version.list_anonymous` / `os_version.view_anonymous` —
enumeration-trail для SIEM (с rate-limit'ом для защиты от bot'ов).

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `os_version.create` | INFO | INSERT в `os_versions` | `os_version` | `name` |
| `os_version.update` | INFO | PATCH | `os_version` | поля diff'а |
| `os_version.delete` | WARNING | DELETE | `os_version` | `name` |
| `os_version.list_anonymous` | INFO | анонимный (без bearer) GET `/os-versions` — enumeration-trail для SIEM; rate-limit отдельный (`_ANON_LIMIT`) | `os_version` | `caller_type=anonymous`, `page_size`/`total`, `has_more` (keyset) |
| `os_version.view_anonymous` | INFO | анонимный GET `/os-versions/{id}` или `/os-versions/by-name/{name}` — карточка | `os_version` | `caller_type=anonymous`, `lookup in {by_id, by_name}`, `name` (для by_name) |

---

## Installed packages — live SSH-probe через worker

Таблицы `server_installed_packages` больше нет; единственный endpoint —
`POST /servers/{id}/installed-packages?pattern=...` (см. миграцию
`c8e4f6a9b1d2_drop_installed_packages_table`). server_service эмитит
dispatch-событие, server_worker (`tasks/installed_packages.py`) —
выполнение task'а с тем же `action`.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `installed_packages.list` | INFO | dispatch success / denied / worker failure + worker task SUCCEEDED/FAILED | `server` | `task_id`, `task_kind`, `pattern`, `department_id`, `package_manager`, `count`, либо `reason` |

---

## Worker task lifecycle (cancel)

Единственная server_service-ручка управления task-row'ой — `POST /api/server/v1/tasks/{id}/cancel`. Row физически живёт в `dev_server_worker.tasks`; server_service ходит туда cross-DB через `worker_client`. Cancel применяется немедленно: queued → cancelled, running → worker завершает текущий stage и видит `status=cancelled` при попытке terminal `mark_succeeded/failed` (CAS отбрасывает финализацию). Force-kill процесса нет.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `task.cancelled` | WARNING | POST `/tasks/{id}/cancel` — success на cancel pending/running task'и | `task` | success: `task_id`, `previous_status`, `task_kind`, `target_server_id`, `cancel_reason`. denied: `reason in {permission_denied, system_task_admin_required, task_not_found_or_cross_dept}` (+ `task_kind`, `target_server_id` где известно). failure: `reason in {task_not_found, not_cancellable}` (+ `previous_status` для `not_cancellable`) |

> **denied vs failure semantics:** `denied` пишется когда caller не прошёл прав/видимости (нет `(task, cancel)`; системная task без `account_admin`; cross-dept по `target_server_id`) — попытка отлавливается ДО обращения к worker-DB. `failure` пишется когда права прошли, но row либо исчез между check'ом и cancel'ом (`task_not_found` race), либо уже в терминальном статусе (`not_cancellable` — 409).
>
> **Mid-run cancel side-effect** — отдельного server_service-события нет, но worker (см. `server_worker/src/tasks/_runner.py`) при попытке terminal write'а на cancelled row пишет audit с `action=<task_kind>` (например `power.on`, `account.rotate_password`), `status=failure`, `details.reason=cancelled_midrun` и `details.observed_status=cancelled`. Это покрывает все три ветки (success / failure / retry, последняя re-kick подавляется). SIEM может джойнить `task.cancelled (success)` с парным `<task_kind> (failure, reason=cancelled_midrun)` по `target_id=task_id` / `target_server_id`, чтобы видеть полную картину «оператор отменил, worker зафиксировал отмену в полёте».

---

## Что НЕ аудитится (by design)

- **Health/Ready endpoints** (`/health`, `/ready`) — k8s probes, шумно.
- **429 rate-limit** — anti-amplification (см. middleware order в `main.py`).
- **Успешный GET /{id}** (`server.view` / `*.view` на success) — слишком много шума на rendering UI. Denied (cross-dept / not found) — аудитится.
- **Успешный GET list** (`*.list` на success) — то же.
- **Stub-эндпоинты (501)** — попадают в `http.client_error` через middleware.

---

## SIEM-rules (рекомендуемые)

- `action=http.platform_admin_blocked` — попытка `account_admin`/`loging_admin` тронуть business data. Должен срабатывать редко (любое срабатывание — расследование).
- `action in {server_account.view_password, ipmi_controller.view_credentials}` (WARNING) — раскрытие секрета worker'у через `/internal/*`. Кросс-чекать с request_id worker-job'ы.
- `action in {server_account.password_revealed, ipmi_controller.credentials_revealed}` (CRITICAL) — пользователь раскрыл пароль через GET-карточку (держатель `view_password` / `view_credentials`). Кросс-чекать с identity актёра (user/UI vs worker_bot).
- `action=permission.grant OR permission.revoke` — любое изменение матрицы прав. (Управление каталогом service-ролей переехало в auth_service — соответствующее SIEM-правило живёт там.)
- `action=server.power_* AND status=denied, reason=not_found_or_cross_dept` — cross-dept probe.
- `action=internal.dept_header_missing` — если есть в проде, значит `INTERNAL_REQUIRE_DEPT_HEADER` случайно выключен или worker сломался.
- `action in {ipmi_controller.view_credentials, server_account.view_password, server_account.rotate_password, server.inventory_received, ipmi_controller.credentials_rotated_callback} AND status=denied AND details.reason=actor_department_mismatch` — caller (worker_bot или admin) пытается работать с сервером чужого отдела через `/internal/*`. Высокий приоритет — компрометированный/неправильно выданный PAT.
- `action=secrets.reencrypt_batch AND status=failure` — total-failure батча (все строки упали с decrypt/encrypt). Сигнал битого ciphertext или неправильной версии master-key.
- `action=ipmi_controller.credentials_rotated_callback` — каждое подтверждение BMC-ротации worker'ом. CRITICAL-cross-check с dispatch'ем.
- `action=task.cancelled AND status=denied AND details.reason=system_task_admin_required` — dept-admin с (task, cancel) пытался отменить системную task'у (heartbeat / sweep / cleanup_completed). Заслуживает проверки: либо admin неправильно понимает scope разрешения, либо это попытка повлиять на cluster-wide worker.
- `action=task.cancelled AND status=failure AND details.reason=task_not_found` — гонка между fetch_status_and_meta и cancel: row исчез. В норме маловероятно (cleanup трогает только terminal). Если повторяется — баг в задаче cleanup'а или ручной DELETE в worker-БД.
- `action=<task_kind> AND status=failure AND details.reason=cancelled_midrun` — worker зафиксировал, что задача отменилась в полёте. Парный к `task.cancelled (success)` — джойнить по `task_id`.

---

## Источник

`src/services/audit_events.py:SERVICE_EVENTS` — список, отправляемый
loging_service'у на startup. При изменении этого файла обновите эту
таблицу синхронно (либо генерируйте её скриптом).

Регистрация: `audit_events.register_events()` (POST в loging_service на
startup, best-effort — недоступность loging_service не блокирует boot).
