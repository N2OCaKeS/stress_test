"""Тесты ФИО пользователя (last_name / first_name / middle_name).

Проверяем весь контракт: create сохраняет и отдаёт ФИО, PATCH меняет,
`/me` и `/users` отдают поля, а юзер без заполненного ФИО возвращает null и
ничего не ломает.
"""

URL = "/api/auth/v1/users"
ME_URL = "/api/auth/v1/me"
LOGIN_URL = "/api/auth/v1/login"


async def test_create_user_with_fio_saved_and_returned(client, admin_token, dept_a):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "fio_user", "password": "Pass12345678!", "department_id": dept_a.id,
        "last_name": "Иванов", "first_name": "Иван", "middle_name": "Иванович",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["last_name"] == "Иванов"
    assert body["first_name"] == "Иван"
    assert body["middle_name"] == "Иванович"


async def test_create_user_without_fio_returns_null(client, admin_token, dept_a):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "fio_none", "password": "Pass12345678!", "department_id": dept_a.id,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["last_name"] is None
    assert body["first_name"] is None
    assert body["middle_name"] is None


async def test_create_user_with_partial_fio(client, admin_token, dept_a):
    """Только имя, без фамилии/отчества — остальные приходят null."""
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "fio_partial", "password": "Pass12345678!", "department_id": dept_a.id,
        "first_name": "Пётр",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["first_name"] == "Пётр"
    assert body["last_name"] is None
    assert body["middle_name"] is None


async def test_patch_updates_fio(client, admin_token, dept_a):
    create = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "fio_patch", "password": "Pass12345678!", "department_id": dept_a.id,
        "last_name": "Старый", "first_name": "Имя",
    })
    uid = create.json()["user_id"]

    resp = await client.patch(f"{URL}/{uid}", headers={"Authorization": f"Bearer {admin_token}"}, json={
        "last_name": "Новый", "middle_name": "Отчество",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["last_name"] == "Новый"
    assert body["first_name"] == "Имя"  # не трогали — сохранилось
    assert body["middle_name"] == "Отчество"


async def test_patch_clears_fio_with_null(client, admin_token, dept_a):
    """Явный null очищает поле ФИО."""
    create = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "fio_clear", "password": "Pass12345678!", "department_id": dept_a.id,
        "last_name": "Убрать", "first_name": "Тоже",
    })
    uid = create.json()["user_id"]

    resp = await client.patch(f"{URL}/{uid}", headers={"Authorization": f"Bearer {admin_token}"}, json={
        "last_name": None,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["last_name"] is None
    assert body["first_name"] == "Тоже"


async def test_fio_too_long_rejected(client, admin_token, dept_a):
    """Длина > 128 отбивается Pydantic'ом (422)."""
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "fio_long", "password": "Pass12345678!", "department_id": dept_a.id,
        "last_name": "я" * 129,
    })
    assert resp.status_code == 422, resp.text


async def test_get_user_returns_fio(client, admin_token, dept_a):
    create = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "fio_get", "password": "Pass12345678!", "department_id": dept_a.id,
        "last_name": "Петров", "first_name": "Пётр", "middle_name": "Петрович",
    })
    uid = create.json()["user_id"]

    resp = await client.get(f"{URL}/{uid}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["last_name"] == "Петров"
    assert body["first_name"] == "Пётр"
    assert body["middle_name"] == "Петрович"


async def test_list_users_includes_fio(client, admin_token, dept_a):
    await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "fio_list", "password": "Pass12345678!", "department_id": dept_a.id,
        "last_name": "Сидоров", "first_name": "Сидор",
    })
    resp = await client.get(URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200, resp.text
    listed = {u["username"]: u for u in resp.json()}
    assert "fio_list" in listed
    assert listed["fio_list"]["last_name"] == "Сидоров"
    assert listed["fio_list"]["first_name"] == "Сидор"
    assert listed["fio_list"]["middle_name"] is None


async def test_me_returns_fio(client, admin_token, dept_a):
    """`/me` отдаёт ФИО для UI-отображения (identity-снимок)."""
    await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "fio_me", "password": "Pass12345678!", "department_id": dept_a.id,
        "last_name": "Кузнецов", "first_name": "Кузьма", "middle_name": "Кузьмич",
        "must_change_password": False,
    })
    login = await client.post(LOGIN_URL, json={"username": "fio_me", "password": "Pass12345678!"})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    # identity уже в login-ответе
    ident = login.json()["identity"]
    assert ident["last_name"] == "Кузнецов"
    assert ident["first_name"] == "Кузьма"
    assert ident["middle_name"] == "Кузьмич"

    resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["last_name"] == "Кузнецов"
    assert body["first_name"] == "Кузьма"
    assert body["middle_name"] == "Кузьмич"


async def test_me_without_fio_returns_null(client, user_a_token):
    """Существующий юзер без ФИО — `/me` возвращает null, не падает."""
    resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["last_name"] is None
    assert body["first_name"] is None
    assert body["middle_name"] is None
