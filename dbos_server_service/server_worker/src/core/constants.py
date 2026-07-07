"""Общие enums и константы server_worker'а."""

from enum import StrEnum


# Длина `last_error` в `audit_outbox` и `tasks` — `String(512)`. Truncate'им
# redacted-сообщения до этого предела перед записью, иначе INSERT упадёт с
# `data too long`. Должно совпадать с `String(length=...)` в моделях и миграциях.
LAST_ERROR_MAX_LEN = 512


# TTL для in-flight stash'ей секретов в Redis (provision inline-creds,
# account/ipmi rotate-пароли). Покрывает суммарное окно exponential back-off'а
# (`_runner._compute_backoff_delay` capped 300s) с запасом на сетевые тормоза.
# По истечении ключ исчезает сам, оператор инициирует новый dispatch.
STASH_TTL_SECONDS = 1800


# Sentinel-значение, которым `repositories.task.scrub_payload_keys` заменяет
# секреты в персистентном payload. Reader (`tasks/users._unscrub`) сверяется
# с этой константой, чтобы не использовать sentinel как валидный пароль на
# retry'е. Если поменять — поменять одновременно и в обоих местах.
SCRUBBED_SENTINEL = "<scrubbed>"


# ── VM-менеджер ──────────────────────────────────────────────────────────────
#
# Каталог образов лежит на анонимном FTP тестовой инфраструктуры; при
# hub.prepare воркер тянет `<box>.tar.gz` отсюда и распаковывает в storage-pool.
# Отдельным полем в payload это не гоняем — адрес общий для всего стенда.
VMS_FTP_BOXES_URL = "ftp://10.177.103.10/boxes"

# Дефолтные креды образа: единый статичный аккаунт `u`/`1` во всех боксах
# (sudo NOPASSWD, SSH :22). Пароль — публичный дефолт артефакта, не секрет:
# смена на клиентский пароль/mgmt-креды ВМ — отдельная операция (`vm.passwd`).
VMS_GUEST_LOGIN = "u"
VMS_GUEST_DEFAULT_PASSWORD = "1"

# Universal-бокс `vm_station` несёт внутренние qemu-img снимки нескольких
# версий ОС на одном диске; переключение версии — `qemu-img snapshot -a <ver>`.
VMS_UNIVERSAL_BOX = "vm_station"

# `os-variant` для virt-install: для 1.7/1.8 Astra используем alse17 (квирк —
# отдельного профиля для новых сборок в libosinfo пока нет).
VMS_OS_VARIANT = "alse17"

# NAT-сеть libvirt для транзита при сборке (провижн статики до перевода на
# bridge) и `br0` — мост над физическим NIC для боевого доступа в LAN.
VMS_NAT_NETWORK = "test"
VMS_BRIDGE = "br0"

# Storage-pool по умолчанию (dir-pool в `/vms`), если payload не задал иной путь.
VMS_DEFAULT_POOL_PATH = "/vms"
VMS_POOL_NAME = "vms"


class TaskKind(StrEnum):
    """Поддерживаемые типы task'ов. Значения совпадают с taskiq broker labels.

    Любое изменение `value` ломает обратную совместимость очереди: в DB и
    Redis уже могут лежать сообщения со старым лейблом. Расширять —
    добавлением новых членов; ломать значения существующих — нельзя.

    Источник истины для broker-label'ов на worker- и server-сторонах. На
    сегодня @broker.task(...) декораторы (`tasks/*.py`) и server-side
    dispatch (`server_service/src/api/v1/endpoints/worker_dispatch.py`,
    `worker_client.py`) держат литералы — wiring на enum требует общего
    sdk-модуля (`sdk/task_kinds.py`), чтобы не дублировать определения.
    TODO: вытащить в shared sdk, заменить литералы на TaskKind.<...>.value.

    Рассинхрон каталога и реальных декораторов ловится тестом
    `tests/unit/test_constants.py::TestTaskKindBrokerDrift` (каждый kind имеет
    handler; каждый dispatch'абельный broker-таск есть в enum'е).

    `MANAGEMENT_USER_SYNC` — единственный legacy-лейбл без `<object>.<verb>`
    точки (`management_user_sync`); историческое имя, менять нельзя — в очереди
    могут лежать сообщения с этим лейблом.
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
    SERVER_ROTATE_MANAGEMENT_CREDS = "server.rotate_management_creds"
    SERVER_ASTRA_UPDATE = "server.astra_update"
    IPMI_ROTATE_PASSWORD = "ipmi.rotate_password"
    INSTALLED_PACKAGES_LIST = "installed_packages.list"
    INSTALLED_PACKAGES_INSTALL = "installed_packages.install"
    INSTALLED_PACKAGES_REMOVE = "installed_packages.remove"
    INSTALLED_PACKAGES_UPDATE = "installed_packages.update"
    MANAGEMENT_USER_SYNC = "management_user_sync"
    VMS_HUB_PREPARE = "vms_hub.prepare"
    VM_CREATE = "vm.create"
    VM_POWER = "vm.power"


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
