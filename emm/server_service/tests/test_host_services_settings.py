"""GET/PUT /settings/host-services + CRUD /settings/host-services/units — per-department.

Каждый отдел настраивает своё SSH-подключение и свой список systemd-юнитов.
Управление — department_admin своего отдела ИЛИ носитель `admin` service-роли
`server_service` в этом же отделе; account_admin не имеет доступа вовсе (нет
department_id → отбивается уже на уровне `CurrentIdentity`, до любого
permission-чека). Приватный ключ никогда не отдаётся обратно — только
`private_key_is_set`.
"""

from __future__ import annotations

from tests._helpers import assert_error, auth_hdr as _hdr

URL = "/api/server/v1/settings/host-services"
UNITS_URL = f"{URL}/units"

_FAKE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nfakefakefake\n-----END OPENSSH PRIVATE KEY-----\n"


class TestAuth:
    async def test_anonymous_returns_401(self, client):
        resp = await client.get(URL)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_account_admin_returns_403(self, client, account_admin_token):
        """`platform_admin_guard` blocks account_admin on this path before the
        request ever reaches `CurrentIdentity`/the handler — settings CRUD is
        now business data, not exempted anymore (see middleware docstring)."""
        resp = await client.get(URL, headers=_hdr(account_admin_token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_account_admin_put_returns_403(self, client, account_admin_token):
        resp = await client.put(URL, headers=_hdr(account_admin_token), json={"ssh_host": "1.2.3.4"})
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_non_admin_service_role_returns_403(self, client, reader_token_a):
        """Обычная роль (reader) без гранта `host_service_manage` — PERMISSION_DENIED."""
        resp = await client.get(URL, headers=_hdr(reader_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_department_admin_returns_200(self, client, admin_token):
        resp = await client.get(URL, headers=_hdr(admin_token))
        assert resp.status_code == 200

    async def test_service_admin_role_returns_200(self, client, admin_role_token_a):
        """Носитель `admin` service-роли (не department_admin) тоже управляет."""
        resp = await client.get(URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

    async def test_put_department_admin_returns_200(self, client, admin_token):
        resp = await client.put(URL, headers=_hdr(admin_token), json={"ssh_host": "1.2.3.4"})
        assert resp.status_code == 200


class TestDefaults:
    async def test_no_row_returns_unconfigured_defaults(self, client, admin_token):
        resp = await client.get(URL, headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "configured": False,
            "ssh_host": None,
            "ssh_port": 22,
            "ssh_user": None,
            "private_key_is_set": False,
            "credential_id": None,
            "legacy_private_key_is_set": False,
        }


class TestUpdate:
    async def test_full_update_marks_configured(self, client, admin_token):
        resp = await client.put(
            URL,
            headers=_hdr(admin_token),
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
        assert "ssh_private_key" not in body
        assert "ssh_private_key_encrypted" not in body

    async def test_partial_update_preserves_other_fields(self, client, admin_token):
        await client.put(
            URL,
            headers=_hdr(admin_token),
            json={"ssh_host": "10.177.103.10", "ssh_user": "emm-host-control", "ssh_private_key": _FAKE_KEY},
        )
        resp = await client.put(URL, headers=_hdr(admin_token), json={"ssh_port": 2222})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ssh_port"] == 2222
        assert body["ssh_host"] == "10.177.103.10"
        assert body["ssh_user"] == "emm-host-control"
        assert body["private_key_is_set"] is True
        assert body["configured"] is True

    async def test_missing_host_or_key_not_configured(self, client, admin_token):
        resp = await client.put(URL, headers=_hdr(admin_token), json={"ssh_user": "emm-host-control"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["private_key_is_set"] is False

    async def test_empty_string_host_clears_it(self, client, admin_token):
        await client.put(
            URL, headers=_hdr(admin_token), json={"ssh_host": "10.177.103.10", "ssh_user": "u", "ssh_private_key": _FAKE_KEY},
        )
        resp = await client.put(URL, headers=_hdr(admin_token), json={"ssh_host": ""})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ssh_host"] is None
        assert body["configured"] is False

    async def test_clear_private_key(self, client, admin_token):
        await client.put(URL, headers=_hdr(admin_token), json={"ssh_host": "h", "ssh_user": "u", "ssh_private_key": _FAKE_KEY})
        resp = await client.put(URL, headers=_hdr(admin_token), json={"clear_private_key": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["private_key_is_set"] is False
        assert body["configured"] is False

    async def test_clear_private_key_ignored_when_key_also_sent(self, client, admin_token):
        resp = await client.put(
            URL,
            headers=_hdr(admin_token),
            json={"ssh_host": "h", "ssh_user": "u", "ssh_private_key": _FAKE_KEY, "clear_private_key": True},
        )
        assert resp.status_code == 200
        assert resp.json()["private_key_is_set"] is True

    async def test_invalid_port_rejected(self, client, admin_token):
        resp = await client.put(URL, headers=_hdr(admin_token), json={"ssh_port": 0})
        assert resp.status_code == 422


class TestDepartmentIsolation:
    async def test_departments_have_independent_rows(self, client, admin_token, admin_token_b):
        put_resp = await client.put(
            URL,
            headers=_hdr(admin_token),
            json={"ssh_host": "10.0.0.1", "ssh_user": "dep-a-user", "ssh_private_key": _FAKE_KEY},
        )
        assert put_resp.status_code == 200

        other_dept_resp = await client.get(URL, headers=_hdr(admin_token_b))
        assert other_dept_resp.status_code == 200
        assert other_dept_resp.json() == {
            "configured": False, "ssh_host": None, "ssh_port": 22, "ssh_user": None, "private_key_is_set": False,
            "credential_id": None, "legacy_private_key_is_set": False,
        }

        own_dept_resp = await client.get(URL, headers=_hdr(admin_token))
        assert own_dept_resp.json()["ssh_host"] == "10.0.0.1"


class TestAudit:
    async def test_update_emits_settings_host_services_updated(self, client, admin_token, monkeypatch):
        captured = []

        def _fake_emit(action, **kwargs):
            captured.append((action, kwargs))

        import src.services.host_services_settings as svc_module

        monkeypatch.setattr(svc_module.audit_service, "emit", _fake_emit)

        resp = await client.put(
            URL, headers=_hdr(admin_token), json={"ssh_host": "h", "ssh_user": "u", "ssh_private_key": _FAKE_KEY},
        )
        assert resp.status_code == 200
        assert len(captured) == 1
        action, kwargs = captured[0]
        assert action == "settings.host_services_updated"
        details = kwargs["details"]
        assert "ssh_private_key" not in details
        assert details["private_key_set"] is True
        assert details["ssh_host"] == "h"
        assert details["ssh_user"] == "u"


class TestUnitsAuth:
    async def test_anonymous_401(self, client):
        resp = await client.get(UNITS_URL)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_account_admin_403(self, client, account_admin_token):
        resp = await client.get(UNITS_URL, headers=_hdr(account_admin_token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_non_admin_role_403(self, client, reader_token_a):
        resp = await client.post(UNITS_URL, headers=_hdr(reader_token_a), json={"unit_name": "acs"})
        assert_error(resp, 403, "PERMISSION_DENIED")


class TestUnitsCrud:
    async def test_list_starts_empty(self, client, admin_token):
        resp = await client.get(UNITS_URL, headers=_hdr(admin_token))
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    async def test_create_defaults_label_to_unit_name(self, client, admin_token):
        resp = await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["unit_name"] == "acs"
        assert body["label"] == "acs"
        assert body["id"].startswith("hsu_")

    async def test_create_with_explicit_label(self, client, admin_token):
        resp = await client.post(
            UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "allta_auth", "label": "Auth service"},
        )
        assert resp.status_code == 201
        assert resp.json()["label"] == "Auth service"

    async def test_create_strips_dot_service_suffix(self, client, admin_token):
        resp = await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs.service"})
        assert resp.status_code == 201
        assert resp.json()["unit_name"] == "acs"

    async def test_create_duplicate_returns_409(self, client, admin_token):
        await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs"})
        resp = await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs"})
        assert_error(resp, 409, "HOST_SERVICE_UNIT_ALREADY_EXISTS")

    async def test_create_duplicate_after_service_suffix_normalization_409(self, client, admin_token):
        await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs"})
        resp = await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs.service"})
        assert_error(resp, 409, "HOST_SERVICE_UNIT_ALREADY_EXISTS")

    async def test_create_invalid_charset_returns_422(self, client, admin_token):
        resp = await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs; rm -rf /"})
        assert_error(resp, 422, "HOST_SERVICE_UNIT_NAME_INVALID")

    async def test_list_reflects_created_units(self, client, admin_token):
        await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs"})
        await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "devpi"})
        resp = await client.get(UNITS_URL, headers=_hdr(admin_token))
        names = sorted(item["unit_name"] for item in resp.json()["items"])
        assert names == ["acs", "devpi"]

    async def test_rename_label(self, client, admin_token):
        create_resp = await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs"})
        unit_id = create_resp.json()["id"]
        resp = await client.patch(f"{UNITS_URL}/{unit_id}", headers=_hdr(admin_token), json={"label": "ACS"})
        assert resp.status_code == 200
        assert resp.json()["label"] == "ACS"
        assert resp.json()["unit_name"] == "acs"

    async def test_delete_unit(self, client, admin_token):
        create_resp = await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs"})
        unit_id = create_resp.json()["id"]
        resp = await client.delete(f"{UNITS_URL}/{unit_id}", headers=_hdr(admin_token))
        assert resp.status_code == 204
        list_resp = await client.get(UNITS_URL, headers=_hdr(admin_token))
        assert list_resp.json() == {"items": []}

    async def test_delete_unknown_unit_returns_404(self, client, admin_token):
        resp = await client.delete(f"{UNITS_URL}/hsu_doesnotexist", headers=_hdr(admin_token))
        assert_error(resp, 404, "HOST_SERVICE_UNIT_NOT_FOUND")

    async def test_rename_unknown_unit_returns_404(self, client, admin_token):
        resp = await client.patch(f"{UNITS_URL}/hsu_doesnotexist", headers=_hdr(admin_token), json={"label": "x"})
        assert_error(resp, 404, "HOST_SERVICE_UNIT_NOT_FOUND")

    async def test_delete_emits_audit(self, client, admin_token, monkeypatch):
        captured = []
        import src.services.host_services_settings as svc_module

        monkeypatch.setattr(svc_module.audit_service, "emit", lambda action, **k: captured.append((action, k)))

        create_resp = await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs"})
        unit_id = create_resp.json()["id"]
        captured.clear()
        resp = await client.delete(f"{UNITS_URL}/{unit_id}", headers=_hdr(admin_token))
        assert resp.status_code == 204
        actions = [a for a, _ in captured]
        assert "settings.host_service_unit_removed" in actions

    async def test_create_emits_audit(self, client, admin_token, monkeypatch):
        captured = []
        import src.services.host_services_settings as svc_module

        monkeypatch.setattr(svc_module.audit_service, "emit", lambda action, **k: captured.append((action, k)))

        resp = await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs"})
        assert resp.status_code == 201
        actions = [a for a, _ in captured]
        assert "settings.host_service_unit_added" in actions


class TestUnitsCrossDepartmentIsolation:
    async def test_other_department_does_not_see_unit(self, client, admin_token, admin_token_b):
        create_resp = await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs"})
        unit_id = create_resp.json()["id"]

        list_resp = await client.get(UNITS_URL, headers=_hdr(admin_token_b))
        assert list_resp.json() == {"items": []}

        delete_resp = await client.delete(f"{UNITS_URL}/{unit_id}", headers=_hdr(admin_token_b))
        assert_error(delete_resp, 404, "HOST_SERVICE_UNIT_NOT_FOUND")

        patch_resp = await client.patch(f"{UNITS_URL}/{unit_id}", headers=_hdr(admin_token_b), json={"label": "x"})
        assert_error(patch_resp, 404, "HOST_SERVICE_UNIT_NOT_FOUND")

    async def test_same_unit_name_allowed_in_different_departments(self, client, admin_token, admin_token_b):
        resp_a = await client.post(UNITS_URL, headers=_hdr(admin_token), json={"unit_name": "acs"})
        resp_b = await client.post(UNITS_URL, headers=_hdr(admin_token_b), json={"unit_name": "acs"})
        assert resp_a.status_code == 201
        assert resp_b.status_code == 201
        assert resp_a.json()["id"] != resp_b.json()["id"]
