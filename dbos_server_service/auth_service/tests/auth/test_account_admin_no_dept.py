"""account_admin может существовать без department_id — login/refresh/me
должны корректно отрабатывать на пользователе с `department_id=NULL`.

* `login` возвращает identity с `department_id=None`, `allowed_services=[]`,
  `service_roles={}` (см. `_build_identity` — для account_admin они принудительно
  обнуляются).
* `refresh` использует то же merge, не падает на `dept_id=None`.
* `/me` возвращает то же.
"""

from sqlalchemy import update

from src.models import User

LOGIN_URL = "/api/auth/v1/login"
REFRESH_URL = "/api/auth/v1/refresh"
ME_URL = "/api/auth/v1/me"


class TestAccountAdminNoDept:
    async def test_login_succeeds_without_department(self, client, account_admin):
        """`account_admin` фикстура создаётся с `department_id=None`."""
        resp = await client.post(
            LOGIN_URL, json={"username": "t_admin", "password": "Admin1234!"},
        )
        assert resp.status_code == 200
        identity = resp.json()["identity"]
        assert identity["department_id"] is None
        # account_admin: bypass — services и roles в identity всегда пустые
        assert identity["allowed_services"] == []
        assert identity["service_roles"] == {}
        assert identity["platform_role"] == "account_admin"

    async def test_me_returns_consistent_payload(self, client, admin_token):
        resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["department_id"] is None
        assert body["allowed_services"] == []
        assert body["service_roles"] == {}

    async def test_refresh_works_without_department(self, client, account_admin):
        """Сценарий: login → refresh → не падает на `list_active_services(None)`."""
        login = await client.post(
            LOGIN_URL, json={"username": "t_admin", "password": "Admin1234!"},
        )
        raw_refresh = login.json()["refresh_token"]

        resp = await client.post(REFRESH_URL, json={"refresh_token": raw_refresh})
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["refresh_token"]
        # старый refresh не должен снова работать (rotation)
        again = await client.post(REFRESH_URL, json={"refresh_token": raw_refresh})
        assert again.status_code == 401


class TestRefreshAfterDeptMove:
    async def test_refresh_reflects_new_department(
        self, client, db, account_admin, dept_a_with_service, dept_b,
    ):
        """user_x в dept_a → login → admin переносит в dept_b → refresh →
        новый access_token несёт обновлённый department_id."""
        # Создадим юзера в dept_a через repo
        from src.core.security import hash_password
        from src.utils.ids import _new_id
        u = User(
            id=_new_id("usr_"), username="t_mover",
            password_hash=hash_password("MoveMe1234!"),
            department_id=dept_a_with_service.id,
            status="active", is_active=True,
        )
        db.add(u)
        await db.commit()

        login = await client.post(
            LOGIN_URL, json={"username": "t_mover", "password": "MoveMe1234!"},
        )
        assert login.status_code == 200
        assert login.json()["identity"]["department_id"] == dept_a_with_service.id
        raw_refresh = login.json()["refresh_token"]

        # Перенесём в dept_b напрямую через UPDATE
        await db.execute(
            update(User).where(User.id == u.id).values(department_id=dept_b.id)
        )
        await db.commit()

        # JWT теперь несёт только `sub` + `actor_type` — department_id и роли
        # revalidate'ятся из БД на каждом запросе (см. `_build_access_token`).
        # Поэтому проверяем через /me: на новом access_token должен быть свежий dept.
        resp = await client.post(REFRESH_URL, json={"refresh_token": raw_refresh})
        assert resp.status_code == 200
        new_access = resp.json()["access_token"]

        me = await client.get(ME_URL, headers={"Authorization": f"Bearer {new_access}"})
        assert me.status_code == 200
        assert me.json()["department_id"] == dept_b.id
