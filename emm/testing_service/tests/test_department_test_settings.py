"""Тесты `/api/testing/v1/department-test-settings` (§2.4 плана миграции).

GET открыт любому аутентифицированному актору и никогда не 404 — отсутствие
строки значит дефолты. PUT (upsert) — под матрицей прав
`(department_test_settings, *, update)`.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import auth_hdr as _hdr

BASE = "/api/testing/v1/department-test-settings"


def _dept() -> str:
    return f"dep_{uuid.uuid4().hex[:8]}"


class TestGetDefaults:
    async def test_no_row_returns_defaults(self, client, admin_token):
        dept = _dept()
        resp = await client.get(f"{BASE}/{dept}", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] is None
        assert body["department_id"] == dept
        assert body["retry_enabled"] is True
        assert body["test_username"] == "u"
        assert body["activity_report_schedule"] is None

    async def test_open_to_any_authenticated_role(self, client, guest_token):
        resp = await client.get(f"{BASE}/{_dept()}", headers=_hdr(guest_token))
        assert resp.status_code == 200, resp.text

    async def test_anonymous_rejected(self, client):
        resp = await client.get(f"{BASE}/{_dept()}")
        assert resp.status_code == 401, resp.text


class TestUpsert:
    async def test_put_creates_row(self, client, admin_token):
        dept = _dept()
        resp = await client.put(
            f"{BASE}/{dept}", headers=_hdr(admin_token),
            json={"retry_enabled": False, "test_username": "tester"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] is not None
        assert body["retry_enabled"] is False
        assert body["test_username"] == "tester"

        again = await client.get(f"{BASE}/{dept}", headers=_hdr(admin_token))
        assert again.json()["retry_enabled"] is False
        assert again.json()["test_username"] == "tester"

    async def test_put_partial_update_keeps_other_fields(self, client, admin_token):
        dept = _dept()
        first = await client.put(
            f"{BASE}/{dept}", headers=_hdr(admin_token),
            json={"retry_enabled": False, "test_username": "tester"},
        )
        assert first.status_code == 200, first.text

        second = await client.put(
            f"{BASE}/{dept}", headers=_hdr(admin_token),
            json={"activity_report_schedule": "weekly"},
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["retry_enabled"] is False
        assert body["test_username"] == "tester"
        assert body["activity_report_schedule"] == "weekly"

    async def test_put_requires_update_permission(self, client, guest_token):
        resp = await client.put(
            f"{BASE}/{_dept()}", headers=_hdr(guest_token),
            json={"retry_enabled": False},
        )
        assert resp.status_code == 403, resp.text

    async def test_put_no_role_rejected(self, client, no_role_token):
        resp = await client.put(
            f"{BASE}/{_dept()}", headers=_hdr(no_role_token),
            json={"retry_enabled": False},
        )
        assert resp.status_code == 403, resp.text
