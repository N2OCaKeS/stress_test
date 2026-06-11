"""Тесты: PATCH /api/auth/v1/departments/{id} — точечный апдейт name / description."""

UPDATE_URL = "/api/auth/v1/departments/{dept_id}"


# ── Happy path ────────────────────────────────────────────────────────────────

async def test_account_admin_updates_name_and_description(
    client, admin_token, dept_a, capture_audit_payloads, db,
):
    url = UPDATE_URL.format(dept_id=dept_a.id)
    resp = await client.patch(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "Alpha Updated", "description": "core platform team"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Alpha Updated"
    assert body["description"] == "core platform team"

    # БД содержит новые значения.
    from sqlalchemy import select
    from src.models.department import Department
    refreshed = await db.scalar(select(Department).where(Department.id == dept_a.id))
    assert refreshed is not None
    assert refreshed.name == "Alpha Updated"
    assert refreshed.description == "core platform team"

    # Audit-событие с changes.
    updates = [p for p in capture_audit_payloads if p["action"] == "department.updated"]
    assert updates, "audit-событие должно эмититься"
    last = updates[-1]
    assert last["target_id"] == dept_a.id
    changes = last["details"]["changes"]
    assert changes["name"]["new"] == "Alpha Updated"
    assert changes["description"]["new"] == "core platform team"


async def test_partial_update_only_name(
    client, admin_token, dept_a, db,
):
    """Передан только name — description не трогается."""
    url = UPDATE_URL.format(dept_id=dept_a.id)
    resp = await client.patch(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "Renamed Only"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed Only"

    from sqlalchemy import select
    from src.models.department import Department
    refreshed = await db.scalar(select(Department).where(Department.id == dept_a.id))
    assert refreshed.name == "Renamed Only"
    # description как было (изначально None).
    assert refreshed.description is None


# ── Empty body — 422 EMPTY_UPDATE ─────────────────────────────────────────────

async def test_empty_body_returns_422_empty_update(client, admin_token, dept_a):
    url = UPDATE_URL.format(dept_id=dept_a.id)
    resp = await client.patch(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error_code"] == "EMPTY_UPDATE"


async def test_both_fields_explicit_null_returns_422(client, admin_token, dept_a):
    """Явные null'ы для обоих полей — тоже EMPTY_UPDATE."""
    url = UPDATE_URL.format(dept_id=dept_a.id)
    resp = await client.patch(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": None, "description": None},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error_code"] == "EMPTY_UPDATE"


# ── RBAC: dep_admin / regular user — 403 ─────────────────────────────────────

async def test_dept_admin_cannot_update_department(
    client, dept_admin_a_token, dept_a,
):
    url = UPDATE_URL.format(dept_id=dept_a.id)
    resp = await client.patch(
        url,
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"name": "Hijacked"},
    )
    assert resp.status_code == 403


async def test_regular_user_cannot_update_department(
    client, user_a_token, dept_a,
):
    url = UPDATE_URL.format(dept_id=dept_a.id)
    resp = await client.patch(
        url,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "Hijacked"},
    )
    assert resp.status_code == 403


# ── Not found — 404 ─────────────────────────────────────────────────────────

async def test_update_nonexistent_returns_404(client, admin_token):
    url = UPDATE_URL.format(dept_id="dep_does_not_exist")
    resp = await client.patch(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "Whatever"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error_code"] == "DEPARTMENT_NOT_FOUND"


# ── Conflict — 409 ─────────────────────────────────────────────────────────

async def test_rename_to_existing_name_returns_409(
    client, admin_token, dept_a, dept_b,
):
    """Переименование в имя, занятое другим отделом, → 409 DEPARTMENT_ALREADY_EXISTS."""
    url = UPDATE_URL.format(dept_id=dept_a.id)
    resp = await client.patch(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": dept_b.name},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error_code"] == "DEPARTMENT_ALREADY_EXISTS"


async def test_rename_to_own_name_is_noop_200(
    client, admin_token, dept_a,
):
    """`name`, равный текущему, — не конфликт; просто 200."""
    url = UPDATE_URL.format(dept_id=dept_a.id)
    resp = await client.patch(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": dept_a.name},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == dept_a.name
