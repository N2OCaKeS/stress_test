"""Канонический список audit-событий, которые эмитит server_service.

Отправляется в loging_service на startup через `register_events()`. Любое
новое событие, добавленное сюда, автоматически зарегистрируется при
следующем рестарте.
"""

import logging

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME as _SERVICE_NAME
from src.core.http import bearer_header

logger = logging.getLogger("audit")

# Каждая запись: action, human description, default_severity (для success;
# failure по правилам в loging_service дефолтится в WARNING/CRITICAL).
SERVICE_EVENTS = [
    # Startup / lifecycle
    {"action": "service.started", "description": "Service started up", "default_severity": "INFO"},
    # HTTP middleware
    {"action": "http.access_denied", "description": "HTTP 401/403 response", "default_severity": "CRITICAL"},
    {"action": "http.client_error", "description": "HTTP 4xx response (except 401/403)", "default_severity": "WARNING"},
    {"action": "http.server_error", "description": "HTTP 5xx response", "default_severity": "CRITICAL"},
    # loging_reader НЕ блокируется (он может быть обычным сотрудником со
    # своим department_id и service_roles в server_service) — описание
    # содержит ровно те две роли, что в BLOCKED_PLATFORM_ROLES.
    {"action": "http.platform_admin_blocked", "description": "Platform admin (account_admin + loging_admin) blocked from server_service business endpoint (§7-8 admin-plane separation; loging_reader is NOT blocked)", "default_severity": "WARNING"},
    # Servers — CRUD
    {"action": "server.create", "description": "Server record created", "default_severity": "INFO"},
    {"action": "server.view", "description": "Server record viewed", "default_severity": "INFO"},
    {"action": "server.view_drift", "description": "Aggregated drift summary read for server (queries loging server_account.drift_detected events)", "default_severity": "INFO"},
    {"action": "server.update", "description": "Server record updated", "default_severity": "INFO"},
    {"action": "server.delete", "description": "Server record deleted", "default_severity": "CRITICAL"},
    {"action": "server.clean", "description": "Server clean orchestration after OS reinstall (selected: unbind accounts / rerun prepare / update OS version / inventory sync; reuses per-action paths)", "default_severity": "CRITICAL"},
    {"action": "server.reservation_denied", "description": "Destructive operation blocked because the server is reserved (busy) by another user and caller is neither the reservation owner nor a department/service admin", "default_severity": "WARNING"},
    # Power-cycle (IPMI/iDRAC/iLO/Redfish) — dispatch to server_worker
    {"action": "server.power_on", "description": "Server power-on dispatched to worker", "default_severity": "WARNING"},
    {"action": "server.power_off", "description": "Server power-off dispatched to worker", "default_severity": "WARNING"},
    {"action": "server.power_reboot", "description": "Server reboot dispatched to worker", "default_severity": "WARNING"},
    # Live BMC power-probe — dispatched в worker (power.status task).
    {"action": "server.power_status", "description": "Server power state live-probed via worker (Redfish/ipmitool)", "default_severity": "INFO"},
    # Бутстрап управления (prepare): dispatch и callback воркера.
    {"action": "server.prepare", "description": "Server management bootstrap dispatched to worker (server.prepare; useradd management user + authorized_keys)", "default_severity": "CRITICAL"},
    {"action": "server.prepared", "description": "Worker confirmed server management bootstrap completed (callback marks is_managed)", "default_severity": "CRITICAL"},
    # Обновление ОС Astra (astra_update): dispatch, callback воркера, блокировка операций.
    {"action": "server.astra_update", "description": "OS update dispatched to worker (server.astra_update; rewrite sources.list from OsVersion.repositories + apt update && astra-update). Sets busy_state=updating", "default_severity": "WARNING"},
    {"action": "server.astra_updated", "description": "Worker reported OS update outcome (callback clears updating-lock; on success binds os_version and triggers inventory)", "default_severity": "WARNING"},
    {"action": "server.astra_update_locked", "description": "Operation blocked because the server is being updated (busy_state=updating); all operations rejected with SERVER_UPDATING until the update completes", "default_severity": "WARNING"},
    {"action": "server.astra_update_recovered", "description": "Scheduled sweep force-released a server stuck in busy_state=updating past the TTL (astra-updated callback never arrived: worker died/lost the task). Lock cleared to free so the server is usable again; actor_type=system", "default_severity": "WARNING"},
    {"action": "server_account.bootstrap_resolved", "description": "Linked server account credentials decrypted to bootstrap server.prepare (account-mode prepare instead of manual creds)", "default_severity": "CRITICAL"},
    # Per-server управляющие креды (#3): генерация на prepare, ротация, раскрытие воркеру.
    {"action": "server.management_creds_generated", "description": "Per-server management credentials (Ed25519 key + dbos password) generated and stored on server.prepare dispatch (first generation; sticky reuse does not emit)", "default_severity": "CRITICAL"},
    {"action": "server.management_creds_rotated", "description": "Per-server management credentials rotated: (a) POST /servers/{id}/management-credentials/rotate dispatch; (b) worker applied-callback (pending cleared, previous zeroed, rotated_at set)", "default_severity": "CRITICAL"},
    {"action": "server.management_credentials_revealed", "description": "Decrypted per-server management credentials (private key + dbos password) revealed to worker via internal fetch. WARNING: routine internal pull before managed ops, not a human reveal. failure: reason in {server_not_found, no_creds_stored, decrypt_failed}", "default_severity": "WARNING"},
    # Sensitive: показ расшифрованных секретов (internal endpoints для worker'а)
    {"action": "ipmi_controller.view_credentials", "description": "Decrypted IPMI credentials revealed (internal)", "default_severity": "WARNING"},
    {"action": "ipmi_controller.credentials_revealed", "description": "Decrypted IPMI password revealed to user (base64) via GET ipmi card with view_credentials", "default_severity": "CRITICAL"},
    {"action": "ipmi_controller.credentials_revealed_throttled", "description": "Subsequent BMC password reveal within audit window (actor+controller) — первый reveal уже залогирован CRITICAL `ipmi_controller.credentials_revealed`; INFO-копия для трассировки шумного UI-polling'а без CRITICAL-флуда SIEM", "default_severity": "INFO"},
    {"action": "server_account.view_password", "description": "Decrypted server account password revealed (internal)", "default_severity": "WARNING"},
    {"action": "server_account.password_revealed", "description": "Decrypted server account password revealed to user (base64) via GET account card with view_password", "default_severity": "CRITICAL"},
    {"action": "server_account.password_revealed_throttled", "description": "Subsequent password reveal within audit window (actor+account) — first reveal was already logged as CRITICAL `server_account.password_revealed`; INFO-копия для трассировки шумного UI-polling'а без CRITICAL-флуда SIEM", "default_severity": "INFO"},
    {"action": "server_account.rotate_password", "description": "Server account password rotated", "default_severity": "CRITICAL"},
    # Worker → server_service callbacks (write-direction internal API)
    {"action": "server.inventory_received", "description": "Inventory facts received from worker (hardware probe callback)", "default_severity": "INFO"},
    {"action": "inventory.drift_detected", "description": "Hardware-inventory facts from a box diverge from stored server fields (hostname/cpu_*). DB is source of truth and is NOT overwritten — drift reported with old/new. First-write (NULL) is saved; os_version stays the box→DB exception.", "default_severity": "WARNING"},
    {"action": "server_account.users_inventory_received", "description": "OS-user inventory received from worker and reconciled against server_accounts (callback)", "default_severity": "INFO"},
    {"action": "server_account.drift_detected", "description": "Inventory found OS-user state on a box diverging from the DB (attributes / presence / unknown login). DB is source of truth and is NOT overwritten — only link presence is updated", "default_severity": "WARNING"},
    {"action": "server_account.provision_status", "description": "OS-user provision/update/deprovision result received from worker, present_on_server updated (callback)", "default_severity": "INFO"},
    {"action": "ipmi_controller.credentials_rotated_callback", "description": "Worker confirmed IPMI credentials rotation (callback after worker-side encrypt)", "default_severity": "WARNING"},
    # Постепенная фоновая ротация мастер-ключа (зовётся server_worker'ом)
    {"action": "secrets.reencrypt_batch", "description": "Background secret re-encryption batch processed (called by server_worker periodic task, legacy sync path)", "default_severity": "INFO"},
    {"action": "secrets.reencrypt_seed", "description": "Reencrypt outbox seeded with pending owner rows whose ciphertext version differs from the active key", "default_severity": "INFO"},
    {"action": "secrets.reencrypt_done", "description": "Reencrypt outbox row finalized successfully (owner-row updated with active-version ciphertext)", "default_severity": "INFO"},
    {"action": "secrets.reencrypt_failed", "description": "Reencrypt outbox row marked failed (decrypt/encrypt error reported by worker)", "default_severity": "WARNING"},
    {"action": "secrets.reencrypt_outbox_cleanup", "description": "Done outbox rows older than retention threshold removed", "default_severity": "INFO"},
    {"action": "secrets.migration.skipped", "description": "Outbox-row finalize_done попал на состояние без повторного апдейта (status_not_processing / owner_vanished / owner_ciphertext_changed — owner-row пропал или ротировался параллельно). Идемпотентность сохранена, фиксируем для SIEM.", "default_severity": "WARNING"},
    {"action": "secrets.migration_key_missing", "description": "Re-encrypt batch не смог расшифровать row из-за пропавшего мастер-ключа (ENCRYPTION_KEY_MISSING) — мисконфиг env, требует немедленного вмешательства оператора", "default_severity": "ERROR"},
    {"action": "secrets.migration_decrypt_failed", "description": "Re-encrypt batch не смог расшифровать/перешифровать row (неаутентичный или битый ciphertext, чужой AAD) — отдельная row выпала из миграции", "default_severity": "ERROR"},
    # Ops-runner'ы (rotation_runner, …) — отдельный s2s-канал с shared-secret'ом.
    {"action": "ops.migration_status_read", "description": "Rotation-runner прочитал /internal/migration_status (shared-secret канал); фиксируем кто и когда смотрел на legacy-residue перед drop'ом старого ключа", "default_severity": "INFO"},
    {"action": "ops.encryption_rotate", "description": "Rotation-runner ввёл новую версию мастер-ключа активной через keystore и засидил reencrypt-outbox (рантайм-ротация без простоя)", "default_severity": "CRITICAL"},
    {"action": "ops.encryption_retire", "description": "Rotation-runner убрал старую версию мастер-ключа из keystore после полной ре-шифрации (0 строк на версии)", "default_severity": "CRITICAL"},
    # account_admin инициирует ротацию из UI (платформенный канал) — отдельные
    # action-name'ы от ops-runner'ских, чтобы SIEM различал «человек из UI» и
    # «автоматический s2s-runner».
    {"action": "encryption.admin_rotate", "description": "account_admin ввёл новую версию мастер-ключа активной через UI (admin /admin/encryption/rotate); keystore-bump + reencrypt-outbox seed", "default_severity": "CRITICAL"},
    {"action": "encryption.admin_retire", "description": "account_admin убрал старую версию мастер-ключа из keystore через UI (admin /admin/encryption/retire) после полной ре-шифрации", "default_severity": "CRITICAL"},
    {"action": "encryption.auto_retire", "description": "Не-активная версия мастер-ключа автоматически выведена из keystore сразу после того, как перешифровочный батч осушил её (0 owner-строк и пустой reencrypt-outbox на версии); actor_type=system, материал ключа удалён", "default_severity": "CRITICAL"},
    {"action": "encryption.force_reencrypt_enabled", "description": "Включён force-режим перешифровки при ротации ключа (mode=force): maintenance-gate закрывает сервис 503 REENCRYPT_IN_PROGRESS на всё, кроме статуса и health, пока дренер не осушит legacy-остаток", "default_severity": "CRITICAL"},
    {"action": "encryption.force_reencrypt_cleared", "description": "Force-режим перешифровки автоматически снят после полного осушения (0 legacy-строк и пустой outbox); сервис разблокирован, опустевшие версии ключа выведены; actor_type=system", "default_severity": "CRITICAL"},
    # Конфиг управляющей учётки — платформенный singleton под account_admin.
    {"action": "management_user_config.update", "description": "account_admin обновил конфиг управляющей учётки (имя управляющего пользователя + пер-режимные группы/bootstrap-команды через PUT /management-user-config); смена login помечается login_changed для будущего cutover-фан-аута", "default_severity": "WARNING"},
    {"action": "management_user_config.sync", "description": "Недеструктивный re-bootstrap управляющей учётки (management_user_sync) поставлен на подготовленный сервер high-priority при изменении конфига управляющей учётки (фан-аут PUT /management-user-config)", "default_severity": "WARNING"},
    {"action": "management_user_sync_fanout.truncated", "description": "Фан-аут management_user_sync превысил MANAGEMENT_USER_SYNC_FANOUT_MAX — хвост подготовленных серверов вырезан, выровняется на следующем PUT/prepare", "default_severity": "WARNING"},
    # Настройки проб статуса — платформенный singleton под account_admin.
    {"action": "settings.probes_updated", "description": "account_admin изменил настройки проб статуса (частота/вкл-выкл reachability=ping+ssh и power=ipmi/domstate через PUT /settings/probes); server_worker читает их через internal-эндпоинт. denied-запись — worker без гранта (server, prepare_callback) попытался прочитать internal-настройки", "default_severity": "WARNING"},
    {"action": "server.probe_targets_listed", "description": "server_worker probe-loop прочитал список целей пробинга (GET /internal/probe-targets) — серверы + ВМ для фоновых reachability/power циклов. Эмитится только denied: worker без гранта (server, prepare_callback) попытался прочитать список", "default_severity": "WARNING"},
    {"action": "auto_inventory_sweep.truncated", "description": "Плановый авто-inventory прогон превысил AUTO_INVENTORY_FANOUT_MAX — хвост подготовленных серверов вырезан, выровняется на следующем прогоне", "default_severity": "WARNING"},
    {"action": "power_sweep.truncated", "description": "Частый power-sweep превысил AUTO_INVENTORY_FANOUT_MAX — хвост серверов вырезан, выровняется на следующем прогоне", "default_severity": "WARNING"},
    {"action": "vm_status_sweep.truncated", "description": "Частый статус-sweep ВМ превысил AUTO_INVENTORY_FANOUT_MAX — хвост ВМ вырезан, выровняется на следующем прогоне", "default_severity": "WARNING"},
    # Server accounts — CRUD (user-facing)
    {"action": "server_account.create", "description": "Server account created", "default_severity": "CRITICAL"},
    {"action": "server_account.view", "description": "Server account viewed", "default_severity": "INFO"},
    {"action": "server_account.list", "description": "Server accounts listed (emitted on denied)", "default_severity": "INFO"},
    {"action": "server_account.update", "description": "Server account updated", "default_severity": "INFO"},
    {"action": "server_account.adopted_from_host", "description": "Operator adopted observed OS-user state from a specific host into the DB (per-field: has_sudo/unix_groups/shell), DB-only without fan-out to other servers", "default_severity": "WARNING"},
    {"action": "server_account.imported_from_host", "description": "Operator imported an unknown OS-user found by inventory into the DB as a new account (linked to the server, present_on_server=True; discovered source has no stored password)", "default_severity": "WARNING"},
    {"action": "server_account.ignored_login_added", "description": "Login added to the department ignore-list — inventory will no longer report it as an unknown OS-user", "default_severity": "WARNING"},
    {"action": "server_account.ignored_login_removed", "description": "Login removed from the department ignore-list — inventory will report it as unknown again if present on a box", "default_severity": "INFO"},
    {"action": "server_account.ignored_logins_listed", "description": "Department ignore-list of OS-user logins listed (emitted on denied)", "default_severity": "INFO"},
    {"action": "server_account.recreate_login", "description": "Account OS-login recreated end-to-end (deprovision under old login → DB rename → provision under new login on all linked servers); access restricted to department_admin/service-admin", "default_severity": "CRITICAL"},
    {"action": "server_account.ssh_key_set", "description": "Account SSH key set/replaced (generate Ed25519 or supply public key); saved to DB and applied via account.update_on_host to push authorized_keys", "default_severity": "WARNING"},
    {"action": "server_account.ssh_key_rotate", "description": "Account SSH key regenerated (compromise case): new Ed25519 keypair saved, previous private retained, private returned once, applied via account.update_on_host", "default_severity": "CRITICAL"},
    {"action": "server_account.ssh_key_generated", "description": "Account SSH keypair auto-generated during credential resolve (prepare/provision) because the account had none; public key pushed to authorized_keys", "default_severity": "INFO"},
    {"action": "server_account.ssh_private_key_revealed", "description": "Decrypted server account SSH private key revealed to user (PEM) via GET ssh_private_key with view_password. failure: reason in {no_ssh_private_key_stored, decrypt_failed}", "default_severity": "CRITICAL"},
    {"action": "server_account.reveal_previous_ssh_private_key", "description": "Decrypted retained PREVIOUS server account SSH private key revealed to user (PEM) via GET previous_ssh_private_key with view_password. failure: reason in {no_previous_ssh_key_stored, decrypt_failed}", "default_severity": "CRITICAL"},
    {"action": "server_account.clear_previous_ssh_key", "description": "Operator manually cleared the retained PREVIOUS server account SSH private key via DELETE previous_ssh_private_key with rotate_password. Idempotent: no-op when nothing retained. details.cleared reflects whether a key was actually removed", "default_severity": "INFO"},
    {"action": "server_account.apply_credentials", "description": "Account password+ssh key pushed to linked servers via account.update_on_host (manual POST /apply or auto after set/rotate password/key)", "default_severity": "WARNING"},
    {"action": "server_account.link_servers", "description": "Server account linked to additional servers", "default_severity": "INFO"},
    {"action": "server_account.unlink_servers", "description": "Server account unlinked from servers", "default_severity": "INFO"},
    {"action": "server_account.delete", "description": "Server account deleted", "default_severity": "CRITICAL"},
    # Управление матрицей прав
    {"action": "permission.grant", "description": "entity_permission row granted", "default_severity": "CRITICAL"},
    {"action": "permission.revoke", "description": "entity_permission row revoked", "default_severity": "CRITICAL"},
    # Инстанс-уровневый ACL (resource_role_permissions): точечный грант роли на
    # конкретный ресурс (server / server_account) поверх тип-wide матрицы.
    {"action": "resource_permission.grant", "description": "Instance-level ACL row granted (role gets an action on a specific server/server_account). failure reasons: action_not_instance_grantable / resource_not_found_or_cross_dept / race_already_exists", "default_severity": "CRITICAL"},
    {"action": "resource_permission.revoke", "description": "Instance-level ACL row revoked (role loses an action on a specific server/server_account). failure reasons: resource_not_found_or_cross_dept / not_found", "default_severity": "CRITICAL"},
    {"action": "resource_permission.propagate", "description": "Instance-level ACL grants copied from a source resource to target resources of the same type (mode=merge adds missing; mode=mirror also removes extras). details carry total_added/total_removed/targets_applied", "default_severity": "CRITICAL"},
    # IPMI controllers — CRUD (user-facing)
    {"action": "ipmi_controller.create", "description": "IPMI controller registered for a server", "default_severity": "CRITICAL"},
    {"action": "ipmi_controller.view", "description": "IPMI controller card viewed", "default_severity": "INFO"},
    {"action": "ipmi_controller.list", "description": "IPMI controllers listed (emitted on denied)", "default_severity": "INFO"},
    {"action": "ipmi_controller.update", "description": "IPMI controller updated (kind/endpoint/username)", "default_severity": "INFO"},
    {"action": "ipmi_controller.delete", "description": "IPMI controller deleted", "default_severity": "CRITICAL"},
    {"action": "ipmi_controller.rotate_credentials", "description": "Deprecated user-facing rotate route — 410 GONE warning audit", "default_severity": "WARNING"},
    # Installed packages — live SSH-probe через worker (без БД). Единственный
    # action: dispatch'ер `POST /servers/{id}/installed-packages` + worker-task
    # `installed_packages.list` (обе стороны эмитят с одинаковым action-name).
    {"action": "installed_packages.list", "description": "Installed packages live-listed via worker (SSH dpkg-query/rpm -qa)", "default_severity": "INFO"},
    # История прошлых package-запросов сервера: GET /servers/{id}/packages/history.
    # Эмитится только на denied/not-found (read success не аудитим, как у task.view).
    {"action": "installed_packages.history", "description": "Installed-package request history read (GET /servers/{id}/packages/history); emitted on denied / not-found", "default_severity": "INFO"},
    # Изменяющие массовые операции с пакетами — dispatch'ер
    # `POST /servers/packages/bulk-action` + worker-таски
    # `installed_packages.{install,remove,update}`. WARNING — мутация на боксе.
    {"action": "server.packages_install", "description": "Packages installed on a server via worker (SSH apt-get/dnf/apk under sudo)", "default_severity": "WARNING"},
    {"action": "server.packages_remove", "description": "Packages removed from a server via worker (SSH apt-get/dnf/apk under sudo)", "default_severity": "WARNING"},
    {"action": "server.packages_update", "description": "Packages updated/upgraded on a server via worker (SSH apt-get/dnf/apk under sudo)", "default_severity": "WARNING"},
    # OS versions — глобальный каталог. Чтение доступно любому
    # аутентифицированному актору, без аудита; пишутся только мутации.
    {"action": "os_version.create", "description": "OS version catalog entry created", "default_severity": "INFO"},
    {"action": "os_version.update", "description": "OS version updated", "default_severity": "INFO"},
    {"action": "os_version.delete", "description": "OS version deleted", "default_severity": "WARNING"},
    {"action": "os.unknown_observed", "description": "Inventory callback принёс os_version, не прошедший whitelist KNOWN_OS_PREFIXES. Запись в os_versions НЕ создаётся, server.os_version_id остаётся прежним", "default_severity": "WARNING"},
    # Макросы консоли (личные + системные в отделе). См. services/console_macro.py.
    {"action": "console_macro.create", "description": "Console macro created (personal or system/department-wide). details: is_system, name", "default_severity": "INFO"},
    {"action": "console_macro.update", "description": "Console macro updated. details: is_system, changed fields", "default_severity": "INFO"},
    {"action": "console_macro.delete", "description": "Console macro deleted. details: is_system", "default_severity": "INFO"},
    # Worker-dispatch endpoints (см. endpoints/worker_dispatch.py).
    {"action": "server.inventory_sync", "description": "Inventory-sync (SSH-probe) dispatched to worker", "default_severity": "INFO"},
    {"action": "server.users_inventory_triggered", "description": "OS-user inventory (SSH getent) dispatched to worker — target=server (kick'ается со стороны сервера, аккаунты только результат)", "default_severity": "INFO"},
    {"action": "server_account.rotate_password_dispatch", "description": "Server account password rotation dispatched to worker (SSH apply + storage round-trip)", "default_severity": "CRITICAL"},
    {"action": "mass_rotation.partial_failure", "description": "Массовая ротация (mode=all) частично применилась: на K серверов dispatch успешен, на K+1 worker отбил ServiceUnavailable, остаток не пытались. Auto-cancel НЕ выполняется (риск частичных откатов) — оператору отдаются task_ids для ручной отмены", "default_severity": "WARNING"},
    {"action": "server_account.provision", "description": "OS-user provision (useradd) dispatched to worker", "default_severity": "WARNING"},
    {"action": "server_account.update_on_host", "description": "OS-user attribute sync (usermod) dispatched to worker", "default_severity": "INFO"},
    {"action": "fanout_update_on_host.truncated", "description": "PATCH-fanout превысил FANOUT_UPDATE_ON_HOST_MAX — хвост серверов вырезан, выровняется на следующем sweep'е", "default_severity": "WARNING"},
    {"action": "server_account.deprovision", "description": "OS-user deprovision (userdel) dispatched to worker", "default_severity": "WARNING"},
    {"action": "server_account.link_vms", "description": "Server account (shared pool) linked to VMs (server_account_vms)", "default_severity": "INFO"},
    {"action": "server_account.unlink_vms", "description": "Server account (shared pool) unlinked from VMs", "default_severity": "INFO"},
    {"action": "server_account.vm_provision", "description": "OS-user provision (useradd) in a VM guest dispatched to worker", "default_severity": "WARNING"},
    {"action": "server_account.vm_update_on_host", "description": "OS-user attribute sync (usermod) in a VM guest dispatched to worker", "default_severity": "INFO"},
    {"action": "server_account.vm_deprovision", "description": "OS-user deprovision (userdel) in a VM guest dispatched to worker", "default_severity": "WARNING"},
    {"action": "ipmi_controller.rotate_dispatch", "description": "IPMI controller password rotation dispatched to worker (currently safety-guarded: worker fails fast until storage round-trip exists)", "default_severity": "CRITICAL"},
    # Busy-lease, OS-sync — пользовательский CRUD над servers.busy_state / os_version_id
    {"action": "server.acquire", "description": "Server acquired (busy_state set to busy) for a test/lease", "default_severity": "INFO"},
    {"action": "server.release", "description": "Server released (busy_state back to free)", "default_severity": "INFO"},
    {"action": "server.update_os_version", "description": "Server os_version_id updated manually (without inventory sync)", "default_severity": "INFO"},
    # Read-only IPMI views (user-facing)
    {"action": "ipmi_controller.view_credentials_meta", "description": "IPMI credentials metadata viewed (no plaintext password)", "default_severity": "INFO"},
    {"action": "server.power_status_cached", "description": "Cached power_state viewed from server row (no live probe)", "default_severity": "INFO"},
    {"action": "server.power_state_updated", "description": "Worker wrote live power probe result back to server cache (POST /internal/servers/{id}/power-state): power_state + source (bmc/ping/ssh) + checked_at", "default_severity": "INFO"},
    # Worker-task cancel (POST /tasks/{id}/cancel). Помечает row CANCELLED;
    # worker при подборе следующей попытки пропускает её через CAS на
    # mark_running. running-task'у не убивает принудительно — graceful.
    # denied: permission_denied / system_task_admin_required / task_not_found_or_cross_dept.
    # failure: task_not_found (гонка после check'а) / not_cancellable (terminal status).
    # Mid-run cancel сам по себе фиксируется не здесь, а worker'ом — он пишет
    # audit с action=<task_kind>, status=failure, details.reason=cancelled_midrun.
    {"action": "task.cancelled", "description": "Worker task cancelled by operator (status set to CANCELLED in dev_server_worker.tasks; running task finishes current stage)", "default_severity": "WARNING"},
    {"action": "task.view", "description": "Worker task history read (list GET /tasks or detail GET /tasks/{id}); emitted on denied access (no view grant)", "default_severity": "INFO"},
    # Worker-emitted runner/lifecycle и handler-финальные action'ы. Описаны
    # подробно в `server_worker/AUDIT_EVENTS.md`; здесь регистрируются под
    # тем же сервисом, чтобы оператор/SIEM находили их через registry API
    # (`GET /api/logging/v1/services/server_service/events`). Worker сам в
    # loging_service не регистрируется — публикует от имени `server_service`.
    {"action": "task.worker_shutdown", "description": "Worker graceful shutdown — running task forced to retry или failed (target_id = server_id связанной таски при наличии, иначе task_id; severity ERROR/WARNING зависит от will_retry)", "default_severity": "ERROR"},
    {"action": "task.worker_orphaned", "description": "Orphan-sweep нашёл running task'у с мёртвым worker_id и принудительно перевёл её в failed без retry-decision (target_id = server_id связанной таски при наличии, иначе task_id)", "default_severity": "ERROR"},
    {"action": "task.deleted_midrun", "description": "Worker обнаружил исчезновение task-row между mark_running и terminal write (retention/manual DELETE); terminal mark пропущен, audit фиксирует факт", "default_severity": "WARNING"},
    {"action": "secrets.reencrypt_tick", "description": "Periodic `secrets.reencrypt_lazy` worker-тик: success/idle (allowed=True), warning при finalize_errors, failure при app_env_mismatch worker↔server_service (явный severity ERROR)", "default_severity": "INFO"},
    {"action": "audit.outbox_reattempt_manual", "description": "Оператор форсит CLI-командой `outbox-reattempt` повторную доставку конкретной row'ы worker'ского audit_outbox", "default_severity": "WARNING"},
    {"action": "worker_dispatch.orphan_detected", "description": "Cross-DB orphan: worker-row закоммичен, outbox-INSERT и compensation `_delete_task_row` упали — orphan не подберёт poller (нет outbox-row), сидит до retention cleanup. Каждый случай — сигнал в SIEM (метрика `worker_dispatch_orphans_total`).", "default_severity": "ERROR"},
    {"action": "bmc.tls_downgrade", "description": "Worker BMC-probe перешёл на менее защищённый канал (https_verify→https_noverify или *→http) — фиксируется при каждом фактическом переходе", "default_severity": "WARNING"},
    {"action": "server_account.password_rotate", "description": "Worker завершил ротацию пароля сервисной учётки (apply через SSH + callback `submit_rotated_password`); финальная action-name стороны worker'а, dispatch — `server_account.rotate_password_dispatch`", "default_severity": "CRITICAL"},
    {"action": "ipmi_controller.password_rotate", "description": "Worker завершил ротацию IPMI/BMC-пароля (apply + verify + callback `submit_rotated_ipmi_password`); финальная action-name стороны worker'а, dispatch — `ipmi_controller.rotate_dispatch`", "default_severity": "CRITICAL"},
    {"action": "server_account.users_inventory", "description": "Worker завершил OS-user inventory через SSH `getent` и отдал список через callback `submit_users_inventory`; target_type=server (срез хоста, не конкретной учётки)", "default_severity": "INFO"},
    # Интерактивная SSH-консоль (WebSocket-мост). `session_open`/`session_close`
    # эмитит server_service на connect/disconnect WS; `command` — worker на
    # каждую введённую строку (Enter) в PTY. Категория `ssh_console` отдельная
    # для UI-фильтрации живого shell-доступа.
    {"action": "ssh_console.session_open", "description": "Пользователь открыл интерактивную SSH-консоль к серверу (WebSocket подключился, PTY-сессия запрошена у worker'а). target_type=server", "default_severity": "INFO"},
    {"action": "ssh_console.session_close", "description": "Интерактивная SSH-консоль закрыта (WS disconnect / таймаут бездействия / ошибка PTY). details несут reason. target_type=server", "default_severity": "INFO"},
    {"action": "ssh_console.command", "description": "Команда, введённая в интерактивной SSH-консоли (одна строка по Enter); эмитится worker'ом на PTY-мосте. details: command (redacted), session_id, server_id. WARNING при ненулевом exit-коде, если он доступен. target_type=server", "default_severity": "INFO"},
    # VM-домен. CRITICAL — create/delete/prepare-hub (появление/снос
    # виртуалки, подготовка хоста); WARNING — питание/бронь/отказ по брони;
    # INFO — просмотр и рутинные callback-апдейты состояния.
    {"action": "vm.create", "description": "VM create requested (dispatch vm.create): permission/capacity/hub-readiness checks. failure reasons: department_isolation / hub_not_found_or_cross_dept / hub_not_prepared / duplicate / worker_unreachable", "default_severity": "CRITICAL"},
    {"action": "vm.created", "description": "VM record created and vm.create dispatched to worker (busy_state=creating)", "default_severity": "CRITICAL"},
    {"action": "vm.view", "description": "VM card viewed (emitted on denied/not-found)", "default_severity": "INFO"},
    {"action": "vm.deleted", "description": "VM deleted: record removed and vm.delete dispatched to worker to clean up the hypervisor domain", "default_severity": "CRITICAL"},
    {"action": "vm.create_failed", "description": "VM auto-removed by the reconciler because its vm.create task terminally failed (worker status=failed): best-effort undefine on the hub + cascade row delete. actor = the VM's original creator (created_by); details carry create_task_id / cleanup_task_id / last_error. Emitted per VM by the periodic vms.reconcile_failed_creates sweep", "default_severity": "WARNING"},
    {"action": "vm.powered", "description": "VM power action dispatched to worker (vm.power: start/shutdown/reboot/reset/destroy)", "default_severity": "WARNING"},
    {"action": "vm.status", "description": "VM status live-probe dispatched (vm.status): virsh domstate + guest ping/ssh over the hub. Emitted per VM by the periodic vms.status_sweep (source=auto_vm_status_sweep). failure reasons: idempotent_conflict / worker_unreachable / dispatch_error", "default_severity": "INFO"},
    {"action": "vm.reserved", "description": "VM reserved (status set to run test / debug test / <login>) for a test/lease", "default_severity": "WARNING"},
    {"action": "vm.released", "description": "VM released (status back to free)", "default_severity": "WARNING"},
    {"action": "vm.status_updated", "description": "VM booking status set directly (PATCH /vms/{id}/status)", "default_severity": "WARNING"},
    {"action": "vm.reservation_denied", "description": "VM operation blocked because it is reserved by another user and caller is neither the reservation owner nor a department/service admin", "default_severity": "WARNING"},
    {"action": "vm.busy_denied", "description": "VM operation blocked because a lifecycle operation is in progress (busy_state creating/deleting/updating/powering)", "default_severity": "WARNING"},
    {"action": "vm.state_updated", "description": "Worker wrote VM state back to server cache (POST /internal/vms/{id}/state): power_state / ip / status / busy_state / error (partial, idempotent)", "default_severity": "INFO"},
    # VM update (cpu/ram) + диски.
    {"action": "vm.updated", "description": "VM resources changed (dispatch vm.update: cpu/ram; stop→edit XML→start), busy_state=updating. failure reasons: not_found_or_cross_dept / worker_unreachable", "default_severity": "WARNING"},
    {"action": "vm.disk_managed", "description": "VM disk create/delete/resize dispatched to worker (vm.disk_attach / vm.disk_delete / vm.disk_resize). details.op distinguishes the operation. failure reasons: not_found_or_cross_dept / disk_not_found / duplicate / worker_unreachable", "default_severity": "WARNING"},
    {"action": "vm.disks_synced", "description": "Worker synced VM disk facts back (POST /internal/vms/{id}/disks): state/path/target_dev/serial/size per disk_id (partial, idempotent)", "default_severity": "INFO"},
    # Каталог боксов-образов ВМ.
    {"action": "vm_image.refresh", "description": "VM image catalog synced from the FTP box config (POST /vm-images/refresh, libvirt_box section). details: source/synced/created/updated. denied: permission_denied", "default_severity": "INFO"},
    {"action": "vms_hub.prepared", "description": "Prepare server as VMS-hub: (a) dispatch vms_hub.prepare (permission/prepared/virtualization gate); (b) worker callback POST /internal/servers/{id}/vms-hub-state marks is_vms_hub + virtualization + phy_if. failure reasons: not_found_or_cross_dept / prepare_required / virtualization_unsupported / worker_unreachable", "default_severity": "CRITICAL"},
    # Снимки ВМ + обновления ОС / гостевой allta / пароль.
    {"action": "vm.snapshot_created", "description": "VM snapshot create dispatched (vm.snapshot_create), busy_state=snapshotting. per_snapshot: new snapshot inherits current mgmt creds. failure reasons: not_found_or_cross_dept / system_snapshot_protected / duplicate / worker_unreachable", "default_severity": "WARNING"},
    {"action": "vm.snapshot_reverted", "description": "VM revert to snapshot dispatched (vm.snapshot_revert), busy_state=reverting. per_snapshot switches VM active creds to the snapshot's. failure reasons: snapshot_not_found / system_snapshot_protected / worker_unreachable", "default_severity": "WARNING"},
    {"action": "vm.snapshot_deleted", "description": "VM snapshot delete dispatched (vm.snapshot_delete) and row removed. system _build snapshots cannot be deleted manually (403 VM_SNAPSHOT_SYSTEM_PROTECTED). failure reasons: snapshot_not_found / worker_unreachable", "default_severity": "WARNING"},
    {"action": "vm.snapshots_synced", "description": "Worker synced VM snapshots back (POST /internal/vms/{id}/snapshots): batch upsert by name (name/parent/kind/is_system/state/size_bytes/is_current + per-snapshot creds). is_current=true moves the current flag", "default_severity": "INFO"},
    {"action": "vm.astra_updated", "description": "VM OS update by RC dispatched (vm.astra_update: revert <ver>_build → repo → astra-update → snapshot <rc>), busy_state=updating. repository_urls resolved from the registered os_version. failure reasons: not_found_or_cross_dept / snapshot_exists / os_version_not_registered / worker_unreachable", "default_severity": "WARNING"},
    {"action": "vm.allta_updated", "description": "VM guest allta update (+ optional u password) dispatched (vm.allta_update), busy_state=updating. reroll walks all non-_build snapshots. failure reasons: not_found_or_cross_dept / worker_unreachable", "default_severity": "WARNING"},
    {"action": "vm.passwd_changed", "description": "VM guest u password change dispatched (vm.passwd; same op as allta-update, password mandatory), busy_state=updating. failure reasons: not_found_or_cross_dept / worker_unreachable", "default_severity": "WARNING"},
    {"action": "vm.prepared", "description": "VM management onboarding: (a) dispatch vm.prepare (permission/booking/lock gate) — server_service generates per-VM mgmt ssh key+password, encrypts and hands them to the worker with the image default creds; (b) worker callback POST /internal/vms/{id}/prepared sets is_managed + clears pending. failure reasons: rotation_pending / worker_reported_failure / worker_unreachable", "default_severity": "CRITICAL"},
    {"action": "vm.creds_rotated", "description": "VM management credentials rotation dispatched (vm.prepare with operation=rotate_creds), busy_state=preparing. Generates fresh key+password, encrypts, hands plaintext to the worker to install. failure reasons: prepare_required / rotation_pending / worker_unreachable", "default_severity": "CRITICAL"},
    {"action": "vm.mgmt_credentials_revealed", "description": "Decrypted per-VM management credentials (private key + password) revealed to worker via internal fetch GET /internal/vms/{id}/mgmt-credentials. WARNING: routine internal pull before a managed guest op, not a human reveal. failure reasons: vm_not_found / no_creds_stored / decrypt_failed / target_department_mismatch", "default_severity": "WARNING"},
    {"action": "vm.net_updated", "description": "VM network mode change dispatched (vm.set_network: bridge static from a pool / nat via libvirt), busy_state=networking. bridge allocates or validates the IP against the pool. failure reasons: not_found_or_cross_dept / ip_in_use / pool_exhausted / worker_unreachable", "default_severity": "WARNING"},
    {"action": "vm_ip_pool.created", "description": "VM IP pool created (IPAM, POST /vm-ip-pools). failure reasons: department_isolation / duplicate / invalid_range", "default_severity": "INFO"},
    {"action": "vm_ip_pool.updated", "description": "VM IP pool updated (PATCH /vm-ip-pools/{id})", "default_severity": "INFO"},
    {"action": "vm_ip_pool.deleted", "description": "VM IP pool deleted (DELETE /vm-ip-pools/{id})", "default_severity": "INFO"},
    # Автозапуск ВМ + консоль.
    {"action": "vm.autostart_set", "description": "VM autostart toggled (dispatch vm.set_autostart: virsh autostart [--disable]). Gated by vm_power. failure reasons: not_found_or_cross_dept / worker_unreachable", "default_severity": "WARNING"},
    {"action": "vm.console_accessed", "description": "VM console access granted (POST /vms/{id}/console): token + hub host + port/serial-path/user by kind (ssh/vnc/serial/spice). vnc/spice get a signed token + ws_url to the console proxy. Gated by (vm, view) + booking. No plaintext creds returned — proxy fetches them via internal mgmt-credentials", "default_severity": "WARNING"},
    {"action": "vm.packages_listed", "description": "VM guest package probe dispatched (GET /vms/{id}/packages?refresh=true → vm.list_packages): worker lists dpkg/rpm over SSH via the hub. Gated by (vm, view); requires prepared VM + guest IP + live hub. failure reasons: prepare_required / guest_ip_unknown / hub_unavailable / worker_unreachable", "default_severity": "INFO"},
    {"action": "vm.packages_synced", "description": "Worker wrote VM guest package inventory back (POST /internal/vms/{id}/packages): full overwrite of the stored package list. Access: (server, *, prepare_callback)", "default_severity": "INFO"},
    {"action": "vm.packages_history", "description": "VM package probe request history read (GET /vms/{id}/packages/history); emitted on denied / not-found", "default_severity": "INFO"},
    {"action": "vm.inventory_sync", "description": "VM guest hardware-inventory probe dispatched (POST /vms/{id}/inventory-sync → vm.inventory_sync): worker collects hostname/kernel/cpu/disks/os over SSH via the hub (management key). Gated by (vm, vm_prepare); requires prepared VM + guest IP + live hub. failure reasons: prepare_required / guest_ip_unknown / hub_unavailable / worker_unreachable", "default_severity": "INFO"},
    {"action": "vm.users_inventory", "description": "VM guest OS-user inventory dispatched (POST /vms/{id}/users-inventory → vm.users_inventory): worker snapshots getent passwd/group over SSH via the hub, server reconciles against attached accounts (warn-on-drift). Gated by (vm, vm_prepare); requires prepared VM + guest IP + live hub. failure reasons: prepare_required / guest_ip_unknown / hub_unavailable / worker_unreachable", "default_severity": "INFO"},
    # Пресеты стандартных ВМ (vm_preset) + create-default-vms.
    {"action": "vm_preset.created", "description": "VM preset created (POST /vm-presets). failure reasons: department_isolation / duplicate", "default_severity": "INFO"},
    {"action": "vm_preset.updated", "description": "VM preset updated (PATCH /vm-presets/{id})", "default_severity": "INFO"},
    {"action": "vm_preset.deleted", "description": "VM preset deleted (DELETE /vm-presets/{id})", "default_severity": "INFO"},
    {"action": "vm_preset.deployed", "description": "Department presets deployed to a hub (POST /servers/{id}/create-default-vms: series of vm.create). deploy-once: bridge once globally / nat once per hub; already-deployed skipped. failure reasons: hub_not_found_or_cross_dept / hub_not_prepared / all_deployed / duplicate. per-VM vm.created also emitted", "default_severity": "CRITICAL"},
    # Снос VMS-hub'а (rm-vms-hub).
    {"action": "vms_hub.torn_down", "description": "Server torn down from the VMS-hub role (DELETE /servers/{id}/vms-hub): department VM cards removed from the DB (disks/snapshots cascade), is_vms_hub=False, and vms_hub.teardown dispatched to clean the host. failure reasons: not_found_or_cross_dept / not_a_vms_hub / worker_unreachable", "default_severity": "CRITICAL"},
]


