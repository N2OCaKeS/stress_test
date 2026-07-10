"""Общие enum'ы и константы сервиса."""

from enum import StrEnum

# Имя сервиса для introspect (allowed_services), audit-payload, routing'а
# событий в loging_service. Раньше повторялось в трёх модулях (auth/audit_service/
# audit_events) — теперь источник один.
SERVICE_NAME = "server_service"

# Health/ready paths, которые middleware пропускают без аудита, rate-limit'а
# и introspect'а. Точный матч (никаких endswith), чтобы вложенные пути с
# похожим окончанием — например `/api/server/v1/servers/{id}/health` —
# не обходили guard. Используется одновременно в `main` (rate-limit /
# audit-skip) и `middleware/platform_admin_guard` (public-path whitelist).
HEALTH_PATHS: frozenset[str] = frozenset({
    "/api/server/v1/health",
    "/api/server/v1/ready",
})


class ServerStatus(StrEnum):
    """Жизненный цикл сервера."""

    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"
    DECOMMISSIONED = "decommissioned"


class BusyState(StrEnum):
    """Состояние «занятости» — кто-то взял сервер тестом.

    `updating` — системная блокировка на время обновления ОС (astra_update):
    в отличие от `busy`/`testing` её ставит не оператор, а сам сервис, и снять
    её может только callback воркера (успех/ошибка). Пока сервер `updating`,
    любые управляющие операции над ним отбиваются 409 SERVER_UPDATING —
    включая владельца брони и админа (см. `services/reservation.py`).
    """

    FREE = "free"
    BUSY = "busy"
    TESTING = "testing"
    UPDATING = "updating"


class PowerState(StrEnum):
    """Последнее известное состояние питания (cached). Live — через worker."""

    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


class IpmiKind(StrEnum):
    """Тип BMC. idrac — Dell, ilo — HPE, redfish — стандартизованный REST."""

    IDRAC = "idrac"
    ILO = "ilo"
    IPMI = "ipmi"
    REDFISH = "redfish"


class IpmiProbeStatus(StrEnum):
    """Результат последнего probe BMC."""

    OK = "ok"
    UNREACHABLE = "unreachable"
    AUTH_FAILED = "auth_failed"


class AccountSource(StrEnum):
    """Происхождение OS-аккаунта.

    `managed` — заведён оператором через API (пароль известен и хранится).
    `discovered` — найден инвентаризацией на сервере; пароль API неизвестен.
    """

    MANAGED = "managed"
    DISCOVERED = "discovered"


class ServiceRole(StrEnum):
    """Системные service-роли. Только `guest` (базовый доступ) и `admin`
    (полный доступ) сеются автоматически и защищены `is_system`. Весь
    промежуточный доступ — через кастомные роли (создаются в auth_service),
    которые ссылаются на матрицу действий по имени (free-form string) и грантятся
    per-department строками `entity_permissions`."""

    GUEST = "guest"
    ADMIN = "admin"


# Системные service-роли с фиксированной матрицей: `admin` (всё) и `guest`
# (только server.view). Их набор прав не редактируется через API — ни тип-wide
# (entity_permissions), ни инстанс-гранты (resource_role_permissions). worker_bot
# сюда НЕ входит: это внутренний субъект, его узкие callback-гранты остаются
# управляемыми.
SYSTEM_SERVICE_ROLES: frozenset[str] = frozenset({ServiceRole.GUEST, ServiceRole.ADMIN})


def is_system_role(role: str) -> bool:
    """True iff `role` — системная (`admin`/`guest`) с неизменяемой матрицей."""
    return role in SYSTEM_SERVICE_ROLES


class PlatformRole(StrEnum):
    """Платформенные роли (приходят в `IdentityContext.platform_role`).

    `account_admin` / `loging_admin` — без department, блокируются
    `platform_admin_guard` на бизнес-эндпоинтах. `loging_reader` имеет
    department, доступ к бизнес-данным режется матрицей. `department_admin` —
    легитимный admin своего отдела, full-CRUD через service_roles.
    """

    ACCOUNT_ADMIN = "account_admin"
    DEPARTMENT_ADMIN = "department_admin"
    LOGING_ADMIN = "loging_admin"
    LOGING_READER = "loging_reader"


