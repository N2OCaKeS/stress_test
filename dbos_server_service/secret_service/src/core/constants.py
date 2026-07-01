"""Общие enum'ы и константы сервиса."""

from enum import StrEnum

SERVICE_NAME = "secret_service"

# Health/ready paths, исключаемые из rate-limit / audit / introspect.
HEALTH_PATHS: frozenset[str] = frozenset({
    "/api/secret/v1/health",
    "/api/secret/v1/ready",
})


class ServiceRole(StrEnum):
    """Системные service-роли secret_service.

    Только `guest` (видит наличие/метаданные неличных секретов отдела, без
    значений) и `admin` (полный доступ к неличным секретам отдела) сеются
    автоматически и защищены фиксированной матрицей. Промежуточный доступ —
    через кастомные роли (создаются в auth_service), которые ссылаются на
    матрицу действий по имени и грантятся per-department строками
    `entity_permissions`.
    """

    GUEST = "guest"
    ADMIN = "admin"


# Системные роли с фиксированной матрицей: `admin` (всё на неличных секретах
# отдела) и `guest` (только метаданные). Их набор прав не редактируется через
# API — grant/revoke по ним отбивается 409 SYSTEM_ROLE_IMMUTABLE.
SYSTEM_SERVICE_ROLES: frozenset[str] = frozenset({ServiceRole.GUEST, ServiceRole.ADMIN})


def is_system_role(role: str) -> bool:
    """True iff `role` — системная (`admin`/`guest`) с неизменяемой матрицей."""
    return role in SYSTEM_SERVICE_ROLES


class EntityType(StrEnum):
    """Типы сущностей для матрицы entity_permissions.

    У secret_service ровно одна управляемая сущность — сам credential.
    """

    SECRET = "secret"


class SecretAction(StrEnum):
    """Fine-grained actions для матрицы прав и проверок доступа.

    Имена совпадают со строками, которыми оперирует `access_service.check_access`
    — это единый словарь действий сервиса. Лесенка доступа к значению:
    `read` (метаданные без значения) ⊂ `reveal` (раскрытие значения) ⊂ `write`
    (изменение). `delete`/`grant_acl`/`grant_dept`/`manage_status` — отдельные
    привилегированные действия управления кред'ой.

    `list_guest` — служебное псевдо-действие листинга для роли `guest`; в
    матрицу (`ENTITY_ACTIONS`) не входит, потому что семантика guest фиксирована
    и грантами не управляется.
    """

    READ = "read"
    REVEAL = "reveal"
    WRITE = "write"
    DELETE = "delete"
    GRANT_ACL = "grant_acl"
    GRANT_DEPT = "grant_dept"
    MANAGE_STATUS = "manage_status"
    LIST_GUEST = "list_guest"


# Whitelist валидных пар (entity_type, action). Несовпадение → 422
# INVALID_ACTION_FOR_ENTITY в permission_service.grant_action. `list_guest`
# сюда не входит — это внутренняя проекция guest-листинга, не грантуемое право.
ENTITY_ACTIONS: dict[str, frozenset[str]] = {
    EntityType.SECRET: frozenset({
        SecretAction.READ,
        SecretAction.REVEAL,
        SecretAction.WRITE,
        SecretAction.DELETE,
        SecretAction.GRANT_ACL,
        SecretAction.GRANT_DEPT,
        SecretAction.MANAGE_STATUS,
    }),
}


def is_valid_action(entity_type: str, action: str) -> bool:
    """True iff пара (entity_type, action) есть в whitelist'е ENTITY_ACTIONS."""
    allowed = ENTITY_ACTIONS.get(entity_type)
    return allowed is not None and action in allowed
