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

from src.api.v1.endpoints.worker_dispatch import (
    fanout_apply_credentials,
    fanout_update_on_host,
    recreate_login_orchestrate,
)
from src.core.config import get_settings
from src.core.constants import Action
from src.core.limiter import endpoint_limiter, per_account_key
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.models import ServerAccount
from src.repositories import server_account as account_repo
from src.schemas.common import CursorPaginatedResponse, OkResponse, PaginatedResponse
from src.schemas.server_account import (
    AccountApplyCredentialsResponse,
    AccountKeyFanoutResponse,
    AccountRecreateLoginResponse,
    AccountRotateSkipped,
    AccountRotateTask,
    IgnoredLoginCreate,
    IgnoredLoginResponse,
    ServerAccountAdoptRequest,
    ServerAccountCreate,
    ServerAccountImportRequest,
    ServerAccountRecreateLoginRequest,
    ServerAccountResponse,
    ServerAccountRotateRequest,
    ServerAccountRotateResponse,
    ServerAccountServersUpdate,
    ServerAccountSshKeyRequest,
    ServerAccountSshPrivateKeyResponse,
    ServerAccountUpdate,
)
from src.services import server_account as svc

router = APIRouter(prefix="/server-accounts")


def _to_response(
    obj: ServerAccount,
    password_b64: str | None = None,
    ssh_private_key: str | None = None,
    previous_password_b64: str | None = None,
) -> ServerAccountResponse:
    """Собрать карточку аккаунта из ORM-объекта + список привязанных серверов.

    `ssh_private_key` непустой только в ответе create (ssh_mode='generate') —
    приватный ключ отдаётся ровно один раз и в GET-карточке всегда `None`.
    `previous_password_b64` непуст только при `view_password` и активном
    переходном периоде ротации (старый пароль ещё удерживается).
    """
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
        previous_password_b64=previous_password_b64,
        previous_password_rotated_at=obj.previous_password_rotated_at,
        ssh_public_key=obj.ssh_public_key,
        ssh_key_fingerprint=svc.ssh_public_key_fingerprint(obj.ssh_public_key),
        ssh_private_key=ssh_private_key,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
        created_by=obj.created_by,
    )
    return resp


