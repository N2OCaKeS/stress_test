"""Эндпоинты ботов и service-account'ов."""

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AnyAdmin
from src.dependencies.db import get_db
from src.utils.pagination import PaginationParams, pagination_params
from src.schemas.bots import (
    BotCreate,
    BotResponse,
    BotRoleAssignRequest,
    BotRoleResponse,
    BotTokenCreate,
    BotTokenCreateResponse,
    BotTokenListItem,
    BotUpdate,
)
from src.schemas.common import OkResponse
from src.services import bot_service

router = APIRouter(prefix="/bots")


@router.post(
    "",
    response_model=BotResponse,
    status_code=201,
    summary="Создать бота",
    description="Создаёт service-account внутри отдела. allowed_services задаёт, к каким сервисам бот ходит.",
)
async def create_bot(
    body: BotCreate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> BotResponse:
    """Создать бота (service-account).

    Что делает:
        Создаёт `BotAccount` в указанном отделе. allowed_services — белый
        список сервисов, куда бот сможет ходить через свои токены.

    Доступ:
        account_admin (любой отдел) или department_admin (только свой).

    Возможные ошибки:
        * `BOT_NAME_TAKEN` (409) — имя уже занято (глобально уникально).
        * `DEPARTMENT_NOT_FOUND` (404).
    """
    return await bot_service.create_bot(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        data=body,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get(
    "",
    response_model=list[BotResponse],
    summary="Список ботов",
    description="account_admin видит все боты, department_admin — только своего отдела.",
)
async def list_bots(
    request: Request,
    response: Response,
    identity: AnyAdmin,
    pagination: PaginationParams = Depends(pagination_params),
    department_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[BotResponse]:
    """Список ботов с учётом scope-а смотрящего.

    Пагинация:
        Query-параметры `limit`/`offset`; общее число — в `X-Total-Count`.

    Фильтр `department_id` уважается для `account_admin` (он видит все отделы
    и хочет сузить выдачу). Для `department_admin` фильтр игнорируется — он и
    так залочен на свой отдел.
    """
    items, total = await bot_service.list_bots(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        department_id=department_id,
        pagination=pagination,
        request_id=getattr(request.state, "request_id", None),
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.patch(
    "/{bot_id}",
    response_model=BotResponse,
    summary="Обновить бота",
    description="Patch — отправляй только меняющиеся поля.",
)
async def update_bot(
    bot_id: str,
    body: BotUpdate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> BotResponse:
    """Обновить бота.

    Доступ:
        account_admin (любой) или department_admin (только в своём отделе).
    """
    return await bot_service.update_bot(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        bot_id=bot_id,
        data=body,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/{bot_id}/tokens",
    response_model=BotTokenCreateResponse,
    status_code=201,
    summary="Выдать боту новый токен",
    description="Возвращает raw-токен ОДИН РАЗ (в БД только hash). Префикс `dbos_bot_…`.",
)
async def create_bot_token(
    bot_id: str,
    body: BotTokenCreate,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> BotTokenCreateResponse:
    """Создать новый bot-токен.

    Что делает:
        Генерит opaque-секрет с префиксом `dbos_bot_…`, в БД пишет только
        SHA-256 hash. Raw-значение возвращается ровно один раз — клиент
        обязан сохранить.

    Доступ:
        account_admin или department_admin своего отдела.
    """
    return await bot_service.create_bot_token(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        bot_id=bot_id,
        name=body.name,
        expires_at=body.expires_at,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get(
    "/{bot_id}/tokens",
    response_model=list[BotTokenListItem],
    summary="Список токенов бота",
    description="Только метаданные (prefix, created/expires/last_used). Raw-значения никогда не возвращаются.",
)
async def list_bot_tokens(
    bot_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[BotTokenListItem]:
    """Список токенов бота. Только метаданные."""
    return await bot_service.list_bot_tokens(
        db=db,
        bot_id=bot_id,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get(
    "/{bot_id}/roles",
    response_model=list[BotRoleResponse],
    summary="Service-роли бота",
)
async def list_bot_roles(
    bot_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[BotRoleResponse]:
    """Текущие service-роли бота (`BotServiceRole`).

    Доступ:
        account_admin или department_admin своего отдела.
    """
    return await bot_service.list_bot_roles(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        bot_id=bot_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/{bot_id}/roles",
    response_model=BotRoleResponse,
    status_code=201,
    summary="Назначить боту service-роли",
    description="Replace-семантика: список `roles` для `service_name` заменяет текущий набор.",
)
async def assign_bot_roles(
    bot_id: str,
    body: BotRoleAssignRequest,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> BotRoleResponse:
    """Назначить service-роли боту.

    Что делает:
        Replace-семантика по `(bot_id, service_name)`. Сервис должен быть
        в `allowed_services` бота и в `dept_services` отдела.

    Возможные ошибки:
        * `SERVICE_ACCESS_DENIED` (403).
        * `ROLE_NOT_FOUND` (404) — роль не определена в `ServiceRoleDefinition`.
    """
    return await bot_service.assign_bot_roles(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        bot_id=bot_id,
        service_name=body.service_name,
        roles=body.roles,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete(
    "/{bot_id}/roles/{service_name}",
    response_model=OkResponse,
    summary="Снять у бота все роли для сервиса",
)
async def revoke_bot_roles(
    bot_id: str,
    service_name: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Удалить все `BotServiceRole` для пары (bot, service)."""
    await bot_service.revoke_bot_roles(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        bot_id=bot_id,
        service_name=service_name,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()


@router.delete(
    "/{bot_id}/tokens/{token_id}",
    response_model=OkResponse,
    summary="Отозвать конкретный токен бота",
)
async def revoke_bot_token(
    bot_id: str,
    token_id: str,
    request: Request,
    identity: AnyAdmin,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Помечает токен как revoked. Раз ревокнули — токен уже не воскресить."""
    await bot_service.revoke_bot_token(
        db=db,
        actor_id=identity.user_id,
        actor_role=identity.platform_role,
        bot_id=bot_id,
        token_id=token_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return OkResponse()