class ManagementMode(StrEnum):
    """Режим создания управляющей учётки на боксе.

    Привязан к редакции ОС: три ветки Astra Linux SE (Орёл/Смоленск/Воронеж)
    и общий fallback для прочих ОС. Конфиг управляющей учётки хранит на каждый
    режим свой набор доп-групп и bootstrap-команд (см.
    `models/management_user_config`).
    """

    ASTRA_OREL = "astra_orel"
    ASTRA_SMOLENSK = "astra_smolensk"
    ASTRA_VORONEZH = "astra_voronezh"
    OTHER_OS = "other_os"


class EntityType(StrEnum):
    """Типы сущностей для матрицы entity_permissions."""

    SERVER = "server"
    SERVER_ACCOUNT = "server_account"
    OS_VERSION = "os_version"
    IPMI_CONTROLLER = "ipmi_controller"
    # Самоуправление матрицей прав: view списка / grant / revoke. Управление
    # service-ролями (создание/удаление имён ролей) живёт только в auth_service —
    # server_service сюда не лезет, мы только наполняем матрицу actions для них.
    PERMISSION = "permission"
    # Worker-таска: доступны `view` (list/detail) и `cancel`. Сами task-row'ы
    # живут в dev_server_worker.tasks; server_service ходит туда через cross-DB
    # engine из `worker_client` — и для чтения (list/get), и при dispatch'е.
    # Полноценного CRUD нет: server_service задачи только читает и отменяет,
    # пишет их таблицу сам worker.
    TASK = "task"
    # Виртуальная машина на hub-сервере: создание, питание, бронь под тест,
    # диски и снимки. Отдельная зона матрицы прав со своим набором действий.
    VM = "vm"


