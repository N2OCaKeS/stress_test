"""Канонический список audit-событий, которые эмитит server_worker.

При старте воркера отсылается в loging_service через `register_events()` —
POST на `/api/logging/v1/services/server_worker/events`. Регистрация нужна,
чтобы loging знал про severity-defaults для пар (action, status): без неё
worker-события падают на INFO-default в каталоге.

Раньше свой каталог регистрировал только server_service (worker публиковал
события от его имени), но `audit_client.emit` шлёт их под
`X-Service-Identity: server_worker` — loging трекает каталоги per-service,
поэтому воркер регистрирует собственный набор.

Каждая запись: action, human description, default_severity (для success;
на failure loging переопределяет по своим правилам). Severity worker-эмитов
почти всегда едет явным полем в payload'е (runner-meta и lifecycle обычно
status=failure), но каталог всё равно задаёт дефолт для success-ветки.
"""

import asyncio
import logging
import time

import httpx

from src.core.config import get_settings
from src.core.http import bearer_header

logger = logging.getLogger("audit")

_SERVICE_NAME = "server_worker"

# Регистрация — startup-косметика. Если loging-каталог временно недоступен,
# для работающего воркера это не критично (незарегистрированный action
# дефолтится на INFO). Поэтому короткий retry только на transient 5xx, без
# агрессивных backoff'ов и без блокировки старта.
_REGISTER_EVENTS_MAX_ATTEMPTS = 2
_REGISTER_EVENTS_RETRY_DELAY_SECONDS = 1.0

