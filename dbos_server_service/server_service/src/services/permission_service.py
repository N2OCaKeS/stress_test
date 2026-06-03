"""Управление матрицей прав — list/grant/revoke (write-side).

Это **write-side** модуль: CRUD по `entity_permissions`. Использует
read-side хелперы из :mod:`src.services.permissions` (`require_action`)
для авторизации каждой своей операции. Дёргают только endpoint'ы из
`api/v1/endpoints/permissions.py`; бизнес-сервисы (server / ipmi / disk
и т.д.) сюда не ходят — им нужен только `permissions.has_action`.

**Department scope.** `grant_action` / `revoke_action` кладут строки
исключительно per-department: department/service admin пишет в свой
собственный department. Передача другого ``target_department_id``
отбивается 403 ``DEPARTMENT_ISOLATION`` и аудитится как
``permission.grant`` denied с ``reason=department_isolation_grant``
(или ``..._revoke``).

System-wide строки (``department_id IS NULL``) есть только в seed-миграциях
для встроенных ролей — через runtime endpoint их не создать. Platform-роли
(``account_admin``/``loging_admin``) до этого слоя не доходят: их режет
``platform_admin_guard`` middleware ещё на входе.

Caller без ``department_id`` (edge-кейс — `loging_reader` или прочие
platform-bound токены без отдела) писать не может и обрабатывается как
denied.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    Action,
    EntityType,
    ENTITY_ACTIONS,
    PlatformRole,
    is_valid_action,
)
from src.core.permission_catalog import (
    ACTION_DESCRIPTIONS,
    ENTITY_DESCRIPTIONS,
    SENSITIVE_ACTIONS,
    WORKER_CALLBACK_ACTIONS,
)
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.models import EntityPermission
from src.repositories import entity_permission as repo
from src.schemas.identity import IdentityContext
from src.services import audit_service, permissions
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import entity_permission_id as new_id


def _resolve_target_department_id(
    identity: IdentityContext,
    target_department_id: str | None,
    *,
    audit_action: str,
    audit_details: dict,
    isolation_reason: str,
) -> str | None:
    """Определить department-scope для записи.

    Возвращает `department_id` для новой строки, либо бросает
    :class:`AuthorizationError` с кодом ``DEPARTMENT_ISOLATION``, если caller
    не имеет права писать в запрошенный scope. Эмитит denied-audit ДО raise.

    Caller обязан быть department-bound (`identity.department_id != None`).
    Все writes scoped на ``identity.department_id``; передача другого
    ``target_department_id`` → ``DEPARTMENT_ISOLATION``. System-wide grant'ов
    через этот путь нет — они приходят только из seed-миграций.

    Также явно отбиваем internal-service субъектов (`subject_type == "pat"`
    для worker_bot и подобных). Сейчас матрицу прав никакой service-account
    не редактирует — `permission.grant`/`revoke` есть только у живых ролей
    account_admin/department_admin. Если завтра какому-нибудь боту по ошибке
    выдадут эти actions, мы не хотим, чтобы тут он молча получил scope своего
    department'а — лучше явный DEPARTMENT_ISOLATION на старте.
    """
    if identity.subject_type in {"pat", "oauth_client"}:
        audit_service.emit(
            audit_action,
            target_type="entity_permission",
            status="denied",
            allowed=False,
            details={
                **audit_details,
                "reason": isolation_reason,
                "actor_subject_type": identity.subject_type,
                "target_department_id": target_department_id,
            },
        )
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message=(
                "Service accounts (PAT / OAuth client) cannot manage "
                "entity_permissions; route via human admin"
            ),
            details={"target_department_id": target_department_id},
        )
    actor_dept = identity.department_id
    if actor_dept is None:
        # Caller без department'а вообще. Трактуем как isolation violation,
        # а не как generic permission denial — caller *пытается* писать,
        # но у него нет своего scope'а.
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
            message=(
                "Cannot manage entity_permissions for a different department"
            ),
            details={
                "actor_department_id": actor_dept,
                "target_department_id": target_department_id,
            },
        )
    return actor_dept


_PLATFORM_GLOBAL_VIEWERS: frozenset[PlatformRole] = frozenset(
    {PlatformRole.ACCOUNT_ADMIN, PlatformRole.LOGING_ADMIN}
)


def _read_scope(identity: IdentityContext) -> str | None:
    """Scope для list-выборок матрицы.

    Возвращает `None` → caller видит всю матрицу (platform-уровневые админы
    `account_admin`/`loging_admin`, не привязанные к отделу). Иначе —
    `identity.department_id`, и caller видит только system-wide строки плюс
    строки своего отдела.

    На практике `account_admin`/`loging_admin` блокируются
    `platform_admin_guard` middleware ещё до endpoint'а, поэтому в обычном
    потоке сюда дойдут только department-bound caller'ы. Branch оставлен
    как fallback и единая точка истины: если middleware снимут или появится
    новая platform-роль без отдела, scope-логика останется корректной.
    """
    if identity.platform_role in _PLATFORM_GLOBAL_VIEWERS:
        return None
    return identity.department_id


async def list_all(
    db: AsyncSession,
    identity: IdentityContext,
    *,
    role: str | None = None,
) -> list[EntityPermission]:
    """Список grants. Требует `view` на entity `permission`.

    `role` сужает выборку до грантов одной роли (срез матрицы для UI/ИБ).
    Scope: department-bound caller видит свой отдел + system-wide строки;
    platform-уровневый (account_admin / loging_admin) — всю матрицу.
    """
    await permissions.require_action(db, identity, EntityType.PERMISSION, Action.VIEW)
    scope = _read_scope(identity)
    if role is not None:
        return await repo.list_for_role(db, role, department_id=scope)
    return await repo.list_all(db, department_id=scope)


async def get_catalog(
    db: AsyncSession, identity: IdentityContext
) -> list[dict]:
    """Каталог сущностей и действий с описаниями. Требует `view` на `permission`.

    Состав берётся из `ENTITY_ACTIONS`, описания — из `permission_catalog`.
    Read-only справочник для UI и ИБ-обзора; данные матрицы не затрагивает.
    """
    await permissions.require_action(db, identity, EntityType.PERMISSION, Action.VIEW)
    return build_catalog()


def build_catalog() -> list[dict]:
    """Собрать каталог сущностей и действий из `ENTITY_ACTIONS` + описаний.

    Действия каждой сущности упорядочены по их объявлению в `Action`, чтобы
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
                    "worker_only": action in WORKER_CALLBACK_ACTIONS,
                }
                for action in sorted(actions, key=lambda a: action_order.get(a, 999))
            ],
        })
    return catalog


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


