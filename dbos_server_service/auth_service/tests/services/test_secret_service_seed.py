"""secret_service: системная роль `admin` и платформенная роль `service_admin`.

Проверки:
  * После grant_service_access к secret_service в каждом dept появляется
    `admin` (is_system=True, is_active=True).
  * `service_admin` — валидное значение PlatformRole, проходит CHECK
    `ck_users_platform_role`, создаётся без department_id.
  * Запись пользователя с `platform_role=service_admin` идёт через POST
    `/users` (account_admin only); department_id не требуется.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.core.constants import PlatformRole
from src.models import PlatformService, ServiceRoleDefinition, User
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


# ── 2. platform_role `service_admin` ─────────────────────────────────────────

def test_service_admin_in_platform_role_enum():
    """`service_admin` присутствует в PlatformRole enum и сравнивается со строкой."""
    assert PlatformRole.SERVICE_ADMIN == "service_admin"
    assert PlatformRole("service_admin") is PlatformRole.SERVICE_ADMIN


async def test_create_user_with_service_admin_platform_role(
    client, admin_token,
):
    """`service_admin` — это cross-dept роль, department_id не нужен."""
    resp = await client.post(
        "/api/auth/v1/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "svc_admin_test",
            "password": "ServiceAdm1234!",
            "platform_role": "service_admin",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["platform_role"] == "service_admin"
    assert body.get("department_id") is None


async def test_service_admin_persisted_with_null_department(
    db, client, admin_token,
):
    resp = await client.post(
        "/api/auth/v1/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "svc_admin_persisted",
            "password": "ServiceAdm1234!",
            "platform_role": "service_admin",
        },
    )
    assert resp.status_code == 201, resp.text

    user = await db.scalar(
        select(User).where(User.username == "svc_admin_persisted")
    )
    assert user is not None
    assert user.platform_role == "service_admin"
    assert user.department_id is None


# ── 3. PlatformRole enum coverage ────────────────────────────────────────────

@pytest.mark.parametrize(
    "role_name",
    ["account_admin", "department_admin", "loging_admin", "loging_reader", "service_admin"],
)
def test_platform_role_string_round_trip(role_name: str):
    """Все значения PlatformRole коэрсятся туда-обратно. Регрессия на тот
    случай, когда CHECK constraint расходится с enum'ом."""
    role = PlatformRole(role_name)
    assert role.value == role_name