SERVICE_EVENTS = [
    # Power-cycle (Redfish/ipmitool) — handler'ы tasks/power.py
    {"action": "server.power_on", "description": "Питание сервера включено воркером через BMC", "default_severity": "WARNING"},
    {"action": "server.power_off", "description": "Питание сервера выключено воркером через BMC", "default_severity": "WARNING"},
    {"action": "server.power_reboot", "description": "Сервер перезагружен воркером через BMC", "default_severity": "WARNING"},
    {"action": "server.power_status", "description": "Живой опрос состояния питания через BMC", "default_severity": "INFO"},
    # Инвентаризация и bootstrap — tasks/inventory.py, tasks/prepare.py
    {"action": "server.inventory_sync", "description": "Снятие hardware-фактов сервера по SSH", "default_severity": "INFO"},
    {"action": "server.prepare", "description": "Бутстрап управляющей учётки на сервере (useradd + authorized_keys)", "default_severity": "INFO"},
    # Обновление ОС — tasks/astra_update.py
    {"action": "server.astra_update", "description": "Обновление ОС Astra по SSH: перезапись sources.list + apt update && astra-update", "default_severity": "WARNING"},
    # Установленные пакеты — tasks/installed_packages.py
    {"action": "installed_packages.list", "description": "Список установленных пакетов снят по SSH (dpkg-query/rpm)", "default_severity": "INFO"},
    {"action": "server.packages_install", "description": "Пакеты установлены на сервере по SSH под sudo", "default_severity": "WARNING"},
    {"action": "server.packages_remove", "description": "Пакеты удалены с сервера по SSH под sudo", "default_severity": "WARNING"},
    {"action": "server.packages_update", "description": "Пакеты обновлены на сервере по SSH под sudo", "default_severity": "INFO"},
    # Сервисные учётки — tasks/users.py, tasks/passwords.py
    {"action": "server_account.provision", "description": "Заведение OS-пользователя на хосте (useradd)", "default_severity": "INFO"},
    {"action": "server_account.update_on_host", "description": "Синхронизация атрибутов OS-пользователя (usermod)", "default_severity": "INFO"},
    {"action": "server_account.deprovision", "description": "Удаление OS-пользователя с хоста (userdel)", "default_severity": "WARNING"},
    {"action": "server_account.users_inventory", "description": "Снятие среза OS-пользователей хоста по SSH (getent)", "default_severity": "INFO"},
    {"action": "server_account.password_rotate", "description": "Ротация пароля сервисной учётки применена по SSH + callback", "default_severity": "WARNING"},
    # VM-менеджер — tasks/vms.py (исполнение по SSH на hub'е)
    {"action": "vms_hub.prepare", "description": "Подготовка сервера как VMS-hub по SSH: libvirt + мост br0 + storage-pool + образы", "default_severity": "CRITICAL"},
    {"action": "vm.create", "description": "Создание ВМ на hub'е по SSH: клон диска + virt-install + провижн + снимки", "default_severity": "CRITICAL"},
    {"action": "vm.power", "description": "Управление питанием ВМ на hub'е по SSH (virsh start/shutdown/reboot/reset/destroy)", "default_severity": "WARNING"},
    {"action": "vm.status", "description": "Живая проба статуса ВМ на hub'е: virsh domstate + ping/ssh гостя (read-only)", "default_severity": "INFO"},
    {"action": "vm.delete", "description": "Удаление ВМ на hub'е по SSH: virsh destroy + virsh undefine --remove-all-storage --snapshots-metadata", "default_severity": "CRITICAL"},
    {"action": "vm.update", "description": "Изменение cpu/ram ВМ по SSH: dumpxml + правка vcpu/memory + virsh define", "default_severity": "WARNING"},
    {"action": "vm.disk_attach", "description": "Подключение диска к ВМ по SSH: qemu-img create + virsh attach-disk (опц. mkfs+fstab в госте)", "default_severity": "WARNING"},
    {"action": "vm.disk_delete", "description": "Отключение и удаление диска ВМ по SSH: virsh detach-disk + rm qcow2", "default_severity": "WARNING"},
    {"action": "vm.disk_resize", "description": "Увеличение диска ВМ по SSH: qemu-img resize + growpart/resize2fs в госте", "default_severity": "WARNING"},
    {"action": "vm.snapshot_create", "description": "Создание снимка ВМ по SSH: virsh snapshot-create-as (disk-only/live)", "default_severity": "WARNING"},
    {"action": "vm.snapshot_delete", "description": "Удаление снимка ВМ по SSH: virsh snapshot-delete", "default_severity": "WARNING"},
    {"action": "vm.snapshot_revert", "description": "Откат ВМ на снимок по SSH: virsh snapshot-revert", "default_severity": "WARNING"},
    {"action": "vm.astra_update", "description": "Обновление ОС ВМ по SSH: revert _build → sources.list → astra-update → reboot → снимок новой версии", "default_severity": "CRITICAL"},
    {"action": "vm.allta_update", "description": "Обновление guest-allta по снимкам ВМ по SSH: revert → wget/apt install deb → пересъёмка", "default_severity": "WARNING"},
    {"action": "vm.passwd", "description": "Смена пароля гостевого u по снимкам ВМ по SSH: chpasswd + пересъёмка", "default_severity": "WARNING"},
    {"action": "vm.prepare", "description": "Подготовка ВМ по SSH: заведение управляющей учётки (ключ+sudo), hardening sshd, удаление базовой учётки", "default_severity": "CRITICAL"},
    {"action": "vm.set_network", "description": "Смена сети ВМ по SSH: статика в госте + перевод домена на bridge br0 либо NAT", "default_severity": "WARNING"},
    {"action": "vm.set_autostart", "description": "Смена флага автозапуска ВМ по SSH: virsh autostart / autostart --disable", "default_severity": "WARNING"},
    {"action": "vm.console_prep", "description": "Подготовка консоли ВМ по SSH: vnc/spice-graphics (+ serial) + чтение порта (virsh vncdisplay/domdisplay)", "default_severity": "INFO"},
    {"action": "vm.list_packages", "description": "Список установленных пакетов гостя ВМ снят по SSH (dpkg-query/rpm в госте)", "default_severity": "INFO"},
    {"action": "vm.packages_installed", "description": "Пакеты установлены в госте ВМ по SSH через hub под sudo (VM-аналог server.packages_install)", "default_severity": "WARNING"},
    {"action": "vm.packages_removed", "description": "Пакеты удалены из гостя ВМ по SSH через hub под sudo (VM-аналог server.packages_remove)", "default_severity": "WARNING"},
    {"action": "vm.packages_updated", "description": "Пакеты обновлены в госте ВМ по SSH через hub под sudo (VM-аналог server.packages_update)", "default_severity": "INFO"},
    {"action": "vm.inventory_sync", "description": "Снятие hardware-фактов гостя ВМ по SSH через hub (VM-аналог server.inventory_sync)", "default_severity": "INFO"},
    {"action": "vm.users_inventory", "description": "Снятие среза OS-пользователей гостя ВМ по SSH через hub (getent, VM-аналог server_account.users_inventory)", "default_severity": "INFO"},
    {"action": "vm.account_provision", "description": "Учётка общего пула заведена в госте ВМ по SSH через hub (useradd + пароль/ключ/группы)", "default_severity": "WARNING"},
    {"action": "vm.account_update_on_host", "description": "Группы/sudo учётки синхронизированы в госте ВМ по SSH через hub (usermod)", "default_severity": "INFO"},
    {"action": "vm.account_deprovision", "description": "Учётка удалена из гостя ВМ по SSH через hub (userdel)", "default_severity": "WARNING"},
    {"action": "vms_hub.teardown", "description": "Разбор VMS-hub по SSH: destroy/undefine ВМ отдела + снос storage-pool/образов + опц. purge пакетов и br0", "default_severity": "CRITICAL"},
    # Управляющая учётка — tasks/management_user.py, tasks/management_creds.py
    {"action": "management_user.sync", "description": "Недеструктивный re-bootstrap управляющей учётки на хосте", "default_severity": "INFO"},
    {"action": "server.management_creds_rotated", "description": "Ротация per-server управляющих кредов применена на хосте + callback", "default_severity": "CRITICAL"},
    # Task-lifecycle / runner-meta — tasks/_runner.py, main.py
    {"action": "task.deleted_midrun", "description": "Row задачи исчез между mark_running и terminal write (retention/ручной DELETE)", "default_severity": "ERROR"},
    {"action": "task.worker_shutdown", "description": "Graceful shutdown воркера — живые задачи переведены в retry/failed", "default_severity": "WARNING"},
    {"action": "task.worker_orphaned", "description": "Sweep нашёл running-задачу с мёртвым worker_id и принудительно завалил её", "default_severity": "ERROR"},
    # BMC-транспорт и SSRF-guard — clients/__init__.py, main.py
    {"action": "ipmi_controller.password_rotate", "description": "Ротация пароля IPMI/BMC применена и проверена + callback", "default_severity": "WARNING"},
    {"action": "bmc.tls_downgrade", "description": "BMC-probe перешёл на менее защищённый канал (verify→noverify или *→http)", "default_severity": "CRITICAL"},
    {"action": "bmc.tls_verify_disabled", "description": "BMC Redfish работает по TLS без проверки сертификата (REDFISH_VERIFY_TLS=false)", "default_severity": "CRITICAL"},
    {"action": "bmc.endpoint_blocked", "description": "SSRF-guard заблокировал BMC-endpoint (loopback/link-local/unspecified)", "default_severity": "CRITICAL"},
    # Интерактивная SSH-консоль — services/console_bridge.py
    {"action": "ssh_console.command", "description": "Команда, введённая в интерактивной SSH-консоли (одна строка по Enter)", "default_severity": "WARNING"},
    {"action": "box.download", "description": "Образ бокса скачан по download_url в storage-pool боксов hub'а (таска box.download) и импортирован по формату. Провалы: неподдержанная схема/формат, ошибка скачивания/импорта, сбой hub-сессии", "default_severity": "WARNING"},
    # CLI-интервенция в audit-pipeline — cli/outbox.py
    {"action": "audit.outbox_reattempt_manual", "description": "Оператор форсит CLI-командой повторную доставку row'ы worker'ского audit_outbox", "default_severity": "WARNING"},
]


