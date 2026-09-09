"""CRUD `/department-integration-settings` (§2.4, §3.5 плана миграции).

GET открыт любому аутентифицированному актору, никогда не 404 — отсутствие
строки означает "интеграция не настроена" (все поля `null`). PUT (upsert) —
под матрицей `(department_integration_settings, *, update)`.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.department_integration_settings import (
    DepartmentIntegrationSettingsResponse,
    DepartmentIntegrationSettingsUpdate,
)
from src.services import department_integration_settings as svc

router = APIRouter(prefix="/department-integration-settings")


@router.get(
    "/{department_id}",
    response_model=DepartmentIntegrationSettingsResponse,
    summary="Настройки интеграции отдела с Jira/Zephyr/Confluence",
    description=(
        "Отдаёт эффективные настройки отдела — пустые поля, если строка ещё не "
        "создана (`id: null`). Доступен любому аутентифицированному актору."
    ),
    responses={401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."}},
)
async def get_department_integration_settings(
    department_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> DepartmentIntegrationSettingsResponse:
    """Get настроек отдела. Любой аутентифицированный актор."""
    data = await svc.get_effective(db, department_id)
    return DepartmentIntegrationSettingsResponse(**data)


@router.put(
    "/{department_id}",
    response_model=DepartmentIntegrationSettingsResponse,
    summary="Изменить настройки интеграции отдела",
    description=(
        "Upsert — создаёт строку при первом вызове, иначе обновляет только "
        "переданные поля."
    ),
    responses={403: {"description": "Нет роли с `update`."}},
)
async def upsert_department_integration_settings(
    department_id: str,
    body: DepartmentIntegrationSettingsUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> DepartmentIntegrationSettingsResponse:
    """PUT настроек отдела. Доступ: `(department_integration_settings, *, update)`."""
    obj = await svc.upsert(db, identity, department_id, body)
    return DepartmentIntegrationSettingsResponse.model_validate(obj)
