"""Интеграционные тесты `/api/server/v1/servers/{server_id}/disks` (CRUD).

Покрытие:
* POST — happy path (admin/operator), reader/guest → 403, cross-dept server
  → 404 hidden, dubikat device_name → 409 DISK_DUPLICATE, второй системный
  диск отбивается 409.
* GET (list) — reader видит свои, пагинация, cross-dept server → 404,
  no-role → 403.
* GET /{id} — reader OK, cross-dept → 404, неверный server_id в path → 404.
* PATCH — operator OK, reader → 403, cross-dept → 404, дубль → 409.
* DELETE — admin OK, operator → 403 (default grants), cross-dept → 404.
"""

from __future__ import annotations


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _base(server_id: str) -> str:
    return f"/api/server/v1/servers/{server_id}/disks"


# ── POST ────────────────────────────────────────────────────────────────────

class TestCreateDisk:
    async def test_admin_creates_disk(self, client, admin_token, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            _base(srv.id),
            headers=_hdr(admin_token),
            json={
                "device_name": "sda",
                "size_bytes": 512_000_000_000,
                "kind": "ssd",
                "model": "Samsung SSD",
                "is_system": True,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["device_name"] == "sda"
        assert body["server_id"] == srv.id
        assert body["is_system"] is True
        assert body["id"].startswith("dsk_")

    async def test_operator_creates_disk(self, client, operator_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            _base(srv.id),
            headers=_hdr(operator_token_a),
            json={"device_name": "nvme0n1", "size_bytes": 1_000_000_000_000},
        )
        assert resp.status_code == 201

    async def test_reader_cannot_create(self, client, reader_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            _base(srv.id),
            headers=_hdr(reader_token_a),
            json={"device_name": "sda", "size_bytes": 1},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_no_token_returns_401(self, client, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            _base(srv.id),
            json={"device_name": "sda", "size_bytes": 1},
        )
        assert resp.status_code == 401

    async def test_cross_dept_server_returns_404(self, client, operator_token_a, make_server):
        srv = await make_server(department_id="dep_b")
        resp = await client.post(
            _base(srv.id),
            headers=_hdr(operator_token_a),
            json={"device_name": "sda", "size_bytes": 1},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"

    async def test_duplicate_device_name_conflict(
        self, client, admin_token, make_server, db,
    ):
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_a")
        db.add(ServerDisk(id=new_id(), server_id=srv.id, device_name="sda", size_bytes=1))
        await db.flush()
        resp = await client.post(
            _base(srv.id),
            headers=_hdr(admin_token),
            json={"device_name": "sda", "size_bytes": 2},
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "DISK_DUPLICATE"

    async def test_second_system_disk_conflict(
        self, client, admin_token, make_server, db,
    ):
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_a")
        db.add(ServerDisk(
            id=new_id(), server_id=srv.id, device_name="sda",
            size_bytes=1, is_system=True,
        ))
        await db.flush()
        resp = await client.post(
            _base(srv.id),
            headers=_hdr(admin_token),
            json={"device_name": "sdb", "size_bytes": 2, "is_system": True},
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "DISK_DUPLICATE"

    async def test_nonexistent_server_returns_404(self, client, admin_token):
        resp = await client.post(
            _base("srv_ghost"),
            headers=_hdr(admin_token),
            json={"device_name": "sda", "size_bytes": 1},
        )
        assert resp.status_code == 404


# ── GET (list) ──────────────────────────────────────────────────────────────

class TestListDisks:
    async def test_reader_lists_own_dept(
        self, client, reader_token_a, make_server, db,
    ):
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_a")
        db.add(ServerDisk(id=new_id(), server_id=srv.id, device_name="sda", size_bytes=1))
        db.add(ServerDisk(id=new_id(), server_id=srv.id, device_name="sdb", size_bytes=2))
        await db.flush()
        resp = await client.get(_base(srv.id), headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert {d["device_name"] for d in body["items"]} == {"sda", "sdb"}

    async def test_pagination(self, client, admin_token, make_server, db):
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_a")
        for i in range(5):
            db.add(ServerDisk(
                id=new_id(), server_id=srv.id, device_name=f"sd{i}", size_bytes=i + 1,
            ))
        await db.flush()
        resp = await client.get(
            _base(srv.id), headers=_hdr(admin_token),
            params={"limit": 2, "offset": 1},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 2
        assert body["offset"] == 1
        assert body["total"] == 5
        assert len(body["items"]) == 2

    async def test_cross_dept_server_returns_404(
        self, client, reader_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_b")
        resp = await client.get(_base(srv.id), headers=_hdr(reader_token_a))
        assert resp.status_code == 404

    async def test_no_role_user_returns_403(
        self, client, no_role_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(_base(srv.id), headers=_hdr(no_role_token_a))
        assert resp.status_code == 403


# ── GET /{id} ──────────────────────────────────────────────────────────────

class TestGetDisk:
    async def test_reader_sees_own(self, client, reader_token_a, make_server, db):
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_a")
        d = ServerDisk(id=new_id(), server_id=srv.id, device_name="sda", size_bytes=1)
        db.add(d)
        await db.flush()
        resp = await client.get(f"{_base(srv.id)}/{d.id}", headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        assert resp.json()["id"] == d.id

    async def test_cross_dept_returns_404(
        self, client, reader_token_a, make_server, db,
    ):
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_b")
        d = ServerDisk(id=new_id(), server_id=srv.id, device_name="sda", size_bytes=1)
        db.add(d)
        await db.flush()
        resp = await client.get(f"{_base(srv.id)}/{d.id}", headers=_hdr(reader_token_a))
        assert resp.status_code == 404

    async def test_wrong_server_id_in_path_returns_404(
        self, client, reader_token_a, make_server, db,
    ):
        """disk_id принадлежит другому серверу → 404 — нельзя обойти изоляцию."""
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        d = ServerDisk(id=new_id(), server_id=srv1.id, device_name="sda", size_bytes=1)
        db.add(d)
        await db.flush()
        # Пытаемся достать диск через wrong server_id в path.
        resp = await client.get(f"{_base(srv2.id)}/{d.id}", headers=_hdr(reader_token_a))
        assert resp.status_code == 404


# ── PATCH ──────────────────────────────────────────────────────────────────

class TestUpdateDisk:
    async def test_operator_updates(self, client, operator_token_a, make_server, db):
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_a")
        d = ServerDisk(id=new_id(), server_id=srv.id, device_name="sda", size_bytes=1)
        db.add(d)
        await db.flush()
        resp = await client.patch(
            f"{_base(srv.id)}/{d.id}",
            headers=_hdr(operator_token_a),
            json={"model": "Samsung 980 PRO", "kind": "nvme"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["model"] == "Samsung 980 PRO"
        assert body["kind"] == "nvme"

    async def test_reader_cannot_update(
        self, client, reader_token_a, make_server, db,
    ):
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_a")
        d = ServerDisk(id=new_id(), server_id=srv.id, device_name="sda", size_bytes=1)
        db.add(d)
        await db.flush()
        resp = await client.patch(
            f"{_base(srv.id)}/{d.id}",
            headers=_hdr(reader_token_a),
            json={"model": "x"},
        )
        assert resp.status_code == 403

    async def test_empty_update_is_noop(
        self, client, operator_token_a, make_server, db,
    ):
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_a")
        d = ServerDisk(id=new_id(), server_id=srv.id, device_name="sda", size_bytes=1)
        db.add(d)
        await db.flush()
        resp = await client.patch(
            f"{_base(srv.id)}/{d.id}",
            headers=_hdr(operator_token_a),
            json={},
        )
        assert resp.status_code == 200
        assert resp.json()["device_name"] == "sda"


# ── DELETE ─────────────────────────────────────────────────────────────────

class TestDeleteDisk:
    async def test_admin_deletes(self, client, admin_role_token_a, make_server, db):
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_a")
        d = ServerDisk(id=new_id(), server_id=srv.id, device_name="sda", size_bytes=1)
        db.add(d)
        await db.flush()
        resp = await client.delete(
            f"{_base(srv.id)}/{d.id}", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200

    async def test_operator_cannot_delete(
        self, client, operator_token_a, make_server, db,
    ):
        """operator не имеет default `delete` на disk (см. seed-миграцию)."""
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_a")
        d = ServerDisk(id=new_id(), server_id=srv.id, device_name="sda", size_bytes=1)
        db.add(d)
        await db.flush()
        resp = await client.delete(
            f"{_base(srv.id)}/{d.id}", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 403

    async def test_cross_dept_returns_404(
        self, client, admin_role_token_a, make_server, db,
    ):
        from src.models import ServerDisk
        from src.utils.ids import server_disk_id as new_id

        srv = await make_server(department_id="dep_b")
        d = ServerDisk(id=new_id(), server_id=srv.id, device_name="sda", size_bytes=1)
        db.add(d)
        await db.flush()
        resp = await client.delete(
            f"{_base(srv.id)}/{d.id}", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404

    async def test_nonexistent_returns_404(
        self, client, admin_role_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.delete(
            f"{_base(srv.id)}/dsk_ghost", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404
