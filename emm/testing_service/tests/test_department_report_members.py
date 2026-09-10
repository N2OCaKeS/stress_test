"""Тесты `/api/testing/v1/departments/{department_id}/report-members` (§9.1 плана миграции).

Чтение открыто любому аутентифицированному актору, запись — под матрицей
прав (сид-миграция даёт её системной роли `admin`), тот же паттерн, что у
`test_definitions`/`test_stands`.
"""

from __future__ import annotations

from tests.conftest import auth_hdr as _hdr

BASE = "/api/testing/v1/departments/dep_a/report-members"


def _payload(**overrides) -> dict:
    base = {
        "display_name": "Иванов Иван",
        "bitbucket_username": "ivanov",
        "jira_author_name": "Ivan Ivanov",
        "jira_tempo_worker_key": "JIRAUSER100",
    }
    base.update(overrides)
    return base


class TestCreate:
    async def test_admin_creates_member(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["department_id"] == "dep_a"
        assert body["display_name"] == "Иванов Иван"
        assert body["is_active"] is True

    async def test_guest_forbidden(self, client, guest_token):
        resp = await client.post(BASE, headers=_hdr(guest_token), json=_payload())
        assert resp.status_code == 403, resp.text

    async def test_no_role_forbidden(self, client, no_role_token):
        resp = await client.post(BASE, headers=_hdr(no_role_token), json=_payload())
        assert resp.status_code == 403, resp.text

    async def test_anonymous_401(self, client):
        resp = await client.post(BASE, json=_payload())
        assert resp.status_code == 401, resp.text

    async def test_minimal_payload_defaults(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token), json={"display_name": "Петров Пётр"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["bitbucket_username"] is None
        assert body["jira_author_name"] is None
        assert body["jira_tempo_worker_key"] is None


class TestReadAccess:
    async def test_any_authenticated_can_list(self, client, guest_token, admin_token):
        await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        resp = await client.get(BASE, headers=_hdr(guest_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] >= 1

    async def test_get_by_id(self, client, admin_token):
        created = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        member_id = created.json()["id"]
        resp = await client.get(f"{BASE}/{member_id}", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == member_id

    async def test_get_missing_404(self, client, admin_token):
        resp = await client.get(f"{BASE}/drm_does_not_exist", headers=_hdr(admin_token))
        assert resp.status_code == 404, resp.text

    async def test_anonymous_401(self, client):
        resp = await client.get(BASE)
        assert resp.status_code == 401, resp.text

    async def test_is_active_filter(self, client, admin_token):
        await client.post(BASE, headers=_hdr(admin_token), json=_payload(display_name="Активный"))
        inactive = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(display_name="Неактивный", bitbucket_username="inactive_user"),
        )
        inactive_id = inactive.json()["id"]
        await client.patch(
            f"{BASE}/{inactive_id}", headers=_hdr(admin_token), json={"is_active": False},
        )

        resp = await client.get(f"{BASE}?is_active=true", headers=_hdr(admin_token))
        names = [i["display_name"] for i in resp.json()["items"]]
        assert "Активный" in names
        assert "Неактивный" not in names


class TestUpdate:
    async def test_admin_updates_member(self, client, admin_token):
        created = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        member_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{member_id}", headers=_hdr(admin_token), json={"display_name": "Иванов И.И."},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["display_name"] == "Иванов И.И."

    async def test_guest_forbidden(self, client, admin_token, guest_token):
        created = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        member_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{member_id}", headers=_hdr(guest_token), json={"display_name": "x"},
        )
        assert resp.status_code == 403, resp.text

    async def test_missing_404(self, client, admin_token):
        resp = await client.patch(
            f"{BASE}/drm_does_not_exist", headers=_hdr(admin_token), json={"display_name": "x"},
        )
        assert resp.status_code == 404, resp.text


class TestDelete:
    async def test_admin_deletes_member(self, client, admin_token):
        created = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        member_id = created.json()["id"]
        resp = await client.delete(f"{BASE}/{member_id}", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text

        follow_up = await client.get(f"{BASE}/{member_id}", headers=_hdr(admin_token))
        assert follow_up.status_code == 404

    async def test_guest_forbidden(self, client, admin_token, guest_token):
        created = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        member_id = created.json()["id"]
        resp = await client.delete(f"{BASE}/{member_id}", headers=_hdr(guest_token))
        assert resp.status_code == 403, resp.text
