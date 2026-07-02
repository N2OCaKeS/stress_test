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
| `server.view` | INFO | GET /{id}: success → `status=success`, `details={department_id}`; permission_denied → `status=denied`, `reason=permission_denied`; cross-dept / non-existent → `status=failure`, `reason=not_found_or_cross_dept` | `server` | `department_id` (success) либо `reason in {permission_denied, not_found_or_cross_dept}` (denied/failure) |
| `server.view_drift` | INFO | GET `/servers/{id}/drift` — агрегированная сводка drift-событий | `server` | `department_id`, `since`, `count`, `truncated`. denied: `reason in {permission_denied, not_found_or_cross_dept}` |
| `server.update` | INFO | UPDATE через PATCH | `server` | поля diff'а (whitelist), `department_id` |
| `server.delete` | CRITICAL | hard-delete + CASCADE | `server` | `department_id` |
| `server.clean` | CRITICAL | POST `/servers/{id}/clean` — оркестрация очистки после переустановки ОС (выбранные действия в порядке unbind → rerun_prepare → update_os_version → inventory_sync; каждое переиспользует существующий путь и эмитит свой аудит) | `server` | `department_id`, `actions` (флаги), `results` (per-action status). denied/failure: `reason in {no_view_permission, not_found_or_cross_dept, decommissioned}` |
| `server.reservation_denied` | WARNING | деструктивная операция отбита `reservation.guard`: сервер `busy_state=busy` под чужого владельца, а caller — не владелец брони и не department/service-admin (409 `SERVER_RESERVED`) | `server` | `blocked_action` (машинный ключ заблокированной операции, например `server.power_on` / `server_account.delete`), `server_id`, `department_id`, `busy_user_id`, `busy_note`, опц. `subject_type` |
| `server.power_on` | WARNING | dispatch `power.on` в worker | `server` | `task_id`, `task_kind=power.on`, `department_id`. denied/failure: `reason in {not_found_or_cross_dept, no_view_permission, permission_denied, decommissioned, no_ipmi, idempotent_conflict, worker_unreachable}` |
| `server.power_off` | WARNING | dispatch `power.off` — всегда hard ForceOff (никакого graceful/ACPI shutdown'а) | `server` | как `power_on` |
| `server.power_reboot` | WARNING | dispatch `power.reboot` | `server` | как `power_on` |
| `server.power_status` | INFO | POST `/servers/{id}/power/status` — dispatch live BMC-probe `power.status` через worker'а (Redfish/ipmitool); парный к `server.power_status_cached` (GET-вариант, без worker'а) | `server` | `task_id`, `task_kind=power.status`, `department_id`. denied/failure: как у других power-операций |
| `server.inventory_sync` | INFO | dispatch `inventory.sync` (SSH-probe) | `server` | `task_id`, `task_kind=inventory.sync`. failure (account-резолв для self-сессии на неуправляемом сервере): `reason in {account_required, account_not_linked}` + `task_kind=inventory.sync`, `department_id` (allowed=True — права прошли, отказ доменный) |
| `server.prepare` | CRITICAL | POST `/servers/{id}/prepare` — dispatch бутстрапа управления (`server.prepare`: useradd management-user + authorized_keys) | `server` | `task_id`, `task_kind=server.prepare`, `department_id`. failure: `reason in {creds_store_unavailable, creds_store_failed}` (Redis для bootstrap-кред'ов недоступен или упал с runtime-сбоем; на `creds_store_failed` в details уходит `error_class`, plaintext-кред'ы не утекают) |
| `server.management_creds_generated` | CRITICAL | POST `/servers/{id}/prepare` — server_service впервые сгенерил per-server управляющие креды (Ed25519-пара + пароль `dbos`), зашифровал в `mgmt_*_encrypted` и положил plaintext в `mgmt_install` prepare-stash для воркера. Sticky-reuse при повторном prepare событие НЕ эмитит | `server` | `management_user`, `department_id`, `regenerated=false` |
| `server.management_creds_rotated` | CRITICAL | (a) POST `/servers/{id}/management-credentials/rotate` — dispatch ротации (`server.rotate_management_creds`): новый материал в `mgmt_*`, старый в `previous_mgmt_*`, `mgmt_creds_pending_apply=True`; (b) POST `/internal/servers/{id}/management-credentials/applied` — applied-callback воркера снял pending, занулил previous, проставил `mgmt_creds_rotated_at` | `server` | (a) `task_id`, `task_kind=server.rotate_management_creds`, `department_id`, `idempotent_hit`. denied/failure: `reason in {no_view_permission, not_found_or_cross_dept, decommissioned, prepare_required, creds_store_unavailable, creds_store_failed, idempotent_conflict, worker_unreachable}`. (b) `rotated_at`, `caller_type`, `department_id`. denied/failure: `reason in {permission_denied, server_not_found, target_department_mismatch}` |
| `server.power_status_cached` | INFO | GET `/servers/{id}/ipmi/power` — чтение поля `servers.power_state` из БД без BMC-probe (cached-view для dashboard'ов); парный к `server.power_status` (POST-вариант, дёргает worker'а) | `server` | `department_id`, `power_state`. denied: `reason in {permission_denied, not_found_or_cross_dept}` |
| `server.power_state_updated` | INFO | POST `/internal/servers/{id}/power-state` — worker пишет результат живой пробы питания (`power.status`) в кэш: `power_state` + `power_state_source` (bmc/ping/ssh) + `power_state_checked_at` (UTC). Грант `(server, prepare_callback)`, worker_bot-only | `server` | `power_state`, `source`, `checked_at`, `department_id`, `caller_type`. denied/failure: `reason in {permission_denied, server_not_found, target_department_mismatch}` |
| `server.acquire` | INFO | POST `/servers/{id}/busy` — успех захвата (busy_state → busy) | `server` | `department_id`, `purpose`, `lease_until`. denied/failure: `reason in {not_found_or_cross_dept, permission_denied, decommissioned, already_busy}` |
| `server.release` | INFO | DELETE `/servers/{id}/busy` — успех освобождения (busy_state → free) | `server` | `department_id`, `previous_user_id`. denied/failure: `reason in {not_found_or_cross_dept, permission_denied, not_busy, race_already_free}` |
| `server.update_os_version` | INFO | POST `/servers/{id}/os-sync` — ручной апдейт `os_version_id` без inventory sync | `server` | `department_id`, `previous_os_version_id`, `new_os_version_id`. denied/failure: `reason in {not_found_or_cross_dept, permission_denied, invalid_os_version}` |