class Action(StrEnum):
    """Fine-grained actions для матрицы entity_permissions.

    Комбинации (entity_type, action) валидируются runtime'но против
    ENTITY_ACTIONS — неизвестные пары отбиваются grant-эндпоинтом.
    """

    # CRUD-style
    VIEW = "view"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"

    # Server-specific
    BUSY_ACQUIRE = "busy_acquire"
    BUSY_RELEASE = "busy_release"
    OS_SYNC = "os_sync"
    POWER_ON = "power_on"
    POWER_OFF = "power_off"
    POWER_REBOOT = "power_reboot"
    POWER_STATUS = "power_status"
    INVENTORY_TRIGGER = "inventory_trigger"
    INVENTORY_SUBMIT = "inventory_submit"
    # Интерактивная SSH-консоль к серверу через WebSocket-мост. Право даёт
    # открыть PTY-сессию под управляющим пользователем DBOS на подготовленном
    # сервере; каждая введённая команда логируется в loging как
    # `ssh_console.command`. Чувствительное (живой root-доступ к боксу) —
    # дефолтно только admin (либо кастомная роль с этим грантом).
    CONSOLE = "console"
    # Read aggregated drift-summary по серверу — обращается в loging за
    # событиями `server_account.drift_detected`. Право узкое: даёт смотреть
    # факт расхождения без полного доступа к accounts.
    VIEW_DRIFT = "view_drift"
    # worker_bot callback после бутстрапа управления (prepare): помечает
    # сервер подготовленным. Узкий least-privilege грант, без CRUD над сервером.
    PREPARE_CALLBACK = "prepare_callback"
    # Массовые изменяющие операции с пакетами (install/remove/update) через
    # worker по SSH под управляющим пользователем. Деструктив на боксе —
    # отдельный action поверх view, чтобы право менять состав пакетов можно
    # было выдать прицельно. Дефолтно admin (либо кастомная роль с грантом).
    MANAGE_PACKAGES = "manage_packages"

    # Sensitive: показ расшифрованного секрета. Держатель `view_password` /
    # `view_credentials` получает plaintext (в base64) прямо в GET-карточке —
    # отдельной reveal-ручки нет. Тот же action использует worker через
    # internal endpoint.
    VIEW_PASSWORD = "view_password"
    ROTATE_PASSWORD = "rotate_password"
    VIEW_CREDENTIALS = "view_credentials"
    ROTATE_CREDENTIALS = "rotate_credentials"
    # Раскрытие per-server управляющих кред (privkey + пароль пользователя dbos)
    # воркеру через internal endpoint. Узкий least-privilege грант worker_bot'а:
    # воркер тянет рабочий на боксе ключ перед каждой managed-операцией.
    VIEW_MANAGEMENT_CREDENTIALS = "view_management_credentials"

    # Server-account specific
    GRANT_SUDO = "grant_sudo"
    # worker_bot callback после useradd/usermod/userdel на боксе — узкий
    # least-privilege грант, без CRUD над аккаунтами.
    PROVISION_ON_HOST = "provision_on_host"
    # Принять факт-состояние OS-пользователя с конкретного хоста в БД: оператор
    # руками выбирает поля из drift'а (`found`-значения) и пишет их в аккаунт.
    # Обновляет ТОЛЬКО БД, fan-out на серверы не идёт. Уровень — как `update`,
    # отдельный action нужен, чтобы право принять чужое
    # состояние можно было выдать прицельно.
    ADOPT_FROM_HOST = "adopt_from_host"
    # Управление ignore-list'ом логинов отдела: добавить/снять логин, который
    # инвентаризация не должна показывать как незнакомого пользователя. Скоуп —
    # отдел; уровень update.
    MANAGE_IGNORED_LOGINS = "manage_ignored_logins"

    # Управление permission-матрицей (entity_permissions rows)
    PERMISSION_GRANT = "permission_grant"
    PERMISSION_REVOKE = "permission_revoke"

    # Provision/deprovision OS-пользователя на боксе — полноценные действия
    # ролевой матрицы (в `ENTITY_ACTIONS[SERVER_ACCOUNT]`); dispatch'и
    # provision/deprovision гейтятся именно ими (а не create/delete, как раньше).
    PROVISION = "provision"
    DEPROVISION = "deprovision"

    # Worker-таска: отмена pending/running задачи. Graceful — pending пропадает
    # из dispatch'а через CAS, running доживает текущий stage и не стартует
    # следующий. Force-kill через cancel нет.
    CANCEL = "cancel"

    # ── VM-зона ─────────────────────────────────────────────────────────────
    # Подготовить сервер как VMS-hub (libvirt/kvm/мост/пул образов). Действие
    # зоны vm, но таргетит сервер: dispatch VMS_HUB_PREPARE. Тип-wide (инстанс
    # ВМ на момент вызова ещё нет), гейтит `POST /servers/{id}/prepare-vms-hub`.
    VMS_HUB_PREPARE = "vms_hub_prepare"
    # Питание ВМ: start/shutdown/reboot/reset/destroy — dispatch VM_POWER.
    VM_POWER = "vm_power"
    # Бронь ВМ под тест (status → run test/debug test/<login>).
    VM_RESERVE = "vm_reserve"
    # Снять бронь ВМ (status → free).
    VM_RELEASE = "vm_release"
    # Диски/снимки/prepare/обновления/сеть/пресеты.
    VM_DISK_MANAGE = "vm_disk_manage"
    VM_SNAPSHOT_MANAGE = "vm_snapshot_manage"
    VM_PREPARE = "vm_prepare"
    VM_ASTRA_UPDATE = "vm_astra_update"
    VM_ALLTA_UPDATE = "vm_allta_update"
    VM_PASSWD = "vm_passwd"
    VM_NET_MANAGE = "vm_net_manage"
    VM_PRESET_MANAGE = "vm_preset_manage"


