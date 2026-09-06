"""GET/PUT /settings/host-services — SSH-доступ к хосту для host-service control.

Платформенный singleton под `account_admin`, зеркало `test_acs_lock_gate.py`-
соседа `AcsSettings` по форме (см. `src/services/host_services_settings.py`).
Приватный ключ никогда не отдаётся обратно — только `private_key_is_set`.
"""

from __future__ import annotations

from tests._helpers import assert_error, auth_hdr as _hdr

URL = "/api/server/v1/settings/host-services"

_FAKE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nfakefakefake\n-----END OPENSSH PRIVATE KEY-----\n"


class TestAuth:
    async def test_anonymous_returns_401(self, client):
        resp = await client.get(URL)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_department_admin_returns_403(self, client, admin_role_token_a):
        resp = await client.get(URL, headers=_hdr(admin_role_token_a))
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")

    async def test_account_admin_returns_200(self, client, account_admin_token):
        resp = await client.get(URL, headers=_hdr(account_admin_token))
        assert resp.status_code == 200

    async def test_put_department_admin_returns_403(self, client, admin_role_token_a):
        resp = await client.put(URL, headers=_hdr(admin_role_token_a), json={"ssh_host": "1.2.3.4"})
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")


class TestDefaults:
    async def test_no_row_returns_unconfigured_defaults(self, client, account_admin_token):
        resp = await client.get(URL, headers=_hdr(account_admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "configured": False,
            "ssh_host": None,
            "ssh_port": 22,
            "ssh_user": None,
            "private_key_is_set": False,
        }


class TestUpdate:
    async def test_full_update_marks_configured(self, client, account_admin_token):
        resp = await client.put(
            URL,
            headers=_hdr(account_admin_token),
            json={
                "ssh_host": "10.177.103.10",
                "ssh_port": 22,
                "ssh_user": "emm-host-control",
                "ssh_private_key": _FAKE_KEY,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["ssh_host"] == "10.177.103.10"
        assert body["ssh_port"] == 22
        assert body["ssh_user"] == "emm-host-control"
        assert body["private_key_is_set"] is True
        # Ключ никогда не возвращается.
        assert "ssh_private_key" not in body
        assert "ssh_private_key_encrypted" not in body

    async def test_partial_update_preserves_other_fields(self, client, account_admin_token):
        await client.put(
            URL,
            headers=_hdr(account_admin_token),
            json={"ssh_host": "10.177.103.10", "ssh_user": "emm-host-control", "ssh_private_key": _FAKE_KEY},
        )
        resp = await client.put(URL, headers=_hdr(account_admin_token), json={"ssh_port": 2222})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ssh_port"] == 2222
        assert body["ssh_host"] == "10.177.103.10"
        assert body["ssh_user"] == "emm-host-control"
        assert body["private_key_is_set"] is True
        assert body["configured"] is True

    async def test_missing_host_or_key_not_configured(self, client, account_admin_token):
        resp = await client.put(URL, headers=_hdr(account_admin_token), json={"ssh_user": "emm-host-control"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["private_key_is_set"] is False

    async def test_empty_string_host_clears_it(self, client, account_admin_token):
        await client.put(
            URL,
            headers=_hdr(account_admin_token),
            json={"ssh_host": "10.177.103.10", "ssh_user": "u", "ssh_private_key": _FAKE_KEY},
        )
        resp = await client.put(URL, headers=_hdr(account_admin_token), json={"ssh_host": ""})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ssh_host"] is None
        assert body["configured"] is False

    async def test_clear_private_key(self, client, account_admin_token):
        await client.put(
            URL,
            headers=_hdr(account_admin_token),
            json={"ssh_host": "h", "ssh_user": "u", "ssh_private_key": _FAKE_KEY},
        )
        resp = await client.put(URL, headers=_hdr(account_admin_token), json={"clear_private_key": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["private_key_is_set"] is False
        assert body["configured"] is False

    async def test_clear_private_key_ignored_when_key_also_sent(self, client, account_admin_token):
        resp = await client.put(
            URL,
            headers=_hdr(account_admin_token),
            json={"ssh_host": "h", "ssh_user": "u", "ssh_private_key": _FAKE_KEY, "clear_private_key": True},
        )
        assert resp.status_code == 200
        assert resp.json()["private_key_is_set"] is True

    async def test_invalid_port_rejected(self, client, account_admin_token):
        resp = await client.put(URL, headers=_hdr(account_admin_token), json={"ssh_port": 0})
        assert resp.status_code == 422


class TestAudit:
    async def test_update_emits_settings_host_services_updated(self, client, account_admin_token, monkeypatch):
        captured = []

        def _fake_emit(action, **kwargs):
            captured.append((action, kwargs))

        import src.services.host_services_settings as svc_module

        monkeypatch.setattr(svc_module.audit_service, "emit", _fake_emit)

        resp = await client.put(
            URL,
            headers=_hdr(account_admin_token),
            json={"ssh_host": "h", "ssh_user": "u", "ssh_private_key": _FAKE_KEY},
        )
        assert resp.status_code == 200
        assert len(captured) == 1
        action, kwargs = captured[0]
        assert action == "settings.host_services_updated"
        # Ключ не логируется — только факт private_key_set.
        details = kwargs["details"]
        assert "ssh_private_key" not in details
        assert details["private_key_set"] is True
        assert details["ssh_host"] == "h"
        assert details["ssh_user"] == "u"