---

## Interactive SSH console (WebSocket)

`WS /api/server/v1/servers/{id}/console/ws` — интерактивная PTY-консоль через
Redis pub/sub мост к worker'у. server_service эмитит session-события на стороне
WS; per-команда (`ssh_console.command`) эмитит worker (см.
`server_worker/AUDIT_EVENTS.md`). Доступ: `(server, console)` — sensitive,
дефолтно admin/operator. Сервер обязан быть `is_managed` (prepared).

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `ssh_console.session_open` | INFO | WS connect → RBAC/visibility/prepared-gate. success → PTY-сессия запрошена у worker'а; denied → нет `console`-права; failure → `not_found_or_cross_dept` / `decommissioned` / `prepare_required` | `server` | success: `session_id`, `department_id`, `management_user`. denied: `reason=permission_denied`. failure: `reason in {not_found_or_cross_dept, decommissioned, prepare_required}`, `department_id` |
| `ssh_console.session_close` | INFO | WS disconnect / таймаут / ошибка моста — закрытие сессии | `server` | `session_id`, `reason in {client_disconnect, bridge_error, start_failed:*}`, `department_id` |

`ssh_console.command` (worker-emitted, severity INFO / WARNING на ненулевом
exit) — здесь session-события; per-команда зарегистрирована под server_service
и описана в разделе «Worker-emitted» ниже, полное поведение — в
`server_worker/AUDIT_EVENTS.md`.

---

## Server / IPMI worker callbacks (write-direction internal API)

Worker → server_service эмиты от `/internal/*` endpoint'ов, которые
worker дёргает после реальной работы.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `server.inventory_received` | INFO | POST `/internal/servers/{id}/inventory` — worker отдал hardware-facts после `inventory.sync` | `server` | `server_id`, `department_id`, `cpu_brand`, `cpu_model`, `os_version`, `disks`, `first_write_fields` (заполнены впервые), `drift_fields` (разошлись с БД, НЕ перетёрты). denied: `reason=permission_denied` |
| `inventory.drift_detected` | WARNING | hardware-факты бокса (`cpu_brand`/`cpu_model`/`cpu_cores`/`cpu_threads`/`cpu_frequency_ghz`) разошлись с уже заполненными полями сервера. БД — источник истины, значения НЕ перетираются; drift фиксируется с old/new для оператора. First-write (поле было NULL) сохраняется штатно и drift НЕ поднимает. `os_version` — box→DB исключение (обновляет каталог, drift не считается); `hostname` остаётся authoritative с бокса | `server` | `fields` (список разошедшихся), `drift` (`{field: {old, new}}`), `department_id`, `caller_type` |
| `server.prepared` | CRITICAL | POST `/internal/servers/{id}/prepared` — worker подтвердил завершение бутстрапа управления (callback помечает `is_managed`) | `server` | `server_id`, `department_id`. denied/failure: `reason in {permission_denied, server_not_found, target_department_mismatch}` |
| `server_account.bootstrap_resolved` | CRITICAL | POST `/servers/{id}/prepare` в account-режиме (`{account_id}`) — server_service расшифровал креды привязанного аккаунта, чтобы забутстрапить prepare вместо ручного ввода. Гейт — `view_password` на server_account | `server_account` | `login`, `server_id`, `department_id`, `has_ssh_private_key` |
| `server_account.users_inventory_received` | INFO | POST `/internal/servers/{id}/users/inventory` — worker отдал OS-user-инвентаризацию, reconcile против `server_accounts` (callback) | `server` | `server_id`, `department_id`, `created` (новые discovered), `present` (подтверждены на боксе), `drifted` (attribute-drift + discovered + missing), `found` (всего на боксе). target_type — `server` (а не `server_account`): callback kick'ается со стороны сервера, аккаунты — реконсайл-результат; convention «primary entity, на которой обновляется состояние» |
| `server_account.drift_detected` | WARNING | reconcile инвентаризации обнаружил расхождение состояния на боксе с БД. БД — источник истины, поля аккаунта НЕ перетираются; обновляется только presence связки | `server_account` | `server_id`, `login`, `department_id`, `drift in {attributes, missing_on_box, unknown_login}`; для `attributes` — `fields` (список разошедшихся) и `expected`/`found` по каждому полю |
| `server_account.provision_status` | INFO | POST `/internal/servers/{id}/accounts/{aid}/provision_status` — worker отдал результат useradd/usermod/userdel, обновлён `present_on_server` (callback) | `server_account` | `server_id`, `account_id`, `department_id`, `operation`, `present_on_server` |
| `ipmi_controller.credentials_rotated_callback` | WARNING | POST `/internal/ipmi-controllers/{id}/credentials_rotated` — worker подтвердил ротацию (отдал plaintext, server_service зашифровал) | `ipmi_controller` | success: `server_id`, `controller_id`, `department_id`. denied/failure: `reason in {permission_denied, server_not_found, target_department_mismatch, rotated_at_too_old}` |
| `secrets.reencrypt_batch` | INFO | POST `/internal/secrets/reencrypt_batch` — фоновая ротация мастер-ключа (`processed=0 ∧ errors>0` → status=failure для эскалации severity) | `secret` | `limit`, `processed`, `errors` |
| `secrets.reencrypt_seed` | INFO | POST `/internal/secrets/reencrypt_outbox/seed` — скан owner-таблиц, вставка pending outbox-row для row'ов с устаревшей версией ciphertext | `secret` | `inserted`, `scanned`, `active_version`, `limit` |
| `secrets.reencrypt_done` | INFO | POST `/internal/secrets/reencrypt_outbox/{id}/done` — worker подтвердил, outbox-row закрыт, owner-row перешифрован активной версией ключа. На `skipped=true` endpoint эмитит этот же event со `status="warning"` (+ `reason`), а service-layer дополнительно эмитит парный `secrets.migration.skipped` с тем же reason — двойная запись осознанная, чтобы SIEM видел и факт finalize'а, и причину skip'а отдельно | `secret` | `outbox_id`, `status`, `skipped`. На skipped: + `reason in {owner_vanished, owner_ciphertext_changed, status_not_processing}` |
| `secrets.reencrypt_failed` | WARNING | POST `/internal/secrets/reencrypt_outbox/{id}/failed` — worker не смог закрыть outbox-row (decrypt/encrypt error), row помечен `failed` | `secret` | `outbox_id`, `status` |
| `secrets.reencrypt_outbox_cleanup` | INFO | POST `/internal/secrets/reencrypt_outbox/cleanup` — удаление `done`-row'ов старше `older_than_hours` (bounded growth таблицы) | `secret` | `deleted`, `older_than_hours` |
| `secrets.migration.skipped` | WARNING | `secrets_migration_service.finalize_done` — outbox-row уже закрыт другой ветвью (`status_not_processing`), либо owner-row пропал/перетёрся параллельно (`owner_vanished`, `owner_ciphertext_changed`). Идемпотентность сохранена, но факт требует видимости в SIEM | `secrets_reencrypt_outbox` | `reason in {status_not_processing, owner_vanished, owner_ciphertext_changed}`, `entity_type`, `entity_id`, `current_status` (для `status_not_processing`) |
| `secrets.migration_key_missing` | ERROR | `secrets_migration_service.reencrypt_batch` — sync re-encrypt не смог расшифровать row из-за пропавшего мастер-ключа (`ENCRYPTION_KEY_MISSING`). Мисконфиг env, требует немедленного вмешательства оператора | `server_account` / `ipmi_controller` | `reason=encryption_key_missing`, `entity_type`, `error_class`, `error_code` |
| `secrets.migration_decrypt_failed` | ERROR | `secrets_migration_service.reencrypt_batch` — sync re-encrypt не смог расшифровать/перешифровать row (неаутентичный или битый ciphertext, чужой AAD). Row выпала из миграции, требует ручного разбора | `server_account` / `ipmi_controller` | `reason=decrypt_failed`, `entity_type`, `error_class`, `error_code` |

