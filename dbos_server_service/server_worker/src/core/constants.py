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
    ACCOUNT_ROTATE_PASSWORD = "account.rotate_password"
    IPMI_ROTATE_PASSWORD = "ipmi.rotate_password"


class TaskStatus(StrEnum):
    """Lifecycle-статусы task'и.

    QUEUED → RUNNING → (SUCCEEDED | FAILED). RUNNING → QUEUED возможен при
    retry'е (см. `mark_pending_for_retry`). CANCELLED — пока не используется,
    зарезервирован под operator cancel из UI.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
