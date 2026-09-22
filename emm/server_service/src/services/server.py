"""Use cases для серверов — role-проверки, изоляция отделов, persistence-оркестровка."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import (
    Action,
    BusyActorType,
    BusyState,
    EntityType,
    ServerStatus,
)
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.models import IpmiController, Server, ServerDisk
from src.repositories import ipmi_controller as ipmi_repo
from src.repositories import resource_role_permission as resource_perm_repo
from src.repositories import server as repo
from src.repositories import server_account as account_repo
from src.repositories import server_category as category_repo
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
from src.services import audit_service, permissions, reservation, secrets_service
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import ipmi_controller_id, server_disk_id, server_id as new_id

logger = logging.getLogger(__name__)

_DUPLICATE_HINT = (
    "hostname, ip_address или serial_number уже используется, либо номер "
    "стенда уже занят в этом отделе"
)


async def _ensure_category_exists(db: AsyncSession, category_id: str | None) -> None:
    """Проверить FK на `server_categories` до записи.

    Без явной проверки битый `category_id` дошёл бы до INSERT/UPDATE и вернулся
    бы как SERVER_DUPLICATE — общий обработчик IntegrityError не различает, куда
    именно прилетело нарушение.
    """
    if category_id is None:
        return
    if await category_repo.get_by_id(db, category_id) is None:
        raise DomainValidationError(
            error_code="INVALID_SERVER_CATEGORY",
            message="category_id does not reference an existing server category",
            details={"category_id": category_id},
        )


async def _ensure_visible(
    db: AsyncSession, identity: IdentityContext, server: Server
) -> None:
    """Скрыть невидимый сервер за 404, чтобы не выдавать его существование.

    Сервер видим, если выполнено любое из:

      * он в отделе caller'а;
      * у caller'а есть хоть один инстанс-грант на сам сервер (через его роли);
      * у caller'а есть инстанс-грант на учётку, привязанную к этому серверу —
        тогда сервер-контейнер виден неявно (read-only), чтобы дойти до учётки
        и отрисовать контекст. Тип-wide прав на сервер это НЕ даёт: операции над
        сервером гейтятся `require_resource_action(SERVER, ...)`, а у такого
        caller'а серверных грантов нет.

    Иначе → 404, а не 403 — иначе по разнице ответов можно перечислить чужие
    server_id'ы. Platform-роли (`account_admin`/`loging_admin`) сюда физически
    не доходят: `platform_admin_guard` middleware режет их 403 до endpoint-слоя.
    """
    if identity.department_id == server.department_id:
        return
    if await permissions.has_resource_grant(db, identity, EntityType.SERVER, server.id):
        return
    if await _visible_via_account_grant(db, identity, server.id):
        return
    raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")


async def _visible_via_account_grant(
    db: AsyncSession, identity: IdentityContext, server_id: str
) -> bool:
    """True iff у caller'а есть инстанс-грант на учётку, привязанную к серверу.

    Неявная видимость сервера-контейнера ради навигации к доступной учётке.
    Считаем по привязанным учёткам сервера, пересекая их с инстанс-грантами
    caller'а — без единого гранта (или без привязанных учёток) → False.
    """
    linked_account_ids = await account_repo.linked_account_ids_for_server(db, server_id)
    if not linked_account_ids:
        return False
    granted = await permissions.visible_resource_ids(
        db, identity, EntityType.SERVER_ACCOUNT, linked_account_ids
    )
    return bool(granted)


async def load_visible_server(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
) -> Server:
    """Загрузить сервер с visibility-check, БЕЗ require_action(SERVER, VIEW).

    Используется в endpoint'ах, где основное разрешение даёт другой action
    на дочерней сущности (например, `ipmi_controller.view_credentials`
    у worker_bot — у него нет `server.view`, но он легитимно читает
    credentials привязанного к серверу controller'а). Видимость — свой отдел
    ИЛИ инстанс-грант (`_ensure_visible`).
    """
    obj = await repo.get_by_id(db, server_id)
    if obj is None:
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    await _ensure_visible(db, identity, obj)
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
    same_dept = {
        sid: srv for sid, srv in found.items()
        if identity.department_id == srv.department_id
    }
    # Сервера чужого отдела видимы, только если на них есть инстанс-грант.
    cross_dept_ids = [sid for sid in found if sid not in same_dept]
    if cross_dept_ids:
        granted = await permissions.visible_resource_ids(
            db, identity, EntityType.SERVER, cross_dept_ids
        )
        for sid in granted:
            same_dept[sid] = found[sid]
    return same_dept


async def load_storage(db: AsyncSession, server_id: str) -> list[ServerDisk]:
    """Диски сервера для раздела `storage` в ответе. Порядок — по слоту."""
    return await disk_repo.list_all_for_server(db, server_id)


async def load_storage_for_servers(
    db: AsyncSession, server_ids: list[str],
) -> dict[str, list[ServerDisk]]:
    """Bulk-вариант `load_storage` для list-эндпоинтов.

    Раньше `GET /servers` дёргал `load_storage` per row → N+1 (до 501 SELECT'ов
    при limit=500). Здесь — один SELECT с `WHERE server_id IN (...)`, маппинг
    `{server_id: [disks]}`. Cursor- и offset-страницы используют одинаково;
    detail-вьюхи (`GET /servers/{id}`) остаются на старом single-вызове.
    """
    return await disk_repo.list_for_servers(db, server_ids)


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
        spec.password(),
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


async def list_servers_cursor(
    db: AsyncSession,
    identity: IdentityContext,
    *,
    limit: int,
    after: str | None,
) -> tuple[list[Server], str | None, bool]:
    """Keyset-страница серверов своего отдела. Возвращает `(items, next_cursor, has_more)`.

    Запрашиваем у репозитория `limit + 1` строк, чтобы по факту наличия лишней
    понять, есть ли следующая страница, без отдельного COUNT'а. Если страница
    переполнена — отрезаем лишний элемент и кодируем курсор на last-в-странице.
    """
    from src.utils.cursor import (
        InvalidCursorError,
        decode_cursor,
        encode_cursor,
        normalize_limit,
        parse_cursor_datetime,
    )

    # Тип-wide `server.view` даёт весь отдел; без него — grant-only листинг
    # (видны ровно сервера с инстанс-грантом). Нет ни того, ни другого → 403.
    has_type_view = await permissions.has_action(
        db, identity, EntityType.SERVER, Action.VIEW
    )
    granted_ids: list[str] = []
    if not has_type_view:
        granted_ids = sorted(
            await permissions.granted_resource_ids(db, identity, EntityType.SERVER)
        )
        if not granted_ids:
            await permissions.require_action(db, identity, EntityType.SERVER, Action.VIEW)
    if identity.department_id is None and not granted_ids:
        return [], None, False
    page_size = normalize_limit(limit)
    after_created_at = None
    after_id = None
    if after:
        cur = decode_cursor(after)
        try:
            after_created_at = parse_cursor_datetime(cur.sort_value)
        except InvalidCursorError:
            raise
        after_id = cur.row_id
    if has_type_view:
        dept_filter = [identity.department_id]
        rows = await repo.list_in_departments_after(
            db,
            dept_filter,
            limit=page_size + 1,
            after_created_at=after_created_at,
            after_id=after_id,
        )
    else:
        rows = await repo.list_by_ids_after(
            db,
            granted_ids,
            limit=page_size + 1,
            after_created_at=after_created_at,
            after_id=after_id,
        )
    has_more = len(rows) > page_size
    items = rows[:page_size]
    next_cursor = (
        encode_cursor(items[-1].created_at, items[-1].id) if has_more and items else None
    )
    return items, next_cursor, has_more


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
    has_type_view = await permissions.has_action(
        db, identity, EntityType.SERVER, Action.VIEW
    )
    if has_type_view:
        if identity.department_id is None:
            return [], 0
        dept_filter = [identity.department_id]
        items = await repo.list_in_departments(db, dept_filter, limit=limit, offset=offset)
        total = await repo.count_in_departments(db, dept_filter)
        return items, total
    # grant-only листинг: роль без тип-wide view видит ровно сервера с
    # инстанс-грантом. Без единого гранта — 403 (require view поднимает отказ).
    granted_ids = sorted(
        await permissions.granted_resource_ids(db, identity, EntityType.SERVER)
    )
    if not granted_ids:
        await permissions.require_action(db, identity, EntityType.SERVER, Action.VIEW)
    items = await repo.list_by_ids(db, granted_ids, limit=limit, offset=offset)
    total = await repo.count_by_ids(db, granted_ids)
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
    # Visibility-check: 404 для non-existent / cross-dept. Permission и visibility
    # эмитятся разными status'ами — permission_denied → `denied`/`allowed=False`,
    # visibility-404 (caller прошёл VIEW, цель невидима) → `failure`/`allowed=True`.
    try:
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.VIEW
        )
        obj = await repo.get_by_id(db, server_id)
        if obj is None:
            raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
        await _ensure_visible(db, identity, obj)
    except (NotFoundError, AuthorizationError) as exc:
        if isinstance(exc, NotFoundError):
            audit_service.emit(
                "server.view",
                target_id=server_id,
                target_type="server",
                status="failure",
                allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
        else:
            audit_service.emit(
                "server.view",
                target_id=server_id,
                target_type="server",
                status="denied",
                allowed=False,
                details={"reason": "permission_denied"},
            )
        raise
    audit_service.emit(
        "server.view",
        target_id=obj.id, target_type="server",
        status="success", allowed=True,
        details={"department_id": obj.department_id},
    )
    return obj


async def get_server_by_number(
    db: AsyncSession,
    identity: IdentityContext,
    number: int,
) -> Server:
    """SELECT сервера по номеру стенда своего отдела + permission (через get_server).

    Номер уникален только в рамках department_id — lookup всегда идёт в
    department caller'а, чужой номер (даже существующий в другом отделе)
    не резолвится. Не найдено → 404 SERVER_NOT_FOUND (тот же маск, что и у
    get_server).
    """
    obj = await repo.get_by_department_number(db, identity.department_id, number)
    if obj is None:
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    return await get_server(db, identity, obj.id)


async def query_drift_summary(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    since: datetime,
) -> tuple[list[dict], bool]:
    """Собрать drift-сводку по серверу за окно `[since, now]`.

    Право — `(server, view_drift)`. Cross-dept сервер скрыт за 404 ровно как
    у `get_server`. Сами события достаём из loging_service через
    `loging_client.fetch_drift_events`; на стороне server_service делаем
    только permission/visibility-check и трансформацию в schema-ready dict.

    Возвращает кортеж `(drifts, truncated)`:

    * `drifts` — список dict'ов, валидируемых далее `DriftEventItem`.
    * `truncated` — флаг переполнения окна (см. `loging_client`).
    """
    from src.services import loging_client  # локальный импорт — рвём цикл при тестах

    try:
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.VIEW_DRIFT
        )
        obj = await repo.get_by_id(db, server_id)
        if obj is None:
            raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
        await _ensure_visible(db, identity, obj)
    except (NotFoundError, AuthorizationError) as exc:
        reason = (
            "not_found_or_cross_dept"
            if isinstance(exc, NotFoundError)
            else "permission_denied"
        )
        audit_service.emit(
            "server.view_drift",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": reason},
        )
        raise

    events, truncated = await loging_client.fetch_drift_events(
        server_id=server_id, since=since,
    )

    # Из event'а пытаемся достать login / drift_type / fields. Контракт:
    # emitter (`internal_service.receive_users_inventory`) кладёт login и
    # drift в `details`, fields появляется только при drift='attributes'.
    items: list[dict] = []
    for ev in events:
        details = ev.get("details") or {}
        items.append({
            "login": details.get("login") or "",
            "drift_type": details.get("drift") or "",
            "fields": details.get("fields"),
            "detected_at": ev.get("timestamp"),
        })

    audit_service.emit(
        "server.view_drift",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "department_id": obj.department_id,
            "since": since.isoformat(),
            "count": len(items),
            "truncated": truncated,
        },
    )
    return items, truncated


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
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.SERVER, Action.CREATE)
    if payload.department_id != identity.department_id:
        # Caller с CREATE прошёл матрицу прав, но указал чужой department —
        # cross-dept isolation, `failure`/`allowed=True` симметрично visibility-сайтам.
        audit_service.emit(
            "server.create",
            target_type="server",
            status="failure",
            allowed=True,
            details={"reason": "department_isolation", "department_id": payload.department_id},
        )
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message="Cannot create a server in a different department",
        )
    await _ensure_category_exists(db, payload.category_id)
    data = payload.model_dump(mode="json", exclude={"storage", "ipmi"})
    data["id"] = new_id()
    data["created_by"] = identity.user_id
    ipmi_obj: IpmiController | None = None
    # Куда именно прилетел UNIQUE — `server` (hostname/ip/serial), `storage`
    # (server_id + slot) или `ipmi` (server_id-дубль). На transactional
    # rollback'е inspect'ить constraint name из `exc.orig` нестабильно (зависит
    # от диалекта); проще трекать стадию, на которой свалились.
    failing_stage = "server"
    try:
        obj = await repo.create(db, data)
        if payload.storage:
            failing_stage = "storage"
            await _sync_storage(db, obj.id, payload.storage)
        if payload.ipmi is not None:
            failing_stage = "ipmi"
            ipmi_obj = await _insert_ipmi(db, obj.id, payload.ipmi)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на создании сервера stage=%s: %s",
            failing_stage, type(exc.orig).__name__,
        )
        if failing_stage == "ipmi":
            error_code = "IPMI_DUPLICATE"
            message = "IPMI controller for this server already exists"
        else:
            error_code = "SERVER_DUPLICATE"
            message = (
                "Server with this hostname, IP, serial_number, stand number "
                "(within the department) or disk slot already exists"
            )
        audit_service.emit(
            "server.create",
            target_id=data["id"],
            target_type="server",
            status="failure",
            allowed=True,
            details={
                "reason": "duplicate",
                "stage": failing_stage,
                "department_id": payload.department_id,
            },
        )
        raise ConflictError(
            error_code=error_code,
            message=message,
            details={"hint": _DUPLICATE_HINT, "stage": failing_stage},
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
        # Локальный импорт — `services/ipmi_controller` тянет `load_visible_server`
        # из этого модуля, чтобы не словить циклический импорт на старте.
        from src.services.ipmi_controller import emit_create_success
        emit_create_success(ipmi_obj, obj.department_id)
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
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.UPDATE
        )
    # Visibility-check: 404 для non-existent / cross-dept. Permission уже прошёл выше
    # через `emit_denied_on_authz_error`, здесь — visibility, поэтому `failure`/
    # `allowed=True`. Эмит ДО re-raise, иначе попытка теряется в middleware'е
    # как `http.client_error` без action-key.
    try:
        obj = await repo.get_by_id(db, server_id)
        if obj is None:
            raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
        await _ensure_visible(db, identity, obj)
    except (NotFoundError, AuthorizationError) as exc:
        audit_service.emit(
            "server.update",
            target_id=server_id,
            target_type="server",
            status="failure",
            allowed=True,
            details={
                "reason": (
                    "not_found_or_cross_dept"
                    if isinstance(exc, NotFoundError)
                    else "cross_department"
                ),
            },
        )
        raise
    # Бронь гейтит правку карточки: занятый сервер меняет только владелец
    # брони или админ отдела/сервиса. Проверка после visibility — caller уже
    # прошёл permission и видит сервер; чужому занятому → 409 SERVER_RESERVED.
    reservation.ensure_not_reserved_for(identity, obj, action="server.update")
    changes = payload.model_dump(exclude_unset=True, mode="json")
    # storage синхронизируется отдельно (full-replace дочерних строк), а не
    # пишется как колонка в `servers`. `None`/не прислано → диски не трогаем.
    sync_storage = "storage" in changes
    changes.pop("storage", None)
    if not changes and not sync_storage:
        return obj
    if "category_id" in changes:
        await _ensure_category_exists(db, changes["category_id"])
    previous_category_id = obj.category_id
    audit_fields = list(changes.keys())
    if sync_storage:
        audit_fields.append("storage")
    # Куда прилетел UNIQUE — в строку `servers` (hostname/ip/serial) или в
    # дочерние disk-row'ы (server_id + slot). Симметрично `create_server`:
    # constraint name из `exc.orig` парсить нестабильно (диалект-зависимо),
    # проще трекать стадию.
    failing_stage = "server"
    try:
        if changes:
            await repo.update(db, obj, changes)
        if sync_storage:
            failing_stage = "storage"
            await _sync_storage(db, obj.id, payload.storage or [])
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на обновлении сервера %s stage=%s: %s",
            server_id, failing_stage, type(exc.orig).__name__,
        )
        audit_service.emit(
            "server.update",
            target_id=server_id,
            target_type="server",
            status="failure",
            allowed=True,
            details={"reason": "duplicate", "stage": failing_stage, "fields": audit_fields},
        )
        raise ConflictError(
            error_code="SERVER_DUPLICATE",
            message="Update collides with an existing server (hostname/IP/serial_number/stand number/disk slot)",
            details={"hint": _DUPLICATE_HINT, "stage": failing_stage},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server.update",
        target_id=obj.id,
        target_type="server",
        status="success",
        allowed=True,
        details={
            "fields": audit_fields,
            "changed_fields": audit_fields,
            "department_id": obj.department_id,
        },
    )
    if "category_id" in changes:
        # Отдельное событие поверх общего server.update: категория по мощности —
        # это роль стенда, по которой testing_service подбирает, куда класть тест.
        audit_service.emit(
            "server.category_assigned",
            target_id=obj.id,
            target_type="server",
            status="success",
            allowed=True,
            details={
                "department_id": obj.department_id,
                "previous_category_id": previous_category_id,
                "new_category_id": obj.category_id,
            },
        )
    return obj


async def delete_server(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
) -> None:
    """Hard-delete сервера + каскад дочерних записей (ipmi/disks/packages/связки).

    Join-строки `server_account_servers` уходят каскадом (ondelete=CASCADE), но
    сами строки `server_accounts` — нет. Аккаунт, привязанный только к этому
    серверу, иначе остался бы orphan'ом: висит с зашифрованным паролем и
    недостижим (листинг требует server_id). Тот же конечный стейт (аккаунт без
    серверов) unlink-путь запрещает через ACCOUNT_NO_SERVERS, поэтому здесь мы
    такие аккаунты удаляем явно — консистентно с инвариантом «аккаунт без
    серверов не существует». Аккаунты, привязанные ещё к другим серверам,
    переживают delete: у них уходит одна связка каскадом.

    Audit `server.delete` с CRITICAL severity — это deliberately destructive.
    Снос каждого осиротевшего аккаунта пишется отдельным `server_account.delete`.
    """
    with emit_denied_on_authz_error(
        "server.delete",
        target_id=server_id,
        target_type="server",
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.DELETE
        )
    # Visibility-check: 404 для non-existent / cross-dept. Permission уже прошёл выше
    # через `emit_denied_on_authz_error`, здесь — visibility, поэтому `failure`/
    # `allowed=True`. Эмит ДО re-raise, иначе попытка теряется в middleware'е
    # как `http.client_error` без action-key.
    try:
        obj = await repo.get_by_id(db, server_id)
        if obj is None:
            raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
        await _ensure_visible(db, identity, obj)
    except (NotFoundError, AuthorizationError) as exc:
        audit_service.emit(
            "server.delete",
            target_id=server_id,
            target_type="server",
            status="failure",
            allowed=True,
            details={
                "reason": (
                    "not_found_or_cross_dept"
                    if isinstance(exc, NotFoundError)
                    else "cross_department"
                ),
            },
        )
        raise
    # Удаление занятого сервера разрешено только владельцу брони или админу —
    # иначе чужой тест внезапно теряет железо из-под себя.
    reservation.ensure_not_reserved_for(identity, obj, action="server.delete")
    department_id = obj.department_id
    hostname = obj.hostname

    # Снимаем «снимок» аккаунтов, для которых этот сервер — последний, ДО
    # delete'а: после каскада join-строк такой запрос их уже не вернёт.
    orphaned_accounts = await account_repo.list_accounts_only_on_server(db, server_id)
    orphan_meta = [
        {"id": a.id, "login": a.login, "department_id": a.department_id}
        for a in orphaned_accounts
    ]
    for acc in orphaned_accounts:
        await resource_perm_repo.delete_for_resource(
            db, EntityType.SERVER_ACCOUNT, acc.id
        )
        await account_repo.delete(db, acc)
    # Инстанс-гранты удаляемого сервера осиротели бы — чистим их в той же
    # транзакции (soft-FK без каскада на уровне БД).
    await resource_perm_repo.delete_for_resource(db, EntityType.SERVER, server_id)
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "server.delete",
        target_id=server_id,
        target_type="server",
        status="success",
        allowed=True,
        details={
            "hostname": hostname,
            "department_id": department_id,
            "orphaned_accounts_deleted": [m["id"] for m in orphan_meta],
        },
    )
    for meta in orphan_meta:
        audit_service.emit(
            "server_account.delete",
            target_id=meta["id"],
            target_type="server_account",
            status="success",
            allowed=True,
            details={
                "reason": "server_deleted",
                "server_id": server_id,
                "login": meta["login"],
                "department_id": meta["department_id"],
            },
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

    Порядок проверок — канон permission → visibility (`require_action`
    смотрит на роли caller'а, не на target, поэтому 403 не делает
    existence-oracle). Та же раскладка, что и в `_dispatch_power`.
    """
    with emit_denied_on_authz_error(
        "server.acquire",
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.BUSY_ACQUIRE,
        )
    try:
        obj = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "server.acquire",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
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
    busy_note = payload.purpose or None
    now = datetime.now(timezone.utc)
    # Атомарный CAS: UPDATE ... WHERE busy_state='free' AND status<>decommissioned.
    # status в WHERE'е закрывает race: между `load_visible_server` и UPDATE'ом
    # параллельный decommission успел бы перевести сервер в DECOMMISSIONED,
    # а старая CAS условие (только по busy_state) спокойно прошла бы и оставила
    # сервер busy+decommissioned. rowcount==0 теперь означает «либо уже busy,
    # либо decommissioned, либо нет вообще»; точную причину достаём re-fetch'ем.
    result = await db.execute(
        sa_update(Server)
        .where(
            Server.id == server_id,
            Server.busy_state == BusyState.FREE,
            Server.status != ServerStatus.DECOMMISSIONED,
        )
        .values(
            busy_state=BusyState.BUSY,
            busy_user_id=identity.user_id,
            busy_actor_type=BusyActorType.USER,
            busy_service_name=None,
            busy_since=now,
            busy_note=busy_note,
        )
    )
    if result.rowcount == 0:
        # Re-fetch без кэша — нужно увидеть, что положил параллельный writer.
        # Сессия SQLAlchemy могла бы вернуть закэшированный obj; expire
        # принудительно перечитывает (sync API на AsyncSession). READ COMMITTED
        # (дефолт PostgreSQL) выдаёт свежий snapshot на каждый SELECT, поэтому
        # явный rollback'а транзакции не нужен — он ломал тестовые SAVEPOINT'ы.
        db.expire(obj)
        current = await repo.get_by_id(db, server_id)
        if current is None:
            # Сервер исчез между load_visible_server и CAS — крайне маловероятно
            # (hard-delete не предусмотрен), но фиксируем явный 404.
            audit_service.emit(
                "server.acquire",
                target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "vanished_during_acquire"},
            )
            raise NotFoundError(
                error_code="SERVER_NOT_FOUND",
                message="Server not found",
            )
        if current.status == ServerStatus.DECOMMISSIONED:
            audit_service.emit(
                "server.acquire",
                target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={
                    "reason": "decommissioned_race",
                    "department_id": current.department_id,
                },
            )
            raise ConflictError(
                error_code="SERVER_DECOMMISSIONED",
                message="Server is decommissioned and cannot be acquired",
            )
        audit_service.emit(
            "server.acquire",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "already_busy",
                "department_id": current.department_id,
                "current_state": current.busy_state,
            },
        )
        raise ConflictError(
            error_code="SERVER_ALREADY_BUSY",
            message="Server is already busy or being tested",
            details={"current_state": current.busy_state},
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
        },
    )
    return obj


