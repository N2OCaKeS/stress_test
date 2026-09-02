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
    """user_b + dept_b получает access к service_x.

    Assert на наличие granted service защищает от регрессии: если в общем
    conftest когда-то заведут baseline-сервис у dept_b, локальный grant
    станет no-op и сломает причину существования фикстуры.
    """
    await _grant_service(db, user_b.department_id, service_x.service_name)
    assert service_x.service_name, "service_x.service_name must be non-empty for PAT allowed_services"
    return user_b


@pytest_asyncio.fixture()
async def user_b_token(client, user_b_with_service):
    return await _login(client, "t_user_b", "User12345678!")