def register_events() -> None:
    """POST'ит полный список worker-событий в loging_service.

    Если `LOGGING_SERVICE_URL` / `LOGGING_SERVICE_API_KEY` не заданы —
    пропускаем (dev-сценарий без loging). На 5xx делаем короткий retry:
    транзиентный ingress 502/503 не должен оставлять каталог
    нерегистрированным до следующего рестарта. WARNING (не ERROR) при
    провале — регистрация косметика, отсутствие записей даёт INFO-default,
    не валит воркер.
    """
    settings = get_settings()
    logging_url = getattr(settings, "logging_service_url", None)
    api_key = getattr(settings, "logging_service_api_key", None)
    if not logging_url or not api_key:
        logger.debug("audit: skipping event registration — LOGGING_SERVICE_URL not configured")
        return

    url = f"{logging_url.rstrip('/')}/api/logging/v1/services/{_SERVICE_NAME}/events"
    headers = {**bearer_header(api_key), "X-Service-Identity": _SERVICE_NAME}
    payload = {"events": SERVICE_EVENTS}
    timeout = getattr(settings, "register_events_timeout_seconds", 5.0)

    last_error: str | None = None
    for attempt in range(_REGISTER_EVENTS_MAX_ATTEMPTS):
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — best-effort
            last_error = f"transport error: {exc}"
            if attempt + 1 < _REGISTER_EVENTS_MAX_ATTEMPTS:
                time.sleep(_REGISTER_EVENTS_RETRY_DELAY_SECONDS)
                continue
            break

        if resp.status_code == 200:
            data = resp.json()
            logger.info(
                "audit: registered %d events (added=%d updated=%d)",
                data.get("total"), data.get("added"), data.get("updated"),
            )
            return

        last_error = f"{resp.status_code} {resp.text}"
        # 5xx — транзиент, retry. 4xx — конфиг бит (миссинг api-key, кривой
        # JSON), retry бесполезен.
        if 500 <= resp.status_code < 600 and attempt + 1 < _REGISTER_EVENTS_MAX_ATTEMPTS:
            time.sleep(_REGISTER_EVENTS_RETRY_DELAY_SECONDS)
            continue
        break

    logger.warning("audit: event registration failed: %s", last_error)


async def register_events_async() -> None:
    """Обёртка над sync `register_events` для startup-хука воркера.

    WORKER_STARTUP-хук — корутина в основном event-loop'е; выносим
    блокирующий httpx-POST в тред, чтобы не тормозить старт остальных
    хуков и не держать loop на TLS-handshake'е.
    """
    await asyncio.to_thread(register_events)