---

## Master-key rotation — ops-runner (s2s shared-secret)

Канал `/ops/*` под shared-secret'ом `rotation_runner` (`require_internal_caller`),
им ходит `scripts/k8s/rotate_master_key.sh`. Отдельные action-name'ы от
UI-вариантов (`encryption.admin_*`), чтобы SIEM различал автоматический s2s-runner
и человека из UI. `target_type=secret`, `target_id=None` (глобальная операция).

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `ops.migration_status_read` | INFO | GET `/ops/migration_status` — rotation-runner прочитал legacy-residue перед drop'ом старого ключа | `secret` | `identity` (caller), `remaining_legacy_total`, `outbox_pending`, `active_version` |
| `ops.encryption_rotate` | CRITICAL | POST `/ops/encryption/rotate` — новая версия мастер-ключа стала активной через keystore, reencrypt-outbox засеян (рантайм-ротация без простоя) | `secret` | `identity`, `new_version`, `previous_version`, `seeded_inserted`, `idempotent` |
| `ops.encryption_retire` | CRITICAL | POST `/ops/encryption/retire/{version}` — старая версия мастер-ключа убрана из keystore после полной ре-шифрации (0 строк на версии) | `secret` | `identity`, `version`, `retired` |

---

## Master-key rotation — account_admin (UI)

Платформенный канал под `account_admin` (`/admin/encryption/*`). Зеркало
ops-runner'ских ротаций, но action-name'ы отдельные — actor = человек из UI.
`target_type=secret`, `target_id=None`.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `encryption.admin_rotate` | CRITICAL | POST `/admin/encryption/rotate` — account_admin ввёл новую версию мастер-ключа активной; keystore-bump + reencrypt-outbox seed | `secret` | `new_version`, `previous_version`, `seeded_inserted`, `idempotent` |
| `encryption.admin_retire` | CRITICAL | POST `/admin/encryption/retire/{version}` — account_admin убрал старую версию из keystore после полной ре-шифрации | `secret` | `version`, `retired` |
| `encryption.auto_retire` | CRITICAL | Не-активная версия мастер-ключа автоматически выведена из keystore сразу после того, как перешифровочный батч (finalize_done / reencrypt_batch) осушил её — 0 owner-строк и пустой reencrypt-outbox на версии. `actor_type=system`, материал ключа удалён | `secret` | `version`, `active_version` |

---

## Management-user config (платформенный singleton)

Конфиг управляющей учётки (`/management-user-config`) — платформенный singleton
под `account_admin`. PUT правит login + пер-режимные группы/bootstrap-команды; на
смену конфига идёт высокоприоритетный недеструктивный re-bootstrap (`management_user_sync`)
по всем `is_managed`-серверам платформы независимо от отдела.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `management_user_config.update` | WARNING | PUT `/management-user-config` — обновлён конфиг управляющей учётки; смена login помечается `login_changed` для будущего cutover-фан-аута | `management_user_config` | `login_changed`, `modes_changed`, `previous_login`, `new_login`, `modes_updated` |
| `management_user_config.sync` | WARNING | фан-аут `management_user_sync` (high-priority) на конкретный managed-сервер при изменении конфига. success: задача поставлена; failure: `reason in {idempotent_conflict, worker_unreachable}` (allowed=True — best-effort, остальные серверы продолжают) | `server` | success: `task_id`, `task_kind=management_user_sync`, `server_id`, `source=config_fanout`, `management_login`, `rename_pending`, `department_id`, `idempotent_hit`. failure: `reason`, `task_kind`, `server_id`, `source`, `department_id` |
| `management_user_sync_fanout.truncated` | WARNING | фан-аут превысил `MANAGEMENT_USER_SYNC_FANOUT_MAX` — хвост managed-серверов вырезан, выровняется на следующем PUT/prepare | `management_user_config` | `total_managed`, `cap`, `truncated_count` |

