"""Use cases для серверов — role-проверки, изоляция отделов, persistence-оркестровка."""

import logging
from datetime import datetime, timezone

from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, BusyState, EntityType, ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.models import IpmiController, Server, ServerDisk
from src.repositories import ipmi_controller as ipmi_repo
from src.repositories import server as repo
from src.repositories import server_disk as disk_repo
from src.schemas.disk import DiskSpec
from src.schemas.identity import IdentityContext
from src.schemas.server import (
    ServerAcquireRequest,
    ServerCreate,
    ServerIpmiCreate,
    ServerOsVersionUpdate,
    ServerUpdate,
)
from src.services import audit_service, permissions, secrets_service
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import ipmi_controller_id, server_disk_id, server_id as new_id

logger = logging.getLogger(__name__)

_DUPLICATE_HINT = "hostname, ip_address или serial_number уже используется"


def _ensure_visible(identity: IdentityContext, server: Server) -> None:
    """Скрыть cross-department сервер за 404, чтобы не выдавать его существование.

    Чужой dept → 404, а не 403 — иначе по разнице ответов можно перечислить
    чужие server_id'ы. Platform-роли (`account_admin`/`loging_admin`) сюда
    физически не доходят: `platform_admin_guard` middleware режет их 403
    до endpoint-слоя, у них нет department_id и сервисных ролей.
    """
    if identity.department_id != server.department_id:
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")


async def load_visible_server(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
) -> Server:
    """Загрузить сервер с dept-visibility, БЕЗ require_action(SERVER, VIEW).

    Используется в endpoint'ах, где основное разрешение даёт другой action
    на дочерней сущности (например, `ipmi_controller.view_credentials`
    у worker_bot — у него нет `server.view`, но он легитимно читает
    credentials привязанного к серверу controller'а). Department-isolation
    сохраняется через `_ensure_visible`.
    """
    obj = await repo.get_by_id(db, server_id)
    if obj is None:
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    _ensure_visible(identity, obj)
    return obj


async def load_visible_servers(
    db: AsyncSession,
    identity: IdentityContext,
    server_ids: list[str],
) -> dict[str, Server]:
    """Batch-загрузка серверов через `WHERE id IN (...)` + dept-visibility.

    Возвращает словарь {server_id: Server} только по видимым (свой department).
    Cross-dept и несуществующие просто отсутствуют — caller сам решает, как
    оформить ошибку (для массовой ротации мы не валим весь батч, для точечной
    задачи отсутствие = 404).
    """
    if not server_ids:
        return {}
    found = await repo.get_many_by_ids(db, server_ids)
    return {
        sid: srv for sid, srv in found.items()
        if identity.department_id == srv.department_id
    }


async def load_storage(db: AsyncSession, server_id: str) -> list[ServerDisk]:
    """Диски сервера для раздела `storage` в ответе. Порядок — по слоту."""
    return await disk_repo.list_all_for_server(db, server_id)


async def _sync_storage(
    db: AsyncSession, server_id: str, disks: list[DiskSpec],
) -> None:
    """Привести строки `server_disks` к переданному набору (full replace).

    Слот спецификации ложится в `device_name`. Существующие диски, которых нет
    в новом наборе, удаляются; совпадающие по слоту — обновляются; новые —
    вставляются. commit делает caller (диски пишутся в одной транзакции с
    сервером). UNIQUE(server_id, device_name) и partial-unique на is_system
    держат инварианты на уровне БД.
    """
    existing = {d.device_name: d for d in await disk_repo.list_all_for_server(db, server_id)}
    wanted_slots = {d.slot for d in disks}
    for slot, row in existing.items():
        if slot not in wanted_slots:
            await disk_repo.delete(db, row)
    for spec in disks:
        row = existing.get(spec.slot)
        changes = {
            "size_gb": spec.size_gb,
            "model": spec.model,
            "is_system": spec.is_system,
        }
        if row is not None:
            await disk_repo.update(db, row, changes)
        else:
            await disk_repo.create(db, {
                "id": server_disk_id(),
                "server_id": server_id,
                "device_name": spec.slot,
                **changes,
            })


