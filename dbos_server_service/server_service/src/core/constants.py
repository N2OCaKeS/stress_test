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
    """Состояние «занятости» — кто-то взял сервер тестом."""

    FREE = "free"
    BUSY = "busy"
    TESTING = "testing"


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
