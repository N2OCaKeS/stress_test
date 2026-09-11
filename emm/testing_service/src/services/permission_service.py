"""Управление матрицей прав — list/grant/revoke (write-side).

Это **write-side** модуль: CRUD по `entity_permissions`. Использует
read-side хелперы из :mod:`src.services.permissions` (`require_action`) для
авторизации операций, которые не покрыты явным bypass'ом ниже. Дёргают
только endpoint'ы из `api/v1/endpoints/permissions.py`; бизнес-сервисы
(global_variable / test_definition / test_stand и т.д.) сюда не ходят — им
нужен только `permissions.has_action`/`require_action`.

testing_service не имеет per-instance ACL (в отличие от server_service) —
все сущности матрицы department-scoped или platform-scoped целиком, поэтому
здесь нет инстанс-уровневого оверлея, только тип-wide `entity_permissions`.

**Department scope.** `grant_action` / `revoke_action` кладут строки
исключительно per-department: department/service admin пишет в свой
собственный department. Передача другого ``target_department_id``
отбивается 403 ``DEPARTMENT_ISOLATION`` и аудитится как ``permission.grant``
denied с ``reason=department_isolation_grant`` (или ``..._revoke``).
System-wide строки (``department_id IS NULL``) для department-bound caller'ов
есть только в seed-миграциях системной роли `admin` — через runtime endpoint
их не создают.

**Платформенный `account_admin` — мета-админ матрицы.** Он управляет
`entity_permissions` любого отдела (просмотр + grant/revoke), цель задаётся
`target_department_id` без dept-isolation проверки. У него нет сервисных
ролей в testing_service (и не может быть — платформенные роли не несут
`department_id`), поэтому ролевой `require_action` для него снимается.
Обычные бизнес-эндпоинты testing_service ему недоступны вообще —
`get_current_identity` отбивает 403 `SERVICE_ACCESS_DENIED`, у него нет
`testing_service` в `allowed_services`. Достучаться сюда он может только
через `PermissionMatrixIdentity` (см. `dependencies/auth.py`).

**`department_admin` — тоже bypass, но только в своём отделе.** Это
платформенная роль отдела, не сервисная — у него может не быть
`service_roles['testing_service']` вообще, поэтому чистый ролевой
`require_action` его бы не пропустил. Тот же приём, что использует
auth_service для управления `ServiceRoleDefinition`
(`service_role_service._check_can_manage`: account_admin — везде,
department_admin — только свой отдел, либо явный носитель сервисной роли
`admin`). Bypass снимает только ролевую проверку — dept-isolation в
`_resolve_target_department_id` для department_admin работает как обычно
(scope = его собственный `department_id`, чужой `target_department_id` всё
равно 403).

Caller без `department_id` вообще (edge-кейс — platform-bound токен без
отдела, не account_admin) писать не может и обрабатывается как denied.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    Action,
    ENTITY_ACTIONS,
    EntityType,
    PlatformRole,
    is_system_role,
    is_valid_action,
)
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.core.permission_catalog import (
    ACTION_DESCRIPTIONS,
    ENTITY_DESCRIPTIONS,
    SENSITIVE_ACTIONS,
    WORKER_ONLY_ACTIONS,
)
from src.dependencies.auth import Identity
from src.models import EntityPermission
from src.repositories import entity_permission as repo
from src.services import audit_service, permissions
from src.utils.ids import entity_permission_id as new_id


def _is_matrix_meta_admin(identity: Identity) -> bool:
    """True для платформенного `account_admin` — мета-админа матрицы прав.

    Управляет `entity_permissions` любого отдела (просмотр и grant/revoke),
    но самого testing_service (тесты/стенды/очередь) не касается — на
    прочие эндпоинты он не проходит вообще (см. module docstring).
    """
    return identity.platform_role == PlatformRole.ACCOUNT_ADMIN


def _is_department_admin(identity: Identity) -> bool:
    """True для платформенного `department_admin` (любого отдела).

    Bypass ролевой проверки в рамках СВОЕГО отдела — dept-isolation ниже
    по-прежнему ограничивает его записи собственным `department_id`.
    """
    return identity.platform_role == PlatformRole.DEPARTMENT_ADMIN


def _read_scope(identity: Identity) -> str | None:
    """Scope для list-выборок матрицы.

    `None` → caller видит всю матрицу (только `account_admin`, не привязанный
    к отделу). Иначе — `identity.department_id`: caller видит system-wide
    строки плюс строки своего отдела (это верно и для `department_admin`).
    """
    if _is_matrix_meta_admin(identity):
        return None
    return identity.department_id


async def _authorize_view(db: AsyncSession, identity: Identity) -> None:
    """Гейт для read-эндпоинтов матрицы: `(permission, *, view)`.

    account_admin и department_admin проходят без ролевой проверки —
    остальным нужен явный grant `view` на entity `permission`.
    """
    if _is_matrix_meta_admin(identity) or _is_department_admin(identity):
        return
    await permissions.require_action(db, identity, EntityType.PERMISSION, Action.VIEW)


def _resolve_target_department_id(
    identity: Identity,
    target_department_id: str | None,
    *,
    audit_action: str,
    audit_details: dict,
    isolation_reason: str,
) -> str | None:
    """Определить department-scope для записи.

    Возвращает `department_id` для новой строки, либо бросает
    `AuthorizationError` с кодом `DEPARTMENT_ISOLATION`, если caller не имеет
    права писать в запрошенный scope. Эмитит denied-audit ДО raise.

    account_admin — единственный, кто пишет в произвольный отдел
    (`target_department_id` as-is). Все остальные (включая department_admin)
    scoped на `identity.department_id`; передача другого
    `target_department_id` → `DEPARTMENT_ISOLATION`.

    Service-субъекты (`bot`/`oauth_client`) отбиваются явно — матрицу прав
    сейчас редактируют только живые люди (account_admin/department_admin/
    admin-роль сервиса); если завтра боту по ошибке выдадут permission_grant,
    мы не хотим, чтобы он молча получил scope своего department'а.
    """
    if identity.actor_type in {"bot", "oauth_client"}:
        audit_service.emit(
            audit_action,
            target_type="entity_permission",
            status="denied",
            allowed=False,
            details={
                **audit_details,
                "reason": isolation_reason,
                "actor_type": identity.actor_type,
                "target_department_id": target_department_id,
            },
        )
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message=(
                "Service accounts (bot / OAuth client) cannot manage "
                "entity_permissions; route via human admin"
            ),
            details={"target_department_id": target_department_id},
        )
    if _is_matrix_meta_admin(identity):
        # account_admin пишет в любой отдел, переданный target'ом, без
        # isolation-проверки — своего отдела у него нет.
        return target_department_id
    actor_dept = identity.department_id
    if actor_dept is None:
        audit_service.emit(
            audit_action,
            target_type="entity_permission",
            status="denied",
            allowed=False,
            details={
                **audit_details,
                "reason": isolation_reason,
                "actor_department_id": None,
                "target_department_id": target_department_id,
            },
        )
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message=(
                "Caller has no department; only account_admin may manage "
                "system-wide entity_permissions"
            ),
            details={"target_department_id": target_department_id},
        )
    if target_department_id is not None and target_department_id != actor_dept:
        audit_service.emit(
            audit_action,
            target_type="entity_permission",
            status="denied",
            allowed=False,
            details={
                **audit_details,
                "reason": isolation_reason,
                "actor_department_id": actor_dept,
                "target_department_id": target_department_id,
            },
        )
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message="Cannot manage entity_permissions for a different department",
            details={
                "actor_department_id": actor_dept,
                "target_department_id": target_department_id,
            },
        )
    return actor_dept


def _reject_system_role(role: str, *, audit_action: str, audit_details: dict) -> None:
    """Отбить попытку править матрицу системной роли (`admin`/`guest`).

    Их набор прав фиксирован (admin=всё, guest=view открытых каталогов) и
    неизменяем через API.
    """
    if not is_system_role(role):
        return
    audit_service.emit(
        audit_action,
        target_type="entity_permission",
        status="failure",
        allowed=True,
        details={**audit_details, "reason": "system_role_immutable"},
    )
    raise ConflictError(
        error_code="SYSTEM_ROLE_IMMUTABLE",
        message=(
            f"System role '{role}' has a fixed permission matrix and cannot be "
            "modified"
        ),
        details={"role": role},
    )


async def list_all(
    db: AsyncSession,
    identity: Identity,
    *,
    role: str | None = None,
) -> list[EntityPermission]:
    """Список grants. Требует `view` на entity `permission`.

    `role` сужает выборку до грантов одной роли (срез матрицы для UI/ИБ).
    Scope: department-bound caller видит свой отдел + system-wide строки;
    `account_admin` — всю матрицу.
    """
    await _authorize_view(db, identity)
    scope = _read_scope(identity)
    if role is not None:
        return await repo.list_for_role(db, role, department_id=scope)
    return await repo.list_all(db, department_id=scope)


async def list_for_entity(
    db: AsyncSession, identity: Identity, entity_type: str
) -> list[EntityPermission]:
    """Список grants для одного entity_type. Неизвестный type → 422."""
    await _authorize_view(db, identity)
    if entity_type not in {e.value for e in EntityType}:
        raise DomainValidationError(
            error_code="UNKNOWN_ENTITY_TYPE",
            message=f"Unknown entity_type '{entity_type}'",
        )
    return await repo.list_for_entity(db, entity_type, department_id=_read_scope(identity))


def build_catalog() -> list[dict]:
    """Собрать каталог сущностей и действий из `ENTITY_ACTIONS` + описаний.

    Действия каждой сущности упорядочены по объявлению в `Action`, чтобы
    порядок в выдаче был стабилен. Без авторизации — её делает `get_catalog`.
    """
    action_order = {a.value: i for i, a in enumerate(Action)}
    catalog: list[dict] = []
    for entity_type in EntityType:
        actions = ENTITY_ACTIONS.get(entity_type, frozenset())
        catalog.append({
            "entity_type": entity_type.value,
            "description": ENTITY_DESCRIPTIONS[entity_type],
            "actions": [
                {
                    "action": action,
                    "description": ACTION_DESCRIPTIONS[action],
                    "sensitive": action in SENSITIVE_ACTIONS,
                    "worker_only": action in WORKER_ONLY_ACTIONS,
                }
                for action in sorted(actions, key=lambda a: action_order.get(a, 999))
            ],
        })
    return catalog


async def get_catalog(db: AsyncSession, identity: Identity) -> list[dict]:
    """Каталог сущностей и действий с описаниями. Требует `view` на `permission`.

    Состав берётся из `ENTITY_ACTIONS`, описания — из `permission_catalog`.
    Read-only справочник для UI и ИБ-обзора; данные матрицы не затрагивает.
    """
    await _authorize_view(db, identity)
    return build_catalog()


def describe_row(row: EntityPermission) -> dict:
    """Поля-обогащение строки матрицы описаниями каталога.

    Описания могут отсутствовать для entity/action, выпиленных follow-on
    миграциями (строка-сирота в БД) — тогда отдаём пустую строку и
    `sensitive=False`, чтобы не ронять выдачу всей матрицы.
    """
    return {
        "entity_description": ENTITY_DESCRIPTIONS.get(row.entity_type, ""),
        "action_description": ACTION_DESCRIPTIONS.get(row.action, ""),
        "sensitive": row.action in SENSITIVE_ACTIONS,
    }


async def grant_action(
    db: AsyncSession,
    identity: Identity,
    *,
    entity_type: str,
    role: str,
    action: str,
    target_department_id: str | None = None,
) -> EntityPermission:
    """Выдать `action` на `entity_type` для `role`.

    Scope (см. module docstring): department/service admin неявно нацеливается
    на свой department; не совпадающий `target_department_id` → 403
    `DEPARTMENT_ISOLATION`.

    Идемпотентность: повторный grant в тот же scope возвращает существующую
    строку без INSERT'а и без audit-emit — иначе повторный вызов из UI
    поднимал бы false-positive «новая выдача прав» в SIEM.
    """
    audit_details = {"entity_type": entity_type, "role": role, "action": action}
    if not _is_matrix_meta_admin(identity) and not _is_department_admin(identity):
        try:
            await permissions.require_action(
                db, identity, EntityType.PERMISSION, Action.PERMISSION_GRANT
            )
        except AuthorizationError:
            audit_service.emit(
                "permission.grant",
                target_type="entity_permission",
                status="denied", allowed=False,
                details={**audit_details, "reason": "permission_denied"},
            )
            raise
    _reject_system_role(role, audit_action="permission.grant", audit_details=audit_details)
    department_id = _resolve_target_department_id(
        identity,
        target_department_id,
        audit_action="permission.grant",
        audit_details=audit_details,
        isolation_reason="department_isolation_grant",
    )
    if not is_valid_action(entity_type, action):
        audit_service.emit(
            "permission.grant", target_type="entity_permission",
            status="failure", allowed=True,
            details={**audit_details, "reason": "invalid_action_for_entity"},
        )
        raise DomainValidationError(
            error_code="INVALID_ACTION_FOR_ENTITY",
            message=f"Action '{action}' is not valid for entity_type '{entity_type}'",
            details={"entity_type": entity_type, "action": action},
        )
    scope_details = {**audit_details, "department_id": department_id}
    existing = await repo.get(db, entity_type, role, action, department_id)
    if existing is not None:
        return existing
    try:
        obj = await repo.grant(
            db,
            permission_id=new_id(),
            entity_type=entity_type,
            role=role,
            action=action,
            granted_by=identity.user_id,
            department_id=department_id,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "permission.grant", target_type="entity_permission",
            status="failure", allowed=True,
            details={**scope_details, "reason": "race_already_exists"},
        )
        raise ConflictError(
            error_code="PERMISSION_ALREADY_EXISTS",
            message=(
                "Permission row already exists for this "
                "(entity_type, role, action, department_id)"
            ),
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "permission.grant",
        target_id=obj.id, target_type="entity_permission",
        status="success", allowed=True,
        details=scope_details,
    )
    return obj


async def revoke_action(
    db: AsyncSession,
    identity: Identity,
    *,
    entity_type: str,
    role: str,
    action: str,
    target_department_id: str | None = None,
) -> None:
    """Снять существующий grant. Scope-семантика как у `grant_action`.

    Отсутствие строки → 404 `PERMISSION_NOT_FOUND` (а не noop) — чтобы клиент
    знал, что revoke не удалил то, что ожидал.
    """
    audit_details = {"entity_type": entity_type, "role": role, "action": action}
    if not _is_matrix_meta_admin(identity) and not _is_department_admin(identity):
        try:
            await permissions.require_action(
                db, identity, EntityType.PERMISSION, Action.PERMISSION_REVOKE
            )
        except AuthorizationError:
            audit_service.emit(
                "permission.revoke",
                target_type="entity_permission",
                status="denied", allowed=False,
                details={**audit_details, "reason": "permission_denied"},
            )
            raise
    _reject_system_role(role, audit_action="permission.revoke", audit_details=audit_details)
    department_id = _resolve_target_department_id(
        identity,
        target_department_id,
        audit_action="permission.revoke",
        audit_details=audit_details,
        isolation_reason="department_isolation_revoke",
    )
    scope_details = {**audit_details, "department_id": department_id}
    removed = await repo.revoke(db, entity_type, role, action, department_id)
    if removed == 0:
        await db.rollback()
        audit_service.emit(
            "permission.revoke", target_type="entity_permission",
            status="failure", allowed=True,
            details={**scope_details, "reason": "not_found"},
        )
        raise NotFoundError(
            error_code="PERMISSION_NOT_FOUND",
            message=(
                "No permission row matched "
                "(entity_type, role, action, department_id)"
            ),
            details={
                "entity_type": entity_type,
                "role": role,
                "action": action,
                "department_id": department_id,
            },
        )
    await db.commit()
    audit_service.emit(
        "permission.revoke", target_type="entity_permission",
        status="success", allowed=True,
        details=scope_details,
    )
