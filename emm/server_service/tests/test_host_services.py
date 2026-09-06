"""GET /host/services (status) и POST /host/services/{unit}/{action} (control).

`astra_health.check_all` (внешний HTTP+DNS) стабово подменяется на пустой
список во всех тестах — реальные внешние сети/DNS в тестовом окружении
недоступны и не должны участвовать в этом файле (см. `services/astra_health.py`
для его собственного покрытия, если оно появится). SSH-транспорт мокается на
границе `asyncssh.connect`/`asyncssh.import_private_key`, тем же приёмом, что
`server_worker/tests/test_ssh_client.py` — не asyncssh-internals, а сама точка
входа модуля.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh

from tests._helpers import assert_error, auth_hdr as _hdr

from src.api.v1.endpoints import host_services as host_services_endpoint

STATUS_URL = "/api/server/v1/host/services"
SETTINGS_URL = "/api/server/v1/settings/host-services"

_FAKE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nfakefakefake\n-----END OPENSSH PRIVATE KEY-----\n"


def _control_url(unit: str, action: str) -> str:
    return f"{STATUS_URL}/{unit}/{action}"


async def _configure_host_services(client, account_admin_token) -> None:
    resp = await client.put(
        SETTINGS_URL,
        headers=_hdr(account_admin_token),
        json={"ssh_host": "10.177.103.10", "ssh_user": "emm-host-control", "ssh_private_key": _FAKE_KEY},
    )
    assert resp.status_code == 200


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


class TestStatusNotConfigured:
    async def test_no_settings_row_all_not_configured_no_ssh(self, client, admin_role_token_a, monkeypatch):
        _stub_astra(monkeypatch)
        connect_mock = AsyncMock()
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        resp = await client.get(STATUS_URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["astra"] == []
        assert len(body["allta"]) == 12
        assert all(item["status"] == "not_configured" and item["error"] is None for item in body["allta"])
        connect_mock.assert_not_called()


class TestStatusUnreachable:
    async def test_configured_but_unreachable_marks_all_unknown(
        self, client, account_admin_token, admin_role_token_a, monkeypatch,
    ):
        await _configure_host_services(client, account_admin_token)
        _stub_astra(monkeypatch)
        monkeypatch.setattr(asyncssh, "import_private_key", lambda key: MagicMock())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(side_effect=ConnectionRefusedError("refused")))

        resp = await client.get(STATUS_URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["allta"]) == 12
        assert all(item["status"] == "unknown" and item["error"] for item in body["allta"])


class TestStatusReachable:
    async def test_configured_and_reachable_maps_active_inactive(
        self, client, account_admin_token, admin_role_token_a, monkeypatch,
    ):
        await _configure_host_services(client, account_admin_token)
        _stub_astra(monkeypatch)
        monkeypatch.setattr(asyncssh, "import_private_key", lambda key: MagicMock())

        # По одному результату на каждый из 12 юнитов, в порядке ALLTA_HOST_UNITS:
        # первый — active (up), остальные — inactive (down).
        from src.services.host_control import ALLTA_HOST_UNITS

        results = [_run_result(stdout="active\n")] + [
            _run_result(stdout="inactive\n") for _ in range(len(ALLTA_HOST_UNITS) - 1)
        ]
        conn = _fake_conn(run_side_effect=results)
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        resp = await client.get(STATUS_URL, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["allta"]) == 12
        assert body["allta"][0]["status"] == "up"
        assert all(item["status"] == "down" for item in body["allta"][1:])
        conn.close.assert_called_once()


# ── POST /host/services/{unit}/{action} ──────────────────────────────────────


class TestControlAuth:
    async def test_anonymous_401(self, client):
        resp = await client.post(_control_url("acs", "restart"))
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_department_admin_403(self, client, admin_role_token_a):
        resp = await client.post(_control_url("acs", "restart"), headers=_hdr(admin_role_token_a))
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")

    async def test_invalid_action_422(self, client, account_admin_token):
        resp = await client.post(_control_url("acs", "reload"), headers=_hdr(account_admin_token))
        assert resp.status_code == 422


class TestControlUnknownUnit:
    async def test_unknown_unit_404_with_denied_audit(self, client, account_admin_token, monkeypatch):
        captured = []
        monkeypatch.setattr(
            host_services_endpoint.audit_service, "emit",
            lambda action, **k: captured.append((action, k)),
        )

        resp = await client.post(_control_url("nginx", "restart"), headers=_hdr(account_admin_token))
        assert_error(resp, 404, "HOST_UNIT_UNKNOWN")

        # Кроме нашего события, HTTP-middleware сам аудирует 4xx/5xx-ответ
        # (`http.client_error`/`http.access_denied`/`http.server_error`) —
        # фильтруем по своему action, не полагаясь на точное число событий.
        control_events = [(a, k) for a, k in captured if a == "host_service.control"]
        assert len(control_events) == 1
        action, kwargs = control_events[0]
        assert action == "host_service.control"
        assert kwargs["status"] == "denied"
        assert kwargs["allowed"] is False
        assert kwargs["target_id"] == "nginx"
        assert kwargs["details"]["reason"] == "unknown_unit"


class TestControlNotConfigured:
    async def test_not_configured_returns_503(self, client, account_admin_token, monkeypatch):
        captured = []
        monkeypatch.setattr(
            host_services_endpoint.audit_service, "emit",
            lambda action, **k: captured.append((action, k)),
        )

        resp = await client.post(_control_url("acs", "restart"), headers=_hdr(account_admin_token))
        assert_error(resp, 503, "HOST_SERVICES_NOT_CONFIGURED")

        control_events = [(a, k) for a, k in captured if a == "host_service.control"]
        assert len(control_events) == 1
        action, kwargs = control_events[0]
        assert action == "host_service.control"
        assert kwargs["status"] == "failure"


class TestControlSuccess:
    async def test_success_runs_exact_command_and_audits(self, client, account_admin_token, monkeypatch):
        await _configure_host_services(client, account_admin_token)
        captured = []
        monkeypatch.setattr(
            host_services_endpoint.audit_service, "emit",
            lambda action, **k: captured.append((action, k)),
        )
        monkeypatch.setattr(asyncssh, "import_private_key", lambda key: MagicMock())
        conn = _fake_conn(run_return=_run_result(stdout="", stderr="", exit_status=0))
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        resp = await client.post(_control_url("acs", "restart"), headers=_hdr(account_admin_token))
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "unit": "acs", "action": "restart", "output": ""}

        conn.run.assert_awaited_once()
        (cmd,), _kwargs = conn.run.call_args
        assert cmd == "systemctl restart acs.service"

        control_events = [(a, k) for a, k in captured if a == "host_service.control"]
        assert len(control_events) == 1
        action, kwargs = control_events[0]
        assert action == "host_service.control"
        assert kwargs["status"] == "success"
        assert kwargs["target_id"] == "acs"


class TestControlGuardRejection:
    async def test_nonzero_exit_returns_502_and_failure_audit(self, client, account_admin_token, monkeypatch):
        await _configure_host_services(client, account_admin_token)
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

        resp = await client.post(_control_url("acs", "restart"), headers=_hdr(account_admin_token))
        assert_error(resp, 502, "HOST_SERVICE_CONTROL_FAILED")

        control_events = [(a, k) for a, k in captured if a == "host_service.control"]
        assert len(control_events) == 1
        action, kwargs = control_events[0]
        assert action == "host_service.control"
        assert kwargs["status"] == "failure"

    async def test_ssh_unreachable_returns_503(self, client, account_admin_token, monkeypatch):
        await _configure_host_services(client, account_admin_token)
        monkeypatch.setattr(
            host_services_endpoint.audit_service, "emit", lambda action, **k: None,
        )
        monkeypatch.setattr(asyncssh, "import_private_key", lambda key: MagicMock())
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(side_effect=ConnectionRefusedError("refused")))

        resp = await client.post(_control_url("acs", "restart"), headers=_hdr(account_admin_token))
        assert_error(resp, 503, "HOST_SERVICE_SSH_UNAVAILABLE")