# Whitelist валидных пар (entity_type, action). Несовпадение → 422
# INVALID_ACTION_FOR_ENTITY в permission_service.grant_action.
ENTITY_ACTIONS: dict[str, frozenset[str]] = {
    EntityType.SERVER: frozenset({
        Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE,
        Action.BUSY_ACQUIRE, Action.BUSY_RELEASE,
        Action.OS_SYNC,
        Action.POWER_ON, Action.POWER_OFF, Action.POWER_REBOOT, Action.POWER_STATUS,
        Action.INVENTORY_TRIGGER, Action.INVENTORY_SUBMIT,
        # worker_bot подтверждает завершение бутстрапа управления — callback-only.
        Action.PREPARE_CALLBACK,
        # Интерактивная SSH-консоль (WebSocket-мост).
        Action.CONSOLE,
        # Чтение drift-сводки по серверу (агрегация event'ов из loging).
        Action.VIEW_DRIFT,
        # Массовое изменение пакетов на боксе (install/remove/update).
        Action.MANAGE_PACKAGES,
        # worker_bot тянет per-server управляющие креды (privkey+пароль dbos)
        # через internal endpoint перед каждой managed-операцией.
        Action.VIEW_MANAGEMENT_CREDENTIALS,
    }),
    EntityType.SERVER_ACCOUNT: frozenset({
        Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE,
        Action.VIEW_PASSWORD, Action.ROTATE_PASSWORD,
        Action.GRANT_SUDO,
        # Завести / снести OS-пользователя на боксе — полноценные grantable-
        # действия ролевой матрицы. Dispatch'и provision/deprovision гейтятся
        # именно ими (а не create/delete, как раньше), UI рисует их из каталога.
        Action.PROVISION,
        Action.DEPROVISION,
        # worker_bot пушит результат инвентаризации пользователей обратно
        # через internal callback — узкий least-privilege грант, без CRUD.
        Action.INVENTORY_SUBMIT,
        # worker_bot подтверждает результат useradd/usermod/userdel на боксе —
        # тоже callback-only, без CRUD.
        Action.PROVISION_ON_HOST,
        # Оператор принимает факт-состояние хоста в БД (пополевно, по drift'у).
        Action.ADOPT_FROM_HOST,
        # Управление ignore-list'ом незнакомых логинов отдела.
        Action.MANAGE_IGNORED_LOGINS,
        # Интерактивная консоль учётки (ролевой путь).
        Action.CONSOLE,
    }),
    # Чтение каталога публичное (без auth) — view-грант осиротел и снят
    # миграцией c1a9f2b7e4d8; под матрицей остаётся только запись.
    EntityType.OS_VERSION: frozenset({
        Action.CREATE, Action.UPDATE, Action.DELETE,
    }),
    EntityType.IPMI_CONTROLLER: frozenset({
        Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE,
        Action.VIEW_CREDENTIALS, Action.ROTATE_CREDENTIALS,
    }),
    EntityType.PERMISSION: frozenset({
        Action.VIEW, Action.PERMISSION_GRANT, Action.PERMISSION_REVOKE,
    }),
    # Worker-таска — view (list/detail истории) и cancel. view/cancel сидятся
    # системной роли admin (кастомные роли получают их грантом); см. seed-миграции.
    EntityType.TASK: frozenset({
        Action.VIEW,
        Action.CANCEL,
    }),
    # VM-зона: CRUD-минимум + питание/бронь + подготовка hub'а, плюс
    # диски/снимки/обновления/сеть/пресеты.
    EntityType.VM: frozenset({
        Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE,
        Action.VMS_HUB_PREPARE,
        Action.VM_POWER,
        Action.VM_RESERVE, Action.VM_RELEASE,
        Action.VM_DISK_MANAGE, Action.VM_SNAPSHOT_MANAGE,
        Action.VM_PREPARE,
        Action.VM_ASTRA_UPDATE, Action.VM_ALLTA_UPDATE, Action.VM_PASSWD,
        Action.VM_NET_MANAGE, Action.VM_PRESET_MANAGE,
    }),
}


def is_valid_action(entity_type: str, action: str) -> bool:
    """True iff пара (entity_type, action) есть в whitelist'е ENTITY_ACTIONS."""
    allowed = ENTITY_ACTIONS.get(entity_type)
    return allowed is not None and action in allowed


# Типы ресурсов, которые поддерживают инстанс-уровневый ACL
# (resource_role_permissions). Точечный грант роли на конкретный объект имеет
# смысл только для сущностей с реальными строками в БД этого сервиса — server
# и server_account. os_version (глобальный каталог), ipmi_controller (живёт под
# сервером), permission/task в инстанс-ACL не входят.
RESOURCE_ACL_TYPES: frozenset[str] = frozenset({
    EntityType.SERVER,
    EntityType.SERVER_ACCOUNT,
    EntityType.VM,
})

