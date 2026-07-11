"""Интеграционные тесты CRUD `/api/server/v1/boxes`.

Покрытие:

* POST / — happy path, шифрование пароля образного пользователя в БД, отсутствие
  policy на образных кредах (тривиальный `1` проходит), dept-isolation, дубль
  имени → 409, guest/no-role → 403.
* GET / (list) — фильтр по отделу, guest видит, без роли → 403.
* GET /{id} — owner видит, view_password доносит пароль в base64, cross-dept и
  несуществующий → 404 BOX_NOT_FOUND, guest без пароля.
* PATCH /{id} — частичное обновление, смена пароля с reveal-roundtrip, guest →
  403, cross-dept → 404.
* DELETE /{id} — admin OK (после — 404), guest → 403, cross-dept → 404.
"""

from __future__ import annotations

import base64

import pytest

from tests._helpers import assert_error, auth_hdr as _hdr, b64

BASE = "/api/server/v1/boxes"


def _box_body(**overrides) -> dict:
    body = {
        "department_id": "dep_a",
        "name": "vm_station",
        "format": "qcow2",
        "download_url": "https://images.example.com/vm_station.qcow2",
        "base_user_login": "u",
        "base_user_password_b64": b64("1"),
        "os_versions": ["1.7.5.9", "1.8.1.6"],
        "initial_snapshots": ["build", "orel"],
    }
    body.update(overrides)
    return body


# ── POST /boxes ──────────────────────────────────────────────────────────────


