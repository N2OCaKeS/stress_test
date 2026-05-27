"""CRUD аккаунтов сервера + rotate_password.

GET карточки доступен по `view` или `view_password`. Держателю action
`view_password` тот же GET доносит расшифрованный пароль в `password_b64`;
остальным поле приходит `null`. Отдельной reveal-ручки нет. Расшифрованный
пароль для worker'а параллельно по-прежнему отдаётся через
`internal/.../password` (см. `endpoints/internal.py`).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.server_account import (
    ServerAccountCreate,
    ServerAccountResponse,
    ServerAccountRotateRequest,
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
    resp = ServerAccountResponse.model_validate(obj)
    resp.password_b64 = password_b64
    return resp


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
        "Принимает опциональный `password` в body. Если передан — проходит "
        "политику (минимум 8 символов, буквы и цифры) и сохраняется; если "
        "нет — генерит новый через `secrets.token_urlsafe(32)`. Plaintext в "
        "ответ НЕ возвращается — забрать его сможет только worker через "
        "internal-endpoint при следующем SSH-apply'е. Параллельный "
        "worker-callback path (`internal/.../password/rotate`) продолжает "
        "работать."
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