async def _insert_ipmi(
    db: AsyncSession, server_id: str, spec: ServerIpmiCreate,
) -> IpmiController:
    """Записать BMC-контроллер для только что созданного сервера.

    Inline, а не вызов `ipmi_controller.create_controller`: тот делает свой
    permission-check, visibility-load и собственный commit, поэтому в общей
    транзакции с сервером его не переиспользовать. Шифрование пароля и AAD —
    тот же паттерн, что в обычном create. commit делает caller.
    """
    controller_id = ipmi_controller_id()
    encrypted = secrets_service.encrypt(
        spec.password,
        aad=secrets_service.aad_for_ipmi_credential(controller_id),
    )
    return await ipmi_repo.create(db, {
        "id": controller_id,
        "server_id": server_id,
        "kind": spec.kind.value,
        "endpoint_url": spec.endpoint_url,
        "username": spec.username,
        "password_encrypted": encrypted,
    })


async def list_servers(
    db: AsyncSession,
    identity: IdentityContext,
    limit: int,
    offset: int,
) -> tuple[list[Server], int]:
    """List + count в одном вызове.

    Department-фильтр строится тут (а не в endpoint'е), чтобы было сложнее
    случайно проскочить изоляцию: у caller'а без department_id — пустой
    результат сразу. Platform-роли отрезаны guard'ом ещё в middleware.
    """
    await permissions.require_action(db, identity, EntityType.SERVER, Action.VIEW)
    if identity.department_id is None:
        return [], 0
    dept_filter = [identity.department_id]
    items = await repo.list_in_departments(db, dept_filter, limit=limit, offset=offset)
    total = await repo.count_in_departments(db, dept_filter)
    return items, total


async def get_server(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
) -> Server:
    """SELECT сервера + visibility-check.

    Денid-аудит ДО re-raise — иначе попытка чтения чужого сервера теряется
    в middleware'е как generic `http.client_error` без action-key, и SIEM
    не отличит её от обычной 404. Try/except охватывает и `require_action`
    (PERMISSION_DENIED по матрице) и `_ensure_visible` (cross-dept 404) —
    оба исхода — отказ доступа к ресурсу, оба должны попадать в audit
    с тем же action-key `server.view`.
    """
    # Visibility-check: 404 для non-existent / cross-dept. Эмитим explicit `denied`
    # audit ДО re-raise — симметрия с `create_server` / `update_server` / `delete_server`
    # и `_dispatch_power` в endpoints/ipmi.py.
    try:
        await permissions.require_action(db, identity, EntityType.SERVER, Action.VIEW)
        obj = await repo.get_by_id(db, server_id)
        if obj is None:
            raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
        _ensure_visible(identity, obj)
    except (NotFoundError, AuthorizationError) as exc:
        reason = (
            "not_found_or_cross_dept"
            if isinstance(exc, NotFoundError)
            else "permission_denied"
        )
        audit_service.emit(
            "server.view",
            target_id=server_id,
            target_type="server",
            status="denied",
            allowed=False,
            details={"reason": reason},
        )
        raise
    audit_service.emit(
        "server.view",
        target_id=obj.id, target_type="server",
        status="success", allowed=True,
        details={"department_id": obj.department_id},
    )
    return obj