class TestCreateBox:
    async def test_admin_creates_box(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_box_body())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"].startswith("box_")
        assert body["department_id"] == "dep_a"
        assert body["name"] == "vm_station"
        assert body["format"] == "qcow2"
        assert body["base_user_login"] == "u"
        assert body["os_versions"] == ["1.7.5.9", "1.8.1.6"]
        assert body["initial_snapshots"] == ["build", "orel"]
        # plaintext пароль не течёт в ответ create
        assert body["base_user_password_b64"] is None
        assert "base_user_password_encrypted" not in body

    async def test_password_is_encrypted_in_db(self, client, admin_token, db):
        from sqlalchemy import select

        from src.models import Box
        from src.services import box_service, secrets_service

        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_box_body(name="enc-box", base_user_password_b64=b64("imgpass-secret")),
        )
        assert resp.status_code == 201
        box_id = resp.json()["id"]
        row = (
            await db.execute(select(Box).where(Box.id == box_id))
        ).scalar_one()
        assert "imgpass-secret" not in (row.base_user_password_encrypted or "")
        assert row.base_user_password_encrypted.startswith("v")
        assert secrets_service.decrypt(
            row.base_user_password_encrypted,
            aad=box_service.aad_for_box_base_user_password(box_id),
        ) == "imgpass-secret"

    async def test_trivial_image_password_bypasses_policy(self, client, admin_token):
        """Образные креды (например `u:1`) не проходят серверную парольную
        политику, но для бокса сохраняются как есть."""
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_box_body(name="trivial-pw", base_user_password_b64=b64("1")),
        )
        assert resp.status_code == 201

    async def test_box_without_base_user(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json={
                "department_id": "dep_a",
                "name": "no-user-box",
                "format": "raw",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["base_user_login"] is None
        assert body["base_user_password_b64"] is None
        assert body["os_versions"] == []

    async def test_guest_cannot_create(self, client, guest_token_a):
        resp = await client.post(BASE, headers=_hdr(guest_token_a), json=_box_body())
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_role_cannot_create(self, client, no_role_token_a):
        resp = await client.post(BASE, headers=_hdr(no_role_token_a), json=_box_body())
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_department_isolation(self, client, admin_token_b):
        # admin отдела dep_b пытается завести бокс в dep_a
        resp = await client.post(
            BASE, headers=_hdr(admin_token_b), json=_box_body(department_id="dep_a"),
        )
        assert_error(resp, 409, "DEPARTMENT_ISOLATION")

    async def test_duplicate_name_returns_409(self, client, admin_token):
        first = await client.post(BASE, headers=_hdr(admin_token), json=_box_body(name="dup"))
        assert first.status_code == 201
        second = await client.post(BASE, headers=_hdr(admin_token), json=_box_body(name="dup"))
        assert_error(second, 409, "BOX_DUPLICATE")

    async def test_bad_download_url_scheme_422(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_box_body(name="badurl", download_url="gopher://x/y"),
        )
        assert resp.status_code == 422

    async def test_no_token_401(self, client):
        resp = await client.post(BASE, json=_box_body())
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")


# ── GET /boxes/{id} ──────────────────────────────────────────────────────────


class TestGetBox:
    async def _create(self, client, token, **overrides) -> str:
        resp = await client.post(BASE, headers=_hdr(token), json=_box_body(**overrides))
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    async def test_admin_gets_box_with_password(self, client, admin_token):
        box_id = await self._create(
            client, admin_token, name="reveal", base_user_password_b64=b64("secret-9"),
        )
        resp = await client.get(f"{BASE}/{box_id}", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == box_id
        # admin держит view_password → пароль доносится в base64
        assert body["base_user_password_b64"] is not None
        assert base64.b64decode(body["base_user_password_b64"]).decode() == "secret-9"

    async def test_guest_gets_box_without_password(self, client, admin_token, guest_token_a):
        box_id = await self._create(client, admin_token, name="guest-view")
        resp = await client.get(f"{BASE}/{box_id}", headers=_hdr(guest_token_a))
        assert resp.status_code == 200
        assert resp.json()["base_user_password_b64"] is None

    async def test_cross_dept_returns_404(self, client, admin_token, admin_token_b):
        box_id = await self._create(client, admin_token, name="secret-box")
        resp = await client.get(f"{BASE}/{box_id}", headers=_hdr(admin_token_b))
        assert_error(resp, 404, "BOX_NOT_FOUND")

    async def test_missing_returns_404(self, client, admin_token):
        resp = await client.get(f"{BASE}/box_deadbeef", headers=_hdr(admin_token))
        assert_error(resp, 404, "BOX_NOT_FOUND")

    async def test_no_role_returns_403(self, client, admin_token, no_role_token_a):
        box_id = await self._create(client, admin_token, name="norole")
        resp = await client.get(f"{BASE}/{box_id}", headers=_hdr(no_role_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── GET /boxes (list) ────────────────────────────────────────────────────────


class TestListBoxes:
    async def test_list_scoped_to_department(
        self, client, admin_token, admin_token_b,
    ):
        await client.post(BASE, headers=_hdr(admin_token), json=_box_body(name="a1"))
        await client.post(BASE, headers=_hdr(admin_token), json=_box_body(name="a2"))
        await client.post(
            BASE, headers=_hdr(admin_token_b),
            json=_box_body(name="b1", department_id="dep_b"),
        )
        resp = await client.get(BASE, headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        names = {item["name"] for item in body["items"]}
        assert names == {"a1", "a2"}
        # список не светит пароль
        for item in body["items"]:
            assert item["base_user_password_b64"] is None

    async def test_guest_can_list(self, client, admin_token, guest_token_a):
        await client.post(BASE, headers=_hdr(admin_token), json=_box_body(name="g1"))
        resp = await client.get(BASE, headers=_hdr(guest_token_a))
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_no_role_returns_403(self, client, no_role_token_a):
        resp = await client.get(BASE, headers=_hdr(no_role_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── PATCH /boxes/{id} ────────────────────────────────────────────────────────


class TestUpdateBox:
    async def _create(self, client, token, **overrides) -> str:
        resp = await client.post(BASE, headers=_hdr(token), json=_box_body(**overrides))
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    async def test_admin_updates_fields(self, client, admin_token):
        box_id = await self._create(client, admin_token, name="upd")
        resp = await client.patch(
            f"{BASE}/{box_id}", headers=_hdr(admin_token),
            json={"format": "raw", "os_versions": ["1.8.1.6"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["format"] == "raw"
        assert body["os_versions"] == ["1.8.1.6"]

    async def test_update_password_reveal_roundtrip(self, client, admin_token):
        box_id = await self._create(client, admin_token, name="upd-pw")
        resp = await client.patch(
            f"{BASE}/{box_id}", headers=_hdr(admin_token),
            json={"base_user_password_b64": b64("new-pass-1")},
        )
        assert resp.status_code == 200
        assert resp.json()["base_user_password_b64"] is None
        get = await client.get(f"{BASE}/{box_id}", headers=_hdr(admin_token))
        revealed = get.json()["base_user_password_b64"]
        assert base64.b64decode(revealed).decode() == "new-pass-1"

    async def test_guest_cannot_update(self, client, admin_token, guest_token_a):
        box_id = await self._create(client, admin_token, name="guest-upd")
        resp = await client.patch(
            f"{BASE}/{box_id}", headers=_hdr(guest_token_a), json={"format": "raw"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_returns_404(self, client, admin_token, admin_token_b):
        box_id = await self._create(client, admin_token, name="xdept-upd")
        resp = await client.patch(
            f"{BASE}/{box_id}", headers=_hdr(admin_token_b), json={"format": "raw"},
        )
        assert_error(resp, 404, "BOX_NOT_FOUND")


# ── DELETE /boxes/{id} ───────────────────────────────────────────────────────


class TestDeleteBox:
    async def _create(self, client, token, **overrides) -> str:
        resp = await client.post(BASE, headers=_hdr(token), json=_box_body(**overrides))
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    async def test_admin_deletes(self, client, admin_token):
        box_id = await self._create(client, admin_token, name="del")
        resp = await client.delete(f"{BASE}/{box_id}", headers=_hdr(admin_token))
        assert resp.status_code == 200
        get = await client.get(f"{BASE}/{box_id}", headers=_hdr(admin_token))
        assert_error(get, 404, "BOX_NOT_FOUND")

    async def test_guest_cannot_delete(self, client, admin_token, guest_token_a):
        box_id = await self._create(client, admin_token, name="guest-del")
        resp = await client.delete(f"{BASE}/{box_id}", headers=_hdr(guest_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_returns_404(self, client, admin_token, admin_token_b):
        box_id = await self._create(client, admin_token, name="xdept-del")
        resp = await client.delete(f"{BASE}/{box_id}", headers=_hdr(admin_token_b))
        assert_error(resp, 404, "BOX_NOT_FOUND")
