"""CRUD `/department-integration-settings` (§2.4, §3.5 плана миграции).

GET доступен своему отделу, никогда не 404 — отсутствие строки означает
"интеграция не настроена" (все поля `null`). PUT (upsert) — department-scoped
матрица `(department_integration_settings, *, update)`. Чужой отдел — 403
`DEPARTMENT_ISOLATION`: строка несёт ссылки на кредентиалы отдела, а
sprint-board этими кредентиалами ещё и ходит в Jira.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import BearerToken, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.department_integration_settings import (
    DepartmentIntegrationSettingsResponse,
    DepartmentIntegrationSettingsUpdate,
)
from src.schemas.jira_sprint_board import JiraSprintBoardResponse
from src.services import department_integration_settings as svc
from src.services import jira_sprint_board as sprint_board_svc

router = APIRouter(prefix="/department-integration-settings")


@router.get(
    "/{department_id}",
    response_model=DepartmentIntegrationSettingsResponse,
    summary="Настройки интеграции отдела с Jira/Zephyr/Confluence",
    description=(
        "Отдаёт эффективные настройки отдела — пустые поля, если строка ещё не "
        "создана (`id: null`). Доступен пользователям этого же отдела."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        403: {"description": "DEPARTMENT_ISOLATION — настройки чужого отдела."},
    },
)
async def get_department_integration_settings(
    department_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> DepartmentIntegrationSettingsResponse:
    """Get настроек отдела. Пользователь этого же отдела."""
    data = await svc.get_effective_for(db, identity, department_id)
    return DepartmentIntegrationSettingsResponse(**data)


@router.put(
    "/{department_id}",
    response_model=DepartmentIntegrationSettingsResponse,
    summary="Изменить настройки интеграции отдела",
    description=(
        "Upsert — создаёт строку при первом вызове, иначе обновляет только "
        "переданные поля. credential_id/confluence_credential_id/"
        "bitbucket_credential_id проверяются в secret_service правами "
        "вызывающего перед сохранением."
    ),
    responses={
        403: {"description": "Нет роли с `update`, либо secret_service отказал в доступе к credential."},
        404: {"description": "CREDENTIAL_NOT_FOUND — credential не существует или не видна вызывающему."},
        422: {"description": "CREDENTIAL_SCOPE_INVALID — ссылка указывает на personal-credential."},
    },
)
async def upsert_department_integration_settings(
    department_id: str,
    body: DepartmentIntegrationSettingsUpdate,
    identity: CurrentUserIdentity,
    token: BearerToken,
    db: AsyncSession = Depends(get_db),
) -> DepartmentIntegrationSettingsResponse:
    """PUT настроек отдела. Доступ: `(department_integration_settings, *, update)`."""
    obj = await svc.upsert(db, identity, department_id, body, token)
    return DepartmentIntegrationSettingsResponse.model_validate(obj)


@router.get(
    "/{department_id}/sprint-board",
    response_model=JiraSprintBoardResponse,
    summary="Дубль доски активного спринта Jira отдела (read-only)",
    description=(
        "Тянет активный спринт настроенной доски Jira и группирует его issue "
        "по статусу. Только чтение — в Jira ничего не пишется. Отсутствие "
        "настройки, активного спринта или недоступность Jira отдаётся как "
        "`warning` в теле ответа, не как ошибка. Доступен пользователям этого "
        "же отдела, как и остальные GET этого файла."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        403: {"description": "DEPARTMENT_ISOLATION — доска чужого отдела."},
    },
)
async def get_department_sprint_board(
    department_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> JiraSprintBoardResponse:
    """Get доски активного спринта отдела. Пользователь этого же отдела."""
    return await sprint_board_svc.get_sprint_board(db, identity, department_id)