async def release_server(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
) -> Server:
    """Снятие busy-флага. Требует роль с `busy_release`.

    Сам lessee без `busy_release` отпустить захват не может — это известный
    зазор: операционно lessee должен уметь освободить «своё», но реализовать
    self-release без открытия escalation-пути (выдать busy_release вообще
    всем) нетривиально и оставлено как будущая фича.

    Если сервер уже free — 409 SERVER_NOT_BUSY (идемпотентный release клиенту
    осмыслен не очень — он сигналит о рассинхронизации состояния).

    Порядок проверок — канон permission → visibility (тот же паттерн, что
    у `_dispatch_power` и `acquire_server`).
    """
    with emit_denied_on_authz_error(
        "server.release",
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.BUSY_RELEASE,
        )
    try:
        obj = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "server.release",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    # Симметрия с `acquire_server`: pre-check `busy_state` без лока читал бы
    # stale значение, если параллельный release/acquire уже изменил строку
    # между `load_visible_server` и проверкой. Re-fetch с FOR UPDATE даёт
    # live snapshot и сериализует с другими release'ами по этой же строке.
    db.expire(obj)
    locked = await repo.get_for_update(db, server_id)
    if locked is None:
        audit_service.emit(
            "server.release",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "vanished_during_release"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="Server not found",
        )
    if locked.busy_state == BusyState.FREE:
        audit_service.emit(
            "server.release",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_busy", "department_id": locked.department_id},
        )
        raise ConflictError(
            error_code="SERVER_NOT_BUSY",
            message="Server is already free",
        )
    if locked.busy_state == BusyState.UPDATING:
        # updating — системная блокировка на время astra-update, а не обычная
        # бронь: снимать её через release нельзя (любой носитель busy_release
        # разблокировал бы сервер посреди apt/astra-update). Снимает её только
        # callback воркера `astra-updated`; залипшую (воркер не долетел) чинит
        # плановый sweep `recover_stuck_updating` по TTL.
        audit_service.emit(
            "server.release",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "updating", "department_id": locked.department_id},
        )
        raise ConflictError(
            error_code="SERVER_UPDATING",
            message=(
                "Server is being updated (astra_update in progress); the "
                "updating lock cannot be released manually and clears on the "
                "worker callback or a TTL recovery sweep"
            ),
            details={"busy_note": locked.busy_note},
        )
    if locked.busy_state == BusyState.TESTING:
        # Тест реально выполняется — снять бронь может только держащий сервис
        # своим internal-каналом (`release-for-service*`), это human-facing
        # DELETE его не заменяет. Никакого admin-обхода, симметрично тому, как
        # `is_reserved_for_other` трактует `testing` для деструктивных операций.
        audit_service.emit(
            "server.release",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "testing_in_progress", "department_id": locked.department_id},
        )
        raise ConflictError(
            error_code="SERVER_TESTING_IN_PROGRESS",
            message=(
                "Server is running a test; only the holding service can "
                "release it, through its internal channel"
            ),
            details={"busy_note": locked.busy_note},
        )
    if locked.busy_state == BusyState.ACS:
        # Тот же гейт, что и для остальных операций над сервером под
        # ACS-снимком/восстановлением — снять бронь может только админ.
        reservation.ensure_not_acs_locked(identity, locked)
    obj = locked
    previous_user_id = obj.busy_user_id
    # Атомарный release из любого non-free состояния (busy / acs — testing уже
    # отбит выше). Свежий SELECT мог увидеть state='busy', но к моменту UPDATE
    # параллельный release уже мог его снять — rowcount==0 в этом случае
    # значит «кто-то успел раньше», тоже ошибка (409 SERVER_NOT_BUSY).
    result = await db.execute(
        sa_update(Server)
        .where(Server.id == server_id, Server.busy_state != BusyState.FREE)
        .values(
            busy_state=BusyState.FREE,
            busy_user_id=None,
            busy_actor_type=BusyActorType.USER,
            busy_service_name=None,
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


# ── Бронь от имени сервиса (internal s2s-канал) ─────────────────────────────
#
# Отличия от пользовательских acquire/release выше: caller — не человек, а
# уже провалидированная service-identity, поэтому здесь нет ни матрицы
# `entity_permissions`, ни department-видимости (у сервисного каллера отдела
# нет). Взамен действует более узкое правило: снять или переключить бронь
# может только тот сервис, который её взял.


def _service_holds(server: Server, service_name: str) -> bool:
    """True, если текущая бронь сервера принадлежит именно этому сервису."""
    return (
        server.busy_actor_type == BusyActorType.SERVICE
        and server.busy_service_name == service_name
    )


def _ensure_service_holds(server: Server, service_name: str, *, action: str) -> None:
    """Отбить 409, если бронь держит кто-то другой (человек или другой сервис).

    Сервис не может ни освободить, ни переключить чужую бронь — иначе
    testing_service мог бы снять ACS-лок посреди перезаписи диска.
    """
    if _service_holds(server, service_name):
        return
    audit_service.emit(
        action,
        target_id=server.id, target_type="server",
        status="denied", allowed=False,
        details={
            "reason": "reservation_held_by_other",
            "service_name": service_name,
            "department_id": server.department_id,
            "busy_state": server.busy_state,
            "busy_actor_type": server.busy_actor_type,
            "busy_service_name": server.busy_service_name,
        },
    )
    raise ConflictError(
        error_code="SERVER_RESERVED_BY_OTHER",
        message=(
            "Server reservation is held by another actor; a service can only "
            "release or update the reservation it acquired itself"
        ),
        details={
            "busy_actor_type": server.busy_actor_type,
            "busy_service_name": server.busy_service_name,
        },
    )


async def acquire_server_for_service(
    db: AsyncSession,
    *,
    server_id: str,
    service_name: str,
    busy_state: str,
    busy_note: str | None,
    requested_by_department_id: str | None = None,
    takeover: bool = False,
) -> tuple[Server, dict | None]:
    """Захват сервера от имени сервиса — аналог `acquire_server` без человека.

    Держатель — `busy_service_name`, `busy_user_id` остаётся пустым (иначе
    `ck_servers_busy_actor` отобьёт запись). Гонка решается тем же атомарным
    CAS `UPDATE ... WHERE busy_state='free' AND status<>decommissioned`, что
    и у пользовательского acquire: rowcount==0 значит «уже занят кем-то»,
    точную причину достаём re-fetch'ем.

    `requested_by_department_id` — необязательная страховка: сервисный каллер
    своего отдела не имеет, привязку стенда к отделу он ведёт у себя, но если
    прислал — сверяем с отделом сервера. Несовпадение маскируем под 404, как
    `_check_target_department_for_server` у worker-callback'ов: разница
    403/404 работала бы enumeration-oracle'ом по чужим отделам.

    `takeover=True` дополнительно позволяет отнять бронь в `busy` (человек)
    или `testing_done`: строка блокируется, прежний держатель снимается в
    снимок, бронь переписывается на вызывающий сервис. Возвращает
    `(server, previous_holder)`; `previous_holder` — None при обычном захвате.
    `updating` / `acs` / `testing` не отнимаются никогда — 409, как без takeover.
    """
    obj = await repo.get_by_id(db, server_id)
    if obj is None:
        audit_service.emit(
            "server.acquired_for_service",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_found", "service_name": service_name},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="Server not found",
        )
    if (
        requested_by_department_id is not None
        and requested_by_department_id != obj.department_id
    ):
        audit_service.emit(
            "server.acquired_for_service",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={
                "reason": "target_department_mismatch",
                "service_name": service_name,
                "server_department_id": obj.department_id,
                "requested_by_department_id": requested_by_department_id,
            },
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="Server not found",
        )
    if obj.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            "server.acquired_for_service",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "decommissioned",
                "service_name": service_name,
                "department_id": obj.department_id,
            },
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot be acquired",
        )
    now = datetime.now(timezone.utc)
    result = await db.execute(
        sa_update(Server)
        .where(
            Server.id == server_id,
            Server.busy_state == BusyState.FREE,
            Server.status != ServerStatus.DECOMMISSIONED,
        )
        .values(
            busy_state=busy_state,
            busy_user_id=None,
            busy_actor_type=BusyActorType.SERVICE,
            busy_service_name=service_name,
            busy_since=now,
            busy_note=busy_note,
        )
    )
    if result.rowcount == 0:
        db.expire(obj)
        current = await repo.get_by_id(db, server_id)
        if current is None:
            audit_service.emit(
                "server.acquired_for_service",
                target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "vanished_during_acquire", "service_name": service_name},
            )
            raise NotFoundError(
                error_code="SERVER_NOT_FOUND",
                message="Server not found",
            )
        if current.status == ServerStatus.DECOMMISSIONED:
            audit_service.emit(
                "server.acquired_for_service",
                target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={
                    "reason": "decommissioned_race",
                    "service_name": service_name,
                    "department_id": current.department_id,
                },
            )
            raise ConflictError(
                error_code="SERVER_DECOMMISSIONED",
                message="Server is decommissioned and cannot be acquired",
            )
        if takeover and current.busy_state in _TAKEOVER_STATES:
            taken = await _takeover_service_reservation(
                db,
                current,
                server_id=server_id,
                service_name=service_name,
                busy_state=busy_state,
                busy_note=busy_note,
            )
            if taken is not None:
                return taken
            db.expire(current)
            current = await repo.get_by_id(db, server_id)
            if current is None or current.busy_state == BusyState.FREE:
                # Бронь успели снять между проверкой и локом — повторяем
                # обычный захват, а не отдаём 409 на свободный сервер.
                return await acquire_server_for_service(
                    db,
                    server_id=server_id,
                    service_name=service_name,
                    busy_state=busy_state,
                    busy_note=busy_note,
                    requested_by_department_id=requested_by_department_id,
                    takeover=takeover,
                )
        audit_service.emit(
            "server.acquired_for_service",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "already_busy",
                "service_name": service_name,
                "department_id": current.department_id,
                "current_state": current.busy_state,
                "busy_actor_type": current.busy_actor_type,
                "busy_service_name": current.busy_service_name,
                "takeover": takeover,
            },
        )
        raise ConflictError(
            error_code="SERVER_ALREADY_BUSY",
            message="Server is already busy or being tested",
            details={"current_state": current.busy_state},
        )
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "server.acquired_for_service",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "service_name": service_name,
            "department_id": obj.department_id,
            "busy_state": obj.busy_state,
            "busy_note": busy_note,
        },
    )
    return obj, None


