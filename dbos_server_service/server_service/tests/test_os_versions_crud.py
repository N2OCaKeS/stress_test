"""Интеграционные тесты `/api/server/v1/os-versions` (CRUD глобального каталога).

OS-версии — глобальный каталог: dept-isolation НЕ работает.
Read доступен всем носителям view, CRUD — admin (seed-миграция).
"""

from __future__ import annotations


BASE = "/api/server/v1/os-versions"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _payload(**overrides):
    base = {"name": "astra-1.7", "description": "Astra Linux SE 1.7"}
    base.update(overrides)
    return base


# ── POST ────────────────────────────────────────────────────────────────────

class TestCreateOsVersion:
    async def test_admin_creates(self, client, admin_role_token_a):
        resp = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "astra-1.7"
        assert body["id"].startswith("osv_")

    async def test_reader_cannot_create(self, client, reader_token_a):
        resp = await client.post(
            BASE, headers=_hdr(reader_token_a), json=_payload(name="x"),
        )
        assert resp.status_code == 403

    async def test_operator_cannot_create(self, client, operator_token_a):
        """operator — только view на os_version."""
        resp = await client.post(
            BASE, headers=_hdr(operator_token_a), json=_payload(name="y"),
        )
        assert resp.status_code == 403

    async def test_no_token_returns_401(self, client):
        resp = await client.post(BASE, json=_payload())
        assert resp.status_code == 401

    async def test_duplicate_name_conflict(self, client, admin_role_token_a):
        resp = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="ubuntu-22.04"),
        )
        assert resp.status_code == 201
        resp2 = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="ubuntu-22.04"),
        )
        assert resp2.status_code == 409
        assert resp2.json()["error_code"] == "OS_VERSION_DUPLICATE"


# ── GET (list) ──────────────────────────────────────────────────────────────

class TestListOsVersions:
    async def test_reader_lists(self, client, reader_token_a, admin_role_token_a):
        for i in range(3):
            await client.post(
                BASE, headers=_hdr(admin_role_token_a),
                json=_payload(name=f"osv-list-{i}"),
            )
        resp = await client.get(BASE, headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 3

    async def test_pagination(self, client, admin_role_token_a):
        for i in range(5):
            await client.post(
                BASE, headers=_hdr(admin_role_token_a),
                json=_payload(name=f"osv-page-{i}"),
            )
        resp = await client.get(
            BASE, headers=_hdr(admin_role_token_a),
            params={"limit": 2, "offset": 0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 2
        assert len(body["items"]) == 2

    async def test_no_role_returns_403(self, client, no_role_token_a):
        resp = await client.get(BASE, headers=_hdr(no_role_token_a))
        assert resp.status_code == 403


# ── GET /{id} ──────────────────────────────────────────────────────────────

class TestGetOsVersion:
    async def test_reader_gets(self, client, reader_token_a, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-get-1"),
        )
        ov_id = created.json()["id"]
        resp = await client.get(f"{BASE}/{ov_id}", headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        assert resp.json()["id"] == ov_id

    async def test_nonexistent_returns_404(self, client, reader_token_a):
        resp = await client.get(f"{BASE}/osv_ghost", headers=_hdr(reader_token_a))
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "OS_VERSION_NOT_FOUND"


# ── PATCH ──────────────────────────────────────────────────────────────────

class TestUpdateOsVersion:
    async def test_admin_updates(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-upd-1"),
        )
        ov_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{ov_id}",
            headers=_hdr(admin_role_token_a),
            json={"description": "updated desc"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "updated desc"

    async def test_reader_cannot_update(self, client, reader_token_a, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-ro-1"),
        )
        ov_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{ov_id}", headers=_hdr(reader_token_a),
            json={"description": "no"},
        )
        assert resp.status_code == 403

    async def test_empty_update_is_noop(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-noop"),
        )
        ov_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{ov_id}", headers=_hdr(admin_role_token_a), json={},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "osv-noop"

    async def test_update_to_existing_name_conflict(self, client, admin_role_token_a):
        await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-existing"),
        )
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-to-rename"),
        )
        ov_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{ov_id}",
            headers=_hdr(admin_role_token_a),
            json={"name": "osv-existing"},
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "OS_VERSION_DUPLICATE"

    async def test_nonexistent_returns_404(self, client, admin_role_token_a):
        resp = await client.patch(
            f"{BASE}/osv_ghost", headers=_hdr(admin_role_token_a),
            json={"description": "x"},
        )
        assert resp.status_code == 404


# ── DELETE ─────────────────────────────────────────────────────────────────

class TestDeleteOsVersion:
    async def test_admin_deletes(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-del-1"),
        )
        ov_id = created.json()["id"]
        resp = await client.delete(f"{BASE}/{ov_id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

    async def test_reader_cannot_delete(self, client, reader_token_a, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-del-ro"),
        )
        ov_id = created.json()["id"]
        resp = await client.delete(f"{BASE}/{ov_id}", headers=_hdr(reader_token_a))
        assert resp.status_code == 403

    async def test_delete_in_use_returns_409(
        self, client, admin_role_token_a, make_server, db,
    ):
        """FK ondelete=RESTRICT — server ссылается → OS_VERSION_IN_USE."""
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-in-use"),
        )
        ov_id = created.json()["id"]
        srv = await make_server(department_id="dep_a")
        srv.os_version_id = ov_id
        await db.flush()
        resp = await client.delete(
            f"{BASE}/{ov_id}", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "OS_VERSION_IN_USE"

    async def test_delete_nonexistent_returns_404(self, client, admin_role_token_a):
        resp = await client.delete(
            f"{BASE}/osv_ghost", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404
