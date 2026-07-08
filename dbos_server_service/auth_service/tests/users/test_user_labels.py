"""`GET /users/labels?ids=usr_a,usr_b` — батч-резолв user_id → имена юзера.

Обратное направление к `/users/resolve`: UI получает набор user_id (например
владельцев/получателей grant'ов personal-секрета) и подставляет человекочитаемые
имена. Username, display_name и ФИО — не чувствительные данные, поэтому доступ —
любой user-context.

Что проверяем:
* обычный юзер резолвит имя по id (в т.ч. чужого отдела — scope не режется);
* несколько id за раз → словарь;
* значение несёт username + display_name + ФИО; у юзера без ФИО поля = null;
* несуществующий id молча пропускается;
* ответ содержит только labels, без статусов/ролей/email;
* m2m (client_credentials) → 403 USER_CONTEXT_REQUIRED;
* без токена → 401;
* пустой/отсутствующий `ids` → 422.
"""

import pytest_asyncio

from tests.conftest import _make_user

LABELS_URL = "/api/auth/v1/users/labels"
CLIENTS_URL = "/api/auth/v1/oauth2/clients"
TOKEN_URL = "/api/auth/v1/oauth2/token"


@pytest_asyncio.fixture()
async def user_with_fio(db, dept_a):
    """Юзер с заполненными display_name и ФИО — для проверки, что labels отдаёт
    все имена, а не только username."""
    return await _make_user(
        db, "t_user_fio", "User12345678!",
        department_id=dept_a.id,
        display_name="Ксюша",
        last_name="Иванова",
        first_name="Ксения",
        middle_name="Петровна",
    )


async def _labels(client, token, ids):
    return await client.get(
        LABELS_URL,
        params={"ids": ids},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_regular_user_resolves_single_label(client, user_a_token, user_a):
    resp = await _labels(client, user_a_token, user_a.id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["labels"] == {
        user_a.id: {
            "user_id": user_a.id,
            "username": "t_user_a",
            "display_name": None,
            "last_name": None,
            "first_name": None,
            "middle_name": None,
        }
    }


async def test_resolves_batch(client, user_a_token, user_a, dept_admin_a):
    resp = await _labels(client, user_a_token, f"{user_a.id},{dept_admin_a.id}")
    assert resp.status_code == 200
    labels = resp.json()["labels"]
    assert labels[user_a.id]["username"] == "t_user_a"
    assert labels[dept_admin_a.id]["username"] == "t_dept_admin_a"


async def test_label_carries_fio(client, user_a_token, user_with_fio):
    """У юзера с заполненным ФИО labels отдаёт last/first/middle_name и
    display_name — фронт может собрать полное имя."""
    resp = await _labels(client, user_a_token, user_with_fio.id)
    assert resp.status_code == 200
    entry = resp.json()["labels"][user_with_fio.id]
    assert entry == {
        "user_id": user_with_fio.id,
        "username": "t_user_fio",
        "display_name": "Ксюша",
        "last_name": "Иванова",
        "first_name": "Ксения",
        "middle_name": "Петровна",
    }


async def test_label_fio_null_when_absent(client, user_a_token, user_a):
    """У юзера без ФИО поля ФИО и display_name = null (фронт фолбэкнется на
    username)."""
    resp = await _labels(client, user_a_token, user_a.id)
    assert resp.status_code == 200
    entry = resp.json()["labels"][user_a.id]
    assert entry["last_name"] is None
    assert entry["first_name"] is None
    assert entry["middle_name"] is None
    assert entry["display_name"] is None
    assert entry["username"] == "t_user_a"


async def test_batch_mixes_fio_and_plain(client, user_a_token, user_a, user_with_fio):
    """Батч с юзером с ФИО и юзером без — каждый отдаётся со своими полями."""
    resp = await _labels(client, user_a_token, f"{user_a.id},{user_with_fio.id}")
    assert resp.status_code == 200
    labels = resp.json()["labels"]
    assert labels[user_a.id]["last_name"] is None
    assert labels[user_with_fio.id]["last_name"] == "Иванова"
    assert labels[user_with_fio.id]["first_name"] == "Ксения"


async def test_cross_dept_label_still_resolved(client, user_a_token, user_b):
    """Имена не чувствительны — резолв не режется по отделу (в отличие от
    /resolve, который скрывает существование чужого username)."""
    resp = await _labels(client, user_a_token, user_b.id)
    assert resp.status_code == 200
    assert resp.json()["labels"][user_b.id]["username"] == "t_user_b"


async def test_nonexistent_id_skipped(client, user_a_token, user_a):
    resp = await _labels(client, user_a_token, f"{user_a.id},usr_does_not_exist")
    assert resp.status_code == 200
    labels = resp.json()["labels"]
    assert set(labels.keys()) == {user_a.id}
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
    # Значение — только имена: id, username, display_name, ФИО. Никаких
    # статусов/ролей/email/department.
    entry = body["labels"][user_a.id]
    assert set(entry.keys()) == {
        "user_id", "username", "display_name",
        "last_name", "first_name", "middle_name",
    }


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