# Состояния, которые takeover вправе перебить: бронь человека и «отстоявшийся»
# после теста стенд. updating/acs — чужая операция над боксом, testing — бронь
# самого testing_service (решается режимами очереди), их не отнимаем.
_TAKEOVER_STATES = (BusyState.BUSY, BusyState.TESTING_DONE)


async def _takeover_service_reservation(
    db: AsyncSession,
    current: Server,
    *,
    server_id: str,
    service_name: str,
    busy_state: str,
    busy_note: str | None,
) -> tuple[Server, dict] | None:
    """Переписать бронь `busy` / `testing_done` на вызывающий сервис.

    Под `FOR UPDATE`, чтобы снимок прежнего держателя и перезапись были
    одной атомарной операцией; UPDATE дополнительно сверяет состояние. None —
    бронь за это время сменилась на не-takeover-able (или освободилась), решает
    вызывающий.
    """
    db.expire(current)
    locked = await repo.get_for_update(db, server_id)
    if (
        locked is None
        or locked.status == ServerStatus.DECOMMISSIONED
        or locked.busy_state not in _TAKEOVER_STATES
    ):
        return None
    previous = {
        "busy_state": locked.busy_state,
        "busy_user_id": locked.busy_user_id,
        "busy_service_name": locked.busy_service_name,
        "busy_note": locked.busy_note,
    }
    result = await db.execute(
        sa_update(Server)
        .where(Server.id == server_id, Server.busy_state == previous["busy_state"])
        .values(
            busy_state=busy_state,
            busy_user_id=None,
            busy_actor_type=BusyActorType.SERVICE,
            busy_service_name=service_name,
            busy_since=datetime.now(timezone.utc),
            busy_note=busy_note,
        )
    )
    if result.rowcount == 0:
        return None
    await db.commit()
    await db.refresh(locked)
    audit_service.emit(
        "server.reservation_taken_over",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "service_name": service_name,
            "department_id": locked.department_id,
            "busy_state": locked.busy_state,
            "busy_note": busy_note,
            "previous_busy_state": previous["busy_state"],
            "previous_busy_user_id": previous["busy_user_id"],
            "previous_busy_service_name": previous["busy_service_name"],
            "previous_busy_note": previous["busy_note"],
        },
    )
    return locked, previous


