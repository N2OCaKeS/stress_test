"""`/department-test-account` — «Тестовая учётка» отдела.

Логин, пароль и SSH-ключ пользователя исполнения теста. Хранятся в
secret_service (credential scope=service, service=test_account), здесь —
только ссылка. Оба метода гейтятся `(department_test_account, view|update)`
своего отдела; department_admin проходит мимо матрицы. Пароль и приватный
ключ в ответ не попадают никогда.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import BearerToken, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.department_test_account import (
    DepartmentTestAccountResponse,
    DepartmentTestAccountUpdate,
)
from src.services import test_account as svc

router = APIRouter(prefix="/department-test-account")


@router.get(
    "/{department_id}",
    response_model=DepartmentTestAccountResponse,
    summary="Тестовая учётка отдела",
    description=(
        "Логин, публичный SSH-ключ и шаблон домашнего каталога тестовой учётки. "
        "Пароль и приватный ключ не отдаются. Учётка не настроена — "
        "`configured=false` и подсказка логина `login_hint`."
    ),
    responses={
        403: {"description": "PERMISSION_DENIED — нет `(department_test_account, view)` или чужой отдел."},
        503: {"description": "SECRET_SERVICE_* — secret_service недоступен."},
    },
)
async def get_department_test_account(
    department_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> DepartmentTestAccountResponse:
    """GET карточки учётки. Доступ: admin testing_service или department_admin своего отдела."""
    return DepartmentTestAccountResponse(**await svc.get_for(db, identity, department_id))


@router.put(
    "/{department_id}",
    response_model=DepartmentTestAccountResponse,
    summary="Задать или сменить тестовую учётку отдела",
    description=(
        "Заводит (первый вызов, пароль обязателен) или меняет credential учётки в "
        "secret_service правами вызывающего. SSH-пара генерируется сервисом: на "
        "первой настройке всегда, дальше — по `regenerate_ssh_key`. Изменения "
        "действуют со следующей подготовки стенда."
    ),
    responses={
        403: {"description": "PERMISSION_DENIED / CREDENTIAL_ACCESS_DENIED — нет права здесь или в secret_service."},
        422: {"description": "TEST_ACCOUNT_PASSWORD_REQUIRED или невалидные поля."},
        503: {"description": "SECRET_SERVICE_* — secret_service недоступен."},
    },
)
async def upsert_department_test_account(
    department_id: str,
    body: DepartmentTestAccountUpdate,
    identity: CurrentUserIdentity,
    token: BearerToken,
    db: AsyncSession = Depends(get_db),
) -> DepartmentTestAccountResponse:
    """PUT учётки. Доступ: `(department_test_account, update)` своего отдела."""
    return DepartmentTestAccountResponse(**await svc.upsert(db, identity, department_id, body, token))
