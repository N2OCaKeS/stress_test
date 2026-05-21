"""CRUD аккаунтов сервера + rotate_password + reveal_password.

`view_password` сюда НЕ выведен — расшифрованный пароль для worker'а
по-прежнему отдаётся только через `internal/.../password`
(см. `endpoints/internal.py`).

`reveal_password` — отдельный пользовательский endpoint, возвращающий
plaintext в base64 для UI/CLI. Default — admin/operator, separate action
от worker-only `view_password`.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.server_account import (
    RevealPasswordResponse,
    ServerAccountCreate,
    ServerAccountResponse,
    ServerAccountRotateResponse,
    ServerAccountUpdate,
)
from src.services import server_account as svc

router = APIRouter(prefix="/server-accounts")


@router.post(
    "",
    response_model=ServerAccountResponse,
    status_code=201,
    summary="Создать аккаунт сервера (пароль шифруется at-rest)",
    description=(
        "Заводит OS-аккаунт на указанном сервере. Пароль либо передаётся в "
        "body, либо генерируется сервером (`secrets.token_urlsafe(32)`). "
        "В любом случае шифруется через `secrets_service.encrypt()` и в "
        "ответ не возвращается. `has_sudo=True` требует action `grant_sudo`."
    ),
    responses={
        201: {"description": "Аккаунт создан."},
        403: {"description": "Нет роли с `create` (или `grant_sudo` при has_sudo=True), либо чужой department."},
        404: {"description": "Сервер не найден или принадлежит чужому department."},
        409: {"description": "Конфликт по (server_id, login)."},
    },
)
async def create_account(
    body: ServerAccountCreate,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Create-эндпоинт. Доступ: `(server_account, *, create)` (+ `grant_sudo` опц.)."""
    obj = await svc.create_account(db, identity, body)
    return ServerAccountResponse.model_validate(obj)


@router.get(
    "",
    response_model=PaginatedResponse[ServerAccountResponse],
    summary="Список аккаунтов конкретного сервера",
    description=(
        "Принимает `server_id` query-параметром. Возвращает страницу аккаунтов "
        "этого сервера, отсортированных по `created_at DESC`. Cross-dept "
        "сервер скрыт за 404."
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
        items=[ServerAccountResponse.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{account_id}",
    response_model=ServerAccountResponse,
    summary="Получить карточку аккаунта (без plaintext-пароля)",
    description=(
        "Не возвращает `password_encrypted` и тем более plaintext. "
        "Для пароля worker'у — отдельный internal endpoint."
    ),
    responses={
        403: {"description": "Нет роли с `view`."},
        404: {"description": "Аккаунт не найден или чужой dept (скрыто за 404)."},
    },
)
async def get_account(
    account_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountResponse:
    """Get-эндпоинт. Доступ: `(server_account, *, view)`."""
    obj = await svc.get_account(db, identity, account_id)
    return ServerAccountResponse.model_validate(obj)


@router.patch(
    "/{account_id}",
    response_model=ServerAccountResponse,
    summary="Обновить поля аккаунта (без пароля)",
    description=(
        "Частичное обновление (PATCH). Смена пароля — отдельный endpoint "
        "`/rotate_password`. Подъём `has_sudo=False → True` требует action "
        "`grant_sudo` (admin-only). Снятие sudo допустимо обычным `update`."
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
    return ServerAccountResponse.model_validate(obj)


@router.delete(
    "/{account_id}",
    response_model=OkResponse,
    summary="Удалить аккаунт сервера",
    description=(
        "Hard-delete строки в БД. На реальном сервере OS-аккаунт не удаляется — "
        "для этого нужен отдельный worker pipeline. Требует роль с `delete`."
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
    summary="Ротация пароля (user-initiated)",
    description=(
        "Генерит новый пароль через `secrets.token_urlsafe(32)`, шифрует и "
        "сохраняет. Plaintext в ответ НЕ возвращается — забрать его сможет "
        "только worker через internal-endpoint при следующем SSH-apply'е. "
        "Параллельный worker-callback path (`internal/.../password/rotate`) "
        "продолжает работать."
    ),
    responses={
        403: {"description": "Нет `rotate_password`."},
        404: {"description": "Аккаунт не найден / чужой dept."},
    },
)
async def rotate_password(
    account_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAccountRotateResponse:
    """Rotate-эндпоинт. Доступ: `(server_account, *, rotate_password)`. Аудит — CRITICAL."""
    obj = await svc.rotate_password(db, identity, account_id)
    return ServerAccountRotateResponse(
        id=obj.id,
        login=obj.login,
        rotated_at=obj.password_rotated_at,
    )


@router.post(
    "/{account_id}/reveal-password",
    response_model=RevealPasswordResponse,
    summary="Расшифровать пароль аккаунта (base64)",
    description=(
        "Возвращает текущий plaintext-пароль аккаунта в base64-encoded "
        "форме (`password_b64`). Plain-эквивалент того, что получает "
        "server_worker через internal endpoint, но проверяется по "
        "отдельному user-facing action `reveal_password` (по дефолту "
        "только `operator`/`admin`). Каждое раскрытие пишет аудит "
        "`server_account.password_revealed` с severity WARNING."
    ),
    responses={
        403: {"description": "Нет роли с `reveal_password`."},
        404: {"description": "Аккаунт не найден / чужой dept / нет сохранённого пароля."},
        500: {"description": "DECRYPT_FAILED — сломанный ciphertext или неверный ключ."},
    },
)
async def reveal_password(
    account_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> RevealPasswordResponse:
    """Reveal-эндпоинт. Доступ: `(server_account, *, reveal_password)`. Аудит — WARNING."""
    password_b64 = await svc.reveal_password(db, identity, account_id)
    return RevealPasswordResponse(password_b64=password_b64)
