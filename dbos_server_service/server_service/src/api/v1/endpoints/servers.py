"""CRUD-эндпоинты серверов + busy-lease (acquire/release) + ручной os-sync."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.server import (
    ServerAcquireRequest,
    ServerCreate,
    ServerOsVersionUpdate,
    ServerResponse,
    ServerUpdate,
)
from src.services import server as svc

router = APIRouter(prefix="/servers")


@router.get(
    "",
    response_model=PaginatedResponse[ServerResponse],
    summary="Список серверов, видимых вызывающему",
    description=(
        "Возвращает страницу серверов своего отдела. Без роли с `view` на "
        "entity_type=server — 403 PERMISSION_DENIED. Platform-админам "
        "(`account_admin`/`loging_admin`) сюда вход запрещён middleware'ом — "
        "403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED."
    ),
    response_description="Страница серверов + total/limit/offset",
    responses={
        401: {"description": "Нет/невалидный bearer-токен."},
        403: {"description": "Нет роли с `view` на server, либо department не имеет доступа к server_service, либо platform-админ заблокирован."},
    },
)
async def list_servers(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[ServerResponse]:
    """
    Что делает: возвращает страницу серверов из таблицы `servers`, упорядоченных
    по `created_at DESC`. Результат всегда отфильтрован по
    `identity.department_id` (изоляция отделов).

    Доступ:
      - `(server, *, view)` — обязателен для всех ролей.
      - `department_admin` своего отдела + сервисные роли `reader`/`operator`/`admin` своего отдела.
      - Пользователь без `department_id` — пустой список.
      - `account_admin`/`loging_admin` отбиваются 403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED middleware'ом.

    Возможные ошибки:
      - 401 `ACCESS_TOKEN_MISSING` / `ACCESS_TOKEN_INVALID` / `USER_BANNED`.
      - 403 `SERVICE_ACCESS_DENIED` (department без доступа к server_service).
      - 403 `PERMISSION_DENIED` (роль без `view`).
      - 403 `PLATFORM_ADMIN_BUSINESS_DATA_DENIED` (platform-админ).

    Связано: `services/server.py::list_servers`, `repositories/server.py::list_in_departments`.
    """
    items, total = await svc.list_servers(db, identity, limit=limit, offset=offset)
    cards = [
        ServerResponse.from_server(i, await svc.load_storage(db, i.id))
        for i in items
    ]
    return PaginatedResponse[ServerResponse](
        items=cards,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=ServerResponse,
    status_code=201,
    summary="Зарегистрировать новый сервер",
    description=(
        "Создаёт карточку сервера. `department_id` обязан совпадать со своим, "
        "иначе 403 DEPARTMENT_ISOLATION. Конфликт по "
        "`hostname`/`ip_address`/`serial_number` → 409 SERVER_DUPLICATE. "
        "Опциональный блок `ipmi` создаёт BMC-контроллер атомарно вместе с "
        "сервером — дальше им управляют через /servers/{id}/ipmi-эндпоинты."
    ),
    response_description="Созданная карточка сервера.",
    responses={
        201: {"description": "Сервер создан."},
        403: {"description": "Нет `create` либо попытка создать сервер в чужом отделе."},
        409: {"description": "Конфликт по hostname/ip/serial_number."},
    },
)
async def create_server(
    body: ServerCreate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    """
    Что делает: вставляет новую строку в `servers`, выставляя `created_by` из
    identity. ID генерится сервером (`srv_<uuid>`), `created_at`/`updated_at`
    проставляет БД.

    Доступ:
      - `(server, *, create)` — обязателен.
      - `department_id` в теле должен совпадать со своим (иначе 403).
      - Platform-админы блокируются 403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED middleware'ом.

    Возможные ошибки:
      - 403 `DEPARTMENT_ISOLATION` — попытка создать сервер в чужом отделе.
      - 403 `PERMISSION_DENIED` — нет роли с `create`.
      - 409 `SERVER_DUPLICATE` — UNIQUE-конфликт по hostname/ip/serial_number.

    Аудит: `server.create` (success/denied/failure).
    """
    obj = await svc.create_server(db, identity, body)
    return ServerResponse.from_server(obj, await svc.load_storage(db, obj.id))


@router.get(
    "/{server_id}",
    response_model=ServerResponse,
    summary="Получить карточку сервера",
    description=(
        "Возвращает полную карточку сервера. Cross-department доступ скрыт "
        "за 404 (а не 403), чтобы не утечь сам факт существования чужого "
        "сервера. Platform-админы блокируются 403 ещё в middleware."
    ),
    response_description="Карточка сервера.",
    responses={
        404: {"description": "Сервер не найден либо принадлежит чужому department."},
    },
)
async def get_server(
    server_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    """
    Что делает: SELECT по PK + visibility-check (department-isolation).

    Доступ:
      - `(server, *, view)` — обязателен.
      - Видны только сервера своего department.
      - Чужой department → 404 (не 403, чтобы не выдавать существование).
      - `account_admin`/`loging_admin` сюда не доходят — middleware 403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED.

    Возможные ошибки:
      - 403 `PERMISSION_DENIED`.
      - 404 `SERVER_NOT_FOUND`.

    Аудит на denied: `server.view` со `status=denied`, причина —
    `permission_denied` (нет роли с VIEW), `not_found_or_cross_dept`
    (несуществующий или чужой сервер) либо `cross_department` (объект
    есть, но dept не совпал).
    """
    obj = await svc.get_server(db, identity, server_id)
    return ServerResponse.from_server(obj, await svc.load_storage(db, obj.id))


@router.patch(
    "/{server_id}",
    response_model=ServerResponse,
    summary="Обновить карточку сервера",
    description=(
        "Частичное обновление через `model_dump(exclude_unset=True)`. "
        "Все поля опциональны — присылается только то, что меняется. "
        "Конфликт по unique-полям (hostname/ip/serial_number) → 409."
    ),
    response_description="Обновлённая карточка.",
    responses={
        404: {"description": "Сервер не найден или чужой department."},
        409: {"description": "UNIQUE-конфликт по hostname/ip/serial_number."},
    },
)
async def update_server(
    server_id: str,
    body: ServerUpdate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    """
    Что делает: PATCH-диф из `body.model_dump(exclude_unset=True)`. Пустой
    диф → возвращает текущий объект без UPDATE.

    Доступ:
      - `(server, *, update)`.
      - Department-isolation как в GET.

    Возможные ошибки: 403 `PERMISSION_DENIED`, 404 `SERVER_NOT_FOUND`,
    409 `SERVER_DUPLICATE`.

    Аудит: `server.update` (success/denied/failure).
    """
    obj = await svc.update_server(db, identity, server_id, body)
    return ServerResponse.from_server(obj, await svc.load_storage(db, obj.id))


@router.delete(
    "/{server_id}",
    response_model=OkResponse,
    summary="Удалить сервер (требует delete в матрице)",
    description=(
        "Жёсткое удаление строки + каскад на `server_accounts`, "
        "`ipmi_controllers`, `server_disks` (ondelete=CASCADE). "
        "Восстановить нельзя — для soft-delete используется "
        "`ServerStatus.DECOMMISSIONED`. Только роль с `delete` в матрице."
    ),
    response_description="OK.",
    responses={
        403: {"description": "Нет роли с `delete`."},
        404: {"description": "Сервер не найден или чужой department."},
    },
)
async def delete_server(
    server_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """
    Что делает: hard-delete с CASCADE на дочерние записи.

    Доступ:
      - `(server, *, delete)`.
      - Department-isolation.

    Аудит: `server.delete` (CRITICAL severity).
    """
    await svc.delete_server(db, identity, server_id)
    return OkResponse()


# ── Busy-lease ───────────────────────────────────────────────────────────────


@router.post(
    "/{server_id}/busy",
    response_model=ServerResponse,
    summary="Захватить сервер (busy_state: free → busy) под тест/задачу",
    description=(
        "Атомарный CAS-UPDATE по busy_state: проходит только если сервер "
        "сейчас `free`. Конкурентный acquire (две параллельные попытки) → "
        "одна возвращает 200 с обновлённой карточкой, вторая 409 "
        "`SERVER_ALREADY_BUSY` без переписывания первой. "
        "Decommissioned-сервера отбиваются 409 `SERVER_DECOMMISSIONED`."
    ),
    responses={
        200: {"description": "Сервер захвачен, busy_user_id выставлен в caller'а."},
        403: {"description": "Нет роли с `busy_acquire`."},
        404: {"description": "Сервер не найден / чужой dept."},
        409: {"description": "SERVER_ALREADY_BUSY / SERVER_DECOMMISSIONED."},
    },
)
async def acquire_server(
    server_id: str,
    identity: CurrentIdentity,
    body: ServerAcquireRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    """
    Что делает: атомарным `UPDATE ... WHERE busy_state='free'` помечает сервер
    занятым: `busy_state=busy`, `busy_user_id=identity.user_id`,
    `busy_since=now()`, `busy_note` (формируется из purpose / lease_until).

    Доступ: `(server, *, busy_acquire)`. Department-isolation как в GET.

    Возможные ошибки: 403 `PERMISSION_DENIED`, 404 `SERVER_NOT_FOUND`,
    409 `SERVER_ALREADY_BUSY`, 409 `SERVER_DECOMMISSIONED`.

    Аудит: `server.acquire` (success/denied/failure).
    """
    payload = body if body is not None else ServerAcquireRequest()
    obj = await svc.acquire_server(db, identity, server_id, payload)
    return ServerResponse.from_server(obj, await svc.load_storage(db, obj.id))


@router.delete(
    "/{server_id}/busy",
    response_model=ServerResponse,
    summary="Освободить сервер (busy_state → free)",
    description=(
        "Сбрасывает `busy_state` в free, обнуляет `busy_user_id`/`busy_since`/"
        "`busy_note`. Освобождать может любая роль с `busy_release` — обычно "
        "тот же caller, что захватывал, либо admin отдела. Уже свободный "
        "сервер → 409 `SERVER_NOT_BUSY` (сигнал рассинхрона у клиента)."
    ),
    responses={
        200: {"description": "Сервер освобождён."},
        403: {"description": "Нет роли с `busy_release`."},
        404: {"description": "Сервер не найден / чужой dept."},
        409: {"description": "SERVER_NOT_BUSY — сервер уже free."},
    },
)
async def release_server(
    server_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    """
    Что делает: атомарный `UPDATE ... WHERE busy_state != 'free'` → выставляет
    free + чистит busy_* поля.

    Доступ: `(server, *, busy_release)`. Department-isolation.

    Возможные ошибки: 403 `PERMISSION_DENIED`, 404 `SERVER_NOT_FOUND`,
    409 `SERVER_NOT_BUSY`.

    Аудит: `server.release` (success/denied/failure).
    """
    obj = await svc.release_server(db, identity, server_id)
    return ServerResponse.from_server(obj, await svc.load_storage(db, obj.id))


# ── Ручной OS-sync (без inventory probe) ────────────────────────────────────


@router.post(
    "/{server_id}/os-sync",
    response_model=ServerResponse,
    summary="Сменить os_version_id вручную (без inventory probe)",
    description=(
        "Прямое выставление `servers.os_version_id` + `os_last_synced_at=now()`. "
        "Полезно admin/operator'у когда железо переустановили без worker'а или "
        "нужен быстрый ручной фикс. Inventory-probe (`POST /inventory/probe` → "
        "worker) — отдельный flow. `os_version_id=null` сбрасывает версию. "
        "Невалидный id → 422 `INVALID_OS_VERSION`."
    ),
    responses={
        200: {"description": "os_version_id обновлён."},
        403: {"description": "Нет роли с `os_sync`."},
        404: {"description": "Сервер не найден / чужой dept."},
        422: {"description": "INVALID_OS_VERSION — нет такой строки в os_versions."},
    },
)
async def update_os_version(
    server_id: str,
    body: ServerOsVersionUpdate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    """
    Что делает: UPDATE `servers.os_version_id` + `os_last_synced_at = now()`.

    Доступ: `(server, *, os_sync)`. Department-isolation.

    Возможные ошибки: 403 `PERMISSION_DENIED`, 404 `SERVER_NOT_FOUND`,
    422 `INVALID_OS_VERSION` (FK violation).

    Аудит: `server.update_os_version` (success/denied/failure).
    """
    obj = await svc.update_os_version(db, identity, server_id, body)
    return ServerResponse.from_server(obj, await svc.load_storage(db, obj.id))
