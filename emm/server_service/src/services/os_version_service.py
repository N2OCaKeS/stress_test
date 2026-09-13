"""Use cases для OS-версий — глобальный каталог.

Read (list + get по id + get по имени) не требует прав и не аудитится —
каталог ОС открыт на чтение любому аутентифицированному актору (сам факт
аутентификации проверяется на endpoint-уровне зависимостью
`AuthenticatedIdentity`). Запись (create/update/delete) остаётся под
матрицей прав. Удаление версии, на которую ссылается хоть один сервер
(`servers.os_version_id`), отбивается IntegrityError от FK
ondelete=RESTRICT → 409 OS_VERSION_IN_USE.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from src.models import OsVersion
from src.repositories import os_version as repo
from src.schemas.identity import IdentityContext
from src.schemas.os_version import (
    OsVersionCreate,
    OsVersionUpdate,
    encode_bootstrap_password_b64,
)
from src.services import audit_service, os_version_repo_resolver, permissions
from src.services import os_version_bootstrap_password as bootstrap_password_svc
from src.utils.ids import os_version_id as new_id

logger = logging.getLogger(__name__)


async def create_os_version(
    db: AsyncSession,
    identity: IdentityContext,
    payload: OsVersionCreate,
) -> OsVersion:
    """INSERT новой OS-версии. UNIQUE(name) → 409 OS_VERSION_DUPLICATE."""
    try:
        await permissions.require_action(db, identity, EntityType.OS_VERSION, Action.CREATE)
    except AuthorizationError:
        audit_service.emit(
            "os_version.create",
            target_type="os_version",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    data = payload.model_dump(mode="json")
    # build_version — транзиентный вход резолвера, в модели колонки под него нет.
    build_version = data.pop("build_version", None)
    if build_version and not data.get("repositories"):
        # Неизвестная версия / недоступный индекс поднимутся наружу (404/503) —
        # осознанно не создаём запись с пустыми репозиториями молча.
        data["repositories"] = await os_version_repo_resolver.resolve_repository_urls(
            build_version
        )
    data["id"] = new_id()
    try:
        obj = await repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на создании os_version: %s", type(exc.orig).__name__)
        audit_service.emit(
            "os_version.create",
            target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "duplicate", "name": payload.name},
        )
        raise ConflictError(
            error_code="OS_VERSION_DUPLICATE",
            message="OS version with this name already exists",
            details={"hint": "уникальное поле — name"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "os_version.create",
        target_id=obj.id, target_type="os_version",
        status="success", allowed=True,
        details={"name": obj.name},
    )
    return obj


async def get_os_version(
    db: AsyncSession,
    os_version_id: str,
) -> OsVersion:
    """SELECT OS-версии по PK. Read без проверки прав и без аудита."""
    obj = await repo.get_by_id(db, os_version_id)
    if obj is None:
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="OS version not found",
        )
    return obj


async def get_os_version_by_name(
    db: AsyncSession,
    name: str,
) -> OsVersion:
    """SELECT OS-версии по UNIQUE name. Read без проверки прав и без аудита."""
    obj = await repo.get_by_name(db, name)
    if obj is None:
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="OS version not found",
        )
    return obj


async def list_os_versions_cursor(
    db: AsyncSession,
    *,
    limit: int,
    after: str | None,
) -> tuple[list[OsVersion], str | None, bool]:
    """Keyset-страница каталога OS-версий. Возвращает `(items, next_cursor, has_more)`.

    Каталог глобальный, без dept-фильтра. `limit + 1` row-fetch для
    has_more-детекта без отдельного COUNT'а.
    """
    from src.utils.cursor import (
        decode_cursor,
        encode_cursor,
        normalize_limit,
        parse_cursor_datetime,
    )

    page_size = normalize_limit(limit)
    after_discovered_at = None
    after_id = None
    if after:
        cur = decode_cursor(after)
        after_discovered_at = parse_cursor_datetime(cur.sort_value)
        after_id = cur.row_id
    rows = await repo.list_all_after(
        db,
        limit=page_size + 1,
        after_discovered_at=after_discovered_at,
        after_id=after_id,
    )
    has_more = len(rows) > page_size
    items = rows[:page_size]
    next_cursor = (
        encode_cursor(items[-1].discovered_at, items[-1].id) if has_more and items else None
    )
    return items, next_cursor, has_more


async def list_os_versions(
    db: AsyncSession,
    limit: int,
    offset: int,
) -> tuple[list[OsVersion], int]:
    """List + count полного каталога. Read без проверки прав и без аудита."""
    items = await repo.list_all(db, limit=limit, offset=offset)
    total = await repo.count_all(db)
    return items, total


async def update_os_version(
    db: AsyncSession,
    identity: IdentityContext,
    os_version_id: str,
    payload: OsVersionUpdate,
) -> OsVersion:
    """PATCH-обновление. Пустой диф → возврат без UPDATE."""
    try:
        await permissions.require_action(db, identity, EntityType.OS_VERSION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "os_version.update",
            target_id=os_version_id, target_type="os_version",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, os_version_id)
    if obj is None:
        # Permission уже прошёл — отказ из-за отсутствия row, не из-за прав.
        audit_service.emit(
            "os_version.update",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="OS version not found",
        )

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        return obj
    try:
        await repo.update(db, obj, changes)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на обновлении os_version %s: %s",
            os_version_id, type(exc.orig).__name__,
        )
        audit_service.emit(
            "os_version.update",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(changes.keys())},
        )
        raise ConflictError(
            error_code="OS_VERSION_DUPLICATE",
            message="Update collides with an existing OS version (name UNIQUE)",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "os_version.update",
        target_id=obj.id, target_type="os_version",
        status="success", allowed=True,
        details={
            "fields": list(changes.keys()),
            "changed_fields": list(changes.keys()),
            "name": obj.name,
        },
    )
    return obj


async def resolve_repositories(
    db: AsyncSession,
    identity: IdentityContext,
    os_version_id: str,
    build_version: str,
) -> OsVersion:
    """Перестроить `repositories` версии из индекса релизов по build-версии.

    То же право, что и штатный `update` (пишем в repositories). Неизвестная
    версия → 404 OS_RELEASE_NOT_FOUND, недоступный индекс → 503.
    """
    try:
        await permissions.require_action(db, identity, EntityType.OS_VERSION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "os_version.update",
            target_id=os_version_id, target_type="os_version",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, os_version_id)
    if obj is None:
        audit_service.emit(
            "os_version.update",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="OS version not found",
        )

    resolved = await os_version_repo_resolver.resolve_repository_urls(build_version)
    await repo.update(db, obj, {"repositories": resolved})
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "os_version.update",
        target_id=obj.id, target_type="os_version",
        status="success", allowed=True,
        details={
            "fields": ["repositories"],
            "changed_fields": ["repositories"],
            "name": obj.name,
            "resolved_from": build_version,
            "repositories_count": len(resolved),
        },
    )
    return obj


async def delete_os_version(
    db: AsyncSession,
    identity: IdentityContext,
    os_version_id: str,
) -> None:
    """Hard-delete. FK ondelete=RESTRICT от servers.os_version_id → 409 OS_VERSION_IN_USE."""
    try:
        await permissions.require_action(db, identity, EntityType.OS_VERSION, Action.DELETE)
    except AuthorizationError:
        audit_service.emit(
            "os_version.delete",
            target_id=os_version_id, target_type="os_version",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, os_version_id)
    if obj is None:
        # Permission уже прошёл — отказ из-за отсутствия row, не из-за прав.
        audit_service.emit(
            "os_version.delete",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="OS version not found",
        )
    name = obj.name
    # Pre-check: SQLAlchemy без passive_deletes обнуляет дочерние FK сам,
    # поэтому DB-уровневый RESTRICT не срабатывает. Явный count даёт
    # детерминированный 409 ещё до DELETE.
    in_use = await repo.count_referencing_servers(db, os_version_id)
    if in_use > 0:
        audit_service.emit(
            "os_version.delete",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "in_use", "referencing_servers": in_use},
        )
        raise ConflictError(
            error_code="OS_VERSION_IN_USE",
            message="Cannot delete OS version: at least one server still references it",
            details={"hint": "сначала переключите servers.os_version_id или удалите соответствующие сервера"},
        )
    try:
        await repo.delete(db, obj)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на удалении os_version %s: %s",
            os_version_id, type(exc.orig).__name__,
        )
        audit_service.emit(
            "os_version.delete",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "in_use"},
        )
        raise ConflictError(
            error_code="OS_VERSION_IN_USE",
            message="Cannot delete OS version: at least one server still references it",
        ) from exc
    audit_service.emit(
        "os_version.delete",
        target_id=os_version_id, target_type="os_version",
        status="success", allowed=True,
        details={"name": name},
    )


async def get_os_version_bootstrap_password(
    db: AsyncSession,
    identity: IdentityContext,
    os_version_id: str,
    reveal: bool = False,
) -> dict:
    """GET-статус bootstrap-пароля версии — логин + факт "задан/не задан".

    В отличие от обычного чтения каталога, это НЕ публичный эндпоинт: пароль
    хоть и не отдаётся в открытом виде, сам факт его наличия/логин — это
    management-конфигурация, доступная только тем, кто может версию менять.
    """
    try:
        await permissions.require_action(db, identity, EntityType.OS_VERSION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "os_version.bootstrap_password_updated",
            target_id=os_version_id, target_type="os_version",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "op": "read"},
        )
        raise
    obj = await repo.get_by_id(db, os_version_id)
    if obj is None:
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="OS version not found",
        )
    status = await bootstrap_password_svc.get_bootstrap_password_status(db, os_version_id)
    if status is None:
        return {"ssh_username": None, "has_password": False}
    if reveal:
        try:
            await permissions.require_action(
                db, identity, EntityType.OS_VERSION, Action.VIEW_PASSWORD
            )
        except AuthorizationError:
            audit_service.emit(
                "os_version.bootstrap_password_revealed",
                target_id=os_version_id, target_type="os_version",
                status="denied", allowed=False,
                details={"reason": "permission_denied"},
            )
            raise
        creds = await bootstrap_password_svc.get_bootstrap_password_for_os_version(
            db, os_version_id
        )
        if creds is not None:
            status["password_b64"] = encode_bootstrap_password_b64(creds["password"])
        audit_service.emit(
            "os_version.bootstrap_password_revealed",
            target_id=os_version_id, target_type="os_version",
            status="success", allowed=True,
            details={"ssh_username": status.get("ssh_username")},
        )
    return status


async def update_os_version_bootstrap_password(
    db: AsyncSession,
    identity: IdentityContext,
    os_version_id: str,
    ssh_username: str,
    password: str,
) -> dict:
    """Upsert bootstrap-пароля версии. Право — то же `update`, что у остального CRUD.

    Нужен для авто-`server.prepare` после restore снимка ACS: диск
    переписывается целиком, старые управляющие креды не переживают reimage,
    единственный вход на свежий образ — этот заранее заведённый пароль.
    Аудит `os_version.bootstrap_password_updated` (WARNING) не несёт сам
    пароль, только факт обновления и логин.
    """
    try:
        await permissions.require_action(db, identity, EntityType.OS_VERSION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "os_version.bootstrap_password_updated",
            target_id=os_version_id, target_type="os_version",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "op": "write"},
        )
        raise
    obj = await repo.get_by_id(db, os_version_id)
    if obj is None:
        audit_service.emit(
            "os_version.bootstrap_password_updated",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="OS version not found",
        )
    await bootstrap_password_svc.upsert_bootstrap_password(
        db, os_version_id, ssh_username, password, updated_by=identity.user_id,
    )
    audit_service.emit(
        "os_version.bootstrap_password_updated",
        target_id=os_version_id, target_type="os_version",
        status="success", allowed=True,
        details={"ssh_username": ssh_username},
    )
    return await bootstrap_password_svc.get_bootstrap_password_status(db, os_version_id)


async def resolve_kernels(db: AsyncSession, os_version_id: str) -> OsVersion:
    """Обновить производный каталог ядер из настроенных репозиториев версии."""
    from src.services import os_kernel_resolver
    obj = await get_os_version(db, os_version_id)
    initial_repositories = list(obj.repositories or [])
    repositories = list(initial_repositories)
    if not repositories:
        repositories = await os_version_repo_resolver.resolve_repository_urls(obj.name)
    kernels = await os_kernel_resolver.resolve_kernels(repositories)
    await db.refresh(obj)
    if list(obj.repositories or []) != initial_repositories:
        raise ConflictError(error_code="OS_REPOSITORIES_CHANGED", message="Репозитории ОС изменились во время поиска ядер. Повторите поиск.")
    await repo.update(db, obj, {"kernels": kernels, "repositories": repositories})
    await db.commit()
    await db.refresh(obj)
    audit_service.emit("os_version.update", target_id=obj.id, target_type="os_version",
        status="success", allowed=True, details={"changed_fields": ["kernels"], "source": "package_indexes", "kernels_count": len(kernels)})
    return obj
