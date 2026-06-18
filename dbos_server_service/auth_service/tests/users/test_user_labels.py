"""`GET /users/labels?ids=usr_a,usr_b` — батч-резолв user_id → username.

Обратное направление к `/users/resolve`: UI получает набор user_id (например
владельцев/получателей grant'ов personal-секрета) и подставляет человекочитаемые
имена. Username — не чувствительные данные, поэтому доступ — любой user-context.

Что проверяем:
* обычный юзер резолвит имя по id (в т.ч. чужого отдела — scope не режется);
* несколько id за раз → словарь;
* несуществующий id молча пропускается;
* ответ содержит только labels {id: username}, без статусов/ролей/email;
* m2m (client_credentials) → 403 USER_CONTEXT_REQUIRED;
* без токена → 401;
* пустой/отсутствующий `ids` → 422.
"""

LABELS_URL = "/api/auth/v1/users/labels"
CLIENTS_URL = "/api/auth/v1/oauth2/clients"
TOKEN_URL = "/api/auth/v1/oauth2/token"


async def _labels(client, token, ids):
    return await client.get(
        LABELS_URL,
        params={"ids": ids},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_regular_user_resolves_single_label(client, user_a_token, user_a):
    resp = await _labels(client, user_a_token, user_a.id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["labels"] == {user_a.id: "t_user_a"}


async def test_resolves_batch(client, user_a_token, user_a, dept_admin_a):
    resp = await _labels(client, user_a_token, f"{user_a.id},{dept_admin_a.id}")
    assert resp.status_code == 200
    labels = resp.json()["labels"]
    assert labels[user_a.id] == "t_user_a"
    assert labels[dept_admin_a.id] == "t_dept_admin_a"


async def test_cross_dept_label_still_resolved(client, user_a_token, user_b):
    """Username не чувствителен — резолв не режется по отделу (в отличие от
    /resolve, который скрывает существование чужого username)."""
    resp = await _labels(client, user_a_token, user_b.id)
    assert resp.status_code == 200
    assert resp.json()["labels"] == {user_b.id: "t_user_b"}


async def test_nonexistent_id_skipped(client, user_a_token, user_a):
    resp = await _labels(client, user_a_token, f"{user_a.id},usr_does_not_exist")
    assert resp.status_code == 200
    labels = resp.json()["labels"]
    assert labels == {user_a.id: "t_user_a"}
    assert "usr_does_not_exist" not in labels


async def test_all_nonexistent_returns_empty(client, user_a_token):
    resp = await _labels(client, user_a_token, "usr_nope1,usr_nope2")
    assert resp.status_code == 200
    assert resp.json()["labels"] == {}


async def test_response_has_no_sensitive_fields(client, user_a_token, user_a):
    resp = await _labels(client, user_a_token, user_a.id)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"labels"}
    # Значения — только строки-username, не вложенные объекты со статусами/ролями.
    assert all(isinstance(v, str) for v in body["labels"].values())


async def test_missing_ids_param_rejected(client, user_a_token):
    resp = await client.get(
        LABELS_URL, headers={"Authorization": f"Bearer {user_a_token}"}
    )
    assert resp.status_code == 422


async def test_blank_ids_param_rejected(client, user_a_token):
    resp = await _labels(client, user_a_token, "")
    assert resp.status_code == 422


class TestNonUserContext:
    async def _make_m2m_token(self, client, admin_token, dept_id):
        create = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "department_id": dept_id,
                "name": "labels_m2m_client",
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

    async def test_m2m_token_rejected(self, client, admin_token, dept_a, user_a):
        token = await self._make_m2m_token(client, admin_token, dept_a.id)
        resp = await _labels(client, token, user_a.id)
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "USER_CONTEXT_REQUIRED"

    async def test_unauthenticated_returns_401(self, client, user_a):
        resp = await client.get(LABELS_URL, params={"ids": user_a.id})
        assert resp.status_code == 401
