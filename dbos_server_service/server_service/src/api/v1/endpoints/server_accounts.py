"""CRUD аккаунтов сервера + rotate_password + M2M-линковка серверов.

Аккаунт привязывается к набору серверов (`server_ids`); пароль общий на все.
GET карточки доступен по `view` или `view_password`. Держателю action
`view_password` тот же GET доносит расшифрованный пароль в `password_b64`;
остальным поле приходит `null`. Отдельной reveal-ручки нет. Расшифрованный
пароль для worker'а параллельно по-прежнему отдаётся через
`internal/.../password` (см. `endpoints/internal.py`).

Линковка/отвязка серверов у существующего аккаунта — под-операции
`POST/DELETE /server-accounts/{id}/servers` (гейтятся `update`).
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.v1.endpoints.worker_dispatch import fanout_update_on_host
from src.core.config import get_settings
from src.core.limiter import endpoint_limiter, per_account_key
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.models import ServerAccount
from src.repositories import server_account as account_repo
from src.schemas.common import CursorPaginatedResponse, OkResponse, PaginatedResponse
from src.schemas.server_account import (
    IgnoredLoginCreate,
    IgnoredLoginResponse,
    ServerAccountAdoptRequest,
    ServerAccountCreate,
    ServerAccountImportRequest,
    ServerAccountResponse,
    ServerAccountRotateRequest,
    ServerAccountRotateResponse,
    ServerAccountServersUpdate,
    ServerAccountUpdate,
)
from src.services import server_account as svc

router = APIRouter(prefix="/server-accounts")


def _to_response(obj: ServerAccount, password_b64: str | None = None) -> ServerAccountResponse:
    """Собрать карточку аккаунта из ORM-объекта + список привязанных серверов."""
    resp = ServerAccountResponse(
        id=obj.id,
        server_ids=account_repo.linked_server_ids(obj),
        department_id=obj.department_id,
        login=obj.login,
        source=obj.source,
        has_sudo=obj.has_sudo,
        unix_groups=list(obj.unix_groups),
        linked_user_id=obj.linked_user_id,
        shell=obj.shell,
        home_dir=obj.home_dir,
        is_active=obj.is_active,
        password_rotated_at=obj.password_rotated_at,
        password_b64=password_b64,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
        created_by=obj.created_by,
    )
    return resp


@router.post(
    "",
    response_model=ServerAccountResponse,
    status_code=201,
    summary="Создать аккаунт сразу на нескольких серверах (пароль шифруется at-rest)",
    description=(
        "Заводит OS-аккаунт и привязывает его к списку серверов `server_ids` "
        "(≥1, все в одном department'е). Пароль общий на все серверы — либо "
        "передаётся в body как `password_b64` (`base64.b64encode(plaintext)`), "
        "либо генерируется сервером (`secrets.token_urlsafe(32)`). Если "
        "передан — декодируется и проходит политику по plaintext; в любом "
        "случае шифруется через `secrets_service.encrypt()` и в ответ не "
        "возвращается. `has_sudo=True` требует action `grant_sudo`."
    ),
    responses={
        201: {"description": "Аккаунт создан."},
        403: {"description": "Нет роли с `create` (или `grant_sudo` при has_sudo=True), либо чужой department."},
        404: {"description": "Один из серверов не найден или принадлежит чужому department."},
        409: {"description": "Конфликт по (server_id, login) на одном из серверов."},
    },
)
async def create_account(
    body: ServerAccountCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Create-эндпоинт. Доступ: `(server_account, *, create)` (+ `grant_sudo` опц.)."""
    obj = await svc.create_account(db, identity, body)
    return _to_response(obj)