# Действия, которые НЕЛЬЗЯ привязать к конкретному инстансу — они остаются
# только в глобальном слое (entity_permissions). Сюда входят:
#   * `create` — на момент проверки инстанса ещё нет;
#   * callback-действия воркера (inventory_submit / provision_on_host /
#     prepare_callback) — бот оперирует всеми серверами разом, инстанс-скоуп
#     его сломал бы;
#   * `view_management_credentials` — служебный pull воркера через internal
#     endpoint, человеку не назначается;
#   * `manage_ignored_logins` — скоуп отдела, а не отдельной учётки.
_NON_INSTANCE_ACTIONS: frozenset[str] = frozenset({
    Action.CREATE,
    # callback-действия воркера (дублируют permission_catalog.WORKER_CALLBACK_ACTIONS;
    # держим набор тут, чтобы constants не зависел от permission_catalog)
    Action.INVENTORY_SUBMIT,
    Action.PROVISION_ON_HOST,
    Action.PREPARE_CALLBACK,
    Action.VIEW_MANAGEMENT_CREDENTIALS,
    Action.MANAGE_IGNORED_LOGINS,
    # Подготовка сервера как VMS-hub таргетит сервер, а не инстанс ВМ —
    # инстанс-грант на конкретную ВМ тут смысла не имеет.
    Action.VMS_HUB_PREPARE,
})

# Инстанс-грантуемые действия на каждый resource_type — производное от
# ENTITY_ACTIONS за вычетом глобально-только. grant-эндпоинт инстанс-ACL
# отбивает попытку выдать право на действие вне этого набора.
INSTANCE_GRANTABLE_ACTIONS: dict[str, frozenset[str]] = {
    rt: frozenset(ENTITY_ACTIONS[rt]) - _NON_INSTANCE_ACTIONS
    for rt in RESOURCE_ACL_TYPES
}


def is_instance_grantable(resource_type: str, action: str) -> bool:
    """True iff `action` можно выдать инстанс-грантом на `resource_type`.

    Отбивает как неизвестные resource_type (нет в RESOURCE_ACL_TYPES), так и
    действия, которые остаются только в глобальном слое (`create`, callback'и
    воркера и т.п.).
    """
    allowed = INSTANCE_GRANTABLE_ACTIONS.get(resource_type)
    return allowed is not None and action in allowed


class VmNetworkMode(StrEnum):
    """Сетевой режим ВМ: мост в реальный LAN либо NAT libvirt."""

    BRIDGE = "bridge"
    NAT = "nat"


class VmPowerState(StrEnum):
    """Последнее известное состояние питания ВМ (из `virsh domstate`)."""

    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


class VmCredStrategy(StrEnum):
    """Стратегия связи mgmt-кред со снимками ВМ (§6 дизайна).

    `per_snapshot` (дефолт) — каждый снимок хранит свои креды; `reroll` —
    единый текущий пароль перекатывается по всем снимкам при ротации.
    """

    PER_SNAPSHOT = "per_snapshot"
    REROLL = "reroll"


class VmDiskState(StrEnum):
    """Жизненный цикл диска ВМ. Источник истины — hub; БД зеркалит.

    `creating` — строка заведена, задача создания/подключения диска в работе;
    `ready` — диск создан и подключён к домену (callback воркера);
    `error` — операция на hub'е упала (текст — в `last_error` ВМ).
    """

    CREATING = "creating"
    READY = "ready"
    ERROR = "error"


class VmImageKind(StrEnum):
    """Тип бокса-образа в каталоге `vm_images`.

    `universal` — `vm_station`: один артефакт с внутренними qemu-снимками
    нескольких ОС; версия при create не выбирается. `single` — бокс под
    конкретную ОС/ФС/размер.
    """

    UNIVERSAL = "universal"
    SINGLE = "single"


class VmSnapshotType(StrEnum):
    """Способ снятия снимка ВМ (virsh-механика).

    `disk_only` — только диск (`virsh snapshot-create-as --disk-only`);
    `full` — диск + состояние RAM/устройств.
    """

    DISK_ONLY = "disk_only"
    FULL = "full"


