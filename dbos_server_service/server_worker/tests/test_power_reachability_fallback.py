"""Тесты reachability-fallback'а для power.status.

Когда BMC-проба не дала определённого on/off (сеть/breaker/отказ), power.status
переходит к сетевой пробе самого сервера: ICMP-ping + TCP SSH-порт. Доступен →
`on` (source ping/ssh), недоступен → `unknown` (source bmc).
"""

from __future__ import annotations

from src.clients.redfish import RedfishError
from src.core.constants import TaskStatus
from src.tasks import power
from src.tasks import _reachability


class _DummyBmc:
    """BMC-клиент-заглушка: dispatch берётся из monkeypatch'а, aclose — no-op."""

    async def aclose(self) -> None:
        return None


def _neutralize_breaker(monkeypatch):
    """Заглушить per-host BMC circuit breaker на время теста.

    Breaker держит state в общем Redis по ключу хоста и НЕ сбрасывается между
    тестами (conftest чистит только audit-publisher-канал). Без заглушки серия
    падающих BMC-проб в этом файле открыла бы circuit для `bmc.test` и
    отравила бы и success-тест здесь, и соседние power-файлы.
    """
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr("src.tasks.power._breaker.check", _noop)
    monkeypatch.setattr("src.tasks.power._breaker.record_failure", _noop)
    monkeypatch.setattr("src.tasks.power._breaker.record_success", _noop)


def _patch_bmc_failure(monkeypatch):
    """Подменить fetch-креды, фабрику клиента и get_power_state на падающий BMC."""
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


class TestPowerStatusReachabilityFallback:
    async def test_bmc_unreachable_but_ssh_open_reports_on(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )
        _patch_bmc_failure(monkeypatch)

        async def fake_probe(host, *, ssh_port, ping_timeout, tcp_timeout):
            assert host == "10.0.0.9"
            return "ssh"

        monkeypatch.setattr("src.tasks.power.probe_power_reachability", fake_probe)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on", "source": "ssh"}

    async def test_bmc_unreachable_but_ping_ok_reports_on(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )
        _patch_bmc_failure(monkeypatch)

        async def fake_probe(host, *, ssh_port, ping_timeout, tcp_timeout):
            return "ping"

        monkeypatch.setattr("src.tasks.power.probe_power_reachability", fake_probe)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on", "source": "ping"}

    async def test_bmc_unreachable_and_server_unreachable_reports_unknown(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )
        _patch_bmc_failure(monkeypatch)

        async def fake_probe(host, *, ssh_port, ping_timeout, tcp_timeout):
            return None

        monkeypatch.setattr("src.tasks.power.probe_power_reachability", fake_probe)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        # read-only запрос не падает — отдаёт unknown, а не FAILED
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "unknown", "source": "bmc"}

    async def test_no_host_in_payload_skips_probe_and_returns_unknown(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )
        _patch_bmc_failure(monkeypatch)

        called = {"probe": False}

        async def fake_probe(host, *, ssh_port, ping_timeout, tcp_timeout):
            called["probe"] = True
            return "ssh"

        monkeypatch.setattr("src.tasks.power.probe_power_reachability", fake_probe)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.result == {"power_state": "unknown", "source": "bmc"}
        assert called["probe"] is False

    async def test_fallback_disabled_returns_unknown_without_probe(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )
        _patch_bmc_failure(monkeypatch)

        from src.core.config import get_settings
        monkeypatch.setattr(
            get_settings(), "power_reachability_fallback_enabled", False,
        )

        called = {"probe": False}

        async def fake_probe(host, *, ssh_port, ping_timeout, tcp_timeout):
            called["probe"] = True
            return "ssh"

        monkeypatch.setattr("src.tasks.power.probe_power_reachability", fake_probe)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.result == {"power_state": "unknown", "source": "bmc"}
        assert called["probe"] is False

    async def test_bmc_ok_reports_source_bmc_without_probe(
        self, make_task, fetch_task, monkeypatch,
    ):
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )

        _neutralize_breaker(monkeypatch)

        async def fake_fetch(server_id, target_department_id=None):
            return {"endpoint_url": "https://bmc.test", "username": "u", "password": "p"}

        async def _bmc_factory(creds, *, prefer="redfish"):
            return _DummyBmc()

        async def _state(_client):
            return "On"

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr("src.tasks.power._get_bmc", _bmc_factory)
        monkeypatch.setattr("src.tasks.power.dispatch_get_power_state", _state)

        called = {"probe": False}

        async def fake_probe(*a, **k):
            called["probe"] = True
            return "ssh"

        monkeypatch.setattr("src.tasks.power.probe_power_reachability", fake_probe)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.result == {"power_state": "on", "source": "bmc"}
        assert called["probe"] is False


