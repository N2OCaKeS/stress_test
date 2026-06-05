"""`POST /token` — OAuth2 password flow для Swagger UI Authorize.

Не задокументирован в OpenAPI (`include_in_schema=False`), но валиден как
endpoint и пишет audit как обычный логин. Едитируется через form-data,
не JSON.
"""

from src.core.security import hash_password
from src.models import User
from src.utils.ids import _new_id

TOKEN_URL = "/api/auth/v1/token"


class TestTokenForm:
    async def test_valid_credentials_return_access_token(self, client, account_admin):
        resp = await client.post(
            TOKEN_URL,
            data={"username": "t_admin", "password": "Admin1234!"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "Bearer"
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["identity"]["username"] == "t_admin"

    async def test_invalid_password_returns_401(self, client, account_admin):
        resp = await client.post(
            TOKEN_URL,
            data={"username": "t_admin", "password": "WrongPassword!"},
        )
        assert resp.status_code == 401

    async def test_unknown_user_returns_401(self, client):
        resp = await client.post(
            TOKEN_URL,
            data={"username": "ghost_user", "password": "any"},
        )
        assert resp.status_code == 401

    async def test_missing_form_fields_returns_422(self, client):
        resp = await client.post(TOKEN_URL, data={"username": "only-this"})
        assert resp.status_code == 422

    async def test_json_body_is_rejected(self, client, account_admin):
        """OAuth2 form-flow требует form-data, JSON-тело не должно проходить."""
        resp = await client.post(
            TOKEN_URL,
            json={"username": "t_admin", "password": "Admin1234!"},
        )
        # FastAPI вернёт 422 на отсутствие username/password в form-полях
        # (JSON-body не парсится как form, поля «не найдены»).
        assert resp.status_code == 422, resp.text

    async def test_not_in_openapi_schema(self, client):
        """`include_in_schema=False` — этот endpoint не должен попадать в /openapi.json."""
        resp = await client.get("/openapi.json")
        spec = resp.json()
        token_path = "/api/auth/v1/token"
        assert token_path not in spec.get("paths", {}), (
            f"{token_path} must be hidden from OpenAPI (used internally by Swagger)"
        )

    async def test_banned_user_returns_403(self, client, db):
        """Form-flow дёргает тот же `auth_service.login`: бан = 403 USER_BANNED."""
        user = User(
            id=_new_id("usr_"),
            username="tf_banned",
            password_hash=hash_password("Pass1234!"),
            status="banned",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        resp = await client.post(
            TOKEN_URL,
            data={"username": "tf_banned", "password": "Pass1234!"},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "USER_BANNED"

    async def test_lockout_after_5_failed_attempts(self, client, db):
        """5 неудачных + правильный пароль → 429 ACCOUNT_TEMPORARILY_LOCKED."""
        user = User(
            id=_new_id("usr_"),
            username="tf_lockout",
            password_hash=hash_password("Correct1!"),
            status="active",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        for _ in range(5):
            await client.post(
                TOKEN_URL,
                data={"username": "tf_lockout", "password": "wrong"},
            )
        resp = await client.post(
            TOKEN_URL,
            data={"username": "tf_lockout", "password": "Correct1!"},
        )
        assert resp.status_code == 429
        assert resp.json()["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"

    async def test_grant_type_password_explicit(self, client, account_admin):
        """OAuth2PasswordRequestForm принимает `grant_type=password` явно
        (Swagger так и шлёт). Поле не обязательно, но не должно ломать flow.
        """
        resp = await client.post(
            TOKEN_URL,
            data={
                "username": "t_admin",
                "password": "Admin1234!",
                "grant_type": "password",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["token_type"] == "Bearer"
