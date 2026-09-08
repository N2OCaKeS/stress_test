"""Интеграционные тесты `/api/server/v1/server-categories` (каталог по мощности).

Каталог платформенный: dept-isolation НЕ работает. Чтение (list / get по id /
get по коду) доступно любому аутентифицированному актору, аноним без bearer'а
→ 401, аудита на чтении нет. Запись (create/update/delete) — под матрицей прав
(seed-миграция даёт её системной роли `admin`). Простановка категории серверу
идёт обычным `PATCH /servers/{id}` полем `category_id`.
"""

from __future__ import annotations

import pytest

from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/server-categories"
SERVERS = "/api/server/v1/servers"


def _payload(**overrides):
    base = {"code": "gpu_server", "label": "GpuServer"}
    base.update(overrides)
    return base


@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает вызовы audit_service.emit (call kwargs)."""
    from tests._helpers import make_emit_capture

    return make_emit_capture(monkeypatch)


# ── Сиды миграции ───────────────────────────────────────────────────────────

class TestSeededCategories:
    async def test_four_starting_categories_present(self, client, no_role_token_a):
        resp = await client.get(BASE, params={"limit": 500}, headers=_hdr(no_role_token_a))
        assert resp.status_code == 200
        codes = {item["code"] for item in resp.json()["items"]}
        assert {"low_server", "middle_server", "high_server", "workstation"} <= codes

    async def test_seeded_labels(self, client, no_role_token_a):
        resp = await client.get(f"{BASE}/by-code/workstation", headers=_hdr(no_role_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["label"] == "WorkStation"
        assert body["id"] == "scat_workstation"


class TestListOpenApiSchema:
    def test_list_response_schema_is_typed(self):
        from src.main import app

        spec = app.openapi()
        get_op = spec["paths"][BASE]["get"]
        schema = get_op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema != {}
        assert "PaginatedResponse_ServerCategoryResponse_" in schema.get("$ref", "")

    def test_category_schema_fields(self):
        from src.main import app

        spec = app.openapi()
        props = spec["components"]["schemas"]["ServerCategoryResponse"]["properties"]
        assert {"id", "code", "label", "description", "created_at"} <= set(props)


# ── POST ────────────────────────────────────────────────────────────────────

class TestCreateServerCategory:
    async def test_admin_creates(self, client, admin_role_token_a):
        resp = await client.post(BASE, headers=_hdr(admin_role_token_a), json=_payload())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["code"] == "gpu_server"
        assert body["label"] == "GpuServer"
        assert body["id"].startswith("scat_")

    async def test_reader_cannot_create(self, client, reader_token_a):
        resp = await client.post(
            BASE, headers=_hdr(reader_token_a), json=_payload(code="ro_cat"),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_operator_cannot_create(self, client, operator_token_a):
        resp = await client.post(
            BASE, headers=_hdr(operator_token_a), json=_payload(code="op_cat"),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_returns_401(self, client):
        resp = await client.post(BASE, json=_payload(code="anon_cat"))
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_duplicate_code_conflict(self, client, admin_role_token_a):
        resp = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code="dup_cat"),
        )
        assert resp.status_code == 201
        resp2 = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code="dup_cat"),
        )
        assert_error(resp2, 409, "SERVER_CATEGORY_DUPLICATE")

    async def test_duplicate_with_seeded_code_conflict(self, client, admin_role_token_a):
        resp = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code="low_server"),
        )
        assert_error(resp, 409, "SERVER_CATEGORY_DUPLICATE")

    @pytest.mark.parametrize("bad_code", [
        "Low_Server", "low server", "1low", "low-server", "", "низкий",
    ])
    async def test_rejects_bad_code(self, client, admin_role_token_a, bad_code):
        resp = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code=bad_code),
        )
        assert_error(resp, 422, "VALIDATION_ERROR")


# ── GET (list / карточка) ───────────────────────────────────────────────────

class TestReadServerCategories:
    async def test_no_role_token_still_lists(self, client, no_role_token_a):
        resp = await client.get(BASE, headers=_hdr(no_role_token_a))
        assert resp.status_code == 200

    async def test_anonymous_list_rejected_401(self, client):
        resp = await client.get(BASE)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_pagination(self, client, no_role_token_a):
        resp = await client.get(
            BASE, params={"limit": 2, "offset": 0}, headers=_hdr(no_role_token_a),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 2
        assert len(body["items"]) == 2
        assert body["total"] >= 4

    async def test_get_by_id(self, client, admin_role_token_a, no_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code="get_cat"),
        )
        cat_id = created.json()["id"]
        resp = await client.get(f"{BASE}/{cat_id}", headers=_hdr(no_role_token_a))
        assert resp.status_code == 200
        assert resp.json()["id"] == cat_id

    async def test_get_nonexistent_returns_404(self, client, no_role_token_a):
        resp = await client.get(f"{BASE}/scat_ghost", headers=_hdr(no_role_token_a))
        assert_error(resp, 404, "SERVER_CATEGORY_NOT_FOUND")

    async def test_get_by_code_nonexistent_returns_404(self, client, no_role_token_a):
        resp = await client.get(f"{BASE}/by-code/ghost_cat", headers=_hdr(no_role_token_a))
        assert_error(resp, 404, "SERVER_CATEGORY_NOT_FOUND")

    async def test_anonymous_get_by_code_rejected_401(self, client):
        resp = await client.get(f"{BASE}/by-code/low_server")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_read_emits_no_audit(self, client, no_role_token_a, captured_emits):
        resp = await client.get(BASE, headers=_hdr(no_role_token_a))
        assert resp.status_code == 200
        events = [e for e in captured_emits if e["action"].startswith("server_category")]
        assert events == []


# ── PATCH ──────────────────────────────────────────────────────────────────

class TestUpdateServerCategory:
    async def test_admin_updates_label(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code="upd_cat"),
        )
        cat_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{cat_id}",
            headers=_hdr(admin_role_token_a),
            json={"label": "Renamed", "description": "теперь с пояснением"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["label"] == "Renamed"
        assert body["description"] == "теперь с пояснением"

    async def test_empty_update_is_noop(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code="noop_cat"),
        )
        cat_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{cat_id}", headers=_hdr(admin_role_token_a), json={},
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == "noop_cat"

    async def test_update_to_existing_code_conflict(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code="rename_me"),
        )
        cat_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{cat_id}",
            headers=_hdr(admin_role_token_a),
            json={"code": "high_server"},
        )
        assert_error(resp, 409, "SERVER_CATEGORY_DUPLICATE")

    async def test_reader_cannot_update(self, client, reader_token_a, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code="ro_upd_cat"),
        )
        cat_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{cat_id}", headers=_hdr(reader_token_a), json={"label": "no"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_nonexistent_returns_404(self, client, admin_role_token_a):
        resp = await client.patch(
            f"{BASE}/scat_ghost", headers=_hdr(admin_role_token_a), json={"label": "x"},
        )
        assert_error(resp, 404, "SERVER_CATEGORY_NOT_FOUND")


# ── DELETE ─────────────────────────────────────────────────────────────────

class TestDeleteServerCategory:
    async def test_admin_deletes(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code="del_cat"),
        )
        cat_id = created.json()["id"]
        resp = await client.delete(f"{BASE}/{cat_id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        gone = await client.get(f"{BASE}/{cat_id}", headers=_hdr(admin_role_token_a))
        assert_error(gone, 404, "SERVER_CATEGORY_NOT_FOUND")

    async def test_reader_cannot_delete(self, client, reader_token_a, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code="ro_del_cat"),
        )
        cat_id = created.json()["id"]
        resp = await client.delete(f"{BASE}/{cat_id}", headers=_hdr(reader_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_delete_nonexistent_returns_404(self, client, admin_role_token_a):
        resp = await client.delete(f"{BASE}/scat_ghost", headers=_hdr(admin_role_token_a))
        assert_error(resp, 404, "SERVER_CATEGORY_NOT_FOUND")

    async def test_delete_in_use_returns_409(
        self, client, admin_role_token_a, make_server, db,
    ):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(code="in_use_cat"),
        )
        cat_id = created.json()["id"]
        srv = await make_server(department_id="dep_a")
        srv.category_id = cat_id
        await db.flush()
        resp = await client.delete(f"{BASE}/{cat_id}", headers=_hdr(admin_role_token_a))
        assert_error(resp, 409, "SERVER_CATEGORY_IN_USE")


# ── Присвоение категории серверу (PATCH /servers/{id}) ──────────────────────

class TestAssignCategoryToServer:
    async def test_patch_sets_category(self, client, operator_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.patch(
            f"{SERVERS}/{srv.id}",
            headers=_hdr(operator_token_a),
            json={"category_id": "scat_high_server"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["category_id"] == "scat_high_server"

    async def test_patch_clears_category(self, client, operator_token_a, make_server, db):
        srv = await make_server(department_id="dep_a")
        srv.category_id = "scat_low_server"
        await db.flush()
        resp = await client.patch(
            f"{SERVERS}/{srv.id}",
            headers=_hdr(operator_token_a),
            json={"category_id": None},
        )
        assert resp.status_code == 200
        assert resp.json()["category_id"] is None

    async def test_patch_unknown_category_returns_422(
        self, client, operator_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.patch(
            f"{SERVERS}/{srv.id}",
            headers=_hdr(operator_token_a),
            json={"category_id": "scat_ghost"},
        )
        assert_error(resp, 422, "INVALID_SERVER_CATEGORY")

    async def test_create_server_with_unknown_category_returns_422(
        self, client, admin_role_token_a,
    ):
        resp = await client.post(
            SERVERS,
            headers=_hdr(admin_role_token_a),
            json={
                "hostname": "srv-bad-category",
                "ip_address": "192.168.77.11",
                "department_id": "dep_a",
                "category_id": "scat_ghost",
            },
        )
        assert_error(resp, 422, "INVALID_SERVER_CATEGORY")

    async def test_create_server_with_category(self, client, admin_role_token_a):
        resp = await client.post(
            SERVERS,
            headers=_hdr(admin_role_token_a),
            json={
                "hostname": "srv-with-category",
                "ip_address": "192.168.77.12",
                "department_id": "dep_a",
                "category_id": "scat_middle_server",
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["category_id"] == "scat_middle_server"

    async def test_assignment_emits_audit(
        self, client, operator_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        captured_emits.clear()
        resp = await client.patch(
            f"{SERVERS}/{srv.id}",
            headers=_hdr(operator_token_a),
            json={"category_id": "scat_workstation"},
        )
        assert resp.status_code == 200
        assigned = [e for e in captured_emits if e["action"] == "server.category_assigned"]
        assert len(assigned) == 1
        details = assigned[0]["details"]
        assert details["previous_category_id"] is None
        assert details["new_category_id"] == "scat_workstation"

    async def test_plain_update_emits_no_category_audit(
        self, client, operator_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        captured_emits.clear()
        resp = await client.patch(
            f"{SERVERS}/{srv.id}",
            headers=_hdr(operator_token_a),
            json={"display_name": "Renamed"},
        )
        assert resp.status_code == 200
        assigned = [e for e in captured_emits if e["action"] == "server.category_assigned"]
        assert assigned == []
