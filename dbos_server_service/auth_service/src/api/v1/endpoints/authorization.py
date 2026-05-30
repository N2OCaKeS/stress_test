"""Эндпоинты introspect токенов и проверки service-access.

Оба эндпоинта закрыты shared `SERVICE_API_KEY` — это service-to-service входные
точки (вызываются config_service, loging_service, server_service и т.д.), не
user-facing. Без guard'а кто угодно с network access мог бы brute-force'ить
introspect ворованных PAT/bot токенов или проверять service-access как
оракул.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import require_service_token
from src.dependencies.db import get_db
from src.schemas.authorization import IntrospectRequest, IntrospectResponse, ServiceAccessRequest, ServiceAccessResponse
from src.services import authorization_service

router = APIRouter(prefix="/authorization")


@router.post(
    "/introspect",
    response_model=IntrospectResponse,
    dependencies=[Depends(require_service_token)],
    summary="Introspect токена (service-to-service)",
    description="Валидирует JWT/PAT/bot-токен и возвращает identity + effective service_roles.",
    response_description="Структура IntrospectResponse — `active`, identity-поля, effective роли.",
    responses={
        401: {"description": "Нет/неверный `SERVICE_API_KEY` или `X-Service-Identity` (в strict-режиме)."},
    },
)
async def introspect(
    body: IntrospectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> IntrospectResponse:
    """Introspect — основная точка валидации для других сервисов.

    Что делает:
        Парсит токен (JWT / PAT / bot), revalidate'ит юзера через БД
        (`is_banned`, `status`), считает effective роли через
        `collect_user_permissions` (с INTERSECT). Чувствительные claims
        берутся не из JWT payload, а из свежего состояния — даже с валидной
        подписью забаненный юзер не пройдёт.

    Доступ:
        Только service-to-service. Закрыт `SERVICE_API_KEY` + опционально
        `X-Service-Identity` (soft/strict через ENV `STRICT_SERVICE_IDENTITY`).
    """
    return await authorization_service.introspect(
        db=db,
        token=body.token,
        request_id=getattr(request.state, "request_id", None),
        caller_ip=body.caller_ip,
    )


@router.post(
    "/service-access",
    response_model=ServiceAccessResponse,
    dependencies=[Depends(require_service_token)],
    summary="Проверка доступа к сервису",
    description="Возвращает `allowed` + список service-ролей субъекта для конкретного сервиса.",
)
async def check_service_access(
    body: ServiceAccessRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServiceAccessResponse:
    """Конкретный вопрос «есть ли у субъекта access к сервису X».

    Что делает:
        Лёгкая обёртка над introspect: фильтрует результат по запрошенному
        `service_name`. Используется server_service для проверки прав
        конкретного юзера на конкретный сервис без full introspect-нагрузки.

    Доступ:
        Только service-to-service (`SERVICE_API_KEY`).
    """
    return await authorization_service.check_service_access(
        db=db,
        token=body.subject_token,
        service_name=body.service_name,
        request_id=getattr(request.state, "request_id", None),
    )
