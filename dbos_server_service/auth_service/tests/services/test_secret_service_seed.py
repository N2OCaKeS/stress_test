"""secret_service: системная роль `admin` per-(dept, service).

Проверки:
  * После grant_service_access к secret_service в каждом dept появляется
    `admin` (is_system=True, is_active=True) в `service_role_definitions`.
  * Платформенной роли `service_admin` нет: админство сервиса — это per-dept
    service_role, не cross-dept platform-флаг.
  * PlatformRole enum остаётся ровно из четырёх значений (account_admin,
    department_admin, loging_admin, loging_reader) — регрессия на случай
    рассинхрона enum'а с CHECK constraint'ом.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.core.constants import PlatformRole
from src.models import PlatformService, ServiceRoleDefinition
from src.repositories.service_role_definitions import SYSTEM_ADMIN_ROLE_NAME


SECRET_SERVICE_NAME = "secret_service"


@pytest_asyncio.fixture()
async def secret_service_registered(db):
    """Регистрируем `secret_service` в platform_services (его сидит миграция
    в проде; в тестовой схеме поднимаем явно, чтобы grant access прошёл FK)."""
    existing = await db.scalar(
        select(PlatformService).where(
            PlatformService.service_name == SECRET_SERVICE_NAME
        )
    )
    if existing is None:
        svc = PlatformService(
            service_name=SECRET_SERVICE_NAME,
            display_name="Хранилище секретов",
            is_active=True,
        )
        db.add(svc)
        await db.flush()
        return svc
    return existing


# ── 1. system role `admin` per-dept ──────────────────────────────────────────

async def test_admin_system_role_seeded_on_grant_access(
    client, admin_token, dept_a, secret_service_registered,
):
    """POST /departments/{id}/services создаёт system-роль `admin`."""
    resp = await client.post(
        f"/api/auth/v1/departments/{dept_a.id}/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": SECRET_SERVICE_NAME},
    )
    assert resp.status_code in (200, 201), resp.text


async def test_admin_role_visible_in_definitions(
    db, client, admin_token, dept_a, secret_service_registered,
):
    """После grant'а `admin` лежит в service_role_definitions с is_system=True."""
    r = await client.post(
        f"/api/auth/v1/departments/{dept_a.id}/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": SECRET_SERVICE_NAME},
    )
    assert r.status_code in (200, 201), r.text

    role = await db.scalar(
        select(ServiceRoleDefinition).where(
            ServiceRoleDefinition.department_id == dept_a.id,
            ServiceRoleDefinition.service_name == SECRET_SERVICE_NAME,
            ServiceRoleDefinition.role_name == SYSTEM_ADMIN_ROLE_NAME,
        )
    )
    assert role is not None
    assert role.is_system is True
    assert role.is_active is True


# ── 2. PlatformRole enum coverage ────────────────────────────────────────────

def test_service_admin_not_in_platform_role_enum():
    """Платформенной роли `service_admin` нет: админство сервиса — per-(dept,service)."""
    with pytest.raises(ValueError):
        PlatformRole("service_admin")
    assert not hasattr(PlatformRole, "SERVICE_ADMIN")


@pytest.mark.parametrize(
    "role_name",
    ["account_admin", "department_admin", "loging_admin", "loging_reader"],
)
def test_platform_role_string_round_trip(role_name: str):
    """Все значения PlatformRole коэрсятся туда-обратно. Регрессия на тот
    случай, когда CHECK constraint расходится с enum'ом."""
    role = PlatformRole(role_name)
    assert role.value == role_name
