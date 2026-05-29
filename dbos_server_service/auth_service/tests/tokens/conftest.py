"""Локальные фикстуры для тестов PAT.

PATCreate требует `allowed_services` с минимум одним сервисом, причём
этот сервис должен быть доступен отделу актора (account_admin без отдела —
исключение, dept-чек пропускается). Поэтому user_b нужно дотягивать —
у базового `user_b` в общем conftest нет dept-service access.
"""

import pytest_asyncio

from tests.conftest import _grant_service, _login


@pytest_asyncio.fixture()
async def user_b_with_service(db, user_b, service_x):
    """user_b + dept_b получает access к service_x."""
    await _grant_service(db, user_b.department_id, service_x.service_name)
    return user_b


@pytest_asyncio.fixture()
async def user_b_token(client, user_b_with_service):
    return await _login(client, "t_user_b", "User1234!")
