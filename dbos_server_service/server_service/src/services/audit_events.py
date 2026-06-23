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
    {"action": "server_account.bootstrap_resolved", "description": "Linked server account credentials decrypted to bootstrap server.prepare (account-mode prepare instead of manual creds)", "default_severity": "CRITICAL"},
    # Sensitive: показ расшифрованных секретов (internal endpoints для worker'а)
    {"action": "ipmi_controller.view_credentials", "description": "Decrypted IPMI credentials revealed (internal)", "default_severity": "WARNING"},
    {"action": "ipmi_controller.credentials_revealed", "description": "Decrypted IPMI password revealed to user (base64) via GET ipmi card with view_credentials", "default_severity": "CRITICAL"},
    {"action": "ipmi_controller.credentials_revealed_throttled", "description": "Subsequent BMC password reveal within audit window (actor+controller) — первый reveal уже залогирован CRITICAL `ipmi_controller.credentials_revealed`; INFO-копия для трассировки шумного UI-polling'а без CRITICAL-флуда SIEM", "default_severity": "INFO"},
    {"action": "server_account.view_password", "description": "Decrypted server account password revealed (internal)", "default_severity": "WARNING"},
    {"action": "server_account.password_revealed", "description": "Decrypted server account password revealed to user (base64) via GET account card with view_password", "default_severity": "CRITICAL"},
    {"action": "server_account.password_revealed_throttled", "description": "Subsequent password reveal within audit window (actor+account) — first reveal was already logged as CRITICAL `server_account.password_revealed`; INFO-копия для трассировки шумного UI-polling'а без CRITICAL-флуда SIEM", "default_severity": "INFO"},
    {"action": "server_account.rotate_password", "description": "Server account password rotated", "default_severity": "CRITICAL"},
    {"action": "internal.dept_header_missing", "description": "Internal endpoint called without X-Target-Department-Id header (soft mode bypassed server lookup)", "default_severity": "WARNING"},
    # Worker → server_service callbacks (write-direction internal API)
    {"action": "server.inventory_received", "description": "Inventory facts received from worker (hardware probe callback)", "default_severity": "INFO"},
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
    {"action": "secrets.migration.skipped", "description": "Outbox-row finalize_done попал на состояние, не требующее повторного апдейта (status_not_processing — закрыта другой ветвью; owner_vanished / owner_ciphertext_changed — owner-row пропал или ротировался параллельно). Идемпотентность сохранена, факт фиксируем для SIEM", "default_severity": "WARNING"},
    # Ops-runner'ы (rotation_runner, …) — отдельный s2s-канал с shared-secret'ом.
    {"action": "ops.migration_status_read", "description": "Rotation-runner прочитал /internal/migration_status (shared-secret канал); фиксируем кто и когда смотрел на legacy-residue перед drop'ом старого ключа", "default_severity": "INFO"},
    {"action": "ops.encryption_rotate", "description": "Rotation-runner ввёл новую версию мастер-ключа активной через keystore и засидил reencrypt-outbox (рантайм-ротация без простоя)", "default_severity": "CRITICAL"},
    {"action": "ops.encryption_retire", "description": "Rotation-runner убрал старую версию мастер-ключа из keystore после полной ре-шифрации (0 строк на версии)", "default_severity": "CRITICAL"},
    # account_admin инициирует ротацию из UI (платформенный канал) — отдельные
    # action-name'ы от ops-runner'ских, чтобы SIEM различал «человек из UI» и
    # «автоматический s2s-runner».
    {"action": "encryption.admin_rotate", "description": "account_admin ввёл новую версию мастер-ключа активной через UI (admin /admin/encryption/rotate); keystore-bump + reencrypt-outbox seed", "default_severity": "CRITICAL"},
    {"action": "encryption.admin_retire", "description": "account_admin убрал старую версию мастер-ключа из keystore через UI (admin /admin/encryption/retire) после полной ре-шифрации", "default_severity": "CRITICAL"},
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
    {"action": "server_account.ssh_key_set", "description": "Account SSH key set/replaced (generate Ed25519 or supply public key); saved to DB and fanned out via account.provision to push authorized_keys", "default_severity": "WARNING"},
    {"action": "server_account.ssh_key_rotate", "description": "Account SSH key regenerated (compromise case): new Ed25519 keypair saved, private returned once, fanned out via account.provision", "default_severity": "CRITICAL"},
    {"action": "server_account.link_servers", "description": "Server account linked to additional servers", "default_severity": "INFO"},
    {"action": "server_account.unlink_servers", "description": "Server account unlinked from servers", "default_severity": "INFO"},
    {"action": "server_account.delete", "description": "Server account deleted", "default_severity": "CRITICAL"},
    # Управление матрицей прав
    {"action": "permission.grant", "description": "entity_permission row granted", "default_severity": "CRITICAL"},
    {"action": "permission.revoke", "description": "entity_permission row revoked", "default_severity": "CRITICAL"},
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
    # OS versions — глобальный каталог. Чтение доступно любому
    # аутентифицированному актору, без аудита; пишутся только мутации.
    {"action": "os_version.create", "description": "OS version catalog entry created", "default_severity": "INFO"},
    {"action": "os_version.update", "description": "OS version updated", "default_severity": "INFO"},
    {"action": "os_version.delete", "description": "OS version deleted", "default_severity": "WARNING"},
    {"action": "os.unknown_observed", "description": "Inventory callback принёс os_version, не прошедший whitelist KNOWN_OS_PREFIXES. Запись в os_versions НЕ создаётся, server.os_version_id остаётся прежним", "default_severity": "WARNING"},
    # Worker-dispatch endpoints (см. endpoints/worker_dispatch.py).
    {"action": "server.inventory_sync", "description": "Inventory-sync (SSH-probe) dispatched to worker", "default_severity": "INFO"},
    {"action": "server.users_inventory_triggered", "description": "OS-user inventory (SSH getent) dispatched to worker — target=server (kick'ается со стороны сервера, аккаунты только результат)", "default_severity": "INFO"},
    {"action": "server_account.rotate_password_dispatch", "description": "Server account password rotation dispatched to worker (SSH apply + storage round-trip)", "default_severity": "CRITICAL"},
    {"action": "mass_rotation.partial_failure", "description": "Массовая ротация (mode=all) частично применилась: на K серверов dispatch успешен, на K+1 worker отбил ServiceUnavailable, остаток не пытались. Auto-cancel НЕ выполняется (риск частичных откатов) — оператору отдаются task_ids для ручной отмены", "default_severity": "WARNING"},
    {"action": "server_account.provision", "description": "OS-user provision (useradd) dispatched to worker", "default_severity": "WARNING"},
    {"action": "server_account.update_on_host", "description": "OS-user attribute sync (usermod) dispatched to worker", "default_severity": "INFO"},
    {"action": "fanout_update_on_host.truncated", "description": "PATCH-fanout превысил FANOUT_UPDATE_ON_HOST_MAX — хвост серверов вырезан, выровняется на следующем sweep'е", "default_severity": "WARNING"},
    {"action": "server_account.deprovision", "description": "OS-user deprovision (userdel) dispatched to worker", "default_severity": "WARNING"},
    {"action": "ipmi_controller.rotate_dispatch", "description": "IPMI controller password rotation dispatched to worker (currently safety-guarded: worker fails fast until storage round-trip exists)", "default_severity": "CRITICAL"},
    # Busy-lease, OS-sync — пользовательский CRUD над servers.busy_state / os_version_id
    {"action": "server.acquire", "description": "Server acquired (busy_state set to busy) for a test/lease", "default_severity": "INFO"},
    {"action": "server.release", "description": "Server released (busy_state back to free)", "default_severity": "INFO"},
    {"action": "server.update_os_version", "description": "Server os_version_id updated manually (without inventory sync)", "default_severity": "INFO"},
    # Read-only IPMI views (user-facing)
    {"action": "ipmi_controller.view_credentials_meta", "description": "IPMI credentials metadata viewed (no plaintext password)", "default_severity": "INFO"},
    {"action": "server.power_status_cached", "description": "Cached power_state viewed from server row (no live probe)", "default_severity": "INFO"},
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
    {"action": "worker_dispatch.orphan_detected", "description": "Cross-DB сценарий: worker-row уже закоммичен в dev_server_worker, outbox-INSERT упал, compensation `_delete_task_row` тоже упала — остался настоящий orphan, который poller не подберёт (нет outbox-row), но он сидит в worker-БД до retention cleanup'а. Каждое срабатывание — отдельный сигнал в SIEM, growth `worker_dispatch_orphans_total` метрики из `/ready`", "default_severity": "ERROR"},
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
