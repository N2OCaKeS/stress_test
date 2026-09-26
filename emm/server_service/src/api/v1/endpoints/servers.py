"""CRUD-эндпоинты серверов + busy-lease (acquire/release) + ручной os-sync."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.limiter import endpoint_limiter, per_account_key
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import CursorPaginatedResponse, OkResponse, PaginatedResponse
from src.schemas.drift import DriftEventItem, ServerDriftResponse
from src.schemas.server import (
    ServerAcquireRequest,
    ServerCreate,
    ServerOsVersionUpdate,
    ServerResponse,
    ServerTestCredentialsResponse,
    ServerUpdate,
)
from src.services import prepare_for_test as pft_svc
from src.services import server as svc

router = APIRouter(prefix="/servers")


@router.get(
    "",
    response_model=(
        PaginatedResponse[ServerResponse]
        | CursorPaginatedResponse[ServerResponse]
    ),
    summary="Список серверов, видимых вызывающему",
    description=(
        "Возвращает страницу серверов своего отдела, упорядоченных по "
        "`created_at DESC`. Поддерживает два режима пагинации:\n\n"
        "* **Cursor (рекомендуемый):** `?after=<cursor>&limit=<N>`. "
        "Envelope `{items, next_cursor, has_more}`. `next_cursor` подставляется "
        "в следующий запрос; `null` означает конец. Битый/чужой `after` → 400.\n"
        "* **Offset (deprecated):** `?limit=<N>&offset=<M>`. Envelope "
        "`{items, total, limit, offset}`. Оставлен для совместимости; новые "
        "интеграции должны использовать cursor — он стабильнее на вставках "
        "в начало и не платит за глубокий offset.\n\n"
        "Без роли с `view` на `entity_type=server` — 403 PERMISSION_DENIED. "
        "Platform-админам (`account_admin`/`loging_admin`) вход запрещён "
        "middleware'ом — 403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED."
    ),
    response_description="Страница серверов в одном из двух envelope'ов.",
    responses={
        200: {"description": "Страница серверов."},
        400: {"description": "INVALID_CURSOR — `after` не декодируется."},
        401: {"description": "Нет/невалидный bearer-токен."},
        403: {"description": "Нет роли с `view` на server, либо department не имеет доступа к server_service, либо platform-админ заблокирован."},
    },
)
async def list_servers(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, description="DEPRECATED — используйте cursor-пагинацию (`after` + `cursor=true`)."),
    after: str | None = Query(default=None, description="Opaque cursor предыдущей страницы; вместе с `cursor=true` переводит endpoint в keyset-режим."),
    cursor: bool = Query(default=False, description="Включить cursor-envelope (даже для первой страницы — `after` опустить)."),
) -> PaginatedResponse[ServerResponse] | CursorPaginatedResponse[ServerResponse]:
    """
    Что делает: возвращает страницу серверов из таблицы `servers`, упорядоченных
    по `created_at DESC, id DESC`. Результат всегда отфильтрован по
    `identity.department_id` (изоляция отделов).

    Доступ:
      - `(server, *, view)` — обязателен для всех ролей.
      - `department_admin` своего отдела + сервисные роли (`admin` либо кастомная с `view`) своего отдела.
      - Пользователь без `department_id` — пустой список.
      - `account_admin`/`loging_admin` отбиваются 403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED middleware'ом.

    Возможные ошибки:
      - 400 `INVALID_CURSOR` (битый `after`).
      - 401 `ACCESS_TOKEN_MISSING` / `ACCESS_TOKEN_INVALID` / `USER_BANNED`.
      - 403 `SERVICE_ACCESS_DENIED` (department без доступа к server_service).
      - 403 `PERMISSION_DENIED` (роль без `view`).
      - 403 `PLATFORM_ADMIN_BUSINESS_DATA_DENIED` (platform-админ).
    """
    # Cursor-режим включается явным `cursor=true` или подачей `after`. Без них
    # endpoint остаётся в legacy offset/limit envelope'е — старые клиенты,
    # которые шлют `?limit=X&offset=Y`, продолжают работать без изменений.
    if cursor or after is not None:
        from src.utils.cursor import InvalidCursorError, to_bad_request
        try:
            items, next_cursor, has_more = await svc.list_servers_cursor(
                db, identity, limit=limit, after=after,
            )
        except InvalidCursorError as exc:
            raise to_bad_request(exc) from exc
        # Bulk-load дисков одним SELECT — раньше шёл per-row `load_storage`
        # (до limit+1 SELECT'ов на /servers). См. server_disk_repo.list_for_servers.
        storages = await svc.load_storage_for_servers(db, [i.id for i in items])
        cards = [
            ServerResponse.from_server(i, storages.get(i.id, []))
            for i in items
        ]
        return CursorPaginatedResponse[ServerResponse](
            items=cards,
            next_cursor=next_cursor,
            has_more=has_more,
        )
    items, total = await svc.list_servers(db, identity, limit=limit, offset=offset)
    storages = await svc.load_storage_for_servers(db, [i.id for i in items])
    cards = [
        ServerResponse.from_server(i, storages.get(i.id, []))
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
    identity: CurrentUserIdentity,
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
    "/by-number/{number}",
    response_model=ServerResponse,
    summary="Получить сервер по номеру стенда",
    description=(
        "Номер уникален в рамках department_id, lookup идёт в отделе "
        "caller'а. Cross-department доступ и отсутствие скрыты за 404, как "
        "у GET /servers/{id}."
    ),
    responses={404: {"description": "Сервер не найден / чужой department."}},
)
@router.get(
    "/by-stand-number/{number}",
    response_model=ServerResponse,
    summary="Получить сервер по номеру стенда",
    description=(
        "Синоним GET /servers/by-number/{number} — стабильный путь для "
        "внешних интеграций (например allta_app_service). Номер уникален в "
        "рамках department_id, lookup идёт в отделе caller'а. "
        "Cross-department доступ и отсутствие скрыты за 404."
    ),
    responses={404: {"description": "Сервер не найден / чужой department."}},
)
async def get_server_by_number(
    number: int,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    """GET /servers/by-number/{number} и /servers/by-stand-number/{number} — lookup по номеру."""
    obj = await svc.get_server_by_number(db, identity, number)
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
    identity: CurrentUserIdentity,
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

    Аудит: `server.view` пишется на каждый вызов — `status=success` при
    успешном чтении и `status=denied` при отказе (причина —
    `permission_denied` нет роли с VIEW, либо `not_found_or_cross_dept`
    несуществующий / чужой сервер).
    """
    obj = await svc.get_server(db, identity, server_id)
    return ServerResponse.from_server(obj, await svc.load_storage(db, obj.id))