async def release_server_for_service(
    db: AsyncSession,
    *,
    server_id: str,
    service_name: str,
) -> Server:
    """Снять бронь, взятую этим же сервисом. Чужую бронь не трогает.

    Row-lock перед проверкой держателя — как в `release_server`: без него
    решение принималось бы по stale-снимку, а параллельный release/acquire
    успел бы перевесить бронь на другого актора.
    """
    obj = await repo.get_for_update(db, server_id)
    if obj is None:
        audit_service.emit(
            "server.released_for_service",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_found", "service_name": service_name},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="Server not found",
        )
    if obj.busy_state == BusyState.FREE:
        audit_service.emit(
            "server.released_for_service",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "not_busy",
                "service_name": service_name,
                "department_id": obj.department_id,
            },
        )
        raise ConflictError(
            error_code="SERVER_NOT_BUSY",
            message="Server is already free",
        )
    _ensure_service_holds(obj, service_name, action="server.released_for_service")
    previous_state = obj.busy_state
    await repo.update(db, obj, {
        "busy_state": BusyState.FREE,
        "busy_user_id": None,
        "busy_actor_type": BusyActorType.USER,
        "busy_service_name": None,
        "busy_since": None,
        "busy_note": None,
    })
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "server.released_for_service",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "service_name": service_name,
            "department_id": obj.department_id,
            "previous_state": previous_state,
        },
    )
    return obj


