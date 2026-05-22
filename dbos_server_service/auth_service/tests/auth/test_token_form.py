"""`POST /token` — OAuth2 password flow для Swagger UI Authorize.

Не задокументирован в OpenAPI (`include_in_schema=False`), но валиден как
endpoint и пишет audit как обычный логин. Едитируется через form-data,
не JSON.
"""

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
        # FastAPI вернёт 422 на отсутствие username/password в form
        assert resp.status_code in (422, 415, 400)

    async def test_not_in_openapi_schema(self, client):
        """`include_in_schema=False` — этот endpoint не должен попадать в /openapi.json."""
        resp = await client.get("/openapi.json")
        spec = resp.json()
        token_path = "/api/auth/v1/token"
        assert token_path not in spec.get("paths", {}), (
            f"{token_path} must be hidden from OpenAPI (used internally by Swagger)"
        )
