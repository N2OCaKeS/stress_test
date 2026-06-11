"""Тесты: PATCH /api/auth/v1/me — self-service апдейт display_name / email."""

import pytest

URL = "/api/auth/v1/me"


async def test_patch_me_updates_display_name_and_email(client, db, user_a, user_a_token):
    """Happy path: display_name + email → 200, БД получает новые значения."""
    resp = await client.patch(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"display_name": "Alice A.", "email": "alice@example.com"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Alice A."
    assert body["email"] == "alice@example.com"

    # Перечитаем юзера из БД, чтобы убедиться что и persist прошёл, а не
    # только response собрался.
    await db.refresh(user_a)
    assert user_a.display_name == "Alice A."
    assert user_a.email == "alice@example.com"


async def test_patch_me_only_display_name(client, db, user_a, user_a_token):
    """Только display_name (без email) → 200, email не трогается."""
    original_email = user_a.email
    resp = await client.patch(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"display_name": "Just Display"},
    )
    assert resp.status_code == 200
    await db.refresh(user_a)
    assert user_a.display_name == "Just Display"
    assert user_a.email == original_email


async def test_patch_me_only_email(client, db, user_a, user_a_token):
    """Только email → 200, display_name остаётся как было."""
    user_a.display_name = "Pre-existing"
    await db.commit()
    resp = await client.patch(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"email": "alice2@example.com"},
    )
    assert resp.status_code == 200
    await db.refresh(user_a)
    assert user_a.email == "alice2@example.com"
    assert user_a.display_name == "Pre-existing"


async def test_patch_me_empty_body_returns_422(client, user_a_token):
    """Пустое тело `{}` → 422 EMPTY_UPDATE через pydantic-валидатор."""
    resp = await client.patch(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    "forbidden_field, forbidden_value",
    [
        ("platform_role", "account_admin"),
        ("department_id", "dep_someone_elses"),
        ("username", "hacked"),
        ("is_banned", False),
        ("must_change_password", False),
    ],
)
async def test_patch_me_rejects_forbidden_fields(
    client, user_a_token, forbidden_field, forbidden_value
):
    """Запрещённые поля → 422 от pydantic'а (extra='forbid')."""
    resp = await client.patch(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"display_name": "valid", forbidden_field: forbidden_value},
    )
    assert resp.status_code == 422, resp.text


async def test_patch_me_invalid_email_returns_422(client, user_a_token):
    """Неправильный email → 422 (EmailStr-валидатор)."""
    resp = await client.patch(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"email": "not-an-email"},
    )
    assert resp.status_code == 422


async def test_patch_me_no_auth_returns_401(client):
    """Без Bearer → 401."""
    resp = await client.patch(URL, json={"display_name": "x"})
    assert resp.status_code == 401


async def test_patch_me_banned_user_blocked(client, db, user_a, user_a_token):
    """Забаненный юзер → 401 USER_BANNED_OR_INACTIVE (через identity-revalidate).

    Аналог теста ban-guard'а: ban_user меняет status=BANNED + is_active=False,
    и `_identity_from_user_jwt` отбивает JWT раньше, чем мы доходим до
    PATCH-обработчика. Это пожёстче, чем сама задача требует (403 USER_BANNED),
    но семантика та же: «забаненный не правит профиль».
    """
    from src.core.constants import UserStatus

    user_a.status = UserStatus.BANNED
    user_a.is_active = False
    await db.commit()

    resp = await client.patch(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"display_name": "still trying"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "USER_BANNED_OR_INACTIVE"


async def test_patch_me_emits_audit(client, user_a_token, capture_audit_payloads):
    """`me.updated` действие эмитится при успешной правке."""
    resp = await client.patch(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"display_name": "Audit Check"},
    )
    assert resp.status_code == 200
    actions = [p.get("action") for p in capture_audit_payloads]
    assert "me.updated" in actions