async def release_server_for_service_as_done(
    db: AsyncSession,
    *,
    server_id: str,
    service_name: str,
) -> Server:
    """Отпустить бронь этого сервиса в `testing_done`, а не в `free`.

    Используется `testing_service`, когда очередь стенда опустела: вместо
    немедленного `free` сервер паркуется в промежуточном статусе, который
    снимает вручную любой пользователь через человеческий
    `POST /servers/{id}/acknowledge-testing-done`. `busy_actor_type`/
    `busy_service_name`/`busy_note` сохраняются как есть — это контекст «кто
    тестировал», а не активная бронь, поэтому очищать его тут не нужно.

    Проверка держателя и гонки — те же, что у `release_server_for_service`.
    """
    obj = await repo.get_for_update(db, server_id)
    if obj is None:
        audit_service.emit(
            "server.released_for_service",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_found", "service_name": service_name, "mark_as": BusyState.TESTING_DONE},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="Server not found",
        )
    if obj.busy_state == BusyState.FREE:
        audit_service.emit(
            "server.released_for_service",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "not_busy",
                "service_name": service_name,
                "department_id": obj.department_id,
            },
        )
        raise ConflictError(
            error_code="SERVER_NOT_BUSY",
            message="Server is already free",
        )
    _ensure_service_holds(obj, service_name, action="server.released_for_service")
    previous_state = obj.busy_state
    await repo.update(db, obj, {"busy_state": BusyState.TESTING_DONE})
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "server.released_for_service",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "service_name": service_name,
            "department_id": obj.department_id,
            "previous_state": previous_state,
            "mark_as": BusyState.TESTING_DONE,
        },
    )
    return obj