---

## Server account / IPMI controller — секреты (raw decrypt)

Эмитятся при показе расшифрованных секретов через `/internal/*` endpoints
для worker'а, а также при user-initiated ротации пароля аккаунта.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `server_account.view_password` | WARNING | GET `/internal/servers/{id}/accounts/{aid}/password` — расшифрован и отдан plaintext | `server_account` | `server_id`, `account_id`, `department_id` |
| `server_account.password_revealed` | CRITICAL | GET `/api/server/v1/server-accounts/{id}` вызывающим с `view_password` — пароль расшифрован и доставлен в `password_b64` (services/server_account.get_account → _reveal_account_password). Первое раскрытие per (actor, account) в окне `PASSWORD_REVEAL_AUDIT_WINDOW_SECONDS`; последующие в окне → `server_account.password_revealed_throttled`. Аккаунт-«личность», `server_id` в details не пишется (учётка не привязана к конкретному серверу при раскрытии) | `server_account` | `login`, `department_id`. failure: `reason in {no_password_stored, decrypt_failed}` + `department_id` |
| `server_account.password_revealed_throttled` | INFO | Повторный успешный reveal в окне `PASSWORD_REVEAL_AUDIT_WINDOW_SECONDS` для той же пары (actor, account). Первый reveal в окне уже залогирован CRITICAL `server_account.password_revealed` — здесь INFO-копия, чтобы UI-polling не флудил SIEM CRITICAL'ом | `server_account` | `login`, `department_id`, `window_seconds` |
| `server_account.ssh_private_key_revealed` | CRITICAL | GET `/api/server/v1/server-accounts/{id}/ssh_private_key` вызывающим с `view_password` — приватный SSH-ключ расшифрован и отдан в PEM (services/server_account.reveal_ssh_private_key). Доступен только для сгенерированных сервером ключей; per-IP+account reveal-rate-limit поверх глобального | `server_account` | `login`, `department_id`. failure: `reason in {no_ssh_private_key_stored, decrypt_failed}` + `department_id` |
| `server_account.rotate_password` | CRITICAL | (a) POST `/api/server/v1/server-accounts/{id}/rotate_password` — user-initiated ротация записи в БД без SSH apply (services/server_account.py); (b) POST `/internal/servers/{id}/accounts/{aid}/password/rotate` — worker положил новый ciphertext после SSH apply (internal_service) | `server_account` | для (a) success: `login`, `department_id`, `reason in {user_provided, user_initiated}`, `rotated_at`. Для (b) success: `server_id`, `login`, `rotated_at`, `caller_type`. denied/failure: `reason in {permission_denied, not_found_or_cross_dept, account_not_found, server_not_found, target_department_mismatch}` |
| `server.management_credentials_revealed` | WARNING | GET `/internal/servers/{id}/management/credentials` — расшифрованы per-server управляющие креды (privkey + пароль `dbos`) и отданы воркеру. WARNING (не CRITICAL): штатный internal pull перед каждой managed-операцией, не раскрытие человеку. Пока `mgmt_creds_pending_apply=True` и есть previous — отдаётся previous (`source=previous`), иначе current | `server` | `department_id`, `management_user`, `source in {current, previous}`, `caller_type`. denied/failure: `reason in {permission_denied, server_not_found, no_creds_stored, decrypt_failed, target_department_mismatch}` |
| `ipmi_controller.view_credentials` | WARNING | GET `/internal/servers/{id}/ipmi/credentials` — расшифрованы IPMI-creds | `ipmi_controller` | `server_id`, `department_id` |
| `ipmi_controller.credentials_revealed` | CRITICAL | GET `/api/server/v1/servers/{id}/ipmi` вызывающим с `view_credentials` — BMC-пароль расшифрован и доставлен в `password_b64` (services/ipmi_controller.get_controller → _reveal_controller_password). Первое раскрытие per (actor, controller) в окне `PASSWORD_REVEAL_AUDIT_WINDOW_SECONDS`; последующие в окне → `ipmi_controller.credentials_revealed_throttled` | `ipmi_controller` | `server_id`, `username`, `department_id`, `total_reveals_in_window`. failure: `reason in {no_password_stored, decrypt_failed}` |
| `ipmi_controller.credentials_revealed_throttled` | INFO | Повторный успешный reveal BMC-пароля в окне `PASSWORD_REVEAL_AUDIT_WINDOW_SECONDS` для той же пары (actor, controller). Первый reveal в окне уже залогирован CRITICAL `ipmi_controller.credentials_revealed` — здесь INFO-копия, чтобы UI-polling не флудил SIEM CRITICAL'ом | `ipmi_controller` | `server_id`, `username`, `department_id`, `window_seconds`, `total_reveals_in_window` |

---

## Worker-dispatch (admin-initiated rotation)