async def create_server(
    db: AsyncSession,
    identity: IdentityContext,
    payload: ServerCreate,
) -> Server:
    """INSERT нового сервера.

    Caller обязан указывать свой department_id, иначе DEPARTMENT_ISOLATION.
    UNIQUE-конфликт (hostname/ip/serial_number) → SERVER_DUPLICATE 409.

    Если в теле есть блок `ipmi`, контроллер пишется в той же транзакции, что
    и сервер: либо создаётся всё, либо ничего. Битый IPMI (например, дубль
    server_id) откатывает и сервер. Право на сервер (`server.create`) покрывает
    и создание вложенного контроллера — отдельный `ipmi_controller.create`
    grant тут не требуется, операция идёт под одним server-create.
    """
    with emit_denied_on_authz_error(
        "server.create",
        target_type="server",
        extra_details={"department_id": payload.department_id},
    ):
        await permissions.require_action(db, identity, EntityType.SERVER, Action.CREATE)
    if payload.department_id != identity.department_id:
        audit_service.emit(
            "server.create",
            target_type="server",
            status="denied",
            allowed=False,
            details={"reason": "department_isolation", "department_id": payload.department_id},
        )
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message="Cannot create a server in a different department",
        )
    data = payload.model_dump(mode="json", exclude={"storage", "ipmi"})
    data["id"] = new_id()
    data["created_by"] = identity.user_id
    ipmi_obj: IpmiController | None = None
    try:
        obj = await repo.create(db, data)
        if payload.storage:
            await _sync_storage(db, obj.id, payload.storage)
        if payload.ipmi is not None:
            ipmi_obj = await _insert_ipmi(db, obj.id, payload.ipmi)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на создании сервера: %s", type(exc.orig).__name__)
        audit_service.emit(
            "server.create",
            target_id=data["id"],
            target_type="server",
            status="failure",
            allowed=True,
            details={"reason": "duplicate", "department_id": payload.department_id},
        )
        raise ConflictError(
            error_code="SERVER_DUPLICATE",
            message="Server with this hostname, IP, serial_number or disk slot already exists",
            details={"hint": _DUPLICATE_HINT},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server.create",
        target_id=obj.id,
        target_type="server",
        status="success",
        allowed=True,
        details={
            "hostname": obj.hostname,
            "department_id": obj.department_id,
        },
    )
    if ipmi_obj is not None:
        await db.refresh(ipmi_obj)
        audit_service.emit(
            "ipmi_controller.create",
            target_id=ipmi_obj.id,
            target_type="ipmi_controller",
            status="success",
            allowed=True,
            details={
                "server_id": ipmi_obj.server_id,
                "kind": ipmi_obj.kind,
                "endpoint_url": ipmi_obj.endpoint_url,
                "department_id": obj.department_id,
            },
        )
    return obj


async def update_server(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: ServerUpdate,
) -> Server:
    """PATCH-update сервера. Пустой диф → возврат без UPDATE.

    Denid-аудит ДО re-raise + явный handling IntegrityError для
    SERVER_DUPLICATE (UNIQUE-конфликт по unique-полям).
    """
    with emit_denied_on_authz_error(
        "server.update",
        target_id=server_id,
        target_type="server",
    ):
        await permissions.require_action(db, identity, EntityType.SERVER, Action.UPDATE)
    # Visibility-check: 404 для non-existent / cross-dept. Эмитим explicit `denied`
    # audit ДО re-raise — иначе попытка теряется в middleware'е как `http.client_error`
    # без action-key (симметрия с create_server и _dispatch_power).
    try:
        obj = await repo.get_by_id(db, server_id)
        if obj is None:
            raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
        _ensure_visible(identity, obj)
    except (NotFoundError, AuthorizationError) as exc:
        audit_service.emit(
            "server.update",
            target_id=server_id,
            target_type="server",
            status="denied",
            allowed=False,
            details={
                "reason": (
                    "not_found_or_cross_dept"
                    if isinstance(exc, NotFoundError)
                    else "cross_department"
                ),
            },
        )
        raise
    changes = payload.model_dump(exclude_unset=True, mode="json")
    # storage синхронизируется отдельно (full-replace дочерних строк), а не
    # пишется как колонка в `servers`. `None`/не прислано → диски не трогаем.
    sync_storage = "storage" in changes
    changes.pop("storage", None)
    if not changes and not sync_storage:
        return obj
    audit_fields = list(changes.keys())
    if sync_storage:
        audit_fields.append("storage")
    try:
        if changes:
            await repo.update(db, obj, changes)
        if sync_storage:
            await _sync_storage(db, obj.id, payload.storage or [])
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на обновлении сервера %s: %s", server_id, type(exc.orig).__name__)
        audit_service.emit(
            "server.update",
            target_id=server_id,
            target_type="server",
            status="failure",
            allowed=True,
            details={"reason": "duplicate", "fields": audit_fields},
        )
        raise ConflictError(
            error_code="SERVER_DUPLICATE",
            message="Update collides with an existing server (hostname/IP/serial_number/disk slot)",
            details={"hint": _DUPLICATE_HINT},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server.update",
        target_id=obj.id,
        target_type="server",
        status="success",
        allowed=True,
        details={"fields": audit_fields, "department_id": obj.department_id},
    )
    return obj