@router.get(
    "/{server_id}/test-credentials",
    response_model=ServerTestCredentialsResponse,
    summary="Учётка исполнения теста стенда (живая отладка)",
    description=(
        "Без `?reveal=true` — только метаданные (`exists`/`username`/"
        "`ssh_public_key`/`rotated_at`). С `?reveal=true` добавляет "
        "`password_b64`/`ssh_private_key_b64` (`base64.b64encode(plaintext)`) — "
        "CRITICAL audit `server.test_credentials_revealed`, отдельный от "
        "просто-просмотра карточки.\n\n"
        "Per-IP+server rate-limit `PASSWORD_REVEAL_RATE_LIMIT` поверх "
        "глобального — тот же принцип, что и у раскрытия пароля server_account."
    ),
    responses={
        403: {"description": "Нет `view_test_credentials`."},
        404: {"description": "Сервер не найден или чужой department (скрыто за 404)."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP+server reveal-rate-limit пробит (только при reveal=true)."},
    },
)
@endpoint_limiter.limit(
    get_settings().password_reveal_rate_limit, key_func=per_account_key,
)
async def get_test_credentials(
    request: Request,
    server_id: str,
    identity: CurrentUserIdentity,
    reveal: bool = Query(default=False, description="Раскрыть пароль и приватный ключ."),
    db: AsyncSession = Depends(get_db),
) -> ServerTestCredentialsResponse:
    """Доступ: `(server, *, view_test_credentials)` — единый гейт на метаданные и на секрет."""
    data = await pft_svc.reveal_test_credentials(db, identity, server_id, reveal=reveal)
    return ServerTestCredentialsResponse(**data)


@router.get(
    "/{server_id}/drift",
    response_model=ServerDriftResponse,
    summary="Drift-сводка сервера за окно",
    description=(
        "Агрегирует события `server_account.drift_detected` для конкретного "
        "сервера за окно `[since, now]`. По умолчанию окно — последние 24 часа.\n\n"
        "Drift-события эмитятся `internal/.../users/inventory` (инвентаризация "
        "OS-пользователей) — БД-истина не перетирается, но расхождения "
        "поднимают WARNING для оператора. Endpoint удобен как «что случилось "
        "с этим сервером недавно», без выгрузки логов в SIEM.\n\n"
        "Доступ: `(server, *, view_drift)`. Cross-dept сервер скрыт за 404."
    ),
    response_description="Список drift'ов + флаг переполнения окна.",
    responses={
        403: {"description": "Нет роли с `view_drift`."},
        404: {"description": "Сервер не найден / чужой dept."},
        503: {"description": "LOGING_SERVICE_UNAVAILABLE / LOGING_SERVICE_NOT_CONFIGURED / LOGING_SERVICE_AUTH_FAILED — loging_service недоступен или не сконфигурён."},
    },
)
async def get_server_drift(
    server_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    since: datetime | None = Query(
        default=None,
        description="Начало окна (ISO 8601 UTC). По умолчанию — `now - 24h`.",
    ),
) -> ServerDriftResponse:
    """GET /servers/{id}/drift — собрать drift-сводку из loging."""
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
    elif since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    drifts, truncated = await svc.query_drift_summary(db, identity, server_id, since)
    return ServerDriftResponse(
        server_id=server_id,
        since=since,
        drifts=[DriftEventItem(**item) for item in drifts],
        truncated=truncated,
    )


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
    identity: CurrentUserIdentity,
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
        "Жёсткое удаление строки + каскад на `ipmi_controllers`, "
        "`server_disks` и связки `server_account_servers` (ondelete=CASCADE). "
        "Аккаунты `server_accounts` каскадом НЕ удаляются: M2M-связь "
        "теряет одну строку, сам аккаунт переживает delete, если привязан "
        "ещё к другим серверам. Аккаунт, для которого это был последний "
        "сервер, сносится явно (иначе остался бы orphan'ом без серверов). "
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
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """
    Что делает: hard-delete сервера. CASCADE сносит ipmi/disks/packages и
    связки `server_account_servers`; сами аккаунты переживают, кроме тех,
    для которых это был последний сервер (их service-слой сносит явно).

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
    identity: CurrentUserIdentity,
    body: ServerAcquireRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    """
    Что делает: атомарным `UPDATE ... WHERE busy_state='free'` помечает сервер
    занятым: `busy_state=busy`, `busy_user_id=identity.user_id`,
    `busy_since=now()`, `busy_note` (из необязательного purpose).

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
    identity: CurrentUserIdentity,
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


@router.post(
    "/{server_id}/acknowledge-testing-done",
    response_model=ServerResponse,
    summary="Подтвердить, что стенд принят после «Тестирование завершено» (busy_state → free)",
    description=(
        "Снимает промежуточный статус `testing_done`, который `testing_service` "
        "ставит вместо немедленного `free`, когда очередь стенда опустела. "
        "Доступно любому пользователю с обычным `(server, *, view)` — не "
        "требует `busy_release` и не привязано к роли admin: владелец стенда "
        "должен уметь принять его сам, кто бы это ни был. Вне `testing_done` "
        "(в том числе для обычного `busy`) — 409, это не замена `DELETE "
        "/servers/{id}/busy`."
    ),
    responses={
        200: {"description": "Статус снят, сервер свободен."},
        403: {"description": "Нет `view`."},
        404: {"description": "Сервер не найден / чужой dept."},
        409: {"description": "SERVER_NOT_TESTING_DONE — сервер не в статусе «Тестирование завершено»."},
    },
)
async def acknowledge_testing_done(
    server_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerResponse:
    """
    Что делает: атомарный `UPDATE ... WHERE busy_state='testing_done'` →
    `free`, чистит busy_* поля целиком (как обычный release, а не смена
    стадии — контекст «кто тестировал» дальше не нужен).

    Доступ: `(server, *, view)`. Department-isolation как в GET.

    Возможные ошибки: 403 `PERMISSION_DENIED`, 404 `SERVER_NOT_FOUND`,
    409 `SERVER_NOT_TESTING_DONE`.

    Аудит: `server.acknowledge_testing_done` (success/denied/failure).
    """
    obj = await svc.acknowledge_testing_done(db, identity, server_id)
    return ServerResponse.from_server(obj, await svc.load_storage(db, obj.id))


# ── Ручной OS-sync (без inventory sync) ─────────────────────────────────────


@router.post(
    "/{server_id}/os-sync",
    response_model=ServerResponse,
    summary="Сменить os_version_id вручную (без inventory sync)",
    description=(
        "Прямое выставление `servers.os_version_id` + `os_last_synced_at=now()`. "
        "Полезно носителю права на update сервера, когда железо переустановили без worker'а или "
        "нужен быстрый ручной фикс. Hardware-inventory sync "
        "(`POST /inventory/sync` → worker) — отдельный flow. "
        "`os_version_id=null` сбрасывает версию. "
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
    identity: CurrentUserIdentity,
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