Public endpoint'ы, через которые user (обычно admin) запускает ротацию
паролей. Сам rewrite в БД делает worker через callback'и выше.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `server_account.rotate_password_dispatch` | CRITICAL | POST `/api/server/v1/server-accounts/{id}/rotate` — dispatch SSH-rotation task. Эмитится один агрегированный success на запрос (даже при частичных пропусках в массовом режиме); per-server-фейлы (idempotent-конфликт) идут отдельными failure-эмитами. Worker-unreachable: в `mode=single` или mass без успешных dispatch'ей — 503; в mass с хотя бы одним успешным — структурный 202-ответ с `partial_failure=True` (см. `mass_rotation.partial_failure` ниже) | `server_account` | success: `mode`, `task_kind=account.rotate_password`, `task_ids`, `server_ids` (только реально поставленные), `dispatched`, `skipped` (список `{server_id, reason}`), `skipped_count`, `login`, `department_id` |
| `mass_rotation.partial_failure` | WARNING | Массовая ротация (`mode=all`) частично применилась: на K серверов dispatch успешен, на K+1 worker отбил `ServiceUnavailable`, остаток не пытались. Auto-cancel НЕ выполняется (риск частичных откатов на уже-применившихся боксах). Эндпоинт отдаёт 202 со structured response (`partial_failure=true`, `next_action=manual_cancel_dispatched`, список `tasks` для ручной отмены через `/tasks/{id}/cancel`) | `server_account` | `task_kind`, `dispatched_count`, `failed_count`, `not_attempted_count`, `dispatched_task_ids`, `failed_server_id`, `not_attempted_server_ids`, `login`, `department_id` |
| `server_account.provision` | WARNING | POST `/api/server/v1/server-accounts/{id}/provision` — dispatch useradd на боксе | `server_account` | `task_id`, `task_kind=account.provision` |
| `server_account.update_on_host` | INFO | POST `/api/server/v1/server-accounts/{id}/update_on_host` — dispatch usermod (синк атрибутов) | `server_account` | `task_id`, `task_kind=account.update_on_host` |
| `server_account.deprovision` | WARNING | POST `/api/server/v1/server-accounts/{id}/deprovision` — dispatch userdel | `server_account` | `task_id`, `task_kind=account.deprovision` |
| `server.users_inventory_triggered` | INFO | POST `/api/server/v1/servers/{id}/users/inventory` — dispatch инвентаризации OS-пользователей (SSH getent). target=server: kick'ается со стороны сервера, аккаунты — только результат | `server` | `task_id`, `task_kind=users.inventory`. failure (account-резолв для self-сессии на неуправляемом сервере): `reason in {account_required, account_not_linked}` + `task_kind=users.inventory`, `department_id` (allowed=True) |
| `ipmi_controller.rotate_dispatch` | CRITICAL | POST `/api/server/v1/ipmi-controllers/{id}/rotate` — dispatch BMC-rotation (currently safety-guarded; worker fails fast пока storage round-trip не реализован) | `ipmi_controller` | `task_id`, `task_kind=ipmi.rotate_password` |

---

## Internal cross-dept scoping

> **`X-Target-Department-Id` — единственный cross-dept guard** internal-эндпоинтов
> и enforce'ится безусловно (глобальный worker-бот обслуживает серверы всех
> отделов, его собственный `department_id` в авторизации не участвует):
>
> * заголовок отсутствует → 403 `TARGET_DEPARTMENT_HEADER_REQUIRED`,
>   denied-audit `<action>` с `details.reason=missing_target_department_header`;
> * заголовок ≠ `server.department_id` → 404 (маска not-found:
>   `SERVER_NOT_FOUND` / `ACCOUNT_NOT_FOUND` / `NO_IPMI_CONTROLLER`),
>   denied-audit `<action>` с `details.reason=target_department_mismatch`,
>   `details.header_department_id`, `details.actor_department_id` (для
>   наблюдаемости, на блокировку не влияет), `details.server_department_id`;
> * совпал → пропускаем.
>
> `<action>` — тот же ключ, что caller использует для success/denied emit'ов
> (например `ipmi_controller.view_credentials`, `server_account.view_password`,
> `server_account.rotate_password`, `server.inventory_received`).

---

## Permission matrix (entity_permissions)

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `permission.grant` | CRITICAL | INSERT в `entity_permissions` через PUT `/permissions/{e}/{r}/{a}`. Повторный grant (exact-scope row уже есть) — no-op без audit-emit: SIEM-правило «выдан новый grant» иначе ловит false-positive на каждый идемпотентный POST из UI | `entity_permission` | success: `entity_type`, `role`, `action`, `department_id`. failure: `reason in {invalid_action_for_entity, race_already_exists}`. denied (через `_resolve_target_department_id`): `reason=department_isolation_grant` + `target_department_id`; для subject `pat`/`oauth_client` дополнительно `actor_subject_type`; для actor без department'а — `actor_department_id=None` |
| `permission.revoke` | CRITICAL | DELETE из `entity_permissions` через DELETE `/permissions/{e}/{r}/{a}` | `entity_permission` | success: `entity_type`, `role`, `action`, `department_id`. failure: `reason=not_found` (нет такой строки). denied (через `_resolve_target_department_id`): `reason=department_isolation_revoke` + `target_department_id`; для subject `pat`/`oauth_client` дополнительно `actor_subject_type` |

---

## Instance-level ACL (resource_role_permissions)

Точечные гранты роли на конкретный ресурс (`server` / `server_account`) поверх
тип-wide матрицы. Управление — `/resource-permissions/*`, доступ как у
`/permissions` (`account_admin` — мета-админ, остальным `(permission, *,
permission_grant/revoke/view)` своего отдела). Все мутации — CRITICAL.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `resource_permission.grant` | CRITICAL | INSERT в `resource_role_permissions` через PUT `/resource-permissions/{rt}/{rid}/{role}/{action}` — роль получила action на конкретный ресурс. Повтор (строка уже есть) — no-op без audit-emit (как `permission.grant`) | `server` / `server_account` | success: `target_id` = id строки грантa, `resource_type`, `resource_id`, `role`, `action`, `department_id` (отдел ресурса). denied (через `emit_denied_on_authz_error`): `reason=permission_denied`. failure: `reason in {action_not_instance_grantable, resource_not_found_or_cross_dept, race_already_exists}` |
| `resource_permission.revoke` | CRITICAL | DELETE из `resource_role_permissions` через DELETE `/resource-permissions/{rt}/{rid}/{role}/{action}` — роль лишилась action на ресурсе | `server` / `server_account` | success: `resource_type`, `resource_id`, `role`, `action`, `department_id`. denied: `reason=permission_denied`. failure: `reason in {resource_not_found_or_cross_dept, not_found}` |
| `resource_permission.propagate` | CRITICAL | POST `/resource-permissions/{rt}/{source_rid}/propagate` — гранты образца скопированы на цели того же типа (`mode=merge` добавляет недостающее; `mode=mirror` ещё удаляет лишнее, требует `permission_revoke`). Все цели — в отделе образца | `server` / `server_account` | success: `target_id` = образец, `resource_type`, `source_resource_id`, `mode`, `department_id`, `source_grant_count`, `targets_applied`, `total_added`, `total_removed`. denied: `reason=permission_denied`. failure: `reason in {resource_not_found_or_cross_dept, target_not_found_or_cross_dept}` (+ `target_resource_id` для цели) |

