"""`GET /users/resolve?username=<exact>` — точечный username → user_id lookup.

Dept-scoped резолв для адресации шаринга personal-секретов: обычный юзер
находит id коллеги по точному username, не получая список всех юзеров и не
имея возможности enumerate'ить чужие отделы.

Что проверяем:
* same-dept резолв успешен (обычный юзер находит коллегу своего отдела);
* cross-dept → 404 (не палим существование чужого username);
* несуществующий username → 404;
* account_admin резолвит cross-dept;
* department_admin — свой отдел, чужой → 404;
* ответ содержит только id/username/department_id (без email/status/role);
* m2m (client_credentials) → 403 USER_CONTEXT_REQUIRED;
* без токена → 401.
"""

RESOLVE_URL = "/api/auth/v1/users/resolve"
CLIENTS_URL = "/api/auth/v1/oauth2/clients"
TOKEN_URL = "/api/auth/v1/oauth2/token"


async def _resolve(client, token, username):
    return await client.get(
        RESOLVE_URL,
        params={"username": username},
        headers={"Authorization": f"Bearer {token}"},
    )


class TestSameDepartmentResolve:
    async def test_regular_user_resolves_same_dept_colleague(
        self, client, user_a_token, dept_admin_a, dept_a,
    ):
        """Обычный юзер dept_a находит коллегу (dept_admin_a) того же отдела."""
        resp = await _resolve(client, user_a_token, "t_dept_admin_a")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == dept_admin_a.id
        assert body["username"] == "t_dept_admin_a"
        assert body["department_id"] == dept_a.id

    async def test_regular_user_resolves_self(self, client, user_a_token, user_a):
        resp = await _resolve(client, user_a_token, "t_user_a")
        assert resp.status_code == 200
        assert resp.json()["user_id"] == user_a.id

    async def test_response_has_no_sensitive_fields(
        self, client, user_a_token, dept_admin_a,
    ):
        """Только id/username/department_id — без email/status/platform_role/roles."""
        resp = await _resolve(client, user_a_token, "t_dept_admin_a")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"user_id", "username", "department_id"}


class TestCrossDepartmentHidden:
    async def test_regular_user_cross_dept_returns_404(
        self, client, user_a_token, user_b,
    ):
        """Юзер dept_a не может зарезолвить юзера dept_b — 404, не 403."""
        resp = await _resolve(client, user_a_token, "t_user_b")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "USER_NOT_FOUND"

    async def test_dept_admin_cross_dept_returns_404(
        self, client, dept_admin_a_token, user_b,
    ):
        resp = await _resolve(client, dept_admin_a_token, "t_user_b")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "USER_NOT_FOUND"


class TestNonexistent:
    async def test_nonexistent_username_returns_404(self, client, user_a_token):
        resp = await _resolve(client, user_a_token, "no_such_user_xyz")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "USER_NOT_FOUND"

    async def test_blank_username_rejected(self, client, user_a_token):
        """Пустой username не проходит валидацию query-параметра (422)."""
        resp = await client.get(
            RESOLVE_URL,
            params={"username": ""},
            headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp.status_code == 422

    async def test_missing_username_param_rejected(self, client, user_a_token):
        resp = await client.get(
            RESOLVE_URL,
            headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp.status_code == 422


class TestAdminWiderScope:
    async def test_account_admin_resolves_cross_dept(
        self, client, admin_token, user_b, dept_b,
    ):
        """account_admin режется не по отделу — видит любой username."""
        resp = await _resolve(client, admin_token, "t_user_b")
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == user_b.id
        assert body["department_id"] == dept_b.id

    async def test_dept_admin_resolves_own_dept(
        self, client, dept_admin_a_token, user_a, dept_a,
    ):
        resp = await _resolve(client, dept_admin_a_token, "t_user_a")
        assert resp.status_code == 200
        assert resp.json()["user_id"] == user_a.id


class TestNonUserContext:
    async def _make_m2m_token(self, client, admin_token, dept_id):
        create = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "department_id": dept_id,
                "name": "resolve_m2m_client",
                "grant_types": ["client_credentials"],
                "redirect_uris": [],
                "allowed_scopes": [],
            },
        )
        assert create.status_code == 201, create.text
        cred = create.json()
        issued = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "client_credentials",
                "client_id": cred["client_id"],
                "client_secret": cred["client_secret"],
            },
        )
        assert issued.status_code == 200, issued.text
        return issued.json()["access_token"]

    async def test_m2m_token_rejected(self, client, admin_token, dept_a):
        token = await self._make_m2m_token(client, admin_token, dept_a.id)
        resp = await _resolve(client, token, "t_user_a")
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "USER_CONTEXT_REQUIRED"

    async def test_unauthenticated_returns_401(self, client):
        resp = await client.get(RESOLVE_URL, params={"username": "t_user_a"})
        assert resp.status_code == 401
