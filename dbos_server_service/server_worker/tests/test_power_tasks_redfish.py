"""End-to-end-style тесты power.* handler'ов через RedfishClient.

Здесь собран реальный `RedfishClient` поверх `httpx.MockTransport` (а не
голый мок). Покрытие: actual httpx-flow внутри handler'а + Redfish
error-mapping → `AppException(BMC_*)` → audit failure.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.clients.redfish import RedfishClient
from src.core.constants import TaskStatus
from src.tasks import power


def _redfish_with(handler):
    """RedfishClient поверх MockTransport — подмена реального BMC-клиента."""
    client = RedfishClient("https://bmc.test", "u", "p")
    client._client = httpx.AsyncClient(
        base_url="https://bmc.test",
        auth=("u", "p"),
        timeout=5.0,
        transport=httpx.MockTransport(handler),
    )
    return client


class TestPowerOnRedfishIntegration:
    async def test_power_on_calls_reset_and_returns_state(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="power.on", target_server_id="srv_1")

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

        requests_seen: list[tuple[str, str]] = []
        def handler(request: httpx.Request) -> httpx.Response:
            requests_seen.append((request.method, request.url.path))
            if request.method == "POST":
                return httpx.Response(204)
            if request.url.path == "/redfish/v1/Systems/1":
                return httpx.Response(200, json={"PowerState": "On"})
            return httpx.Response(404)

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        async def _bmc_factory(creds, *, prefer="redfish"):
            return _redfish_with(handler)
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)

        await power.power_on.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on"}
        # Был POST на Reset + GET на /Systems/1
        assert ("POST", "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset") in requests_seen
        assert ("GET", "/redfish/v1/Systems/1") in requests_seen
        assert captured_audit[0]["status"] == "success"

    async def test_power_on_409_already_on_marks_failed(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """BMC отверг ResetType=On (уже On) — handler пишет BMC_REJECTED."""
        from tests.test_task_handlers import _make_task_max1, _patch_no_retry

        tid = await _make_task_max1(make_task, task_kind="power.on", target_server_id="srv_1")
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(409, json={
                    "error": {
                        "code": "Base.1.0.PropertyValueNotInList",
                        "@Message.ExtendedInfo": [
                            {"Message": "System already powered On"},
                        ],
                    }
                })
            return httpx.Response(200, json={"PowerState": "On"})

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        async def _bmc_factory(creds, *, prefer="redfish"):
            return _redfish_with(handler)
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)

        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_REJECTED" in t.last_error
        assert captured_audit[0]["status"] == "failure"

    async def test_power_on_unreachable_retries_then_fails(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        from tests.test_task_handlers import _make_task_max1, _patch_no_retry

        tid = await _make_task_max1(make_task, task_kind="power.on", target_server_id="srv_1")
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("bmc down")

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        async def _bmc_factory(creds, *, prefer="redfish"):
            return _redfish_with(handler)
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)

        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_UNREACHABLE" in t.last_error


class TestPowerOffRedfishIntegration:
    async def test_power_off_includes_previous_state(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="power.off", target_server_id="srv_1")

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

        # State machine: первый GET On → POST 204 → второй GET Off
        state = {"value": "On"}
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content)
                if body["ResetType"] == "ForceOff":
                    state["value"] = "Off"
                return httpx.Response(204)
            return httpx.Response(200, json={"PowerState": state["value"]})

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        async def _bmc_factory(creds, *, prefer="redfish"):
            return _redfish_with(handler)
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)

        await power.power_off.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["power_state"] == "off"
        assert t.result["previous_power_state"] == "on"

    async def test_power_off_always_sends_force_off(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Sanity: power.off отправляет в Redfish ровно `ResetType: ForceOff`.

        Покрывает контракт «hard off only» — даже если payload содержит
        `graceful=true` (старый клиент), worker должен слать ForceOff и
        никогда GracefulShutdown.
        """
        tid = await make_task(
            task_kind="power.off",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "graceful": True},
        )

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

        seen_reset_types: list[str] = []
        state = {"value": "On"}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content)
                seen_reset_types.append(body["ResetType"])
                if body["ResetType"] == "ForceOff":
                    state["value"] = "Off"
                return httpx.Response(204)
            return httpx.Response(200, json={"PowerState": state["value"]})

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        async def _bmc_factory(creds, *, prefer="redfish"):
            return _redfish_with(handler)
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)

        await power.power_off.original_func(tid)

        assert seen_reset_types == ["ForceOff"]
        assert "GracefulShutdown" not in seen_reset_types


class TestPowerRebootRedfishIntegration:
    async def test_reboot_uses_force_restart(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="power.reboot", target_server_id="srv_1")

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

        captured_action = {}
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                captured_action["body"] = json.loads(request.content)
                return httpx.Response(204)
            return httpx.Response(200, json={"PowerState": "On"})

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        async def _bmc_factory(creds, *, prefer="redfish"):
            return _redfish_with(handler)
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)

        await power.power_reboot.original_func(tid)
        assert captured_action["body"]["ResetType"] == "ForceRestart"
        t = await fetch_task(tid)
        assert t.result["rebooted"] is True


class TestPowerStatusRedfishIntegration:
    async def test_status_get_only(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(task_kind="power.status", target_server_id="srv_1")
        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

        seen = []
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            return httpx.Response(200, json={"PowerState": "Off"})

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        async def _bmc_factory(creds, *, prefer="redfish"):
            return _redfish_with(handler)
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.result == {"power_state": "off"}
        # Только один GET — никаких POST'ов на status
        assert all(method == "GET" for method, _ in seen)


class TestPowerAuthFail:
    async def test_401_marks_failed_with_auth_code(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        from tests.test_task_handlers import _make_task_max1, _patch_no_retry

        tid = await _make_task_max1(make_task, task_kind="power.on", target_server_id="srv_1")
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc.test", "username": "wrong", "password": "wrong"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"code": "AuthFail"}})

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        async def _bmc_factory(creds, *, prefer="redfish"):
            return _redfish_with(handler)
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)

        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_AUTH_FAILED" in t.last_error


class TestNormalizePowerState:
    def test_camelcase_to_snake(self):
        from src.tasks.power import _normalize_power_state
        assert _normalize_power_state("On") == "on"
        assert _normalize_power_state("Off") == "off"
        assert _normalize_power_state("PoweringOn") == "powering_on"
        assert _normalize_power_state("PoweringOff") == "powering_off"