---

## Server accounts — CRUD (user-facing)

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `server_account.create` | CRITICAL | INSERT в `server_accounts` (содержит шифр-пароль) | `server_account` | `server_id`, `login`, `has_sudo`, `department_id` |
| `server_account.view` | INFO | denied на GET (cross-dept / nonexistent) — success на read не аудитим (шум) | `server_account` | `reason in {permission_denied, cross_department}` |
| `server_account.list` | INFO | denied на GET list (success — by design не аудитится) | `server_account` | `reason=permission_denied` |
| `server_account.update` | INFO | PATCH — изменение метаданных (login/unix_groups/sudo) | `server_account` | поля diff'а (whitelist) |
| `server_account.adopted_from_host` | WARNING | POST `/server-accounts/{id}/adopt_from_host` — оператор принял факт-состояние OS-пользователя с конкретного хоста в БД (пополевно), DB-only без fan-out | `server_account` | success: `server_id`, `adopted_fields`, `changes` (old→new по реально изменённым полям), `department_id`. denied: `reason=permission_denied` + `server_id`. failure: `reason in {not_found_or_cross_dept, server_not_linked}` + `server_id` |
| `server_account.imported_from_host` | WARNING | POST `/server-accounts/import` — оператор завёл в БД новым аккаунтом незнакомого OS-пользователя, найденного инвентаризацией (`source=discovered`, без пароля, `present_on_server=True`, привязан к серверу). Право — `(server_account, create)`; `has_sudo`/sudo-группа дополнительно требуют `grant_sudo` | `server_account` | success: `server_id`, `login`, `has_sudo`, `source`, `department_id`. denied: `reason=grant_sudo_denied` + `server_id`, `login`, `sudo_groups`. failure: `reason=duplicate` + `server_id`, `login` |
| `server_account.ignored_logins_listed` | INFO | GET `/server-accounts/ignored-logins` — список игнор-логинов отдела (эмитится только на denied; success не аудитим) | `server_account` | denied: `reason=permission_denied` |
| `server_account.ignored_login_added` | WARNING | POST `/server-accounts/ignored-logins` — логин добавлен в ignore-list отдела (инвентаризация перестанет репортить его как unknown OS-user). Право — `(server_account, manage_ignored_logins)` | `server_account` | success: `login`, `department_id`. denied: `reason=permission_denied` + `login`. failure: `reason=duplicate` + `login` |
| `server_account.ignored_login_removed` | INFO | DELETE `/server-accounts/ignored-logins/{login}` — логин убран из ignore-list отдела (инвентаризация снова репортит как unknown, если он есть на боксе) | `server_account` | success: `login`, `department_id`. denied: `reason=permission_denied` + `login`. failure: `reason=not_found` + `login` |
| `server_account.recreate_login` | CRITICAL | POST `/server-accounts/{id}/recreate_login` — OS-логин пересоздан end-to-end (deprovision под старым логином → rename в БД → provision под новым на всех привязанных серверах). Доступ — department_admin отдела аккаунта или service-admin | `server_account` | success: `old_login`, `new_login`, `deprovision_count`, `provision_count`, `skipped`, `department_id`. denied: `reason=permission_denied` (+ опц. `subject_type`). failure: `reason=not_found_or_cross_dept` |
| `server_account.ssh_key_set` | WARNING | POST `/server-accounts/{id}/ssh_key` — SSH-ключ задан/заменён (`generate` Ed25519 или `supply` public); сохранён в БД и расходится через `account.provision` (push authorized_keys). Гейт — `update` | `server_account` | `login`, `ssh_mode in {generate, supply}`, `department_id` |
| `server_account.ssh_key_rotate` | CRITICAL | POST `/server-accounts/{id}/rotate_ssh_key` — Ed25519-ключ перегенерён (кейс компрометации): новая пара сохранена, приватный отдан один раз, расходится через `account.provision`. Гейт — `update` | `server_account` | `login`, `department_id` |
| `fanout_update_on_host.truncated` | WARNING | PATCH аккаунта затронул сервер-список длиннее `FANOUT_UPDATE_ON_HOST_MAX`; хвост обрезан, обрезанные хосты выровняются следующим sweep/audit-циклом | `server_account` | `total_links`, `cap`, `truncated_count`, `source=edit_fanout`, `department_id` |
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
| `ipmi_controller.rotate_credentials` | WARNING | 410 GONE на `/ipmi/credentials/rotate` — endpoint снят (писал ciphertext без BMC apply/verify, мог разорвать out-of-band доступ). Канонический путь — worker dispatch + internal callback `credentials_rotated`. | `ipmi_controller` | `server_id`, `caller_type`, `migration`, `reason=user_facing_endpoint_deprecated` |
| `ipmi_controller.view_credentials_meta` | INFO | GET `/ipmi/credentials` — метаданные controller'а без plaintext-пароля (kind/endpoint_url/username/last_probed_at) | `ipmi_controller` | `server_id`, `department_id`. denied: `reason in {permission_denied, not_found_or_cross_dept, not_registered}` |

---

## Console macros (личные + системные в отделе)

