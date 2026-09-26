"""Тесты `/api/testing/v1/department-test-settings` (§2.4 плана миграции).

GET доступен пользователям этого же отдела и никогда не 404 — отсутствие
строки значит дефолты. PUT (upsert) — department-scoped матрица
`(department_test_settings, *, update)`. Чужой отдел в обоих случаях 403 —
это проверяет `test_department_isolation.py`.
"""

from __future__ import annotations

import pytest

from tests.conftest import auth_hdr as _hdr

BASE = "/api/testing/v1/department-test-settings"

# Отдел фикстур `admin_token`/`guest_token`/`no_role_token` (см. conftest).
# Строки этой таблицы чистятся между тестами, поэтому «своего» отдела хватает
# и там, где раньше брался случайный.
OWN_DEPT = "dep_a"


def _dept() -> str:
    return OWN_DEPT


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
        assert body["activity_report_auto_generate"] is False

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
            json={"activity_report_auto_generate": True},
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["retry_enabled"] is False
        assert body["test_username"] == "tester"
        assert body["activity_report_auto_generate"] is True

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


# легаси `available_astra_services_checker` (`liballta.py:1784-1836`).
LEGACY_PREFLIGHT = {
    "enabled": True,
    "http": [
        {"url": "https://jira.astralinux.ru", "ok_status": "200"},
        {"url": "https://life.astralinux.ru", "ok_status": "200"},
        {"url": "https://git.astralinux.ru", "ok_status": "200"},
        {"url": "https://releases.devos.astralinux.ru", "ok_status": "200"},
    ],
    "dns_hosts": ["10.177.128.198", "10.177.180.246", "10.177.181.142"],
    "dns_port": 53,
    "poll_interval_seconds": 180,
    "timeout_seconds": 7200,
    "probe_timeout_seconds": 15,
}


class TestPreflight:
    """настройки preflight отдела (CONTRACTS.md C3/C6)."""

    async def test_defaults_are_legacy(self, client, admin_token):
        resp = await client.get(f"{BASE}/{_dept()}", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["preflight"] == LEGACY_PREFLIGHT

    async def test_row_without_preflight_reports_defaults(self, client, admin_token):
        resp = await client.put(f"{BASE}/{_dept()}", headers=_hdr(admin_token), json={"retry_enabled": False})
        assert resp.status_code == 200, resp.text
        assert resp.json()["preflight"] == LEGACY_PREFLIGHT

    async def test_put_replaces_preflight_and_keeps_other_fields(self, client, admin_token):
        dept = _dept()
        await client.put(f"{BASE}/{dept}", headers=_hdr(admin_token), json={"test_username": "tester"})
        custom = {
            "enabled": True,
            "http": [{"url": "https://repo.example.test/health", "ok_status": "lt500"}],
            "dns_hosts": [" 10.1.1.1 "],
            "dns_port": 5353,
            "poll_interval_seconds": 30,
            "timeout_seconds": 600,
        }
        resp = await client.put(f"{BASE}/{dept}", headers=_hdr(admin_token), json={"preflight": custom})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["test_username"] == "tester"
        assert body["preflight"] == {**custom, "dns_hosts": ["10.1.1.1"], "probe_timeout_seconds": 15}

        again = await client.get(f"{BASE}/{dept}", headers=_hdr(admin_token))
        assert again.json()["preflight"]["http"] == custom["http"]

        # Правка другого поля preflight не трогает.
        other = await client.put(f"{BASE}/{dept}", headers=_hdr(admin_token), json={"retry_enabled": False})
        assert other.json()["preflight"]["dns_port"] == 5353

    async def test_null_resets_to_defaults(self, client, admin_token):
        dept = _dept()
        await client.put(
            f"{BASE}/{dept}", headers=_hdr(admin_token),
            json={"preflight": {**LEGACY_PREFLIGHT, "enabled": False}},
        )
        resp = await client.put(f"{BASE}/{dept}", headers=_hdr(admin_token), json={"preflight": None})
        assert resp.status_code == 200, resp.text
        assert resp.json()["preflight"] == LEGACY_PREFLIGHT

    @pytest.mark.parametrize("bad", [
        {"http": [{"url": "https://x.test", "ok_status": "2xx"}]},
        {"http": [{"url": "ftp://x.test"}]},
        {"http": [{"url": "https://x.test/a b"}]},
        {"dns_hosts": [""]},
        {"dns_port": 0},
        {"poll_interval_seconds": 0},
        {"timeout_seconds": -1},
        {"unexpected": 1},
    ])
    async def test_invalid_preflight_rejected(self, client, admin_token, bad):
        resp = await client.put(
            f"{BASE}/{_dept()}", headers=_hdr(admin_token), json={"preflight": {**LEGACY_PREFLIGHT, **bad}},
        )
        assert resp.status_code == 422, resp.text
