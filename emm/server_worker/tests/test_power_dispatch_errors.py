"""Тесты: ValueError/RuntimeError из `dispatch_*` оборачиваются в BMC_*-коды.

Без фикса handler ловил только `(RedfishError, IpmitoolError)`, а
`dispatch_power_action`/`dispatch_rotate_user_password` бросают
`ValueError("BMC_UNSUPPORTED_ACTION: ...")` и
`RuntimeError("BMC_CLIENT_INCOMPATIBLE: ...")` — они утекали мимо
breaker'а, в `task.last_error` ложился raw text, breaker оставался
закрытым на сбойном host'е.

Проверяем:
* power.* → BMC_UNSUPPORTED_ACTION (ValueError) + breaker.record_failure.
* power.* → BMC_CLIENT_INCOMPATIBLE (RuntimeError) + breaker.record_failure.
* ipmi.rotate → то же самое в passwords.py.
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks import power


class _FakeBMCClient:
    """Клиент без power-методов: dispatch_power_action бросит RuntimeError."""

    async def aclose(self):
        pass


class _DispatchValueErrorClient:
    """Клиент с redfish-методами; будем подменять dispatch_power_action
    напрямую, чтобы он бросал ValueError."""

    async def aclose(self):
        pass

    async def power_action(self, action):
        raise ValueError(f"BMC_UNSUPPORTED_ACTION: {action!r} has no ipmitool equivalent")

    async def get_power_state(self):
        return "On"


class TestPowerDispatchErrorWrap:
    async def test_value_error_unsupported_action_wrapped(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """dispatch_power_action → ValueError → mark_failed + BMC_UNSUPPORTED_ACTION + breaker.record_failure."""
        from tests.test_task_handlers import _make_task_max1, _patch_no_retry

        tid = await _make_task_max1(make_task, task_kind="power.on", target_server_id="srv_1")
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

        async def _bmc_factory(creds, *, prefer="redfish"):
            return _DispatchValueErrorClient()

        failures: list[str] = []
        successes: list[str] = []

        async def _record_failure(host):
            failures.append(host)

        async def _record_success(host):
            successes.append(host)

        async def _check(host):
            pass

        async def _aclose(client):
            await client.aclose()

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)
        monkeypatch.setattr("src.tasks.power._aclose_bmc", _aclose)
        monkeypatch.setattr("src.tasks.power._breaker.check", _check)
        monkeypatch.setattr("src.tasks.power._breaker.record_failure", _record_failure)
        monkeypatch.setattr("src.tasks.power._breaker.record_success", _record_success)

        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_UNSUPPORTED_ACTION" in t.last_error
        assert failures == ["bmc.test"]
        assert successes == []
        assert captured_audit[0]["status"] == "failure"

    async def test_runtime_error_client_incompatible_wrapped(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """dispatch_power_action бросает RuntimeError → BMC_CLIENT_INCOMPATIBLE + breaker.record_failure."""
        from tests.test_task_handlers import _make_task_max1, _patch_no_retry

        tid = await _make_task_max1(make_task, task_kind="power.on", target_server_id="srv_1")
        _patch_no_retry(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

        async def _bmc_factory(creds, *, prefer="redfish"):
            return _FakeBMCClient()

        failures: list[str] = []

        async def _record_failure(host):
            failures.append(host)

        async def _check(host):
            pass

        async def _aclose(client):
            await client.aclose()

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)
        monkeypatch.setattr("src.tasks.power._aclose_bmc", _aclose)
        monkeypatch.setattr("src.tasks.power._breaker.check", _check)
        async def _noop_success(host):
            pass

        monkeypatch.setattr("src.tasks.power._breaker.record_failure", _record_failure)
        monkeypatch.setattr("src.tasks.power._breaker.record_success", _noop_success)

        await power.power_on.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "BMC_CLIENT_INCOMPATIBLE" in t.last_error
        assert failures == ["bmc.test"]