Макросы интерактивной консоли (`/console-macros`). Личный макрос — владелец
caller; системный (department-wide) — только department_admin отдела. Чтение
(`list`) без аудита. См. `services/console_macro.py`.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `console_macro.create` | INFO | POST `/console-macros` — макрос создан (личный или системный). denied на системный без department_admin | `console_macro` | success: `is_system`, `name`. denied: `reason=permission_denied`, `is_system=True` |
| `console_macro.update` | INFO | PATCH `/console-macros/{id}` — макрос изменён. denied: caller не владелец (личный) / не department_admin (системный) | `console_macro` | success: `is_system`, `fields` (изменённые поля). denied: `reason=permission_denied`, `is_system` |
| `console_macro.delete` | INFO | DELETE `/console-macros/{id}` — макрос удалён (те же права, что у PATCH) | `console_macro` | success: `is_system`. denied: `reason=permission_denied`, `is_system` |

---

## OS versions — глобальный каталог

Чтение каталога (`list` / `get` по id / по имени) доступно любому
аутентифицированному актору (токен обязателен; платформенные роли тоже
читают) и не аудитятся (шум на rendering UI). Анонимный запрос без bearer'а
отбивается 401 на endpoint-уровне. Пишутся только мутации каталога.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `os_version.create` | INFO | INSERT в `os_versions` | `os_version` | `name` |
| `os_version.update` | INFO | PATCH | `os_version` | поля diff'а |
| `os_version.delete` | WARNING | DELETE | `os_version` | `name` |
| `os.unknown_observed` | WARNING | inventory-callback от worker'а принёс `os_version`, не прошедший whitelist `KNOWN_OS_PREFIXES` (`core/known_os.py`); запись в `os_versions` НЕ создаётся, `server.os_version_id` остаётся прежним, остальные hardware-поля апдейтятся | `server` | `os_name`, `server_id`, `server_department_id`, `actor_subject_type`, `reason=os_not_in_whitelist` |

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
| `installed_packages.history` | INFO | GET `/servers/{id}/packages/history` — чтение истории прошлых package-запросов. Эмитится только на denied (нет `(server, view)`) / not-found (cross-dept); success не аудитим | `server` | denied: `reason=no_view_permission`; not-found: `reason=not_found_or_cross_dept` |
| `server.packages_install` | WARNING | dispatch (`POST /servers/packages/bulk-action` action=install) success / denied / failure + worker task SUCCEEDED/FAILED | `server` | `task_id`, `task_kind=installed_packages.install`, `operation=install`, `package_count`, `department_id`, либо `reason`. Worker SUCCEEDED: `package_manager`, `packages`, `count`, `returncode` |
| `server.packages_remove` | WARNING | то же, action=remove | `server` | как install, `task_kind=installed_packages.remove`, `operation=remove` |
| `server.packages_update` | WARNING | то же, action=update | `server` | как install, `task_kind=installed_packages.update`, `operation=update` (packages пуст при обновлении всего) |

Массовые мутации (`POST /servers/packages/bulk-action`) — reserve-gated: занятый
чужим оператором сервер отбивается `server.reservation_denied` (WARNING) и в
per-server результате получает статус `reserved`. Право — `(server,
manage_packages)`, по дефолту admin/operator.

---

## Worker task lifecycle (cancel)

Единственная server_service-ручка управления task-row'ой — `POST /api/server/v1/tasks/{id}/cancel`. Row физически живёт в `dev_server_worker.tasks`; server_service ходит туда cross-DB через `worker_client`. Cancel применяется немедленно: queued → cancelled, running → worker завершает текущий stage и видит `status=cancelled` при попытке terminal `mark_succeeded/failed` (CAS отбрасывает финализацию). Force-kill процесса нет.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `task.view` | INFO | GET `/tasks` (list) или GET `/tasks/{id}` (detail) — чтение истории worker-task'. Эмитится только на denied (нет `(task, view)`-грантa); success не аудитим | `task` | denied: `reason=permission_denied` (+ опц. `subject_type`) |
| `task.cancelled` | WARNING | POST `/tasks/{id}/cancel` — success на cancel pending/running task'и | `task` | success: `task_id`, `previous_status`, `task_kind`, `target_server_id`, `cancel_reason`. denied: `reason in {permission_denied, system_task_admin_required}` (+ `task_kind`, `target_server_id` где известно). failure: `reason in {task_not_found, task_not_found_or_cross_dept, not_cancellable}` (+ `previous_status` для `not_cancellable`; `target_server_id`/`task_kind` для `task_not_found_or_cross_dept`) |
| `worker_dispatch.orphan_detected` | ERROR | Cross-DB сценарий в `worker_client._delete_task_row`: worker-row уже закоммичен в `dev_server_worker`, outbox-INSERT упал в caller'скую db, компенсирующий DELETE worker-row тоже упал. В worker-БД остаётся task-row, которую poller не подберёт (нет outbox-row). Парный к `/ready.worker_dispatch_orphans_total` счётчику | `task` | `task_id`, `compensation_exc` (имя класса исключения от DELETE'а worker-БД) |

> **denied vs failure semantics:** `denied` пишется когда caller не прошёл прав (нет `(task, cancel)`; системная task без `account_admin`) — попытка отлавливается на стадии authz. `failure` пишется когда права прошли, но row либо не виден из-за dept-isolation (`task_not_found_or_cross_dept` — 404, маскирует чужой сервер), либо исчез между check'ом и cancel'ом (`task_not_found` race), либо уже в терминальном статусе (`not_cancellable` — 409). Cross-dept формально отказ доступа, но эмитится `failure/allowed=True`: caller прошёл permission-check, отсутствие visibility-факта по чужому серверу не должно светиться в SIEM как denied — это enumeration-guard.
>
> **Mid-run cancel side-effect** — отдельного server_service-события нет, но worker (см. `server_worker/src/tasks/_runner.py`) при попытке terminal write'а на cancelled row пишет audit с `action=<task_kind>` (например `power.on`, `account.rotate_password`), `status=failure`, `details.reason=cancelled_midrun` и `details.observed_status=cancelled`. Это покрывает все три ветки (success / failure / retry, последняя re-kick подавляется). SIEM может джойнить `task.cancelled (success)` с парным `<task_kind> (failure, reason=cancelled_midrun)` по `target_id=task_id` / `target_server_id`, чтобы видеть полную картину «оператор отменил, worker зафиксировал отмену в полёте».

---