class VmSnapshotKind(StrEnum):
    """Смысловая группа снимка ВМ (для выдачи двумя списками в UI).

    `os_baseline` — чистый снимок версии ОС после сборки/astra-update
    (`<ver>_орёл` / `<ver>_смоленск`); заводится сборочным флоу, не руками.
    `user` — снимок, снятый пользователем.
    """

    OS_BASELINE = "os_baseline"
    USER = "user"


class VmSnapshotMode(StrEnum):
    """Режим (уровень безопасности) Astra, зафиксированный в снимке.

    `orel` — Орёл (уровень 0), `smolensk` — Смоленск (уровень 2). `None`
    у пользовательских снимков и до фиксации режима. Старые снимки могли
    писаться как `oryol` — на чтении трактуется как Орёл.
    """

    OREL = "orel"
    SMOLENSK = "smolensk"


class VmSnapshotState(StrEnum):
    """Жизненный цикл снимка ВМ. Источник истины — hub; БД зеркалит.

    `creating` — строка заведена, задача снятия снимка в работе;
    `ready` — снимок снят на гипервизоре (callback воркера);
    `error` — операция на hub'е упала (текст — в `last_error` ВМ).
    """

    CREATING = "creating"
    READY = "ready"
    ERROR = "error"


# Суффикс имени системных golden-снимков (`<ver>_build`). Такие снимки создаёт
# сам сборочный флоу vm.create, они несут дефолт-креды образа (`u:1`) и в UI
# скрыты; руками их нельзя удалять/откатывать (403).
VM_SYSTEM_SNAPSHOT_SUFFIX = "_build"


class VmBusyState(StrEnum):
    """Lifecycle-lock ВМ на время долгой операции (создание/удаление/апдейт).

    Отдельно от `status` (бронь под тест): пока busy_state непустой, любые
    управляющие операции над ВМ отбиваются, пока не придёт callback воркера
    (или не сработает TTL-recovery). NULL = свободна от lifecycle-лока.
    """

    CREATING = "creating"
    DELETING = "deleting"
    UPDATING = "updating"
    POWERING = "powering"
    SNAPSHOTTING = "snapshotting"
    REVERTING = "reverting"
    # Онбординг управления ВМ (vm.prepare / ротация mgmt-кред): установка новой
    # SSH-пары и пароля управляющего пользователя внутри гостя по SSH.
    PREPARING = "preparing"
    # Смена сетевого режима ВМ (bridge↔nat, статик из пула): правка XML домена
    # и провижн статики в госте.
    NETWORKING = "networking"


# Бронь ВМ: свободная и служебные статусы под тест. Любое другое значение —
# это `<login>` забронировавшего (см. §2 дизайна). Здесь только зарезервированные.
VM_STATUS_FREE = "free"
VM_STATUS_RUN_TEST = "run test"
VM_STATUS_DEBUG_TEST = "debug test"


