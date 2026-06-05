"""Регрессия на race-ветку `reloaded is None` в `auth_service.login`.

После того как `auto_unban_if_expired` отрабатывает успешно, login делает
полный `user_repo.get_by_id(user.id)` — а не `db.refresh(user)` — чтобы
прочитать свежий status из БД (CAS-победитель мог сделать commit в чужой
сессии). Если между commit'ом auto-unban'а и этим re-SELECT юзер был удалён
(другой админ дёрнул DELETE /users/{id}), `get_by_id` вернёт None, и код
должен молча продолжить с in-memory объектом (status у которого уже ACTIVE
через update inside `auto_unban_if_expired`) — без AttributeError на
`reloaded.status` и без падения в 500.

Тест прицельный: подменяем `user_repo.get_by_id` на стаб, возвращающий None,
и проверяем, что login проходит до verify_password без craш'а на None.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from src.models import Ban


USERS_URL = "/api/auth/v1/users"
LOGIN_URL = "/api/auth/v1/login"


class TestLoginAutoUnbanReloadedNone:
    async def test_get_by_id_returning_none_does_not_crash_login(
        self, client, admin_token, user_a, db, monkeypatch,
    ):
        """`get_by_id` после auto-unban'а отдаёт None → login должен пройти,
        используя in-memory `user` (которому `auto_unban_if_expired` уже
        проставил `status=ACTIVE`). Без фикса — крэш на следующем `if user.status`."""

        # 1. Готовим истёкший temporary ban (expires_at в прошлом через прямой UPDATE).
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        ban_resp = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "race", "expires_at": future},
        )
        assert ban_resp.status_code == 200, ban_resp.text

        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db.execute(
            update(Ban).where(Ban.user_id == user_a.id).values(expires_at=past)
        )
        await db.commit()

        # 2. Подменяем `UserRepository.get_by_id` — отдаёт None, имитируя
        #    «между commit'ом auto-unban'а и re-SELECT'ом юзер удалён».
        #    `get_by_username` (первый вызов в login) трогать нельзя — он
        #    нужен для самого захода в код-путь.
        from src.repositories.users import UserRepository

        original_get_by_id = UserRepository.get_by_id

        async def _get_by_id_none(self, uid):
            return None

        monkeypatch.setattr(UserRepository, "get_by_id", _get_by_id_none)

        # 3. Login — без фикса упал бы 500'кой. С фиксом — 200 (auto-unban
        #    уже выставил user.status=ACTIVE прямо в in-memory объекте).
        try:
            login = await client.post(
                LOGIN_URL,
                json={"username": "t_user_a", "password": "User1234!"},
            )
        finally:
            monkeypatch.setattr(UserRepository, "get_by_id", original_get_by_id)

        assert login.status_code == 200, login.text
        body = login.json()
        # Свежевыданный access_token присутствует.
        assert "access_token" in body
        assert body.get("token_type", "").lower() == "bearer"
