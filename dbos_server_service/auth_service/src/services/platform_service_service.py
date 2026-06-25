"""Регистр платформенных сервисов: CRUD `PlatformService`."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.repositories.departments import DepartmentRepository
from src.repositories.groups import GroupRepository
from src.repositories.roles import RoleRepository
from src.repositories.service_role_definitions import ServiceRoleDefinitionRepository
from src.repositories.services import ServiceRepository
from src.schemas.services import ServiceResponse
from src.services import audit_service, secret_service_client
from src.services._cache_invalidation import invalidate_identity_cache as _invalidate_identity_cache

_logger = logging.getLogger(__name__)


async def create_service(
    db: AsyncSession,
    actor_id: str,
    service_name: str,
    description: str | None,
    request_id: str | None = None,
) -> ServiceResponse:
    """Зарегистрировать новый платформенный сервис."""
    repo = ServiceRepository(db)
    if await repo.exists(service_name):
        raise ConflictError(error_code="SERVICE_ALREADY_EXISTS", message=f"Service '{service_name}' already exists")

    svc = await repo.create(service_name, description)
    # Role definitions сеются per-(department, service) при выдаче отделу
    # access — здесь заранее ничего не создаём.
    await db.commit()
    audit_service.emit(
        "service.create", actor_id, target_id=service_name, target_type="service",
        request_id=request_id,
        details={
            "service_name": service_name,
            "description": description,
        },
    )
    return ServiceResponse(
        service_name=svc.service_name,
        description=svc.description,
        is_active=svc.is_active,
        created_at=svc.created_at,
    )


async def delete_service(
    db: AsyncSession,
    actor_id: str,
    service_name: str,
    request_id: str | None = None,
) -> None:
    """Снять сервис с регистрации. Каскадно ревокает все dept-access и роли."""
    svc_repo = ServiceRepository(db)
    dept_repo = DepartmentRepository(db)
    role_repo = RoleRepository(db)
    group_repo = GroupRepository(db)

    svc = await svc_repo.get(service_name)
    if svc is None or not svc.is_active:
        raise NotFoundError(error_code="SERVICE_NOT_FOUND", message=f"Service '{service_name}' not found")

    # Собираем список dept'ов, у которых на момент снятия был активный
    # access. Нужен и для (а) симметричного снятия group-level binding'ов,
    # и для lifecycle-callback'ов в secret_service, если сносим secret_service.
    affected_dept_ids: list[str] = []
    for dept in await dept_repo.list_all():
        access = await dept_repo.get_access(dept.id, service_name)
        if access and access.is_active:
            affected_dept_ids.append(dept.id)
            await dept_repo.revoke_access(access, revoked_by=actor_id)
            # Per-dept трейл для SIEM. Глобальный `service.delete` ниже несёт
            # только агрегатные counts — без этого события снятие доступа у
            # конкретного отдела при удалении сервиса не отличить от ничего.
            # Зеркалит `revoke_service_access`-эмит того же action'а.
            audit_service.emit(
                "department.service_revoke", actor_id,
                target_id=dept.id, target_type="department",
                request_id=request_id,
                details={
                    "service_name": service_name,
                    "via": "service.delete",
                },
            )

    # Симметрия с `revoke_service_access`: GroupServiceRole/GroupServiceAccess
    # за пределами `dept_repo.revoke_access` не каскадятся. Без явного
    # `deactivate_all_dept_service_*` group-канал продолжает выдавать роль и
    # service-access через `_merge_permissions`, и `list_active_services_by_groups`
    # возвращает снесённый сервис — `allowed_services` сохраняет stale запись
    # до конца identity-cache TTL.
    affected_member_group_ids: set[str] = set()
    for dept_id in affected_dept_ids:
        gsr_groups = await group_repo.deactivate_all_dept_service_roles(
            dept_id, service_name,
        )
        gsa_groups = await group_repo.deactivate_all_dept_service_access(
            dept_id, service_name,
        )
        affected_member_group_ids.update(gsr_groups)
        affected_member_group_ids.update(gsa_groups)

    affected_user_ids = await role_repo.deactivate_all_for_service(service_name)
    role_def_repo = ServiceRoleDefinitionRepository(db)
    await role_def_repo.deactivate_all_for_service(service_name)
    from src.repositories.bot_roles import BotRoleRepository
    bot_role_repo = BotRoleRepository(db)
    affected_bot_ids = await bot_role_repo.deactivate_all_for_service(service_name)
    await svc_repo.deactivate(svc)

    # Догружаем group-member'ов в invalidation set: если у группы был
    # GroupServiceRole/GroupServiceAccess, её участники тоже должны увидеть
    # ребилд permissions, не только прямые носители роли.
    member_user_ids: set[str] = set(affected_user_ids)
    member_bot_ids: set[str] = set(affected_bot_ids)
    if affected_member_group_ids:
        group_ids_list = list(affected_member_group_ids)
        member_user_ids.update(await group_repo.list_member_user_ids(group_ids_list))
        member_bot_ids.update(await group_repo.list_member_bot_ids(group_ids_list))

    await db.commit()
    # Сервис снесён глобально — у юзеров и ботов с прямой ролью на нём + у
    # group-member'ов сбрасываем identity-cache, иначе до истечения TTL они
    # продолжат видеть роль/service в introspect.
    for uid in member_user_ids:
        _invalidate_identity_cache(uid)
    for bid in member_bot_ids:
        _invalidate_identity_cache(bid)

    # Lifecycle-callback в secret_service per затронутому dept'у. Делаем
    # только когда сносим именно `secret_service`: secret_service'у нужно
    # каскадно revoke'нуть DeptGrant'ы / RoleACL для каждого dep'а, у
    # которого был активный access. Best-effort: ошибки внутри клиента
    # глотаются и уходят в audit `notify_failed`.
    if service_name == "secret_service" and affected_dept_ids:
        actor_username: str | None = None
        try:
            from src.repositories.users import UserRepository
            actor = await UserRepository(db).get_by_id(actor_id)
            if actor is not None:
                actor_username = actor.username
        except Exception:
            pass
        for dept_id in affected_dept_ids:
            try:
                await secret_service_client.notify_dept_service_access_revoked(
                    dept_id=dept_id,
                    service=service_name,
                    actor_id=actor_id,
                    actor_username=actor_username,
                )
            except Exception as exc:
                _logger.warning(
                    "secret_service notify failed (delete_service dept=%s): %s",
                    dept_id, exc,
                )

    audit_service.emit(
        "service.delete", actor_id, target_id=service_name, target_type="service",
        request_id=request_id,
        details={
            "service_name": service_name,
            "cascade_revoked_department_access": True,
            "cascade_deactivated_roles": True,
            "affected_user_count": len(member_user_ids),
            "affected_bot_count": len(member_bot_ids),
            "affected_department_count": len(affected_dept_ids),
        },
    )


async def list_services(
    db: AsyncSession,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> list[ServiceResponse]:
    """Все активные сервисы."""
    repo = ServiceRepository(db)
    result = [
        ServiceResponse(
            service_name=s.service_name,
            description=s.description,
            is_active=s.is_active,
            created_at=s.created_at,
        )
        for s in await repo.list_active()
    ]
    audit_service.emit(
        "service.list", actor_id, status="success", allowed=True,
        request_id=request_id,
        details={"count": len(result)},
    )
    return result