async def acknowledge_testing_done(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
) -> Server:
    """Снять `testing_done` → `free`. Доступно любому с `(server, *, view)`.

    В отличие от `release_server` (требует `busy_release`), это узкое
    человеческое действие намеренно доступно всем, кто видит карточку
    сервера — владелец хочет, чтобы любой, кто наткнулся на «Тестирование
    завершено», мог снять статус, не выпрашивая отдельную роль. Работает
    только из `testing_done`; в остальных состояниях (включая обычный `busy`)
    отбивается 409 — это не замена `release_server`.
    """
    with emit_denied_on_authz_error(
        "server.acknowledge_testing_done",
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.VIEW,
        )
    try:
        obj = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "server.acknowledge_testing_done",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    result = await db.execute(
        sa_update(Server)
        .where(Server.id == server_id, Server.busy_state == BusyState.TESTING_DONE)
        .values(
            busy_state=BusyState.FREE,
            busy_user_id=None,
            busy_actor_type=BusyActorType.USER,
            busy_service_name=None,
            busy_since=None,
            busy_note=None,
        )
    )
    if result.rowcount == 0:
        db.expire(obj)
        current = await repo.get_by_id(db, server_id)
        current_state = current.busy_state if current is not None else None
        audit_service.emit(
            "server.acknowledge_testing_done",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_testing_done", "current_state": current_state},
        )
        raise ConflictError(
            error_code="SERVER_NOT_TESTING_DONE",
            message="Server is not in testing_done state",
            details={"current_state": current_state},
        )
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "server.acknowledge_testing_done",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={"department_id": obj.department_id},
    )
    return obj