async def delete_server(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
) -> None:
    """Hard-delete сервера + каскад дочерних записей (accounts/ipmi/disks/packages).

    Audit `server.delete` с CRITICAL severity — это deliberately destructive.
    """
    with emit_denied_on_authz_error(
        "server.delete",
        target_id=server_id,
        target_type="server",
    ):
        await permissions.require_action(db, identity, EntityType.SERVER, Action.DELETE)
    # Visibility-check: 404 для non-existent / cross-dept. Эмитим explicit `denied`
    # audit ДО re-raise — иначе попытка теряется в middleware'е как `http.client_error`
    # без action-key (симметрия с create_server и _dispatch_power).
    try:
        obj = await repo.get_by_id(db, server_id)
        if obj is None:
            raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
        _ensure_visible(identity, obj)
    except (NotFoundError, AuthorizationError) as exc:
        audit_service.emit(
            "server.delete",
            target_id=server_id,
            target_type="server",
            status="denied",
            allowed=False,
            details={
                "reason": (
                    "not_found_or_cross_dept"
                    if isinstance(exc, NotFoundError)
                    else "cross_department"
                ),
            },
        )
        raise
    department_id = obj.department_id
    hostname = obj.hostname
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "server.delete",
        target_id=server_id,
        target_type="server",
        status="success",
        allowed=True,
        details={"hostname": hostname, "department_id": department_id},
    )


async def acquire_server(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: ServerAcquireRequest,
) -> Server:
    """Захват сервера через atomic UPDATE busy_state.

    Атомарная гонка решается на уровне БД: SQL `UPDATE ... WHERE busy_state='free'`,
    проверяем rowcount. Без этого два теста могут одновременно прочитать
    `busy_state='free'`, оба пройти Python-условие и оба выставить busy_user_id —
    второй тест переписал бы первого молча.

    Decommissioned-сервер захватывать нельзя — это бизнес-правило симметрично
    с power-операциями.

    Порядок проверок — паттерн `_dispatch_power`: visibility ДО role-check,
    иначе guest/cross-dept caller узнаёт о существовании чужого сервера
    через разницу 403/404.
    """
    try:
        obj = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "server.acquire",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    with emit_denied_on_authz_error(
        "server.acquire",
        target_id=server_id,
        target_type="server",
        extra_details={"department_id": obj.department_id},
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.BUSY_ACQUIRE,
        )
    if obj.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            "server.acquire",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "decommissioned", "department_id": obj.department_id},
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot be acquired",
        )
    busy_note_parts: list[str] = []
    if payload.purpose:
        busy_note_parts.append(payload.purpose)
    if payload.lease_until is not None:
        busy_note_parts.append(f"lease_until={payload.lease_until.isoformat()}")
    busy_note = " | ".join(busy_note_parts) if busy_note_parts else None
    now = datetime.now(timezone.utc)
    # Атомарный CAS: UPDATE ... WHERE busy_state='free'. rowcount==0 → 409,
    # потому что либо сервер уже busy/testing, либо параллельный acquire успел
    # первым. Symmetric с CAS-паттерном в auth_service (refresh rotation).
    result = await db.execute(
        sa_update(Server)
        .where(Server.id == server_id, Server.busy_state == BusyState.FREE)
        .values(
            busy_state=BusyState.BUSY,
            busy_user_id=identity.user_id,
            busy_since=now,
            busy_note=busy_note,
        )
    )
    if result.rowcount == 0:
        audit_service.emit(
            "server.acquire",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "already_busy",
                "department_id": obj.department_id,
                "current_state": obj.busy_state,
            },
        )
        raise ConflictError(
            error_code="SERVER_ALREADY_BUSY",
            message="Server is already busy or being tested",
            details={"current_state": obj.busy_state},
        )
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "server.acquire",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "department_id": obj.department_id,
            "purpose": payload.purpose,
            "lease_until": (
                payload.lease_until.isoformat() if payload.lease_until else None
            ),
        },
    )
    return obj


