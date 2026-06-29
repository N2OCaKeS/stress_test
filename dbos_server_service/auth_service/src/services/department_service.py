"""Бизнес-логика отделов: CRUD departments + grant/revoke access к сервисам."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, DomainValidationError, NotFoundError
from src.repositories.bots import BotRepository
from src.repositories.bot_roles import BotRoleRepository
from src.repositories.bot_tokens import BotTokenRepository
from src.repositories.departments import DepartmentRepository
from src.repositories.groups import GroupRepository
from src.repositories.oauth_clients import OAuthClientRepository
from src.repositories.roles import RoleRepository
from src.repositories.service_role_definitions import ServiceRoleDefinitionRepository
from src.repositories.services import ServiceRepository
from src.schemas.departments import (
    DepartmentResponse,
    DepartmentUpdateRequest,
    ServiceAccessResponse,
)
from src.services import audit_service, secret_service_client
from src.services._cache_invalidation import invalidate_identity_cache as _invalidate_identity_cache
from src.utils.time import utcnow


async def create_department(
    db: AsyncSession,
    actor_id: str,
    name: str,
    request_id: str | None = None,
) -> DepartmentResponse:
    """Создать отдел. Уникальность по `name`."""
    repo = DepartmentRepository(db)
    if await repo.get_by_name(name):
        raise ConflictError(error_code="DEPARTMENT_ALREADY_EXISTS", message=f"Department '{name}' already exists")

    dept = await repo.create(name)
    await db.commit()
    audit_service.emit(
        "department.create", actor_id, target_id=dept.id, target_type="department",
        request_id=request_id,
        details={"name": name},
    )
    return DepartmentResponse(
        department_id=dept.id,
        name=dept.name,
        description=dept.description,
        is_active=dept.is_active,
        created_at=dept.created_at,
        user_count=0,
    )


async def update_department(
    db: AsyncSession,
    actor_id: str,
    actor_username: str | None,
    dept_id: str,
    body: DepartmentUpdateRequest,
    request_id: str | None = None,
) -> DepartmentResponse:
    """Точечный апдейт name / description.

    Guard на роль выполняется на уровне endpoint'а (account_admin); сюда
    приходит уже отфильтрованный actor. Пустое тело — 422 `EMPTY_UPDATE`
    (проверка дублируется тут и в endpoint'е — defense in depth). При
    переименовании проверяем уникальность `name`.
    """
    if body.name is None and body.description is None:
        raise DomainValidationError(
            error_code="EMPTY_UPDATE",
            message="At least one of name or description must be provided",
        )

    repo = DepartmentRepository(db)
    dept = await repo.get_by_id(dept_id)
    if dept is None:
        raise NotFoundError(
            error_code="DEPARTMENT_NOT_FOUND",
            message=f"Department {dept_id} not found",
        )

    if body.name is not None and body.name != dept.name:
        existing = await repo.get_by_name(body.name)
        if existing is not None and existing.id != dept.id:
            raise ConflictError(
                error_code="DEPARTMENT_ALREADY_EXISTS",
                message=f"Department '{body.name}' already exists",
            )

    changes: dict[str, dict[str, str | None]] = {}
    if body.name is not None and body.name != dept.name:
        changes["name"] = {"old": dept.name, "new": body.name}
    if body.description is not None and body.description != dept.description:
        changes["description"] = {"old": dept.description, "new": body.description}

    dept = await repo.update(
        dept,
        name=body.name,
        description=body.description,
    )
    await db.commit()

    audit_service.emit(
        "department.updated", actor_id, target_id=dept.id, target_type="department",
        request_id=request_id,
        username=actor_username,
        details={
            "name": dept.name,
            "changes": changes,
        },
    )
    return DepartmentResponse(
        department_id=dept.id,
        name=dept.name,
        description=dept.description,
        is_active=dept.is_active,
        created_at=dept.created_at,
        user_count=await repo.count_users(dept.id),
    )


async def list_departments(
    db: AsyncSession,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> list[DepartmentResponse]:
    """Все отделы."""
    repo = DepartmentRepository(db)
    depts = await repo.list_all()
    # Один агрегатный GROUP BY на весь список — без N+1 по отделам.
    counts = await repo.user_counts_by_department()
    result = [
        DepartmentResponse(
            department_id=d.id,
            name=d.name,
            description=d.description,
            is_active=d.is_active,
            created_at=d.created_at,
            user_count=counts.get(d.id, 0),
        )
        for d in depts
    ]
    audit_service.emit(
        "department.list", actor_id, status="success", allowed=True,
        request_id=request_id,
        details={"count": len(result)},
    )
    return result


async def list_department_services(
    db: AsyncSession,
    department_id: str,
) -> list[str]:
    """Список service_name'ов с активным grant'ом для отдела.

    UI зовёт это чтобы отрисовать «гранты, выданные отделу» без
    fan-out через listServices + per-service introspect.
    """
    repo = DepartmentRepository(db)
    dept = await repo.get_by_id(department_id)
    if dept is None:
        raise DomainValidationError(
            error_code="DEPARTMENT_NOT_FOUND",
            http_status=404,
            message=f"Department {department_id} not found",
        )
    return await repo.list_active_services(department_id)


async def grant_service_access(
    db: AsyncSession,
    actor_id: str,
    department_id: str,
    service_name: str,
    request_id: str | None = None,
) -> ServiceAccessResponse:
    """Выдать отделу access к сервису + засеять system-роль `admin` в новом scope."""
    dept_repo = DepartmentRepository(db)
    svc_repo = ServiceRepository(db)

    dept = await dept_repo.get_by_id(department_id)
    if dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message="Department not found")

    svc = await svc_repo.get(service_name)
    if svc is None or not svc.is_active:
        raise NotFoundError(error_code="SERVICE_NOT_FOUND", message=f"Service '{service_name}' not found")

    existing = await dept_repo.get_access(department_id, service_name)
    if existing and existing.is_active:
        raise ConflictError(error_code="SERVICE_ALREADY_GRANTED", message="Department already has access to this service")

    # Считаем `reactivated` ДО мутации `is_active=True`. Иначе SOC не
    # отличит первой выдачи от реактивации revoked access (severity
    # `department.service_grant` = CRITICAL).
    reactivated = bool(existing and not existing.is_active)

    if existing and not existing.is_active:
        # Реактивация — это эффективно новый grant: обновляем `granted_at` и
        # `granted_by` на текущего актора, иначе list-эндпоинты показывают
        # автора первой выдачи, а ответственным за актуальный доступ
        # числится кто-то другой.
        existing.is_active = True
        existing.revoked_at = None
        existing.revoked_by = None
        existing.granted_at = utcnow()
        existing.granted_by = actor_id
        await db.flush()
    else:
        await dept_repo.grant_access(department_id, service_name, granted_by=actor_id)

    # Засеять (или реактивировать) системные роли (`guest`, `admin`) для пары
    # (dept, service).
    role_def_repo = ServiceRoleDefinitionRepository(db)
    await role_def_repo.seed_system_roles(department_id, service_name, actor_id)

    await db.commit()
    audit_service.emit(
        "department.service_grant", actor_id, target_id=department_id, target_type="department",
        details={
            "department_name": dept.name,
            "service_name": service_name,
            "reactivated": reactivated,
        },
        request_id=request_id,
    )
    return ServiceAccessResponse(department_id=department_id, service_name=service_name, enabled=True)


async def revoke_service_access(
    db: AsyncSession,
    actor_id: str,
    department_id: str,
    service_name: str,
    request_id: str | None = None,
) -> None:
    """Отозвать access отдела + каскадно деактивировать все зависящие роли."""
    dept_repo = DepartmentRepository(db)
    role_repo = RoleRepository(db)

    access = await dept_repo.get_access(department_id, service_name)
    if access is None or not access.is_active:
        raise NotFoundError(error_code="SERVICE_NOT_FOUND", message="Service access not found")

    await dept_repo.revoke_access(access, revoked_by=actor_id)

    # Каскадно сносим всё, что зависело от пары (dept, service): role
    # definitions (включая системный `admin`), user→role assignments, и
    # group→role bindings для групп этого отдела.
    role_def_repo = ServiceRoleDefinitionRepository(db)
    await role_def_repo.deactivate_all_for_dept_service(department_id, service_name)
    affected_direct_user_ids = await role_repo.deactivate_all_in_dept_for_service(
        department_id, service_name,
    )
    group_repo = GroupRepository(db)
    affected_group_ids = await group_repo.deactivate_all_dept_service_roles(
        department_id, service_name,
    )
    # Симметрично снимаем `GroupServiceAccess`: без этого
    # `list_active_services_by_groups` продолжает возвращать `service_name`,
    # и `_merge_permissions` (`dept ∪ group`) добавляет revoked-сервис обратно
    # в `allowed_services` юзера через group-канал — downstream-сервисы
    # пускали бы по stale scope.
    affected_access_group_ids = await group_repo.deactivate_all_dept_service_access(
        department_id, service_name,
    )
    bot_role_repo = BotRoleRepository(db)
    affected_direct_bot_ids = await bot_role_repo.deactivate_all_in_dept_for_service(
        department_id, service_name,
    )

    # Собираем юзеров, которым нужен cache-invalidation: прямые носители роли
    # + члены групп, у которых сняли group→role binding ИЛИ group→service-access.
    # Без сброса они до TTL=5s могли бы продолжать обращаться к сервису, у
    # которого отдел уже не имеет доступа — `_merge_permissions` INTERSECT-
    # инвариант нарушался.
    affected_user_ids: set[str] = set(affected_direct_user_ids)
    affected_bot_ids: set[str] = set(affected_direct_bot_ids)
    member_group_ids = set(affected_group_ids) | set(affected_access_group_ids)
    if member_group_ids:
        affected_user_ids.update(
            await group_repo.list_member_user_ids(list(member_group_ids))
        )
        affected_bot_ids.update(
            await group_repo.list_member_bot_ids(list(member_group_ids))
        )

    await db.commit()
    for uid in affected_user_ids:
        _invalidate_identity_cache(uid)
    for bid in affected_bot_ids:
        _invalidate_identity_cache(bid)
    audit_service.emit(
        "department.service_revoke", actor_id, target_id=department_id, target_type="department",
        details={
            "service_name": service_name,
            "cascade_deactivated_roles": True,
            "affected_user_count": len(affected_user_ids),
            "affected_bot_count": len(affected_bot_ids),
        },
        request_id=request_id,
    )

    # Lifecycle-callback в secret_service — только для service=="secret_service".
    # Когда отдел теряет access к secret_service, secret_service должен
    # каскадно revoke'нуть DeptGrant'ы / RoleACL, где этот отдел — recipient.
    # Best-effort: ошибки внутри клиента уходят в audit, наружу не пробрасываем.
    if service_name == "secret_service":
        actor_username: str | None = None
        try:
            from src.repositories.users import UserRepository
            actor = await UserRepository(db).get_by_id(actor_id)
            if actor is not None:
                actor_username = actor.username
        except Exception:
            pass
        try:
            await secret_service_client.notify_dept_service_access_revoked(
                dept_id=department_id,
                service=service_name,
                actor_id=actor_id,
                actor_username=actor_username,
            )
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "secret_service notify failed (revoke_service_access): %s", exc,
            )


async def hard_delete_department(
    db: AsyncSession,
    actor_id: str,
    actor_username: str | None,
    department_id: str,
    reason: str,
    request_id: str | None = None,
) -> dict:
    """Hard-delete отдела.

    Жёсткое удаление: row в `departments` сносится. CASCADE-FK уносят
    `DepartmentServiceAccess`, `ServiceRoleDefinition`, `UserGroup` (с её
    membership'ами и role-bindings через свои cascade), `DepartmentDockerRegistry`.
    `bots.department_id` и `oauth_clients.department_id` имеют ondelete=RESTRICT —
    эти сущности надо снести явно ДО `DELETE departments`, иначе Postgres
    выкинет ForeignKeyViolation.

    Защита:
        Если в отделе остались активные юзеры (`is_active=True`) — отказ
        `USERS_REMAIN_IN_DEPT` (422). Сначала их надо перевести в другой
        отдел через `PATCH /users/{id}` или hard-delete каждого. Это не
        технический инвариант (FK `users.department_id` имеет
        ondelete=RESTRICT — Postgres всё равно отказал бы), но мы хотим
        вернуть actionable error code до того, как мы начнём что-либо
        мутировать в БД.

    Cascade на ботов и oauth_clients'ы:
        Боты dept'а удаляются вместе с отделом — бот это dept-owned
        entity, без отдела теряет смысл. ORM-cascade ботов снесёт их
        токены и role-bindings.

    Audit:
        `department.hard_deleted` CRITICAL с `reason`. После commit'а
        `secret_service_client.notify_dept_deleted` best-effort.
    """
    dept_repo = DepartmentRepository(db)
    bot_repo = BotRepository(db)
    bot_token_repo = BotTokenRepository(db)
    oauth_client_repo = OAuthClientRepository(db)

    dept = await dept_repo.get_for_update(department_id)
    if dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message="Department not found")

    active_users = await dept_repo.count_active_users(department_id)
    if active_users > 0:
        raise DomainValidationError(
            error_code="USERS_REMAIN_IN_DEPT",
            message=(
                f"Department has {active_users} active user(s); "
                "transfer or hard-delete them before deleting the department"
            ),
        )

    # ── Bots cascade ──────────────────────────────────────────────────────────
    # FK RESTRICT — снимаем bot-токены оптом (для audit-counter'а) и затем
    # сами bot-row'ы (ORM-cascade проведёт BotServiceRole/BotGroupMembership).
    bots = await bot_repo.list_by_department(department_id)
    bot_ids = [b.id for b in bots]
    bot_tokens_revoked = 0
    if bot_ids:
        bot_tokens_revoked = await bot_token_repo.revoke_all_for_bots(bot_ids)
        for bot in bots:
            await db.delete(bot)
        await db.flush()

    # ── OAuth clients cascade ────────────────────────────────────────────────
    # FK RESTRICT — снимаем confidential OAuth-клиентов отдела. Их authorization
    # codes цепляются по FK CASCADE (см. модель), специальной чистки не нужно.
    oauth_clients = await oauth_client_repo.list_by_department(department_id)
    oauth_client_count = len(oauth_clients)
    for client in oauth_clients:
        await db.delete(client)
    if oauth_clients:
        await db.flush()

    dept_name = dept.name

    await dept_repo.delete(dept)
    await db.commit()

    audit_service.emit(
        "department.hard_deleted", actor_id, target_id=department_id, target_type="department",
        details={
            "department_name": dept_name,
            "reason": reason,
            "bots_deleted": len(bot_ids),
            "bot_tokens_revoked": bot_tokens_revoked,
            "oauth_clients_deleted": oauth_client_count,
        },
        request_id=request_id,
    )

    try:
        await secret_service_client.notify_dept_deleted(
            dept_id=department_id,
            actor_id=actor_id,
            actor_username=actor_username,
        )
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "secret_service notify failed (hard_delete_department): %s", exc,
        )

    return {
        "department_id": department_id,
        "bots_deleted": len(bot_ids),
        "bot_tokens_revoked": bot_tokens_revoked,
        "oauth_clients_deleted": oauth_client_count,
    }