async def set_service_busy_status(
    db: AsyncSession,
    *,
    server_id: str,
    service_name: str,
    busy_state: str,
    busy_note: str | None,
) -> Server:
    """Переключить стадию внутри уже взятой этим сервисом брони.

    Этим `testing_service` переводит стенд `acs` → `testing`, получив креды от
    `prepare-for-test`. `busy_since` не трогаем: он отмеряет время всей брони,
    а не отдельной стадии. `busy_note=None` оставляет прежнюю заметку — снять
    её без снятия брони поводов нет.
    """
    obj = await repo.get_for_update(db, server_id)
    if obj is None:
        audit_service.emit(
            "server.service_status_changed",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_found", "service_name": service_name},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="Server not found",
        )
    if obj.busy_state == BusyState.FREE:
        audit_service.emit(
            "server.service_status_changed",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "not_busy",
                "service_name": service_name,
                "department_id": obj.department_id,
            },
        )
        raise ConflictError(
            error_code="SERVER_NOT_BUSY",
            message="Server is free; acquire it before changing its busy state",
        )
    _ensure_service_holds(obj, service_name, action="server.service_status_changed")
    previous_state = obj.busy_state
    updates: dict = {"busy_state": busy_state}
    if busy_note is not None:
        updates["busy_note"] = busy_note
    await repo.update(db, obj, updates)
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "server.service_status_changed",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "service_name": service_name,
            "department_id": obj.department_id,
            "previous_state": previous_state,
            "busy_state": obj.busy_state,
            "busy_note": obj.busy_note,
        },
    )
    return obj


