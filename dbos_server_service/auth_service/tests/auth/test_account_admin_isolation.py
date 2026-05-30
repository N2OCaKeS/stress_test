"""`account_admin` намеренно не принадлежит ни одному отделу.

Это отдельный сценарий — платформенный администратор существует вне dept-scope:
* `department_id IS NULL` в БД;
* `_build_identity` обнуляет `allowed_services` и `service_roles`;
* `create_user(platform_role=account_admin)` не требует `department_id`.

Тест фиксирует контракт «account_admin без dept'а» — попытка создать
account_admin'а с привязкой к отделу должна быть видна как dept_id, просто
сохранённый в БД, но логически account_admin остаётся global-scope.
"""

import pytest

USERS_URL = "/api/auth/v1/users"


async def test_account_admin_can_be_created_without_department(
    client, admin_token,
):
    """`POST /users` с `platform_role=account_admin` без `department_id` — 201."""
    resp = await client.post(
        USERS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "t_second_admin",
            "password": "Admin5678!",
            "platform_role": "account_admin",
            # department_id опущен намеренно — это валидно для платформенного
            # админа (см. `_platform_admins` в create_user).
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["platform_role"] == "account_admin"
    assert body["department_id"] is None, (
        "account_admin не должен иметь привязки к департаменту — "
        "это отдельный пользователь вне dept-scope"
    )


async def test_seed_account_admin_has_no_department(client, admin_token):
    """Seed-админ (`t_admin` через фикстуру `account_admin`) не принадлежит отделу."""
    resp = await client.get(
        "/api/auth/v1/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform_role"] == "account_admin"
    assert body["department_id"] is None
    # account_admin: service-grants идут через bypass, не через service_roles.
    assert body["allowed_services"] == []
    assert body["service_roles"] == {}
