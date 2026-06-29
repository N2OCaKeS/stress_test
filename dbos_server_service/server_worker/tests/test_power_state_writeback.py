"""Тесты writeback'а состояния питания из power.status в server_service.

После живой пробы (BMC либо reachability-fallback) power.status шлёт
`submit_power_state` обратно — server_service обновляет кэш
`servers.power_state`. Callback best-effort: его фейл не валит read-only
power.status, результат всё равно уходит в task.result.
"""

from __future__ import annotations

from src.clients.redfish import RedfishError
from src.core.constants import TaskStatus
from src.core.exceptions import CredentialFetchError
from src.tasks import power


class _DummyBmc:
    """BMC-клиент-заглушка: dispatch берётся из monkeypatch'а, aclose — no-op."""

    async def aclose(self) -> None:
        return None


def _neutralize_breaker(monkeypatch):
    """Заглушить per-host BMC circuit breaker на время теста.

    Breaker держит state в общем Redis по ключу хоста и не сбрасывается
    между тестами. Без заглушки серия падающих BMC-проб открыла бы circuit
    для `bmc.test` и отравила бы соседние power-файлы.
    """
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr("src.tasks.power._breaker.check", _noop)
    monkeypatch.setattr("src.tasks.power._breaker.record_failure", _noop)
    monkeypatch.setattr("src.tasks.power._breaker.record_success", _noop)


def _capture_writeback(monkeypatch):
    """Подменить submit_power_state на захватывающий аргументы стаб."""
    calls: list[dict] = []

    async def fake_submit(server_id, power_state, source, target_department_id=None):
        calls.append({
            "server_id": server_id,
            "power_state": power_state,
            "source": source,
            "target_department_id": target_department_id,
        })
        return {"ok": True, "power_state": power_state, "checked_at": "2026-06-29T00:00:00Z"}

    monkeypatch.setattr(
        "src.tasks.power.server_service_client.submit_power_state", fake_submit,
    )
    return calls


def _patch_bmc_ok(monkeypatch, state: str):
    """Креды + фабрика клиента + get_power_state, отдающий заданное состояние."""
    _neutralize_breaker(monkeypatch)

    async def fake_fetch(server_id, target_department_id=None):
        return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

    async def _bmc_factory(creds, *, prefer="redfish"):
        return _DummyBmc()

    async def _state(_client):
        return state

    monkeypatch.setattr(
        "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
    )
    monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)
    monkeypatch.setattr("src.tasks.power.dispatch_get_power_state", _state)


def _patch_bmc_failure(monkeypatch):
    """Креды + фабрика клиента + падающий get_power_state (для fallback-веток)."""
    _neutralize_breaker(monkeypatch)

    async def fake_fetch(server_id, target_department_id=None):
        return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

    async def _bmc_factory(creds, *, prefer="redfish"):
        return _DummyBmc()

    async def _raise(_client):
        raise RedfishError(None, "bmc down")

    monkeypatch.setattr(
        "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
    )
    monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)
    monkeypatch.setattr("src.tasks.power.dispatch_get_power_state", _raise)


class TestPowerStatusWriteback:
    async def test_bmc_on_writes_back_on_source_bmc(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={
                "server_id": "srv_1",
                "target_department_id": "dep_42",
            },
        )
        _patch_bmc_ok(monkeypatch, "On")
        calls = _capture_writeback(monkeypatch)

        await power.power_status.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on", "source": "bmc"}
        assert calls == [{
            "server_id": "srv_1",
            "power_state": "on",
            "source": "bmc",
            "target_department_id": "dep_42",
        }]

    async def test_fallback_ssh_on_writes_back_on_source_ssh(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={
                "server_id": "srv_1",
                "host": "10.0.0.9",
                "target_department_id": "dep_42",
            },
        )
        _patch_bmc_failure(monkeypatch)
        calls = _capture_writeback(monkeypatch)

        async def fake_probe(host, *, ssh_port, ping_timeout, tcp_timeout):
            return "ssh"

        monkeypatch.setattr("src.tasks.power.probe_power_reachability", fake_probe)

        await power.power_status.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on", "source": "ssh"}
        assert calls == [{
            "server_id": "srv_1",
            "power_state": "on",
            "source": "ssh",
            "target_department_id": "dep_42",
        }]

    async def test_unknown_writes_back_unknown_source_bmc(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )
        _patch_bmc_failure(monkeypatch)
        calls = _capture_writeback(monkeypatch)

        async def fake_probe(host, *, ssh_port, ping_timeout, tcp_timeout):
            return None

        monkeypatch.setattr("src.tasks.power.probe_power_reachability", fake_probe)

        await power.power_status.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "unknown", "source": "bmc"}
        # target_department_id отсутствует в payload — деградируем graceful.
        assert calls == [{
            "server_id": "srv_1",
            "power_state": "unknown",
            "source": "bmc",
            "target_department_id": None,
        }]

    async def test_writeback_failure_does_not_fail_task(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "target_department_id": "dep_42"},
        )
        _patch_bmc_ok(monkeypatch, "On")

        async def boom(server_id, power_state, source, target_department_id=None):
            raise CredentialFetchError(
                error_code="POWER_STATE_REJECTED",
                message="nope",
            )

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.submit_power_state", boom,
        )

        await power.power_status.original_func(tid)

        t = await fetch_task(tid)
        # Фейл callback'а не валит read-only power.status.
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on", "source": "bmc"}