async def get_connection_info_for_service(db: AsyncSession, *, server_id: str) -> Server:
    """Отдать сервер по id для s2s-каллера без пользовательского bearer'а.

    Единственный сегодняшний потребитель — `GET /internal/servers/{id}/
    connection-info` для `testing_worker` (SSH-подключение к стенду). Никакой
    проверки видимости/отдела здесь нет — этот канал уже прошёл
    `require_internal_caller`, department-скоуп у сервисного каллера
    отсутствует по построению (см. `acquire_server_for_service`).
    """
    obj = await repo.get_by_id(db, server_id)
    if obj is None:
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="Server not found",
        )
    return obj


async def get_batch_status_for_service(db: AsyncSession, *, server_ids: list[str]) -> dict[str, Server]:
    """Пачкой отдать ping/busy для s2s-каллера (обзор пула testing_service).

    Один `WHERE id IN (...)` вместо N round-trip'ов на N стендов. Отсутствующие
    id просто не попадают в результат — caller (`get_batch_status`) решает,
    как трактовать пропуск, не 404 на весь батч.
    """
    return await repo.get_many_by_ids(db, server_ids)


async def recover_stuck_updating(db: AsyncSession, *, limit: int = 500) -> dict:
    """Освободить серверы, застрявшие в `busy_state='updating'` дольше TTL.

    Обновление ОС ставит `updating`, а снимает его только callback воркера
    `astra-updated`. Если воркер упал/потерял задачу и callback не пришёл,
    сервер завис бы в `updating` навсегда — `ensure_not_updating` отбивает все
    операции 409 SERVER_UPDATING, включая владельца и админа. Этот sweep — тот
    самый escape hatch: серверы с `busy_since` старше
    `astra_update_stuck_ttl_minutes` переводятся в `free`.

    Освобождаем атомарным CAS с тем же условием (`busy_state='updating' AND
    busy_since < cutoff`), под которым сервер был выбран: если ровно в этот
    момент долетел легитимный `astra-updated`-callback и уже снял блокировку,
    rowcount==0 и мы его не трогаем (нет двойного освобождения). На каждый
    реально освобождённый сервер — WARNING-аудит `server.astra_update_recovered`
    (actor_type=system, ставится в middleware/скедулере). Возвращает
    `{recovered, ttl_minutes}`.
    """
    ttl_minutes = get_settings().astra_update_stuck_ttl_minutes
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ttl_minutes)
    stuck = await repo.list_stuck_updating(db, cutoff, limit)
    recovered = 0
    for server in stuck:
        busy_since = server.busy_since
        result = await db.execute(
            sa_update(Server)
            .where(
                Server.id == server.id,
                Server.busy_state == BusyState.UPDATING,
                Server.busy_since < cutoff,
            )
            .values(
                busy_state=BusyState.FREE,
                busy_user_id=None,
                busy_actor_type=BusyActorType.USER,
                busy_service_name=None,
                busy_since=None,
                busy_note=None,
            )
        )
        if result.rowcount == 0:
            # Callback опередил sweep — блокировка уже снята, пропускаем.
            continue
        await db.commit()
        recovered += 1
        stuck_minutes = None
        if busy_since is not None:
            if busy_since.tzinfo is None:
                busy_since = busy_since.replace(tzinfo=timezone.utc)
            stuck_minutes = round(
                (datetime.now(timezone.utc) - busy_since).total_seconds() / 60, 1
            )
        audit_service.emit(
            "server.astra_update_recovered",
            target_id=server.id, target_type="server",
            status="warning", allowed=True,
            details={
                "reason": "stuck_updating_ttl",
                "department_id": server.department_id,
                "ttl_minutes": ttl_minutes,
                "stuck_minutes": stuck_minutes,
            },
        )
    return {"recovered": recovered, "ttl_minutes": ttl_minutes}


async def update_os_version(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: ServerOsVersionUpdate,
) -> Server:
    """Ручной апдейт `os_version_id` сервера (не через inventory sync).

    Полезно носителю права на update сервера, когда железо физически переустановили без
    участия worker'а или надо быстро сменить версию вручную. FK os_version_id →
    os_versions(id) с ondelete=RESTRICT, поэтому невалидный id → 422
    INVALID_OS_VERSION (через IntegrityError).

    Порядок проверок — канон permission → visibility.
    """
    with emit_denied_on_authz_error(
        "server.update_os_version",
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.OS_SYNC,
        )
    try:
        obj = await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "server.update_os_version",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    # os-sync деструктивен (меняет учтённую версию ОС занятого сервера) — тот
    # же гейт брони, что и на update/delete.
    reservation.ensure_not_reserved_for(identity, obj, action="server.update_os_version")
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