async def release_server(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
) -> Server:
    """Снятие busy-флага. Освободить может сам захвативший либо роль с busy_release.

    Если сервер уже free — 409 SERVER_NOT_BUSY (идемпотентный release клиенту
    осмыслен не очень — он сигналит о рассинхронизации состояния).

    Порядок проверок — visibility ДО role-check (паттерн `_dispatch_power`).
    """
    try:
        obj = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "server.release",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    with emit_denied_on_authz_error(
        "server.release",
        target_id=server_id,
        target_type="server",
        extra_details={"department_id": obj.department_id},
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.BUSY_RELEASE,
        )
    if obj.busy_state == BusyState.FREE:
        audit_service.emit(
            "server.release",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_busy", "department_id": obj.department_id},
        )
        raise ConflictError(
            error_code="SERVER_NOT_BUSY",
            message="Server is already free",
        )
    previous_user_id = obj.busy_user_id
    # Атомарный release из любого non-free состояния (busy / testing). Свежий
    # SELECT мог увидеть state='busy', но к моменту UPDATE параллельный
    # release уже мог его снять — rowcount==0 в этом случае значит «кто-то
    # успел раньше», тоже ошибка (409 SERVER_NOT_BUSY).
    result = await db.execute(
        sa_update(Server)
        .where(Server.id == server_id, Server.busy_state != BusyState.FREE)
        .values(
            busy_state=BusyState.FREE,
            busy_user_id=None,
            busy_since=None,
            busy_note=None,
        )
    )
    if result.rowcount == 0:
        audit_service.emit(
            "server.release",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "race_already_free", "department_id": obj.department_id},
        )
        raise ConflictError(
            error_code="SERVER_NOT_BUSY",
            message="Server is already free",
        )
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "server.release",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "department_id": obj.department_id,
            "previous_user_id": previous_user_id,
        },
    )
    return obj


async def update_os_version(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: ServerOsVersionUpdate,
) -> Server:
    """Ручной апдейт `os_version_id` сервера (не через inventory sync).

    Полезно для admin/operator'а, когда железо физически переустановили без
    участия worker'а или надо быстро сменить версию вручную. FK os_version_id →
    os_versions(id) с ondelete=RESTRICT, поэтому невалидный id → 422
    INVALID_OS_VERSION (через IntegrityError).

    Порядок проверок — visibility ДО role-check (паттерн `_dispatch_power`).
    """
    try:
        obj = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "server.update_os_version",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    with emit_denied_on_authz_error(
        "server.update_os_version",
        target_id=server_id,
        target_type="server",
        extra_details={"department_id": obj.department_id},
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.OS_SYNC,
        )
    previous = obj.os_version_id
    try:
        await repo.update(db, obj, {
            "os_version_id": payload.os_version_id,
            "os_last_synced_at": datetime.now(timezone.utc),
        })
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на обновлении os_version_id для %s: %s", server_id, type(exc.orig).__name__)
        audit_service.emit(
            "server.update_os_version",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "invalid_os_version", "os_version_id": payload.os_version_id},
        )
        raise DomainValidationError(
            error_code="INVALID_OS_VERSION",
            message="os_version_id does not reference an existing OS version",
            details={"os_version_id": payload.os_version_id},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server.update_os_version",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "department_id": obj.department_id,
            "previous_os_version_id": previous,
            "new_os_version_id": payload.os_version_id,
        },
    )
    return obj
