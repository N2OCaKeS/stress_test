"""CRUD `/department-test-settings` (§2.4 плана миграции).

GET открыт любому аутентифицированному актору и никогда не отдаёт 404 —
отсутствие строки в БД означает дефолты (`retry_enabled=true`,
`test_username="u"`). PUT (upsert) — под матрицей прав
`(department_test_settings, *, update)`.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.department_test_settings import (
    DepartmentTestSettingsResponse,
    DepartmentTestSettingsUpdate,
)
from src.services import department_test_settings as svc

router = APIRouter(prefix="/department-test-settings")


@router.get(
    "/{department_id}",
    response_model=DepartmentTestSettingsResponse,
    summary="Настройки тестирования отдела",
    description=(
        "Отдаёт эффективные настройки отдела — дефолты, если строка ещё не "
        "создана (`id: null`). Доступен любому аутентифицированному актору."
    ),
    responses={401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."}},
)
async def get_department_test_settings(
    department_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> DepartmentTestSettingsResponse:
    """Get настроек отдела. Любой аутентифицированный актор."""
    data = await svc.get_effective(db, department_id)
    return DepartmentTestSettingsResponse(**data)


@router.put(
    "/{department_id}",
    response_model=DepartmentTestSettingsResponse,
    summary="Изменить настройки тестирования отдела",
    description=(
        "Upsert — создаёт строку при первом вызове, иначе обновляет только "
        "переданные поля. Незаданные поля первого вызова берут дефолты."
    ),
    responses={403: {"description": "Нет роли с `update`."}},
)
async def upsert_department_test_settings(
    department_id: str,
    body: DepartmentTestSettingsUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> DepartmentTestSettingsResponse:
    """PUT настроек отдела. Доступ: `(department_test_settings, *, update)`."""
    obj = await svc.upsert(db, identity, department_id, body)
    return DepartmentTestSettingsResponse.model_validate(obj)
