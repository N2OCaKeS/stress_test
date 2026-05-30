"""Общие enums и константы server_worker'а."""

from enum import StrEnum


# Длина `last_error` в `audit_outbox` и `tasks` — `String(512)`. Truncate'им
# redacted-сообщения до этого предела перед записью, иначе INSERT упадёт с
# `data too long`. Должно совпадать с `String(length=...)` в моделях и миграциях.
LAST_ERROR_MAX_LEN = 512


class TaskKind(StrEnum):
    """Поддерживаемые типы task'ов. Значения совпадают с taskiq broker labels.

    Любое изменение `value` ломает обратную совместимость очереди: в DB и
    Redis уже могут лежать сообщения со старым лейблом. Расширять —
    добавлением новых членов; ломать значения существующих — нельзя.
    """

    POWER_ON = "power.on"
    POWER_OFF = "power.off"
    POWER_REBOOT = "power.reboot"
    POWER_STATUS = "power.status"
    INVENTORY_SYNC = "inventory.sync"
    USERS_INVENTORY = "users.inventory"
    ACCOUNT_ROTATE_PASSWORD = "account.rotate_password"
    ACCOUNT_PROVISION = "account.provision"
    ACCOUNT_UPDATE_ON_HOST = "account.update_on_host"
    ACCOUNT_DEPROVISION = "account.deprovision"
    SERVER_PREPARE = "server.prepare"
    IPMI_ROTATE_PASSWORD = "ipmi.rotate_password"
    INSTALLED_PACKAGES_LIST = "installed_packages.list"


class TaskStatus(StrEnum):
    """Lifecycle-статусы task'и.

    QUEUED → RUNNING → (SUCCEEDED | FAILED). RUNNING → QUEUED возможен при
    retry'е (см. `mark_pending_for_retry`). CANCELLED выставляется
    server_service'ом через `POST /api/server/v1/tasks/{id}/cancel` —
    graceful: pending пропускается перед запуском (CAS на mark_running
    + явный fast-path в `_runner`), running доживает текущий stage и не
    стартует следующий.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
