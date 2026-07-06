"""Эндпоинты настраиваемой кнопки левой панели web-UI.

`GET /nav-links` — любой аутентифицированный юзер: список кнопок, видимых его
отделу. `GET /admin/nav-links` + `PUT /admin/nav-links` — только account_admin:
чтение и изменение полной конфигурации.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdmin, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.nav_links import (
    NavLinkConfig,
    NavLinkConfigUpdate,
    NavLinkItem,
)
from src.services import nav_link_service

router = APIRouter()


@router.get(
    "/nav-links",
    response_model=list[NavLinkItem],
    summary="Кнопки левой панели, видимые отделу пользователя",
    description=(
        "Любой аутентифицированный пользователь. Возвращает включённые кнопки, "
        "видимые отделу из его identity (по `all_departments` или списку "
        "`department_ids`). Пустой список — кнопка не настроена/выключена/не видна."
    ),
)
async def list_nav_links(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[NavLinkItem]:
    """Кнопки, видимые отделу текущего пользователя.

    Доступ:
        Любой аутентифицированный пользователь.
    """
    return await nav_link_service.list_visible(db, department_id=identity.department_id)


@router.get(
    "/admin/nav-links",
    response_model=NavLinkConfig,
    summary="Полная конфигурация кнопки (account_admin)",
    responses={
        403: {"description": "ROLE_REQUIRED — нужен account_admin."},
    },
)
async def get_nav_links_config(
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> NavLinkConfig:
    """Прочитать полную конфигурацию кнопки.

    Доступ:
        Только account_admin.
    """
    return await nav_link_service.get_config(db)


@router.put(
    "/admin/nav-links",
    response_model=NavLinkConfig,
    summary="Изменить конфигурацию кнопки (account_admin)",
    responses={
        403: {"description": "ROLE_REQUIRED — нужен account_admin."},
        422: {"description": "NAV_LINK_URL_REQUIRED / битый url."},
    },
)
async def update_nav_links_config(
    body: NavLinkConfigUpdate,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> NavLinkConfig:
    """Полная замена конфигурации кнопки.

    Доступ:
        Только account_admin.

    Возможные ошибки:
        * `NAV_LINK_URL_REQUIRED` (422) — `enabled=True`, но `url` пустой.
        * `422` — `url` не http(s) / длиннее лимита.
        * `ROLE_REQUIRED` (403).

    Audit:
        `nav_link.update` (INFO).
    """
    return await nav_link_service.update_config(
        db=db,
        actor_id=identity.user_id,
        actor_username=identity.username,
        body=body,
        request_id=getattr(request.state, "request_id", None),
    )
