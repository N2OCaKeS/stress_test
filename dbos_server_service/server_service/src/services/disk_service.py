"""Use cases для дисков — CRUD с dept-isolation через сервер-родитель.

Диски привязаны к серверу (FK server_id). Изоляция отделов работает
транзитом: чтобы видеть/менять диск, надо иметь доступ к его серверу.
Cross-dept сервер скрыт за 404 — симметрия с `services/server_account.py`.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import (
    ConflictError,
    NotFoundError,
)
from src.models import Server, ServerDisk
from src.repositories import server_disk as repo
from src.schemas.disk import DiskCreate, DiskUpdate
from src.schemas.identity import IdentityContext
from src.services import audit_service, permissions
from src.services.audit_helpers import emit_denied_on_authz_error
from src.services.server import load_visible_server
from src.utils.ids import server_disk_id as new_id

logger = logging.getLogger(__name__)


async def _load_disk_visible(
    db: AsyncSession, identity: IdentityContext, server_id: str, disk_id: str,
) -> tuple[ServerDisk, Server]:
    """SELECT диска + его сервера + dept-isolation. 404 на любую неоднозначность.

    Проверяет, что диск принадлежит именно тому серверу из path — иначе
    можно по чужому disk_id обойти изоляцию через свой server_id.
    """
    server = await load_visible_server(db, identity, server_id)
    disk = await repo.get_by_id(db, disk_id)
    if disk is None or disk.server_id != server.id:
        raise NotFoundError(error_code="DISK_NOT_FOUND", message="Disk not found")
    return disk, server


async def create_disk(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: DiskCreate,
) -> ServerDisk:
    """INSERT нового диска. UNIQUE(server_id, device_name) → 409 DISK_DUPLICATE.

    Partial-unique на is_system=True означает, что одновременно у сервера
    может быть только один системный диск — этот же путь возвращает 409.
    """
    with emit_denied_on_authz_error(
        "disk.create",
        target_type="disk",
        extra_details={"server_id": server_id},
    ):
        await permissions.require_action(db, identity, EntityType.DISK, Action.CREATE)

    try:
        server = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "disk.create",
            target_type="disk",
            status="denied", allowed=False,
            details={"reason": "server_not_found_or_cross_dept", "server_id": server_id},
        )
        raise

    data = payload.model_dump(mode="json")
    data["id"] = new_id()
    data["server_id"] = server.id
    try:
        obj = await repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на создании диска: %s", exc.orig)
        audit_service.emit(
            "disk.create",
            target_type="disk",
            status="failure", allowed=True,
            details={
                "reason": "duplicate",
                "server_id": server_id,
                "device_name": payload.device_name,
            },
        )
        raise ConflictError(
            error_code="DISK_DUPLICATE",
            message="Disk with this device_name (or system flag) already exists on this server",
            details={"hint": "уникальный ключ (server_id, device_name); системный диск — не более одного"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "disk.create",
        target_id=obj.id, target_type="disk",
        status="success", allowed=True,
        details={
            "server_id": obj.server_id,
            "device_name": obj.device_name,
            "department_id": server.department_id,
        },
    )
    return obj


async def get_disk(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    disk_id: str,
) -> ServerDisk:
    """SELECT диска по PK + dept-isolation через сервер."""
    with emit_denied_on_authz_error(
        "disk.view",
        target_id=disk_id,
        target_type="disk",
        extra_details={"server_id": server_id},
    ):
        await permissions.require_action(db, identity, EntityType.DISK, Action.VIEW)
    try:
        disk, _server = await _load_disk_visible(db, identity, server_id, disk_id)
    except NotFoundError:
        audit_service.emit(
            "disk.view",
            target_id=disk_id, target_type="disk",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept", "server_id": server_id},
        )
        raise
    return disk


async def list_disks(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    limit: int,
    offset: int,
) -> tuple[list[ServerDisk], int]:
    """List + count дисков одного сервера. Cross-dept сервер → 404."""
    with emit_denied_on_authz_error(
        "disk.list",
        target_type="disk",
        extra_details={"server_id": server_id},
    ):
        await permissions.require_action(db, identity, EntityType.DISK, Action.VIEW)
    try:
        await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "disk.list",
            target_type="disk",
            status="denied", allowed=False,
            details={"reason": "server_not_found_or_cross_dept", "server_id": server_id},
        )
        raise
    items = await repo.list_for_server(db, server_id, limit=limit, offset=offset)
    total = await repo.count_for_server(db, server_id)
    return items, total


async def update_disk(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    disk_id: str,
    payload: DiskUpdate,
) -> ServerDisk:
    """PATCH-обновление. Пустой диф → возврат без UPDATE."""
    with emit_denied_on_authz_error(
        "disk.update",
        target_id=disk_id,
        target_type="disk",
        extra_details={"server_id": server_id},
    ):
        await permissions.require_action(db, identity, EntityType.DISK, Action.UPDATE)
    try:
        obj, server = await _load_disk_visible(db, identity, server_id, disk_id)
    except NotFoundError:
        audit_service.emit(
            "disk.update",
            target_id=disk_id, target_type="disk",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept", "server_id": server_id},
        )
        raise

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        return obj
    try:
        await repo.update(db, obj, changes)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на обновлении диска %s: %s", disk_id, exc.orig)
        audit_service.emit(
            "disk.update",
            target_id=disk_id, target_type="disk",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(changes.keys()), "server_id": server_id},
        )
        raise ConflictError(
            error_code="DISK_DUPLICATE",
            message="Update collides with an existing disk on this server",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "disk.update",
        target_id=obj.id, target_type="disk",
        status="success", allowed=True,
        details={
            "fields": list(changes.keys()),
            "server_id": obj.server_id,
            "department_id": server.department_id,
        },
    )
    return obj


async def delete_disk(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    disk_id: str,
) -> None:
    """Hard-delete диска. На реальном железе диск, конечно, не трогает."""
    with emit_denied_on_authz_error(
        "disk.delete",
        target_id=disk_id,
        target_type="disk",
        extra_details={"server_id": server_id},
    ):
        await permissions.require_action(db, identity, EntityType.DISK, Action.DELETE)
    try:
        obj, server = await _load_disk_visible(db, identity, server_id, disk_id)
    except NotFoundError:
        audit_service.emit(
            "disk.delete",
            target_id=disk_id, target_type="disk",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept", "server_id": server_id},
        )
        raise
    device_name = obj.device_name
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "disk.delete",
        target_id=disk_id, target_type="disk",
        status="success", allowed=True,
        details={
            "server_id": server.id,
            "device_name": device_name,
            "department_id": server.department_id,
        },
    )