class VmTaskKind(StrEnum):
    """task_kind'ы, которые server_service диспатчит воркеру для VM-домена.

    Строки едут в `dev_server_worker.tasks.task_kind` и в `dispatch_outbox`;
    poller публикует их в taskiq-очередь, где живут handler'ы server_worker'а.
    """

    VMS_HUB_PREPARE = "vms_hub.prepare"
    VM_CREATE = "vm.create"
    VM_POWER = "vm.power"
    # Живая проба статуса ВМ: virsh domstate + ping/ssh гостя (read-only sweep).
    VM_STATUS = "vm.status"
    VM_DELETE = "vm.delete"
    # Изменение ресурсов ВМ (cpu/ram): stop → правка XML → start.
    VM_UPDATE = "vm.update"
    # Диски ВМ: создать+подключить / отключить+удалить / расширить.
    VM_DISK_ATTACH = "vm.disk_attach"
    VM_DISK_DELETE = "vm.disk_delete"
    VM_DISK_RESIZE = "vm.disk_resize"
    # Снимки ВМ: снять / удалить / откатить (virsh snapshot-create-as/-delete/
    # -revert). Системные `<ver>_build` — только через сборочный флоу, не руками.
    VM_SNAPSHOT_CREATE = "vm.snapshot_create"
    VM_SNAPSHOT_DELETE = "vm.snapshot_delete"
    VM_SNAPSHOT_REVERT = "vm.snapshot_revert"
    # Обновление ОС ВМ по RC (revert <ver>_build → repo → astra-update → снимок
    # <rc>). Смена гостевой allta + пароля `u` (allta_update и passwd — один op,
    # у passwd пароль обязателен).
    VM_ASTRA_UPDATE = "vm.astra_update"
    VM_ALLTA_UPDATE = "vm.allta_update"
    VM_PASSWD = "vm.passwd"
    # Онбординг управления ВМ: SSH на гость под дефолт-кредами образа (`u:1`),
    # установка per-VM управляющей SSH-пары + пароля, удаление базовой учётки.
    # Тот же kind обслуживает и ротацию mgmt-кред (payload несёт `operation`).
    VM_PREPARE = "vm.prepare"
    # Live-инвентарь пакетов гостя: worker заходит на гостя по SSH через hub,
    # снимает dpkg/rpm-список и отдаёт его callback'ом record_vm_packages.
    VM_LIST_PACKAGES = "vm.list_packages"
    # Мутации пакетов гостя (install/remove/update) — VM-аналоги серверных
    # installed_packages.{install,remove,update}: worker заходит на гостя по SSH
    # через hub под управляющим ключом и гонит apt-get/dnf/apk под sudo.
    VM_INSTALL_PACKAGES = "vm.install_packages"
    VM_REMOVE_PACKAGES = "vm.remove_packages"
    VM_UPDATE_PACKAGES = "vm.update_packages"
    # Инвентаризация гостя ВМ — VM-аналоги inventory.sync / users.inventory: worker
    # снимает hardware-facts / OS-юзеров с гостя по SSH через hub (управляющий ключ)
    # и сдаёт callback'ом (inventory / users/inventory).
    VM_INVENTORY_SYNC = "vm.inventory_sync"
    VM_USERS_INVENTORY = "vm.users_inventory"
    # Смена сетевого режима ВМ: NAT (libvirt, IP через domifaddr) ↔ bridge
    # (br0, статический IP из пула — провижн статики в госте + правка XML).
    VM_SET_NETWORK = "vm.set_network"
    # Автозапуск ВМ при старте hub'а: `virsh autostart [--disable] <vm>`.
    VM_SET_AUTOSTART = "vm.set_autostart"
    # Снос VMS-hub'а: остановить/удалить домены отдела, снять пул/образы,
    # `apt purge` пакетов виртуализации. БД чистит server_service сразу (симметрия
    # старому rm-vms-hub), воркер добивает состояние на хосте.
    VMS_HUB_TEARDOWN = "vms_hub.teardown"


# Действия питания ВМ, принимаемые `POST /vms/{id}/power`. Едут в payload
# VM_POWER как `action`; воркер мапит на virsh start/shutdown/reboot/reset/destroy.
VM_POWER_ACTIONS: frozenset[str] = frozenset({
    "start", "shutdown", "reboot", "reset", "destroy",
})

# Типы консольного доступа к ВМ (`POST /vms/{id}/console`): интерактивный SSH,
# VNC/SPICE (графическая консоль через websockify-прокси) и последовательная
# консоль (`virsh console`).
VM_CONSOLE_KINDS: frozenset[str] = frozenset({"ssh", "vnc", "serial", "spice"})

# Графические консоли ВМ, идущие через токен-прокси (websockify): и vnc, и spice
# отдают UI ws_url + подписанный токен. ssh/serial проксируются иначе.
VM_GRAPHICS_CONSOLE_KINDS: frozenset[str] = frozenset({"vnc", "spice"})

# Тип графики ВМ, выбираемый при создании (`VmCreate.graphics`) и уезжающий
# воркеру (`--graphics`). Дефолт — vnc.
VM_GRAPHICS_KINDS: frozenset[str] = frozenset({"vnc", "spice"})
VM_GRAPHICS_DEFAULT = "vnc"
