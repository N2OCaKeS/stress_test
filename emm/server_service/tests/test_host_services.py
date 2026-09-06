"""GET /host/services (status) и POST /host/services/{unit_id}/{action} (control).

`astra_health.check_all` (внешний HTTP+DNS) стабово подменяется на пустой
список во всех тестах — реальные внешние сети/DNS в тестовом окружении
недоступны и не должны участвовать в этом файле. SSH-транспорт мокается на
границе `asyncssh.connect`/`asyncssh.import_private_key`.

`allta` — теперь per-department (`HostServiceUnit`), не платформенный
12-юнитовый allowlist: каждый тест сам заводит SSH-конфиг + юниты для нужного
отдела через `/settings/host-services*`, ровно как это будет делать
department_admin/service admin через UI.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh

from tests._helpers import assert_error, auth_hdr as _hdr

from src.api.v1.endpoints import host_services as host_services_endpoint

STATUS_URL = "/api/server/v1/host/services"
SETTINGS_URL = "/api/server/v1/settings/host-services"
UNITS_URL = f"{SETTINGS_URL}/units"

_FAKE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nfakefakefake\n-----END OPENSSH PRIVATE KEY-----\n"


def _control_url(unit_id: str, action: str) -> str:
    return f"{STATUS_URL}/{unit_id}/{action}"


async def _configure_host_services(client, token) -> None:
    resp = await client.put(
        SETTINGS_URL,
        headers=_hdr(token),
        json={"ssh_host": "10.177.103.10", "ssh_user": "emm-host-control", "ssh_private_key": _FAKE_KEY},
    )
    assert resp.status_code == 200


async def _create_unit(client, token, unit_name: str) -> str:
    resp = await client.post(UNITS_URL, headers=_hdr(token), json={"unit_name": unit_name})
    assert resp.status_code == 201
    return resp.json()["id"]


def _fake_conn(run_return=None, run_side_effect=None):
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    if run_side_effect is not None:
        conn.run = AsyncMock(side_effect=run_side_effect)
    else:
        conn.run = AsyncMock(return_value=run_return)
    return conn


def _run_result(stdout="", stderr="", exit_status=0):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.exit_status = exit_status
    return result


def _stub_astra(monkeypatch) -> None:
    async def _fake_check_all():
        return []

    monkeypatch.setattr(host_services_endpoint.astra_health, "check_all", _fake_check_all)


# ── GET /host/services ───────────────────────────────────────────────────────


class TestStatusNoDepartment:
    async def test_platform_role_sees_empty_allta_no_ssh(self, client, account_admin_token, monkeypatch):
        """account_admin has no department_id — allta is always [], astra still works."""
        _stub_astra(monkeypatch)
        connect_mock = AsyncMock()
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        resp = await client.get(STATUS_URL, headers=_hdr(account_admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["astra"] == []
        assert body["allta"] == []
        connect_mock.assert_not_called()


class TestStatusNotConfigured:
    async def test_no_settings_row_returns_empty_allta_no_ssh(self, client, admin_role_token_a, monkeypatch):
        _stub_astra(monkeypatch)
        connect_mock = AsyncMock()
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        resp = await client.get(STATUS_URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["astra"] == []
        assert body["allta"] == []
        connect_mock.assert_not_called()

    async def test_configured_but_no_units_returns_empty_allta_no_ssh(
        self, client, admin_token, admin_role_token_a, monkeypatch
    ):
        await _configure_host_services(client, admin_token)
        _stub_astra(monkeypatch)
        connect_mock = AsyncMock()
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        resp = await client.get(STATUS_URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        assert resp.json()["allta"] == []
        connect_mock.assert_not_called()


class TestStatusUnreachable:
    async def test_configured_but_unreachable_marks_all_unknown(
        self, client, admin_token, admin_role_token_a, monkeypatch,
    ):
        await _configure_host_services(client, admin_token)
        await _create_unit(client, admin_token, "acs")
        await _create_unit(client, admin_token, "devpi")
        _stub_astra(monkeypatch)
        monkeypatch.setattr(asyncssh, "import_private_key", lambda key: MagicMock())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(side_effect=ConnectionRefusedError("refused")))

        resp = await client.get(STATUS_URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["allta"]) == 2
        assert all(item["status"] == "unknown" and item["error"] for item in body["allta"])


class TestStatusReachable:
    async def test_configured_and_reachable_maps_active_inactive(
        self, client, admin_token, admin_role_token_a, monkeypatch,
    ):
        await _configure_host_services(client, admin_token)
        await _create_unit(client, admin_token, "a_unit")
        await _create_unit(client, admin_token, "b_unit")
        _stub_astra(monkeypatch)
        monkeypatch.setattr(asyncssh, "import_private_key", lambda key: MagicMock())

        # Список юнитов сортируется по unit_name (a_unit, b_unit) — первый
        # результат active (up), второй inactive (down).
        results = [_run_result(stdout="active\n"), _run_result(stdout="inactive\n")]
        conn = _fake_conn(run_side_effect=results)
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        resp = await client.get(STATUS_URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["allta"]) == 2
        assert body["allta"][0]["status"] == "up"
        assert body["allta"][1]["status"] == "down"
        conn.close.assert_called_once()


class TestStatusDepartmentIsolation:
    async def test_other_department_sees_empty_allta(
        self, client, admin_token, admin_token_b, monkeypatch,
    ):
        await _configure_host_services(client, admin_token)
        await _create_unit(client, admin_token, "acs")
        _stub_astra(monkeypatch)
        connect_mock = AsyncMock()
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        resp = await client.get(STATUS_URL, headers=_hdr(admin_token_b))
        assert resp.status_code == 200
        assert resp.json()["allta"] == []
        connect_mock.assert_not_called()


# ── POST /host/services/{unit_id}/{action} ──────────────────────────────────


class TestControlAuth:
    async def test_anonymous_401(self, client):
        resp = await client.post(_control_url("hsu_x", "restart"))
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_account_admin_403(self, client, account_admin_token):
        """`platform_admin_guard` blocks account_admin here now — control is
        business data (not exempted), unlike `GET /host/services`."""
        resp = await client.post(_control_url("hsu_x", "restart"), headers=_hdr(account_admin_token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_non_admin_role_403(self, client, reader_token_a):
        resp = await client.post(_control_url("hsu_x", "restart"), headers=_hdr(reader_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_invalid_action_422(self, client, admin_token):
        resp = await client.post(_control_url("hsu_x", "reload"), headers=_hdr(admin_token))
        assert resp.status_code == 422


class TestControlUnknownUnit:
    async def test_unknown_unit_404_with_denied_audit(self, client, admin_token, monkeypatch):
        captured = []
        monkeypatch.setattr(
            host_services_endpoint.audit_service, "emit",
            lambda action, **k: captured.append((action, k)),
        )

        resp = await client.post(_control_url("hsu_doesnotexist", "restart"), headers=_hdr(admin_token))
        assert_error(resp, 404, "HOST_UNIT_UNKNOWN")

        control_events = [(a, k) for a, k in captured if a == "host_service.control"]
        assert len(control_events) == 1
        action, kwargs = control_events[0]
        assert kwargs["status"] == "denied"
        assert kwargs["allowed"] is False
        assert kwargs["target_id"] == "hsu_doesnotexist"
        assert kwargs["details"]["reason"] == "unknown_unit"


class TestControlCrossDepartmentUnit:
    async def test_other_departments_unit_id_is_unknown(self, client, admin_token, admin_token_b):
        unit_id = await _create_unit(client, admin_token, "acs")
        resp = await client.post(_control_url(unit_id, "restart"), headers=_hdr(admin_token_b))
        assert_error(resp, 404, "HOST_UNIT_UNKNOWN")


class TestControlNotConfigured:
    async def test_not_configured_returns_503(self, client, admin_token, monkeypatch):
        unit_id = await _create_unit(client, admin_token, "acs")
        captured = []
        monkeypatch.setattr(
            host_services_endpoint.audit_service, "emit",
            lambda action, **k: captured.append((action, k)),
        )

        resp = await client.post(_control_url(unit_id, "restart"), headers=_hdr(admin_token))
        assert_error(resp, 503, "HOST_SERVICES_NOT_CONFIGURED")

        control_events = [(a, k) for a, k in captured if a == "host_service.control"]
        assert len(control_events) == 1
        assert control_events[0][1]["status"] == "failure"


class TestControlSuccess:
    async def test_success_runs_exact_command_and_audits(self, client, admin_token, monkeypatch):
        await _configure_host_services(client, admin_token)
        unit_id = await _create_unit(client, admin_token, "acs")
        captured = []
        monkeypatch.setattr(
            host_services_endpoint.audit_service, "emit",
            lambda action, **k: captured.append((action, k)),
        )
        monkeypatch.setattr(asyncssh, "import_private_key", lambda key: MagicMock())
        conn = _fake_conn(run_return=_run_result(stdout="", stderr="", exit_status=0))
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        resp = await client.post(_control_url(unit_id, "restart"), headers=_hdr(admin_token))
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "unit_id": unit_id, "action": "restart", "output": ""}

        conn.run.assert_awaited_once()
        (cmd,), _kwargs = conn.run.call_args
        assert cmd == "systemctl restart acs.service"

        control_events = [(a, k) for a, k in captured if a == "host_service.control"]
        assert len(control_events) == 1
        action, kwargs = control_events[0]
        assert kwargs["status"] == "success"
        assert kwargs["target_id"] == unit_id


class TestControlGuardRejection:
    async def test_nonzero_exit_returns_502_and_failure_audit(self, client, admin_token, monkeypatch):
        await _configure_host_services(client, admin_token)
        unit_id = await _create_unit(client, admin_token, "acs")
        captured = []
        monkeypatch.setattr(
            host_services_endpoint.audit_service, "emit",
            lambda action, **k: captured.append((action, k)),
        )
        monkeypatch.setattr(asyncssh, "import_private_key", lambda key: MagicMock())
        conn = _fake_conn(
            run_return=_run_result(stdout="", stderr="emm-host-service-guard: rejected command", exit_status=1)
        )
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        resp = await client.post(_control_url(unit_id, "restart"), headers=_hdr(admin_token))
        assert_error(resp, 502, "HOST_SERVICE_CONTROL_FAILED")

        control_events = [(a, k) for a, k in captured if a == "host_service.control"]
        assert len(control_events) == 1
        assert control_events[0][1]["status"] == "failure"

    async def test_ssh_unreachable_returns_503(self, client, admin_token, monkeypatch):
        await _configure_host_services(client, admin_token)
        unit_id = await _create_unit(client, admin_token, "acs")
        monkeypatch.setattr(host_services_endpoint.audit_service, "emit", lambda action, **k: None)
        monkeypatch.setattr(asyncssh, "import_private_key", lambda key: MagicMock())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(side_effect=ConnectionRefusedError("refused")))

        resp = await client.post(_control_url(unit_id, "restart"), headers=_hdr(admin_token))
        assert_error(resp, 503, "HOST_SERVICE_SSH_UNAVAILABLE")