@router.post(
    "/import",
    response_model=ServerAccountResponse,
    status_code=201,
    summary="Импортировать незнакомого OS-пользователя с бокса в БД",
    description=(
        "Заводит аккаунт из атрибутов, найденных инвентаризацией (элемент "
        "`unknown_users` ответа коллбэка): `{server_id, login, has_sudo, "
        "unix_groups, shell, source}`. Пароль НЕ задаётся — на боксе он "
        "неизвестен; по умолчанию `source=discovered` (password_encrypted "
        "NULL). Аккаунт привязывается к `server_id` и помечается "
        "`present_on_server=True` (пользователь уже на боксе). Гейтится "
        "`create`; `has_sudo=True` дополнительно требует `grant_sudo`. Аудит: "
        "`server_account.imported_from_host` (WARNING)."
    ),
    responses={
        201: {"description": "Аккаунт импортирован."},
        403: {"description": "Нет `create` (или `grant_sudo` при has_sudo=True), либо чужой department."},
        404: {"description": "Сервер не найден или чужой department."},
        409: {"description": "ACCOUNT_DUPLICATE — login уже занят на этом сервере."},
    },
)
async def import_account(
    body: ServerAccountImportRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Import-эндпоинт. Доступ: `(server_account, *, create)` (+ `grant_sudo` опц.)."""
    obj = await svc.import_from_host(db, identity, body)
    return _to_response(obj)


@router.get(
    "/ignored-logins",
    response_model=list[IgnoredLoginResponse],
    summary="Список игнор-логинов отдела",
    description=(
        "Возвращает логины, которые инвентаризация не показывает как "
        "незнакомых пользователей, в отделе вызывающего (dept-изоляция). "
        "Гейтится `(server_account, manage_ignored_logins)`."
    ),
    responses={
        200: {"description": "Список игнор-логинов отдела."},
        403: {"description": "Нет `manage_ignored_logins`."},
    },
)
async def list_ignored_logins(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[IgnoredLoginResponse]:
    """List ignore-list. Доступ: `(server_account, *, manage_ignored_logins)`."""
    items = await svc.list_ignored_logins(db, identity)
    return [IgnoredLoginResponse.model_validate(i) for i in items]


@router.post(
    "/ignored-logins",
    response_model=IgnoredLoginResponse,
    status_code=201,
    summary="Заигнорить логин в отделе",
    description=(
        "Добавляет логин в ignore-list отдела: инвентаризация перестаёт "
        "показывать его как незнакомого пользователя (не в `unknown_users`, "
        "не дрейфит). Тело: `{login, reason?}`. UNIQUE(department, login) — "
        "повторный игнор → 409. Гейтится `manage_ignored_logins`. Аудит: "
        "`server_account.ignored_login_added` (WARNING)."
    ),
    responses={
        201: {"description": "Логин заигнорен."},
        403: {"description": "Нет `manage_ignored_logins`."},
        409: {"description": "IGNORED_LOGIN_DUPLICATE — логин уже в игноре."},
    },
)
async def add_ignored_login(
    body: IgnoredLoginCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> IgnoredLoginResponse:
    """Add ignore-list. Доступ: `(server_account, *, manage_ignored_logins)`."""
    obj = await svc.add_ignored_login(db, identity, body)
    return IgnoredLoginResponse.model_validate(obj)


@router.delete(
    "/ignored-logins/{login}",
    response_model=OkResponse,
    summary="Снять игнор с логина в отделе",
    description=(
        "Удаляет логин из ignore-list отдела. Если логина нет в списке — 404. "
        "Гейтится `manage_ignored_logins`. Аудит: "
        "`server_account.ignored_login_removed` (INFO)."
    ),
    responses={
        200: {"description": "Игнор снят."},
        403: {"description": "Нет `manage_ignored_logins`."},
        404: {"description": "IGNORED_LOGIN_NOT_FOUND — логина нет в списке."},
    },
)
async def remove_ignored_login(
    login: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Remove ignore-list. Доступ: `(server_account, *, manage_ignored_logins)`."""
    await svc.remove_ignored_login(db, identity, login)
    return OkResponse()


@router.get(
    "",
    response_model=(
        PaginatedResponse[ServerAccountResponse]
        | CursorPaginatedResponse[ServerAccountResponse]
    ),
    summary="Список аккаунтов, привязанных к серверу",
    description=(
        "Принимает `server_id` query-параметром. Возвращает страницу аккаунтов, "
        "привязанных к этому серверу, отсортированных по `created_at DESC`. "
        "Cross-dept сервер скрыт за 404.\n\n"
        "Два режима пагинации: cursor (рекомендуемый — `cursor=true` или "
        "`after=<token>`, envelope `{items, next_cursor, has_more}`) и legacy "
        "offset/limit (envelope `{items, total, limit, offset}`)."
    ),
    responses={
        200: {"description": "Страница аккаунтов."},
        400: {"description": "INVALID_CURSOR — `after` не декодируется."},
        403: {"description": "Нет роли с `view`."},
        404: {"description": "Сервер не найден / чужой dept."},
    },
)
async def list_accounts(
    identity: CurrentUserIdentity,
    server_id: str = Query(..., description="ID сервера, чьи аккаунты выбрать."),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, description="DEPRECATED — используйте cursor-пагинацию."),
    after: str | None = Query(default=None, description="Opaque cursor предыдущей страницы."),
    cursor: bool = Query(default=False, description="Включить cursor-envelope."),
) -> PaginatedResponse[ServerAccountResponse] | CursorPaginatedResponse[ServerAccountResponse]:
    """List-эндпоинт. Доступ: `(server_account, *, view)`."""
    if cursor or after is not None:
        from src.utils.cursor import InvalidCursorError, to_bad_request
        try:
            items, next_cursor, has_more = await svc.list_accounts_cursor(
                db, identity, server_id, limit=limit, after=after,
            )
        except InvalidCursorError as exc:
            raise to_bad_request(exc) from exc
        return CursorPaginatedResponse[ServerAccountResponse](
            items=[_to_response(i) for i in items],
            next_cursor=next_cursor,
            has_more=has_more,
        )
    items, total = await svc.list_accounts(
        db, identity, server_id, limit=limit, offset=offset
    )
    return PaginatedResponse[ServerAccountResponse](
        items=[_to_response(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{account_id}",
    response_model=ServerAccountResponse,
    summary="Получить карточку аккаунта (с паролем при наличии view_password)",
    description=(
        "Карточка доступна по `view` или `view_password`. Если у вызывающего "
        "есть `view_password`, поле `password_b64` несёт base64(plaintext); "
        "иначе оно `null`. Сырого `password_encrypted` в ответе нет никогда. "
        "Раскрытие пароля пишет CRITICAL audit `server_account.password_revealed`.\n\n"
        "Per-IP+account rate-limit `PASSWORD_REVEAL_RATE_LIMIT` (default 10/min) "
        "поверх глобального, чтобы plaintext-канал нельзя было скрапить даже "
        "до триггера CRITICAL-аудита."
    ),
    responses={
        403: {"description": "Нет роли ни с `view`, ни с `view_password`."},
        404: {"description": "Аккаунт не найден или чужой dept (скрыто за 404)."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP+account reveal-rate-limit пробит."},
        500: {"description": "DECRYPT_FAILED — сломанный ciphertext (только при view_password)."},
    },
)
@endpoint_limiter.limit(
    get_settings().password_reveal_rate_limit, key_func=per_account_key,
)
async def get_account(
    request: Request,
    account_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Get-эндпоинт. Доступ: `view` или `view_password`; пароль — при `view_password`."""
    obj, password_b64 = await svc.get_account(db, identity, account_id)
    return _to_response(obj, password_b64)


# Поля, которые worker применяет на боксе через usermod — их правка
# триггерит fan-out `update_on_host` на привязанные серверы. `home_dir`/
# `linked_user_id` остаются только в БД: modify_user на воркере usermod'ит
# группы/sudo/shell, дом-каталог и метаданные не двигает.
_OS_MANAGED_FIELDS = {"has_sudo", "unix_groups", "shell"}


@router.patch(
    "/{account_id}",
    response_model=ServerAccountResponse,
    summary="Обновить поля аккаунта (без пароля и привязок)",
    description=(
        "Частичное обновление (PATCH). Смена пароля — отдельный endpoint "
        "`/rotate_password`. Привязка/отвязка серверов — `/servers`. Подъём "
        "`has_sudo=False → True` требует action `grant_sudo` (admin-only). "
        "Снятие sudo допустимо обычным `update`. При изменении OS-управляемых "
        "атрибутов (`has_sudo`/`unix_groups`/`shell`) правка рассылается "
        "`update_on_host` на все серверы, где аккаунт присутствует."
    ),
    responses={
        403: {"description": "Нет `update` (или `grant_sudo` при подъёме has_sudo)."},
        404: {"description": "Аккаунт не найден / чужой dept."},
    },
)
async def update_account(
    account_id: str,
    body: ServerAccountUpdate,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Update-эндпоинт. Доступ: `(server_account, *, update)`.

    Если PATCH задел OS-управляемые атрибуты (`has_sudo`/`unix_groups`/`shell`),
    после сохранения рассылаем `account.update_on_host` на все серверы, где
    аккаунт присутствует — синк правки на боксы (см. `fanout_update_on_host`).
    """
    # `applied_fields` — то, что реально изменилось (диф против актуального
    # состояния). Без него no-op PATCH (`{has_sudo: True}` на уже-True аккаунт)
    # запустил бы fanout `update_on_host` на N серверов — лишний шум в audit
    # и worker-нагрузка.
    obj, applied_fields = await svc.update_account(db, identity, account_id, body)
    if applied_fields & _OS_MANAGED_FIELDS:
        await fanout_update_on_host(
            db=db, identity=identity, request=request, account=obj,
        )
    return _to_response(obj)


@router.post(
    "/{account_id}/adopt_from_host",
    response_model=ServerAccountResponse,
    summary="Принять факт-состояние OS-пользователя с хоста в БД (без fan-out)",
    description=(
        "Оператор-инициируемое пополевное принятие drift'а: применяет в БД "
        "только переданные `has_sudo`/`unix_groups`/`shell` (значения = "
        "`found` из diff'а инвентаризации). Обновляет ТОЛЬКО БД — в отличие "
        "от PATCH, fan-out `update_on_host` на серверы НЕ идёт: хост уже в "
        "этом состоянии, а push разнёс бы drift одного сервера на остальные "
        "привязки. `server_id` обязан быть привязан к аккаунту. Доступ: "
        "`(server_account, adopt_from_host)` (operator/admin). Аудит: "
        "`server_account.adopted_from_host` (WARNING)."
    ),
    responses={
        403: {"description": "Нет action `adopt_from_host`."},
        404: {"description": "Аккаунт не найден / чужой dept / сервер не привязан."},
        422: {"description": "NO_FIELDS_TO_ADOPT — ни одно поле не передано, либо невалидные unix_groups."},
    },
)
async def adopt_from_host(
    account_id: str,
    body: ServerAccountAdoptRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Adopt-эндпоинт. Доступ: `(server_account, *, adopt_from_host)`.

    DB-only: fan-out на серверы намеренно не запускается (см. service-докстринг
    `adopt_from_host`).
    """
    obj = await svc.adopt_from_host(db, identity, account_id, body)
    return _to_response(obj)


@router.post(
    "/{account_id}/servers",
    response_model=ServerAccountResponse,
    summary="Привязать аккаунт к дополнительным серверам",
    description=(
        "Добавляет связки аккаунт ↔ сервер. Все новые серверы обязаны быть в "
        "том же department'е, что и аккаунт (cross-dept → 404). Уже "
        "привязанные серверы игнорируются (идемпотентно). Если login занят на "
        "одном из серверов другим аккаунтом — 409. Гейтится `update`."
    ),
    responses={
        403: {"description": "Нет `update`."},
        404: {"description": "Аккаунт или один из серверов не найден / чужой dept."},
        409: {"description": "Login занят на одном из серверов."},
    },
)
async def link_servers(
    account_id: str,
    body: ServerAccountServersUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Линковка серверов. Доступ: `(server_account, *, update)`."""
    obj = await svc.link_servers(db, identity, account_id, body)
    return _to_response(obj)


@router.delete(
    "/{account_id}/servers",
    response_model=ServerAccountResponse,
    summary="Отвязать аккаунт от серверов",
    description=(
        "Снимает связки аккаунт ↔ сервер. Нельзя отвязать последний сервер — "
        "аккаунт должен жить хотя бы на одном (иначе 409 ACCOUNT_NO_SERVERS). "
        "На реальном сервере OS-аккаунт не удаляется. Гейтится `update`."
    ),
    responses={
        403: {"description": "Нет `update`."},
        404: {"description": "Аккаунт не найден / чужой dept."},
        409: {"description": "ACCOUNT_NO_SERVERS — попытка снять последнюю связку."},
    },
)
async def unlink_servers(
    account_id: str,
    body: ServerAccountServersUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Отвязка серверов. Доступ: `(server_account, *, update)`."""
    obj = await svc.unlink_servers(db, identity, account_id, body)
    return _to_response(obj)


@router.delete(
    "/{account_id}",
    response_model=OkResponse,
    summary="Удалить аккаунт сервера",
    description=(
        "Hard-delete строки в БД (связки уходят каскадом). На реальных серверах "
        "OS-аккаунт не удаляется — для этого нужен отдельный worker pipeline. "
        "Требует роль с `delete`."
    ),
    responses={
        403: {"description": "Нет `delete`."},
        404: {"description": "Аккаунт не найден / чужой dept."},
    },
)
async def delete_account(
    account_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete-эндпоинт. Доступ: `(server_account, *, delete)`."""
    await svc.delete_account(db, identity, account_id)
    return OkResponse()


@router.post(
    "/{account_id}/rotate_password",
    response_model=ServerAccountRotateResponse,
    summary="Ротация общего пароля (user-initiated, без SSH-apply)",
    description=(
        "Принимает опциональный `password_b64` (`base64.b64encode(plaintext)`) "
        "в body. Если передан — декодируется, проходит политику по plaintext "
        "(минимум 8 символов, буквы и цифры) и сохраняется; если "
        "нет — генерит новый через `secrets.token_urlsafe(32)`. Меняет только "
        "общий ciphertext в БД (без apply'я на серверы). Apply на конкретный "
        "сервер или на все привязанные — через worker-dispatch `/rotate`. "
        "Plaintext в ответ НЕ возвращается. Per-IP rate-limit см. "
        "`ACCOUNT_ROTATE_PASSWORD_RATE_LIMIT` (10/мин по умолчанию)."
    ),
    responses={
        403: {"description": "Нет `rotate_password`."},
        404: {"description": "Аккаунт не найден / чужой dept."},
        422: {"description": "Переданный пароль не проходит политику."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP rotate-rate-limit пробит."},
    },
)
@endpoint_limiter.limit(get_settings().account_rotate_password_rate_limit)
async def rotate_password(
    request: Request,
    account_id: str,
    identity: CurrentUserIdentity,
    body: ServerAccountRotateRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountRotateResponse:
    """Rotate-эндпоинт. Доступ: `(server_account, *, rotate_password)`. Аудит — CRITICAL."""
    new_password = body.password() if body is not None else None
    obj = await svc.rotate_password(db, identity, account_id, new_password)
    return ServerAccountRotateResponse(
        id=obj.id,
        login=obj.login,
        rotated_at=obj.password_rotated_at,
    )