async def list_for_entity(
    db: AsyncSession, identity: IdentityContext, entity_type: str
) -> list[EntityPermission]:
    """Список grants для одного entity_type. Неизвестный type → 422."""
    await permissions.require_action(db, identity, EntityType.PERMISSION, Action.VIEW)
    if entity_type not in {e.value for e in EntityType}:
        raise DomainValidationError(
            error_code="UNKNOWN_ENTITY_TYPE",
            message=f"Unknown entity_type '{entity_type}'",
        )
    return await repo.list_for_entity(db, entity_type, department_id=_read_scope(identity))


async def grant_action(
    db: AsyncSession,
    identity: IdentityContext,
    *,
    entity_type: str,
    role: str,
    action: str,
    target_department_id: str | None = None,
) -> EntityPermission:
    """Выдать `action` на `entity_type` для `role`.

    Scope (см. module docstring): department/service admin неявно нацеливается
    на свой department; не совпадающий ``target_department_id`` → 403
    ``DEPARTMENT_ISOLATION``.

    Идемпотентность: повторный grant в тот же scope возвращает существующую
    строку без INSERT'а и без audit-emit. SIEM-правила «новая выдача прав»
    висят на `permission.grant`+`status="success"`; раньше no-op ветка тоже
    эмитила `success` и поднимала false-positive на каждый повторный вызов.
    """
    audit_details = {"entity_type": entity_type, "role": role, "action": action}
    # 1. проверка matrix-уровня
    with emit_denied_on_authz_error(
        "permission.grant",
        target_type="entity_permission",
        extra_details=dict(audit_details),
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.PERMISSION, Action.PERMISSION_GRANT
        )
    # 2. определяем department-scope (и enforce'им изоляцию для не-account_admin)
    department_id = _resolve_target_department_id(
        identity,
        target_department_id,
        audit_action="permission.grant",
        audit_details=audit_details,
        isolation_reason="department_isolation_grant",
    )
    # 3. action-vs-entity whitelist
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
    # 4. идемпотентность — exact-scope lookup. На no-op аудит не эмитим:
    # SIEM-rule «выдан новый grant» строится на `permission.grant` +
    # `status="success"`, и повторный вызов с success-эмитом приходил как
    # false-positive (видно бы было «новая выдача прав» на каждый POST из UI).
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
    identity: IdentityContext,
    *,
    entity_type: str,
    role: str,
    action: str,
    target_department_id: str | None = None,
) -> None:
    """Снять существующий grant. Scope-семантика как у :func:`grant_action`.

    Отсутствие строки → 404 PERMISSION_NOT_FOUND (а не noop) — чтобы клиент
    знал, что revoke не удалил то, что ожидал.
    """
    audit_details = {"entity_type": entity_type, "role": role, "action": action}
    with emit_denied_on_authz_error(
        "permission.revoke",
        target_type="entity_permission",
        extra_details=dict(audit_details),
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.PERMISSION, Action.PERMISSION_REVOKE
        )
    department_id = _resolve_target_department_id(
        identity,
        target_department_id,
        audit_action="permission.revoke",
        audit_details=audit_details,
        isolation_reason="department_isolation_revoke",
    )
    scope_details = {**audit_details, "department_id": department_id}
    removed = await repo.revoke(db, entity_type, role, action, department_id)
    await db.commit()
    if removed == 0:
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
    audit_service.emit(
        "permission.revoke", target_type="entity_permission",
        status="success", allowed=True,
        details=scope_details,
    )