## Worker-emitted (зарегистрированы под server_service)

Эти action'ы эмитит **server_worker**, но регистрируются они здесь, под именем
`server_service` (worker сам в loging_service не регистрируется — публикует от
лица server_service). Так оператор/SIEM находят их через registry API
(`GET /api/logging/v1/services/server_service/events`). Полное описание полей и
сценариев — в `server_worker/AUDIT_EVENTS.md`; ниже — severity-default'ы как в
`SERVICE_EVENTS`.

| action | default_severity | эмитится при | target_type | детали |
|---|---|---|---|---|
| `task.worker_shutdown` | ERROR | graceful shutdown worker'а: running-task принудительно переведена в retry или failed | `server` / `task` | `target_id` = `server_id` связанной таски при наличии, иначе `task_id`; severity по факту ERROR/WARNING зависит от `will_retry` |
| `task.worker_orphaned` | ERROR | orphan-sweep нашёл running-task'у с мёртвым `worker_id` и принудительно перевёл её в failed без retry-decision | `server` / `task` | `target_id` = `server_id` связанной таски при наличии, иначе `task_id` |
| `task.deleted_midrun` | WARNING | task-row исчезла между `mark_running` и terminal write (retention / ручной DELETE); terminal mark пропущен, факт фиксируется | `task` | факт пропавшей row между running и terminal write |
| `secrets.reencrypt_tick` | INFO | периодический тик `secrets.reencrypt_lazy`: success/idle (`allowed=True`), warning при `finalize_errors`, failure при `app_env_mismatch` worker↔server_service (явный severity ERROR) | `secret` | признаки `finalize_errors` / `app_env_mismatch` |
| `audit.outbox_reattempt_manual` | WARNING | оператор форсит CLI-командой `outbox-reattempt` повторную доставку конкретной row'ы worker'ского `audit_outbox` | `audit_outbox` | id переотправляемой outbox-row |
| `bmc.tls_downgrade` | WARNING | BMC-probe worker'а перешёл на менее защищённый канал (`https_verify→https_noverify` или `*→http`); фиксируется при каждом фактическом переходе | `ipmi_controller` / `server` | from/to схемы транспорта |
| `server_account.password_rotate` | CRITICAL | worker завершил ротацию пароля сервисной учётки (SSH apply + callback `submit_rotated_password`); dispatch-сторона — `server_account.rotate_password_dispatch` | `server_account` | `server_id`, `login`, `rotated_at`, `caller_type` (финальная сторона ротации) |
| `ipmi_controller.password_rotate` | CRITICAL | worker завершил ротацию IPMI/BMC-пароля (apply + verify + callback `submit_rotated_ipmi_password`); dispatch-сторона — `ipmi_controller.rotate_dispatch` | `ipmi_controller` | `server_id`, `controller_id` (финальная сторона ротации) |
| `server_account.users_inventory` | INFO | worker завершил OS-user inventory через SSH `getent` и отдал список через callback `submit_users_inventory`; срез хоста, не конкретной учётки | `server` | результат инвентаризации OS-пользователей по хосту |
| `ssh_console.command` | INFO | команда, введённая в интерактивной SSH-консоли (одна строка по Enter); эмитит worker на PTY-мосте. WARNING при ненулевом exit-коде, если он доступен | `server` | `command` (redacted), `session_id`, `server_id` |

> Парный к `server.power_*` / `account.rotate_password` mid-run-факт `<task_kind>
> (failure, reason=cancelled_midrun)` тоже worker-emitted, но эмитится под
> `action=<task_kind>` (см. примечание в Worker task lifecycle выше).

---

## Что НЕ аудитится (by design)

- **Health/Ready endpoints** (`/health`, `/ready`) — k8s probes, шумно.
- **429 rate-limit** — anti-amplification (см. middleware order в `main.py`).
- **Успешный GET list** на корневых list-эндпоинтах (`*.list` на success) — шум на UI-пагинацию. Denied и failure-ветки аудитятся.
- **Карточки `*.view` на success для дочерних сущностей** (`server_account.view`, `ipmi_controller.view`) — не аудитим. Исключение: `server.view` пишет и success-ветку (`status=success`, `details={department_id}`) — поверхностный SIEM-сигнал по чтению карточки сервера (см. таблицу выше).
- **Stub-эндпоинты (501)** — попадают в `http.client_error` через middleware.

---

## SIEM-rules (рекомендуемые)

- `action=http.platform_admin_blocked` — попытка `account_admin`/`loging_admin` тронуть business data. Должен срабатывать редко (любое срабатывание — расследование).
- `action in {server_account.view_password, ipmi_controller.view_credentials}` (WARNING) — раскрытие секрета worker'у через `/internal/*`. Кросс-чекать с request_id worker-job'ы.
- `action in {server_account.password_revealed, ipmi_controller.credentials_revealed}` (CRITICAL) — пользователь раскрыл пароль через GET-карточку (держатель `view_password` / `view_credentials`). Кросс-чекать с identity актёра (user/UI vs worker_bot).
- `action=permission.grant OR permission.revoke` — любое изменение матрицы прав. (Управление каталогом service-ролей переехало в auth_service — соответствующее SIEM-правило живёт там.)
- `action in {resource_permission.grant, resource_permission.revoke, resource_permission.propagate}` — изменение инстанс-уровневого ACL (точечный грант роли на конкретный server/server_account). Особое внимание к `propagate` с большим `total_added`/`targets_applied` — массовое расширение прав одним запросом.
- `action=server.power_* AND status=denied, reason=not_found_or_cross_dept` — cross-dept probe.
- `action in {ipmi_controller.view_credentials, server_account.view_password, server_account.rotate_password, server.inventory_received, ipmi_controller.credentials_rotated_callback} AND status=denied AND details.reason in {target_department_mismatch, missing_target_department_header}` — worker пытается работать с сервером через `/internal/*` с неверным/отсутствующим `X-Target-Department-Id` (mismatch → 404, missing → 403). Высокий приоритет — stale payload или компрометированный/неправильно выданный PAT.
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