@router.post(
    "",
    response_model=ServerAccountResponse,
    status_code=201,
    summary="Создать аккаунт (опционально сразу на нескольких серверах; пароль шифруется at-rest)",
    description=(
        "Заводит OS-аккаунт и привязывает его к списку серверов `server_ids` "
        "(0 или более, все в одном department'е). Пустой/опущенный список — "
        "аккаунт заводится как хранимый креден без привязок; серверы добавляются "
        "позже через `/server-accounts/{id}/servers`. Пароль общий на все "
        "серверы — либо "
        "передаётся в body как `password_b64` (`base64.b64encode(plaintext)`), "
        "либо генерируется сервером (`secrets.token_urlsafe(32)`). Если "
        "передан — декодируется и проходит политику по plaintext; в любом "
        "случае шифруется через `secrets_service.encrypt()` и в ответ не "
        "возвращается. `has_sudo=True` ИЛИ добавление в `unix_groups` группы "
        "из набора `SUDO_CONFERRING_GROUPS` (sudo/astra-admin/wheel) требует "
        "action `grant_sudo`.\n\n"
        "Опциональный SSH-ключ (`ssh_mode`): `generate` — сервер генерит "
        "Ed25519-пару, хранит public + зашифрованный private и возвращает "
        "приватный ключ ОДИН раз в поле `ssh_private_key` (в GET его уже нет); "
        "`supply` — клиент передаёт `ssh_public_key` и опционально `ssh_private_key_b64` "
        "(приватный в base64; если приложен — шифруется и хранится, тогда консоль "
        "сможет ходить под аккаунтом по ключу; иначе хранится только public). Ключ "
        "раскатается на боксы при provision'е."
    ),
    responses={
        201: {"description": "Аккаунт создан."},
        403: {"description": "Нет роли с `create` (или `grant_sudo` при has_sudo=True / sudo-группе — `SUDO_GROUP_REQUIRES_GRANT_SUDO`), либо чужой department."},
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
    obj, ssh_private_key = await svc.create_account(db, identity, body)
    return _to_response(obj, ssh_private_key=ssh_private_key)


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
    summary="Список аккаунтов отдела (или привязанных к одному серверу)",
    description=(
        "Без `server_id` возвращает все аккаунты отдела вызывающего, включая "
        "не привязанные ни к одному серверу (хранимые кредены с 0 связок). С "
        "`server_id` — страница аккаунтов, привязанных к этому серверу "
        "(cross-dept сервер скрыт за 404). В обоих режимах сортировка по "
        "`created_at DESC`.\n\n"
        "Два режима пагинации: cursor (рекомендуемый — `cursor=true` или "
        "`after=<token>`, envelope `{items, next_cursor, has_more}`) и legacy "
        "offset/limit (envelope `{items, total, limit, offset}`)."
    ),
    responses={
        200: {"description": "Страница аккаунтов."},
        400: {"description": "INVALID_CURSOR — `after` не декодируется."},
        403: {"description": "Нет роли с `view`."},
        404: {"description": "Сервер не найден / чужой dept (только при заданном `server_id`)."},
    },
)
async def list_accounts(
    identity: CurrentUserIdentity,
    server_id: str | None = Query(
        default=None,
        description=(
            "ID сервера, чьи аккаунты выбрать. Если не задан — выдача по всему "
            "отделу вызывающего (включая аккаунты без привязок)."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, description="DEPRECATED — используйте cursor-пагинацию."),
    after: str | None = Query(default=None, description="Opaque cursor предыдущей страницы."),
    cursor: bool = Query(default=False, description="Включить cursor-envelope."),
) -> PaginatedResponse[ServerAccountResponse] | CursorPaginatedResponse[ServerAccountResponse]:
    """List-эндпоинт. Доступ: `(server_account, *, view)`.

    `server_id` опционален: без него — dept-wide листинг (все аккаунты отдела,
    включая unbound), с ним — прежний per-server режим без изменений.
    """
    if cursor or after is not None:
        from src.utils.cursor import InvalidCursorError, to_bad_request
        try:
            if server_id is None:
                items, next_cursor, has_more = await svc.list_department_accounts_cursor(
                    db, identity, limit=limit, after=after,
                )
            else:
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
    if server_id is None:
        items, total = await svc.list_department_accounts(
            db, identity, limit=limit, offset=offset
        )
    else:
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
        422: {"description": "DECRYPT_FAILED — сломанный ciphertext (только при view_password)."},
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
    obj, password_b64, previous_password_b64 = await svc.get_account(db, identity, account_id)
    return _to_response(obj, password_b64, previous_password_b64=previous_password_b64)


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
        "`has_sudo=False → True` ИЛИ добавление в `unix_groups` группы из "
        "набора `SUDO_CONFERRING_GROUPS` (sudo/astra-admin/wheel) требует "
        "action `grant_sudo` (иначе 403 `SUDO_GROUP_REQUIRES_GRANT_SUDO`). "
        "Снятие sudo / такой группы допустимо обычным `update`. При изменении OS-управляемых "
        "атрибутов (`has_sudo`/`unix_groups`/`shell`) правка рассылается "
        "`update_on_host` на все серверы, где аккаунт присутствует.\n\n"
        "Смена `login` через PATCH — только DB-only переименование, допустимое "
        "лишь когда аккаунт `present_on_server=false` на ВСЕХ привязанных "
        "серверах (пере-проверяется уникальность (server_id, login)). Если "
        "аккаунт присутствует хоть на одном сервере → 409 LOGIN_LOCKED; смена "
        "живого логина — через `POST /server-accounts/{id}/recreate_login`."
    ),
    responses={
        403: {"description": "Нет `update` (или `grant_sudo` при подъёме has_sudo / добавлении sudo-группы — `SUDO_GROUP_REQUIRES_GRANT_SUDO`)."},
        404: {"description": "Аккаунт не найден / чужой dept."},
        409: {"description": "LOGIN_LOCKED (аккаунт present на сервере) / ACCOUNT_DUPLICATE (новый login занят)."},
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
        "`(server_account, adopt_from_host)` (update-уровень). Аудит: "
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
        "Снимает связки аккаунт ↔ сервер немедленно. Можно отвязать и последний "
        "сервер — аккаунт остаётся в БД без серверов (карточка живёт до "
        "отдельного delete). Если OS-учётка реально стояла на боксе "
        "(`present_on_server`), на него best-effort ставится `account.deprovision` "
        "(userdel). Гейтится `update`."
    ),
    responses={
        403: {"description": "Нет `update`."},
        404: {"description": "Аккаунт не найден / чужой dept, либо один из server_id не привязан."},
    },
)
async def unlink_servers(
    account_id: str,
    body: ServerAccountServersUpdate,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Отвязка серверов. Доступ: `(server_account, *, update)`."""
    obj = await svc.unlink_servers(
        db, identity, account_id, body,
        request_id=getattr(request.state, "request_id", None),
    )
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
    """Rotate-эндпоинт. Доступ: `(server_account, *, rotate_password)`. Аудит — CRITICAL.

    После смены ciphertext'а в БД авто-пробрасываем новый пароль (+ ssh-ключ)
    на серверы, где аккаунт присутствует, через `account.update_on_host`
    (best-effort fan-out; недоступность воркера уходит в audit, не валит ответ).
    """
    new_password = body.password() if body is not None else None
    obj = await svc.rotate_password(db, identity, account_id, new_password)
    await fanout_apply_credentials(
        db=db, identity=identity, request=request, account=obj,
        action=Action.ROTATE_PASSWORD, apply_password=True,
    )
    return ServerAccountRotateResponse(
        id=obj.id,
        login=obj.login,
        rotated_at=obj.password_rotated_at,
    )


@router.post(
    "/{account_id}/recreate_login",
    response_model=AccountRecreateLoginResponse,
    status_code=202,
    summary="Сменить живой OS-логин: deprovision → rename → provision",
    description=(
        "Меняет логин аккаунта, который физически присутствует на серверах: "
        "на каждый привязанный сервер ставится `account.deprovision` под старым "
        "логином, логин переименовывается в БД (синхронно с денормализованными "
        "копиями на связках), затем на каждый сервер ставится `account.provision` "
        "под новым логином (с паролем/ключом). Тело: `{login}`.\n\n"
        "Доступ — ТОЛЬКО platform department_admin отдела аккаунта или "
        "service-роль `admin`. Обычный update-грант не проходит. "
        "Аудит CRITICAL. Конфликт нового логина на одном из серверов → 409 "
        "ACCOUNT_DUPLICATE. Серверы decommissioned / недоступные воркеру "
        "уезжают в `skipped` (rename в БД при этом всё равно выполняется)."
    ),
    responses={
        202: {"description": "Логин переименован; deprovision/provision-задачи поставлены."},
        403: {"description": "Не department_admin отдела и не service-admin."},
        404: {"description": "Аккаунт не найден / чужой dept."},
        409: {"description": "ACCOUNT_DUPLICATE — новый логин занят на одном из серверов."},
    },
)
async def recreate_login(
    account_id: str,
    body: ServerAccountRecreateLoginRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountRecreateLoginResponse:
    """Recreate-login. Доступ: platform dep_admin отдела ИЛИ service-admin. Аудит CRITICAL."""
    account = await svc.authorize_recreate_login(db, identity, account_id)
    result = await recreate_login_orchestrate(
        db=db, identity=identity, request=request,
        account=account, new_login=body.login,
    )
    svc.audit_recreate_login(account, result)
    return AccountRecreateLoginResponse(
        id=account.id,
        old_login=result["old_login"],
        new_login=result["new_login"],
        deprovision=result["deprovision"],
        provision=result["provision"],
        skipped=[AccountRotateSkipped(**s) for s in result["skipped"]],
    )


@router.post(
    "/{account_id}/ssh_key",
    response_model=AccountKeyFanoutResponse,
    status_code=202,
    summary="Задать/заменить SSH-ключ аккаунта + раскатать на серверы",
    description=(
        "Сохраняет SSH-ключ в БД и сразу диспатчит `account.provision` на все "
        "серверы, где аккаунт присутствует (`present_on_server=True`) — только "
        "provision кладёт public key в `~/.ssh/authorized_keys`. Тело: "
        "`{ssh_mode, ssh_public_key?, ssh_private_key_b64?}`. `generate` — сервер "
        "генерит Ed25519 и возвращает приватный ключ ОДИН раз (`ssh_private_key`); "
        "`supply` — клиент передаёт `ssh_public_key` и опционально `ssh_private_key_b64` "
        "(если приложен — шифруется и хранится, тогда консоль сможет ходить под "
        "аккаунтом; в ответе приватный не эхуется). Гейтится `update`. Аудит "
        "`server_account.ssh_key_set`."
    ),
    responses={
        202: {"description": "Ключ записан; provision-fan-out поставлен."},
        403: {"description": "Нет `update`."},
        404: {"description": "Аккаунт не найден / чужой dept."},
        422: {"description": "ssh_mode='supply' без ssh_public_key (или наоборот) / битый ключ."},
    },
)
async def set_ssh_key(
    account_id: str,
    body: ServerAccountSshKeyRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountKeyFanoutResponse:
    """Set/replace SSH key. Доступ: `(server_account, *, update)`."""
    obj, public_key, private_key = await svc.set_ssh_key(
        db, identity, account_id,
        ssh_mode=body.ssh_mode, ssh_public_key=body.ssh_public_key,
        ssh_private_key_pem=body.ssh_private_key(),
    )
    # Снимаем скалярные поля до fan-out'а: provision-диспатч крутит свой
    # savepoint вокруг того же объекта аккаунта, и его rollback (например при
    # недоступном воркере) экспайрит атрибуты — чтение obj.id/obj.login после
    # fan-out'а потянуло бы ленивый SELECT уже вне async-greenlet.
    account_id_v, login_v = obj.id, obj.login
    tasks, skipped = await fanout_apply_credentials(
        db=db, identity=identity, request=request, account=obj,
        action=Action.UPDATE, apply_password=False,
    )
    return AccountKeyFanoutResponse(
        id=account_id_v,
        login=login_v,
        ssh_public_key=public_key,
        ssh_private_key=private_key,
        tasks=[AccountRotateTask(**t) for t in tasks],
        skipped=[AccountRotateSkipped(**s) for s in skipped],
    )


@router.post(
    "/{account_id}/rotate_ssh_key",
    response_model=AccountKeyFanoutResponse,
    status_code=202,
    summary="Перегенерить SSH-ключ (компрометация) + раскатать на серверы",
    description=(
        "Генерит новую Ed25519-пару (кейс компрометации старого ключа), "
        "сохраняет public + зашифрованный private, возвращает новый приватный "
        "ключ ОДИН раз, и диспатчит `account.provision` на все серверы, где "
        "аккаунт присутствует (re-push authorized_keys). Гейтится `update`. "
        "Аудит CRITICAL (`server_account.ssh_key_rotate`)."
    ),
    responses={
        202: {"description": "Ключ перегенерён; provision-fan-out поставлен."},
        403: {"description": "Нет `update`."},
        404: {"description": "Аккаунт не найден / чужой dept."},
    },
)
async def rotate_ssh_key(
    account_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountKeyFanoutResponse:
    """Rotate SSH key. Доступ: `(server_account, *, update)`. Аудит CRITICAL."""
    obj, public_key, private_key = await svc.rotate_ssh_key(db, identity, account_id)
    # См. set_ssh_key: снимаем скаляры до fan-out'а, чтобы savepoint-rollback
    # provision-диспатча не заставил читать экспайренный obj вне greenlet.
    account_id_v, login_v = obj.id, obj.login
    tasks, skipped = await fanout_apply_credentials(
        db=db, identity=identity, request=request, account=obj,
        action=Action.UPDATE, apply_password=False,
    )
    return AccountKeyFanoutResponse(
        id=account_id_v,
        login=login_v,
        ssh_public_key=public_key,
        ssh_private_key=private_key,
        tasks=[AccountRotateTask(**t) for t in tasks],
        skipped=[AccountRotateSkipped(**s) for s in skipped],
    )


@router.post(
    "/{account_id}/apply",
    response_model=AccountApplyCredentialsResponse,
    status_code=202,
    summary="Пробросить текущие пароль+ssh-ключ аккаунта на привязанные серверы",
    description=(
        "Ручной проброс: ставит `account.update_on_host` на все серверы, где "
        "аккаунт присутствует (`present_on_server=True`), донося сохранённые в "
        "БД пароль и ssh-ключ (chpasswd + authorized_keys). Тот же apply, что "
        "авто-запускается после set/rotate пароля/ключа. Гейтится "
        "`(server_account, rotate_password)` — та же плоскость, что у ротации "
        "пароля. Best-effort: недоступный/списанный сервер уходит в `skipped`, "
        "не валит остальные. Аудит `server_account.apply_credentials`."
    ),
    responses={
        202: {"description": "Apply-задачи поставлены; сводка в теле."},
        403: {"description": "Нет `rotate_password` либо чужой department."},
        404: {"description": "Аккаунт не найден / чужой dept."},
    },
)
async def apply_credentials(
    account_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountApplyCredentialsResponse:
    """Apply-эндпоинт. Доступ: `(server_account, *, rotate_password)`."""
    account = await svc.authorize_apply_credentials(db, identity, account_id)
    # Снимаем скаляры до fan-out'а: provision-диспатч крутит savepoint вокруг
    # объекта аккаунта, его rollback (недоступный воркер) экспайрит атрибуты.
    account_id_v, login_v, dept_v = account.id, account.login, account.department_id
    tasks, skipped = await fanout_apply_credentials(
        db=db, identity=identity, request=request, account=account,
        action=Action.ROTATE_PASSWORD, apply_password=True,
    )
    svc.audit_apply_credentials(
        account_id=account_id_v, login=login_v, department_id=dept_v,
        tasks=tasks, skipped=skipped,
    )
    return AccountApplyCredentialsResponse(
        id=account_id_v,
        login=login_v,
        tasks=[AccountRotateTask(**t) for t in tasks],
        skipped=[AccountRotateSkipped(**s) for s in skipped],
    )


@router.get(
    "/{account_id}/ssh_private_key",
    response_model=ServerAccountSshPrivateKeyResponse,
    summary="Скачать приватный SSH-ключ аккаунта (под view_password)",
    description=(
        "Расшифровывает и отдаёт сохранённый приватный SSH-ключ аккаунта "
        "(`ssh_private_key` в PEM). Гейт — `view_password` (то же право, что у "
        "раскрытия пароля). Доступен только когда ключ генерировался сервером "
        "(`ssh_mode='generate'` / rotate_ssh_key) — у `supply`-ключа приватной "
        "части нет, как и у аккаунта без ключа: 404 ACCOUNT_NO_SSH_PRIVATE_KEY. "
        "Раскрытие пишет CRITICAL audit `server_account.ssh_private_key_revealed`.\n\n"
        "Тот же per-IP+account reveal-rate-limit, что у раскрытия пароля "
        "(`PASSWORD_REVEAL_RATE_LIMIT`, default 10/min) — plaintext-канал нельзя "
        "скрапить даже до триггера CRITICAL-аудита."
    ),
    responses={
        200: {"description": "Приватный ключ расшифрован и отдан."},
        403: {"description": "Нет роли с `view_password`."},
        404: {"description": "Аккаунт не найден / чужой dept, либо нет сохранённого приватного ключа."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP+account reveal-rate-limit пробит."},
        422: {"description": "DECRYPT_FAILED — сломанный ciphertext приватного ключа."},
    },
)
@endpoint_limiter.limit(
    get_settings().password_reveal_rate_limit, key_func=per_account_key,
)
async def reveal_ssh_private_key(
    request: Request,
    account_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountSshPrivateKeyResponse:
    """Reveal приватного ключа. Доступ: `(server_account, *, view_password)`. Аудит CRITICAL."""
    obj, private_pem = await svc.reveal_ssh_private_key(db, identity, account_id)
    return ServerAccountSshPrivateKeyResponse(
        id=obj.id,
        login=obj.login,
        ssh_private_key=private_pem,
        ssh_public_key=obj.ssh_public_key,
    )


@router.get(
    "/{account_id}/previous_ssh_private_key",
    response_model=ServerAccountSshPrivateKeyResponse,
    summary="Скачать ПРЕЖНИЙ приватный SSH-ключ аккаунта (под view_password)",
    description=(
        "Зеркало `/ssh_private_key`, но отдаёт удержанный прежний приватный ключ "
        "(`previous_ssh_private_key_encrypted`) — он доступен на время переходного "
        "периода ротации ssh-ключа, пока новый не раскатан на серверы. Гейт — "
        "`view_password` (то же право, что у раскрытия пароля / текущего ключа). "
        "Нет удержанного ключа (ротации не было или период закрыт) → 404 "
        "ACCOUNT_NO_PREVIOUS_SSH_KEY. Раскрытие пишет CRITICAL audit "
        "`server_account.reveal_previous_ssh_private_key`.\n\n"
        "Тот же per-IP+account reveal-rate-limit, что у раскрытия пароля "
        "(`PASSWORD_REVEAL_RATE_LIMIT`, default 10/min)."
    ),
    responses={
        200: {"description": "Прежний приватный ключ расшифрован и отдан."},
        403: {"description": "Нет роли с `view_password`."},
        404: {"description": "Аккаунт не найден / чужой dept, либо нет удержанного прежнего ключа."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP+account reveal-rate-limit пробит."},
        422: {"description": "DECRYPT_FAILED — сломанный ciphertext прежнего ключа."},
    },
)
@endpoint_limiter.limit(
    get_settings().password_reveal_rate_limit, key_func=per_account_key,
)
async def reveal_previous_ssh_private_key(
    request: Request,
    account_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountSshPrivateKeyResponse:
    """Reveal прежнего приватного ключа. Доступ: `(server_account, *, view_password)`. Аудит CRITICAL."""
    obj, private_pem = await svc.reveal_previous_ssh_private_key(db, identity, account_id)
    return ServerAccountSshPrivateKeyResponse(
        id=obj.id,
        login=obj.login,
        ssh_private_key=private_pem,
        ssh_public_key=obj.ssh_public_key,
    )
