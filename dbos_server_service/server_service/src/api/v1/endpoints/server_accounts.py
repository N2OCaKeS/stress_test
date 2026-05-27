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

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.models import ServerAccount
from src.repositories import server_account as account_repo
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.server_account import (
    ServerAccountCreate,
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
        "передаётся в body, либо генерируется сервером "
        "(`secrets.token_urlsafe(32)`). В любом случае шифруется через "
        "`secrets_service.encrypt()` и в ответ не возвращается. `has_sudo=True` "
        "требует action `grant_sudo`."
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
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Create-эндпоинт. Доступ: `(server_account, *, create)` (+ `grant_sudo` опц.)."""
    obj = await svc.create_account(db, identity, body)
    return _to_response(obj)


@router.get(
    "",
    response_model=PaginatedResponse[ServerAccountResponse],
    summary="Список аккаунтов, привязанных к серверу",
    description=(
        "Принимает `server_id` query-параметром. Возвращает страницу аккаунтов, "
        "привязанных к этому серверу, отсортированных по `created_at DESC`. "
        "Cross-dept сервер скрыт за 404."
    ),
    responses={
        403: {"description": "Нет роли с `view`."},
        404: {"description": "Сервер не найден / чужой dept."},
    },
)
async def list_accounts(
    identity: CurrentIdentity,
    server_id: str = Query(..., description="ID сервера, чьи аккаунты выбрать."),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[ServerAccountResponse]:
    """List-эндпоинт. Доступ: `(server_account, *, view)`."""
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
        "Раскрытие пароля пишет CRITICAL audit `server_account.password_revealed`."
    ),
    responses={
        403: {"description": "Нет роли ни с `view`, ни с `view_password`."},
        404: {"description": "Аккаунт не найден или чужой dept (скрыто за 404)."},
        500: {"description": "DECRYPT_FAILED — сломанный ciphertext (только при view_password)."},
    },
)
async def get_account(
    account_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Get-эндпоинт. Доступ: `view` или `view_password`; пароль — при `view_password`."""
    obj, password_b64 = await svc.get_account(db, identity, account_id)
    return _to_response(obj, password_b64)


@router.patch(
    "/{account_id}",
    response_model=ServerAccountResponse,
    summary="Обновить поля аккаунта (без пароля и привязок)",
    description=(
        "Частичное обновление (PATCH). Смена пароля — отдельный endpoint "
        "`/rotate_password`. Привязка/отвязка серверов — `/servers`. Подъём "
        "`has_sudo=False → True` требует action `grant_sudo` (admin-only). "
        "Снятие sudo допустимо обычным `update`."
    ),
    responses={
        403: {"description": "Нет `update` (или `grant_sudo` при подъёме has_sudo)."},
        404: {"description": "Аккаунт не найден / чужой dept."},
    },
)
async def update_account(
    account_id: str,
    body: ServerAccountUpdate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Update-эндпоинт. Доступ: `(server_account, *, update)`."""
    obj = await svc.update_account(db, identity, account_id, body)
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
    identity: CurrentIdentity,
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
    identity: CurrentIdentity,
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
    identity: CurrentIdentity,
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
        "Принимает опциональный `password` в body. Если передан — проходит "
        "политику (минимум 8 символов, буквы и цифры) и сохраняется; если "
        "нет — генерит новый через `secrets.token_urlsafe(32)`. Меняет только "
        "общий ciphertext в БД (без apply'я на серверы). Apply на конкретный "
        "сервер или на все привязанные — через worker-dispatch `/rotate`. "
        "Plaintext в ответ НЕ возвращается."
    ),
    responses={
        403: {"description": "Нет `rotate_password`."},
        404: {"description": "Аккаунт не найден / чужой dept."},
        422: {"description": "Переданный пароль не проходит политику."},
    },
)
async def rotate_password(
    account_id: str,
    identity: CurrentIdentity,
    body: ServerAccountRotateRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountRotateResponse:
    """Rotate-эндпоинт. Доступ: `(server_account, *, rotate_password)`. Аудит — CRITICAL."""
    new_password = body.password if body is not None else None
    obj = await svc.rotate_password(db, identity, account_id, new_password)
    return ServerAccountRotateResponse(
        id=obj.id,
        login=obj.login,
        rotated_at=obj.password_rotated_at,
    )
