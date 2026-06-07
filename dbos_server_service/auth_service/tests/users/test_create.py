"""Тесты: POST /api/auth/v1/users — создание пользователей."""

URL = "/api/auth/v1/users"


# ── account_admin creates users ───────────────────────────────────────────────

async def test_admin_creates_user_in_dept(client, admin_token, dept_a):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "new_user_1", "password": "Pass1234!", "department_id": dept_a.id,
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "new_user_1"
    assert body["department_id"] == dept_a.id


async def test_admin_creates_account_admin_without_dept(client, admin_token):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "new_admin", "password": "Admin1234!", "platform_role": "account_admin",
    })
    assert resp.status_code == 201
    assert resp.json()["department_id"] is None


async def test_admin_creates_loging_admin_without_dept(client, admin_token):
    """loging_admin — платформенная роль, создаётся без department_id."""
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "new_log_admin", "password": "Admin1234!", "platform_role": "loging_admin",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["department_id"] is None
    assert body["platform_role"] == "loging_admin"


async def test_admin_creates_loging_reader_without_dept(client, admin_token):
    """loging_reader — платформенная read-only роль, тоже без department_id."""
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "new_log_reader", "password": "Reader1234!", "platform_role": "loging_reader",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["department_id"] is None
    assert body["platform_role"] == "loging_reader"


async def test_admin_creates_dept_admin(client, admin_token, dept_a):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "new_deptadmin", "password": "Admin1234!",
        "department_id": dept_a.id, "platform_role": "department_admin",
    })
    assert resp.status_code == 201
    assert resp.json()["platform_role"] == "department_admin"


# ── department_admin creates users ────────────────────────────────────────────

async def test_dept_admin_creates_user_in_own_dept(client, dept_admin_a_token, dept_a):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"}, json={
        "username": "new_user_own", "password": "Pass1234!", "department_id": dept_a.id,
    })
    assert resp.status_code == 201


async def test_dept_admin_cannot_create_user_in_other_dept(client, dept_admin_a_token, dept_b):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"}, json={
        "username": "cross_user", "password": "Pass1234!", "department_id": dept_b.id,
    })
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"


async def test_dept_admin_b_cannot_create_user_in_dept_a(client, dept_admin_b_token, dept_a):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {dept_admin_b_token}"}, json={
        "username": "cross_ab", "password": "Pass1234!", "department_id": dept_a.id,
    })
    assert resp.status_code == 403


# ── Platform-role escalation guard ──────────────────────────────────────────


async def test_dept_admin_cannot_create_account_admin(client, dept_admin_a_token, dept_a):
    """department_admin не может присвоить platform_role=account_admin → 403."""
    resp = await client.post(URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"}, json={
        "username": "escalated_admin", "password": "Pass1234!",
        "department_id": dept_a.id, "platform_role": "account_admin",
    })
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "PLATFORM_ROLE_ASSIGNMENT_DENIED"


async def test_dept_admin_cannot_assign_loging_admin(client, dept_admin_a_token, dept_a):
    """department_admin не может присвоить и другие платформенные роли."""
    resp = await client.post(URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"}, json={
        "username": "escalated_logadmin", "password": "Pass1234!",
        "department_id": dept_a.id, "platform_role": "loging_admin",
    })
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "PLATFORM_ROLE_ASSIGNMENT_DENIED"


async def test_dept_admin_creates_plain_user_without_platform_role(client, dept_admin_a_token, dept_a):
    """Обычное создание юзера department_admin'ом (без platform_role) работает."""
    resp = await client.post(URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"}, json={
        "username": "plain_user_ok", "password": "Pass1234!", "department_id": dept_a.id,
    })
    assert resp.status_code == 201
    assert resp.json()["platform_role"] is None


# ── Error cases ───────────────────────────────────────────────────────────────

async def test_duplicate_username_returns_409(client, admin_token, dept_a, user_a):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "t_user_a", "password": "Pass1234!", "department_id": dept_a.id,
    })
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "USER_ALREADY_EXISTS"


async def test_nonexistent_dept_returns_404(client, admin_token):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "ghost_user", "password": "Pass1234!", "department_id": "dep_nonexistent",
    })
    assert resp.status_code == 404


async def test_regular_user_without_dept_returns_422(client, admin_token):
    """Non-admin users must have a department."""
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "nodept_user", "password": "Pass1234!",
    })
    assert resp.status_code == 422, resp.text


async def test_regular_user_cannot_create_users(client, user_a_token, dept_a):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {user_a_token}"}, json={
        "username": "hacker", "password": "Pass1234!", "department_id": dept_a.id,
    })
    assert resp.status_code == 403


async def test_unauthenticated_cannot_create_users(client, dept_a):
    resp = await client.post(URL, json={"username": "anon", "password": "Pass1234!", "department_id": dept_a.id})
    assert resp.status_code == 401


# ── Username charset (Pydantic pattern) ─────────────────────────────────────


async def test_username_cyrillic_homoglyph_rejected(client, admin_token, dept_a):
    """Кириллический `а` в "аdmin" — Unicode-homoglyph для латинского `a`.

    Без pattern'а юзеры `admin` и `аdmin` ходят как разные сущности, но в
    UI/SIEM выглядят одинаково. Pattern режет на 422.
    """
    resp = await client.post(
        URL, headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "аdmin",
            "password": "Pass1234!",
            "department_id": dept_a.id,
        },
    )
    assert resp.status_code == 422, resp.text


async def test_username_with_newline_rejected(client, admin_token, dept_a):
    """`\n` в username — log-poisoning вектор."""
    resp = await client.post(
        URL, headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "alice\nadmin",
            "password": "Pass1234!",
            "department_id": dept_a.id,
        },
    )
    assert resp.status_code == 422, resp.text


async def test_username_with_space_rejected(client, admin_token, dept_a):
    resp = await client.post(
        URL, headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "alice bob",
            "password": "Pass1234!",
            "department_id": dept_a.id,
        },
    )
    assert resp.status_code == 422, resp.text


async def test_username_latin_alphanumeric_accepted(client, admin_token, dept_a):
    """Латиница + цифры + `_-.` — корпоративные `first.last`, `ci_bot-1` и т.п."""
    for valid in ("user.name", "user-name", "user_name", "User123", "a.b.c-1_2"):
        resp = await client.post(
            URL, headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": valid,
                "password": "Pass1234!",
                "department_id": dept_a.id,
            },
        )
        assert resp.status_code == 201, f"valid username {valid!r} rejected: {resp.text}"
