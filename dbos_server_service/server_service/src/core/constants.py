"""Общие enum'ы и константы сервиса."""

from enum import StrEnum

# Имя сервиса для introspect (allowed_services), audit-payload, routing'а
# событий в loging_service. Раньше повторялось в трёх модулях (auth/audit_service/
# audit_events) — теперь источник один.
SERVICE_NAME = "server_service"


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
    """Встроенные service-роли. Кастомные роли (через auth_service) ссылаются
    по имени (free-form string) — эти константы только seeded defaults."""

    GUEST = "guest"
    READER = "reader"
    OPERATOR = "operator"
    ADMIN = "admin"


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
    # Worker-таска: единственное доступное действие — `cancel`. Сами task-row'ы
    # живут в dev_server_worker.tasks; server_service ходит туда через cross-DB
    # engine из `worker_client`, как и при dispatch'е. CRUD/view над таблицей
    # tasks не предусмотрен — read идёт через server_worker напрямую.
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
    # Read aggregated drift-summary по серверу — обращается в loging за
    # событиями `server_account.drift_detected`. Право узкое: даёт смотреть
    # факт расхождения без полного доступа к accounts.
    VIEW_DRIFT = "view_drift"
    # worker_bot callback после бутстрапа управления (prepare): помечает
    # сервер подготовленным. Узкий least-privilege грант, без CRUD над сервером.
    PREPARE_CALLBACK = "prepare_callback"

    # Sensitive: показ расшифрованного секрета. Держатель `view_password` /
    # `view_credentials` получает plaintext (в base64) прямо в GET-карточке —
    # отдельной reveal-ручки нет. Тот же action использует worker через
    # internal endpoint.
    VIEW_PASSWORD = "view_password"
    ROTATE_PASSWORD = "rotate_password"
    VIEW_CREDENTIALS = "view_credentials"
    ROTATE_CREDENTIALS = "rotate_credentials"

    # Server-account specific
    GRANT_SUDO = "grant_sudo"
    # worker_bot callback после useradd/usermod/userdel на боксе — узкий
    # least-privilege грант, без CRUD над аккаунтами.
    PROVISION_ON_HOST = "provision_on_host"

    # Управление permission-матрицей (entity_permissions rows)
    PERMISSION_GRANT = "permission_grant"
    PERMISSION_REVOKE = "permission_revoke"

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
        # Чтение drift-сводки по серверу (агрегация event'ов из loging).
        Action.VIEW_DRIFT,
    }),
    EntityType.SERVER_ACCOUNT: frozenset({
        Action.VIEW, Action.CREATE, Action.UPDATE, Action.DELETE,
        Action.VIEW_PASSWORD, Action.ROTATE_PASSWORD,
        Action.GRANT_SUDO,
        # worker_bot пушит результат инвентаризации пользователей обратно
        # через internal callback — узкий least-privilege грант, без CRUD.
        Action.INVENTORY_SUBMIT,
        # worker_bot подтверждает результат useradd/usermod/userdel на боксе —
        # тоже callback-only, без CRUD.
        Action.PROVISION_ON_HOST,
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
    # Worker-таска — только cancel. Подбирать tasks или просматривать историю
    # через матрицу нельзя; для этого нужен прямой read из server_worker.
    EntityType.TASK: frozenset({
        Action.CANCEL,
    }),
}


def is_valid_action(entity_type: str, action: str) -> bool:
    """True iff пара (entity_type, action) есть в whitelist'е ENTITY_ACTIONS."""
    allowed = ENTITY_ACTIONS.get(entity_type)
    return allowed is not None and action in allowed
