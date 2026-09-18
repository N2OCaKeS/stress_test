"""Тесты `/api/testing/v1/test-definitions` — каталог тестов.

Чтение доступно любому аутентифицированному актору, аноним → 401. Запись —
под матрицей прав (seed-миграция даёт её системной роли `admin`), тот же
паттерн, что и у `global_variables`.
"""

from __future__ import annotations

import uuid

from tests.conftest import auth_hdr as _hdr

BASE = "/api/testing/v1/test-definitions"


def _payload(**overrides) -> dict:
    base = {
        "code": f"my.test.{uuid.uuid4().hex[:8]}",
        "full_name": "Мой тест",
    }
    base.update(overrides)
    return base


# ── Чтение ──────────────────────────────────────────────────────────────────

class TestReadAccess:
    async def test_anonymous_gets_401(self, client):
        resp = await client.get(BASE)
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "ACCESS_TOKEN_MISSING"

    async def test_user_without_roles_can_read(self, client, no_role_token):
        resp = await client.get(BASE, headers=_hdr(no_role_token))
        assert resp.status_code == 200

    async def test_get_by_id(self, client, admin_token, no_role_token):
        created = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert created.status_code == 201
        test_id = created.json()["id"]
        resp = await client.get(f"{BASE}/{test_id}", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.json()["id"] == test_id

    async def test_get_by_code(self, client, admin_token, no_role_token):
        code = f"lookup.{uuid.uuid4().hex[:8]}"
        created = await client.post(BASE, headers=_hdr(admin_token), json=_payload(code=code))
        assert created.status_code == 201
        resp = await client.get(f"{BASE}/by-code/{code}", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.json()["code"] == code

    async def test_unknown_id_404(self, client, no_role_token):
        resp = await client.get(f"{BASE}/tdef_nope", headers=_hdr(no_role_token))
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TEST_DEFINITION_NOT_FOUND"


# ── POST ────────────────────────────────────────────────────────────────────

class TestCreate:
    async def test_admin_creates(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"].startswith("tdef_")
        assert body["full_name"] == "Мой тест"
        assert body["created_by"]
        assert body["mode"] == "orel"

    async def test_full_payload_persisted(self, client, admin_token):
        payload = _payload(
            category="postgresql",
            owner="ivanov",
            readiness="ready",
            mode="smolensk",
            department_id="dep_a",
            pinned_stand_id="stand_1",
        )
        resp = await client.post(BASE, headers=_hdr(admin_token), json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["category"] == "postgresql"
        assert body["owner"] == "ivanov"
        assert body["readiness"] == "ready"
        assert body["mode"] == "smolensk"
        assert body["department_id"] == "dep_a"
        assert body["pinned_stand_id"] == "stand_1"

    async def test_user_without_role_gets_403(self, client, no_role_token):
        resp = await client.post(BASE, headers=_hdr(no_role_token), json=_payload())
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_guest_cannot_create(self, client, guest_token):
        resp = await client.post(BASE, headers=_hdr(guest_token), json=_payload())
        assert resp.status_code == 403

    async def test_duplicate_code_409(self, client, admin_token):
        payload = _payload()
        first = await client.post(BASE, headers=_hdr(admin_token), json=payload)
        assert first.status_code == 201
        second = await client.post(BASE, headers=_hdr(admin_token), json=payload)
        assert second.status_code == 409
        assert second.json()["error_code"] == "TEST_DEFINITION_DUPLICATE"

    async def test_invalid_mode_rejected(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload(mode="invalid"))
        assert resp.status_code == 422

    async def test_invalid_code_rejected(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload(code="  "))
        assert resp.status_code == 422

    async def test_missing_full_name_rejected(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token), json={"code": f"x.{uuid.uuid4().hex[:8]}"},
        )
        assert resp.status_code == 422


# ── Список + фильтры (department scoping) ───────────────────────────────────

class TestListFilters:
    async def test_listing_is_scoped_to_own_department(
        self, client, admin_token, no_role_token, make_token,
    ):
        """Свой отдел виден, чужой — нет; платформенный тест (без отдела) виден всем."""
        own = await client.post(
            BASE, headers=_hdr(admin_token), json=_payload(department_id="dep_a"),
        )
        assert own.status_code == 201
        # Чужой отдел заводит запись сам — свой admin_token завести тест "от
        # имени" dep_other больше не может (закрытая cross-department дыра).
        other_admin = make_token(department_id="dep_other", service_roles={"testing_service": ["admin"]})
        other = await client.post(
            BASE, headers=_hdr(other_admin), json=_payload(department_id="dep_other"),
        )
        assert other.status_code == 201
        unscoped = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert unscoped.status_code == 201

        resp = await client.get(BASE, params={"limit": 500}, headers=_hdr(no_role_token))
        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()["items"]}
        assert own.json()["id"] in ids
        assert unscoped.json()["id"] in ids
        assert other.json()["id"] not in ids

    async def test_explicit_own_department_filter_allowed(self, client, admin_token, no_role_token):
        own = await client.post(
            BASE, headers=_hdr(admin_token), json=_payload(department_id="dep_a"),
        )
        assert own.status_code == 201
        resp = await client.get(
            BASE, params={"department_id": "dep_a", "limit": 500}, headers=_hdr(no_role_token),
        )
        assert resp.status_code == 200
        assert own.json()["id"] in {item["id"] for item in resp.json()["items"]}

    async def test_explicit_foreign_department_filter_rejected(self, client, no_role_token):
        resp = await client.get(
            BASE, params={"department_id": "dep_other"}, headers=_hdr(no_role_token),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "DEPARTMENT_ISOLATION"

    async def test_filter_by_category(self, client, admin_token, no_role_token):
        category = f"cat_{uuid.uuid4().hex[:8]}"
        created = await client.post(
            BASE, headers=_hdr(admin_token), json=_payload(category=category),
        )
        assert created.status_code == 201

        resp = await client.get(BASE, params={"category": category}, headers=_hdr(no_role_token))
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == created.json()["id"]

    async def test_filter_by_readiness(self, client, admin_token, no_role_token):
        readiness = "review"
        created = await client.post(
            BASE, headers=_hdr(admin_token), json=_payload(readiness=readiness),
        )
        assert created.status_code == 201

        resp = await client.get(BASE, params={"readiness": readiness}, headers=_hdr(no_role_token))
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == created.json()["id"]

    async def test_no_filter_matches_everything(self, client, admin_token, no_role_token):
        created = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert created.status_code == 201
        resp = await client.get(BASE, params={"limit": 500}, headers=_hdr(no_role_token))
        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()["items"]}
        assert created.json()["id"] in ids


# ── PATCH ───────────────────────────────────────────────────────────────────

class TestUpdate:
    async def _create(self, client, admin_token) -> str:
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_admin_updates_full_name(self, client, admin_token):
        test_id = await self._create(client, admin_token)
        resp = await client.patch(
            f"{BASE}/{test_id}", headers=_hdr(admin_token), json={"full_name": "Переименован"},
        )
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Переименован"

    async def test_update_readiness_and_department(self, client, admin_token):
        """Одновременно readiness + department_id — но только в СВОЙ отдел.

        Чужой `department_id` в теле — отдельный кейс, закрытый
        `require_department_action` (см. `test_department_isolation.py`).
        """
        test_id = await self._create(client, admin_token)
        resp = await client.patch(
            f"{BASE}/{test_id}", headers=_hdr(admin_token),
            json={"readiness": "broken", "department_id": "dep_a"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["readiness"] == "broken"
        assert body["department_id"] == "dep_a"

    async def test_update_mode(self, client, admin_token):
        test_id = await self._create(client, admin_token)
        resp = await client.patch(
            f"{BASE}/{test_id}", headers=_hdr(admin_token), json={"mode": "smolensk"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["mode"] == "smolensk"

    async def test_update_mode_rejects_null_and_invalid(self, client, admin_token):
        test_id = await self._create(client, admin_token)
        for value in (None, "invalid"):
            resp = await client.patch(
                f"{BASE}/{test_id}", headers=_hdr(admin_token), json={"mode": value},
            )
            assert resp.status_code == 422

    async def test_no_role_gets_403(self, client, no_role_token, admin_token):
        test_id = await self._create(client, admin_token)
        resp = await client.patch(
            f"{BASE}/{test_id}", headers=_hdr(no_role_token), json={"full_name": "x"},
        )
        assert resp.status_code == 403

    async def test_empty_body_is_noop(self, client, admin_token):
        test_id = await self._create(client, admin_token)
        resp = await client.patch(f"{BASE}/{test_id}", headers=_hdr(admin_token), json={})
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Мой тест"

    async def test_unknown_id_404(self, client, admin_token):
        resp = await client.patch(
            f"{BASE}/tdef_nope", headers=_hdr(admin_token), json={"full_name": "x"},
        )
        assert resp.status_code == 404

    async def test_code_collision_409(self, client, admin_token):
        first_code = f"collide.{uuid.uuid4().hex[:8]}"
        first = await client.post(BASE, headers=_hdr(admin_token), json=_payload(code=first_code))
        assert first.status_code == 201
        second_id = await self._create(client, admin_token)
        resp = await client.patch(
            f"{BASE}/{second_id}", headers=_hdr(admin_token), json={"code": first_code},
        )
        assert resp.status_code == 409


# ── DELETE ──────────────────────────────────────────────────────────────────

class TestDelete:
    async def _create(self, client, admin_token) -> str:
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_admin_deletes(self, client, admin_token, no_role_token):
        test_id = await self._create(client, admin_token)
        resp = await client.delete(f"{BASE}/{test_id}", headers=_hdr(admin_token))
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        gone = await client.get(f"{BASE}/{test_id}", headers=_hdr(no_role_token))
        assert gone.status_code == 404

    async def test_no_role_gets_403(self, client, no_role_token, admin_token):
        test_id = await self._create(client, admin_token)
        resp = await client.delete(f"{BASE}/{test_id}", headers=_hdr(no_role_token))
        assert resp.status_code == 403

    async def test_unknown_id_404(self, client, admin_token):
        resp = await client.delete(f"{BASE}/tdef_nope", headers=_hdr(admin_token))
        assert resp.status_code == 404

    async def test_delete_cascades_command_args(self, client, admin_token):
        test_id = await self._create(client, admin_token)
        arg = await client.post(
            f"{BASE}/{test_id}/args", headers=_hdr(admin_token),
            json={"kind": "literal", "literal_value": "--foo"},
        )
        assert arg.status_code == 201
        resp = await client.delete(f"{BASE}/{test_id}", headers=_hdr(admin_token))
        assert resp.status_code == 200