class TestPowerStatusNoIpmiFallback:
    """Сервер без IPMI: fetch_ipmi_credentials падает до BMC-пробы.

    Для managed-серверов без BMC power-state раньше вис `unknown` — таска
    падала на `fetch_ipmi_credentials` ещё до reachability-fallback'а. Теперь
    `IPMI_CREDENTIALS_UNAVAILABLE` уводит в сетевую пробу, а `power_state`
    сохраняется через writeback (source ping/ssh).
    """

    def _capture_writeback(self, monkeypatch):
        calls: list[dict] = []

        async def fake_submit(server_id, power_state, source, target_department_id=None):
            calls.append({
                "server_id": server_id,
                "power_state": power_state,
                "source": source,
            })
            return {"ok": True, "power_state": power_state, "checked_at": "x"}

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.submit_power_state", fake_submit,
        )
        return calls

    async def test_no_ipmi_but_ssh_open_reports_on_and_writes_back(
        self, make_task, fetch_task, monkeypatch,
    ):
        from src.core.exceptions import CredentialFetchError

        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )

        async def fake_fetch(server_id, target_department_id=None):
            raise CredentialFetchError(
                error_code="IPMI_CREDENTIALS_UNAVAILABLE",
                message="no ipmi",
            )

        async def fake_probe(host, *, ssh_port, ping_timeout, tcp_timeout):
            assert host == "10.0.0.9"
            return "ssh"

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr("src.tasks.power.probe_power_reachability", fake_probe)
        writes = self._capture_writeback(monkeypatch)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "on", "source": "ssh"}
        # power_state реально уходит обратно в server_service, а не только в result.
        assert writes == [{"server_id": "srv_1", "power_state": "on", "source": "ssh"}]

    async def test_no_ipmi_and_unreachable_reports_unknown(
        self, make_task, fetch_task, monkeypatch,
    ):
        from src.core.exceptions import CredentialFetchError

        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )

        async def fake_fetch(server_id, target_department_id=None):
            raise CredentialFetchError(
                error_code="IPMI_CREDENTIALS_UNAVAILABLE", message="no ipmi",
            )

        async def fake_probe(host, *, ssh_port, ping_timeout, tcp_timeout):
            return None

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr("src.tasks.power.probe_power_reachability", fake_probe)
        writes = self._capture_writeback(monkeypatch)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {"power_state": "unknown", "source": "bmc"}
        assert writes == [{"server_id": "srv_1", "power_state": "unknown", "source": "bmc"}]

    async def test_server_service_unreachable_fails_task(
        self, make_task, fetch_task, monkeypatch,
    ):
        """Транспортный сбой server_service — реально временный, идём в retry/FAILED,
        а не выдаём ложный fallback."""
        from src.core.exceptions import CredentialFetchError

        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )

        async def fake_fetch(server_id, target_department_id=None):
            raise CredentialFetchError(
                error_code="SERVER_SERVICE_UNREACHABLE", message="down",
            )

        called = {"probe": False}

        async def fake_probe(*a, **k):
            called["probe"] = True
            return "ssh"

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr("src.tasks.power.probe_power_reachability", fake_probe)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.status != TaskStatus.SUCCEEDED
        assert called["probe"] is False


class TestProbeReachabilityUnit:
    def test_strip_host_variants(self):
        assert _reachability._strip_host("10.0.0.1") == "10.0.0.1"
        assert _reachability._strip_host("10.0.0.1:22") == "10.0.0.1"
        assert _reachability._strip_host("https://10.0.0.1/redfish/") == "10.0.0.1"
        assert _reachability._strip_host("[2001:db8::1]") == "2001:db8::1"
        assert _reachability._strip_host("[2001:db8::1]:22") == "2001:db8::1"
        assert _reachability._strip_host("2001:db8::1") == "2001:db8::1"
        assert _reachability._strip_host("host.example") == "host.example"
        assert _reachability._strip_host("  ") == ""

    async def test_probe_prefers_ping(self, monkeypatch):
        async def ping_ok(host, timeout):
            return True

        async def tcp_open(host, port, timeout):
            raise AssertionError("tcp probe should not run when ping succeeds")

        monkeypatch.setattr(_reachability, "_ping_ok", ping_ok)
        monkeypatch.setattr(_reachability, "_tcp_port_open", tcp_open)
        assert await _reachability.probe_power_reachability("10.0.0.1") == "ping"

    async def test_probe_falls_back_to_ssh(self, monkeypatch):
        async def ping_ok(host, timeout):
            return False

        async def tcp_open(host, port, timeout):
            assert port == 2222
            return True

        monkeypatch.setattr(_reachability, "_ping_ok", ping_ok)
        monkeypatch.setattr(_reachability, "_tcp_port_open", tcp_open)
        assert await _reachability.probe_power_reachability(
            "10.0.0.1", ssh_port=2222,
        ) == "ssh"

    async def test_probe_none_when_all_fail(self, monkeypatch):
        async def ping_ok(host, timeout):
            return False

        async def tcp_open(host, port, timeout):
            return False

        monkeypatch.setattr(_reachability, "_ping_ok", ping_ok)
        monkeypatch.setattr(_reachability, "_tcp_port_open", tcp_open)
        assert await _reachability.probe_power_reachability("10.0.0.1") is None

    async def test_probe_empty_host_returns_none(self):
        assert await _reachability.probe_power_reachability("  ") is None

    async def test_tcp_port_open_real_listener(self):
        import asyncio

        server = await asyncio.start_server(
            lambda r, w: w.close(), host="127.0.0.1", port=0,
        )
        port = server.sockets[0].getsockname()[1]
        try:
            assert await _reachability._tcp_port_open("127.0.0.1", port, 2.0) is True
        finally:
            server.close()
            await server.wait_closed()
        # Порт закрыт после остановки сервера → коннект не пройдёт.
        assert await _reachability._tcp_port_open("127.0.0.1", port, 0.5) is False