def register_events() -> None:
    """POST полного списка событий в loging_service. Вызывается на startup в фоновом thread'е.

    Не блокирует startup — если loging_service недоступен, мы просто
    логгируем WARNING и едем дальше. Сами события всё равно отправятся
    позже через `audit_service.emit()` — registration нужен только чтобы
    loging_service знал про severity defaults для пар (action, status).
    """
    settings = get_settings()
    logging_url = getattr(settings, "logging_service_url", None)
    api_key = getattr(settings, "logging_service_api_key", None)
    if not logging_url or not api_key:
        logger.debug("audit: skipping event registration — LOGGING_SERVICE_URL not configured")
        return
    try:
        resp = httpx.post(
            f"{logging_url}/api/logging/v1/services/{_SERVICE_NAME}/events",
            json={"events": SERVICE_EVENTS},
            headers={**bearer_header(api_key), "X-Service-Identity": "server_service"},
            timeout=getattr(settings, "register_events_timeout_seconds", 2.0),
        )
        if resp.status_code == 200:
            data = resp.json()
            logger.info(
                "audit: registered %d events (added=%d updated=%d)",
                data.get("total"), data.get("added"), data.get("updated"),
            )
        else:
            logger.warning("audit: event registration failed: %s %s", resp.status_code, resp.text)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("audit: event registration error: %s", exc)
