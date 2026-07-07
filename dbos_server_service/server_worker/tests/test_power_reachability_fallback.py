"""Тесты сетевых сигналов power.status и reachability-пробы.

power.status собирает три НЕЗАВИСИМЫХ сигнала: ping и ssh (reachability +
latency) меряются всегда, BMC опрашивается отдельно (`ipmi_power_state`). Ни
один сигнал не «схлопывает» остальные: недоступный BMC не мешает ping/ssh, и
наоборот. Legacy-пара `power_state`/`source` считается прежней first-wins
логикой для обратной совместимости кэша.

Отдельно — юнит-тесты latency-проб (`_ping_probe`/`_tcp_probe`/
`probe_reachability_signals`) и сохранённой first-wins `probe_power_reachability`.
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


def _no_signals() -> dict:
    """Нейтральный набор сетевых сигналов: всё недоступно, latency нет."""
    return {
        "ping_reachable": False,
        "ping_latency_ms": None,
        "ssh_reachable": False,
        "ssh_latency_ms": None,
    }


def _patch_bmc_state(monkeypatch, state: str):
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
    """Креды + фабрика клиента + падающий get_power_state → ipmi=unknown."""
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


def _mute_writeback(monkeypatch):
    """Заглушить submit_power_state — тут проверяется result, не тело callback'а."""
    async def _noop(*a, **k):
        return {"ok": True}

    monkeypatch.setattr(
        "src.tasks.power.server_service_client.submit_power_state", _noop,
    )


class TestPowerStatusThreeSignals:
    async def test_all_three_signals_collected_independently(
        self, make_task, fetch_task, monkeypatch,
    ):
        # BMC=on, ping и ssh доступны с latency — все три сигнала в result,
        # legacy-пара first-wins садится на bmc.
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )
        _patch_bmc_state(monkeypatch, "On")
        _mute_writeback(monkeypatch)

        async def fake_signals(host, *, ssh_port, ping_timeout, tcp_timeout):
            assert host == "10.0.0.9"
            return {
                "ping_reachable": True,
                "ping_latency_ms": 1.23,
                "ssh_reachable": True,
                "ssh_latency_ms": 4.56,
            }

        monkeypatch.setattr("src.tasks.power.probe_reachability_signals", fake_signals)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {
            "power_state": "on",
            "source": "bmc",
            "ping_reachable": True,
            "ping_latency_ms": 1.23,
            "ssh_reachable": True,
            "ssh_latency_ms": 4.56,
            "ipmi_power_state": "on",
        }

    async def test_ipmi_unknown_does_not_drop_ping(
        self, make_task, fetch_task, monkeypatch,
    ):
        # BMC не ответил → ipmi=unknown, но ping прошёл: таска не падает,
        # ping-сигнал сохраняется, legacy-пара садится на ping.
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )
        _patch_bmc_failure(monkeypatch)
        _mute_writeback(monkeypatch)

        async def fake_signals(host, *, ssh_port, ping_timeout, tcp_timeout):
            return {
                "ping_reachable": True,
                "ping_latency_ms": 0.42,
                "ssh_reachable": False,
                "ssh_latency_ms": None,
            }

        monkeypatch.setattr("src.tasks.power.probe_reachability_signals", fake_signals)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {
            "power_state": "on",
            "source": "ping",
            "ping_reachable": True,
            "ping_latency_ms": 0.42,
            "ssh_reachable": False,
            "ssh_latency_ms": None,
            "ipmi_power_state": "unknown",
        }

    async def test_no_ipmi_credentials_does_not_fail_task(
        self, make_task, fetch_task, monkeypatch,
    ):
        # Сервер без IPMI-контроллера: fetch падает IPMI_CREDENTIALS_UNAVAILABLE,
        # ipmi=unknown, но ssh открыт — таска успешна, power_state on/ssh.
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

        async def fake_signals(host, *, ssh_port, ping_timeout, tcp_timeout):
            return {
                "ping_reachable": False,
                "ping_latency_ms": None,
                "ssh_reachable": True,
                "ssh_latency_ms": 3.14,
            }

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr("src.tasks.power.probe_reachability_signals", fake_signals)
        _mute_writeback(monkeypatch)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {
            "power_state": "on",
            "source": "ssh",
            "ping_reachable": False,
            "ping_latency_ms": None,
            "ssh_reachable": True,
            "ssh_latency_ms": 3.14,
            "ipmi_power_state": "unknown",
        }

    async def test_all_blind_returns_unknown(
        self, make_task, fetch_task, monkeypatch,
    ):
        # BMC не ответил и сервер недоступен по сети — read-only запрос не
        # падает, отдаёт unknown/bmc, сетевую недоступность в off не выдаём.
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )
        _patch_bmc_failure(monkeypatch)
        _mute_writeback(monkeypatch)

        async def fake_signals(host, *, ssh_port, ping_timeout, tcp_timeout):
            return _no_signals()

        monkeypatch.setattr("src.tasks.power.probe_reachability_signals", fake_signals)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result == {
            "power_state": "unknown",
            "source": "bmc",
            "ping_reachable": False,
            "ping_latency_ms": None,
            "ssh_reachable": False,
            "ssh_latency_ms": None,
            "ipmi_power_state": "unknown",
        }

    async def test_no_host_skips_network_probe(
        self, make_task, fetch_task, monkeypatch,
    ):
        # Нет host/ssh_host в payload → ping/ssh не пробуем, но ipmi меряем.
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1"},
        )
        _patch_bmc_state(monkeypatch, "Off")
        _mute_writeback(monkeypatch)

        called = {"probe": False}

        async def fake_signals(*a, **k):
            called["probe"] = True
            return _no_signals()

        monkeypatch.setattr("src.tasks.power.probe_reachability_signals", fake_signals)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert called["probe"] is False
        assert t.result == {
            "power_state": "off",
            "source": "bmc",
            "ping_reachable": False,
            "ping_latency_ms": None,
            "ssh_reachable": False,
            "ssh_latency_ms": None,
            "ipmi_power_state": "off",
        }

    async def test_reachability_disabled_skips_network_probe(
        self, make_task, fetch_task, monkeypatch,
    ):
        # Reachability выключен настройкой → сеть не пробуем, только ipmi.
        tid = await make_task(
            task_kind="power.status",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "host": "10.0.0.9"},
        )
        _patch_bmc_state(monkeypatch, "On")
        _mute_writeback(monkeypatch)

        from src.core.config import get_settings
        monkeypatch.setattr(
            get_settings(), "power_reachability_fallback_enabled", False,
        )

        called = {"probe": False}

        async def fake_signals(*a, **k):
            called["probe"] = True
            return _no_signals()

        monkeypatch.setattr("src.tasks.power.probe_reachability_signals", fake_signals)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert called["probe"] is False
        assert t.result["ipmi_power_state"] == "on"
        assert t.result["power_state"] == "on"
        assert t.result["source"] == "bmc"

    async def test_server_service_unreachable_fails_task(
        self, make_task, fetch_task, monkeypatch,
    ):
        """Транспортный сбой server_service при запросе IPMI-кред — реально
        временный, уходит в retry/FAILED, а не маскируется под unknown."""
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

        async def fake_signals(host, *, ssh_port, ping_timeout, tcp_timeout):
            return _no_signals()

        monkeypatch.setattr(
            "src.tasks.power.server_service_client.fetch_ipmi_credentials", fake_fetch,
        )
        monkeypatch.setattr("src.tasks.power.probe_reachability_signals", fake_signals)

        await power.power_status.original_func(tid)
        t = await fetch_task(tid)
        assert t.status != TaskStatus.SUCCEEDED


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


class _FakeProc:
    """Заглушка subprocess-процесса ping для юнит-тестов latency."""

    def __init__(self, returncode: int, stdout: bytes) -> None:
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self):
        return self._stdout, b""

    async def wait(self):
        return self.returncode

    def kill(self):
        return None


class TestReachabilityLatencyUnit:
    def test_parse_ping_latency_time_equals(self):
        line = b"64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=1.23 ms\n"
        assert _reachability._parse_ping_latency(line) == 1.23

    def test_parse_ping_latency_time_less_than(self):
        # Некоторые ping печатают округление `time<1 ms`.
        assert _reachability._parse_ping_latency(b"... time<1 ms\n") == 1.0

    def test_parse_ping_latency_absent(self):
        assert _reachability._parse_ping_latency(b"no timing here") is None

    async def test_ping_probe_reports_parsed_latency(self, monkeypatch):
        async def fake_exec(*args, **kwargs):
            return _FakeProc(
                0, b"64 bytes from h: icmp_seq=1 ttl=64 time=2.50 ms\n",
            )

        monkeypatch.setattr(
            _reachability.asyncio, "create_subprocess_exec", fake_exec,
        )
        reachable, latency = await _reachability._ping_probe("10.0.0.1", 2.0)
        assert reachable is True
        assert latency == 2.50

    async def test_ping_probe_wallclock_fallback_when_no_time(self, monkeypatch):
        async def fake_exec(*args, **kwargs):
            return _FakeProc(0, b"1 packets transmitted, 1 received\n")

        monkeypatch.setattr(
            _reachability.asyncio, "create_subprocess_exec", fake_exec,
        )
        reachable, latency = await _reachability._ping_probe("10.0.0.1", 2.0)
        assert reachable is True
        # RTT в выводе нет → latency берётся wall-clock'ом, но всё равно число.
        assert isinstance(latency, float)
        assert latency >= 0.0

    async def test_ping_probe_unreachable(self, monkeypatch):
        async def fake_exec(*args, **kwargs):
            return _FakeProc(1, b"")

        monkeypatch.setattr(
            _reachability.asyncio, "create_subprocess_exec", fake_exec,
        )
        assert await _reachability._ping_probe("10.0.0.1", 2.0) == (False, None)

    async def test_tcp_probe_measures_latency_on_real_listener(self):
        import asyncio

        server = await asyncio.start_server(
            lambda r, w: w.close(), host="127.0.0.1", port=0,
        )
        port = server.sockets[0].getsockname()[1]
        try:
            reachable, latency = await _reachability._tcp_probe(
                "127.0.0.1", port, 2.0,
            )
            assert reachable is True
            assert isinstance(latency, float)
            assert latency >= 0.0
        finally:
            server.close()
            await server.wait_closed()
        # Порт закрыт → недоступен, latency нет.
        assert await _reachability._tcp_probe("127.0.0.1", port, 0.5) == (False, None)

    async def test_probe_reachability_signals_runs_both_independently(
        self, monkeypatch,
    ):
        calls = {"ping": 0, "tcp": 0}

        async def fake_ping(host, timeout):
            calls["ping"] += 1
            return True, 1.11

        async def fake_tcp(host, port, timeout):
            calls["tcp"] += 1
            assert port == 2222
            return True, 2.22

        monkeypatch.setattr(_reachability, "_ping_probe", fake_ping)
        monkeypatch.setattr(_reachability, "_tcp_probe", fake_tcp)

        signals = await _reachability.probe_reachability_signals(
            "10.0.0.1", ssh_port=2222,
        )
        # Обе пробы выполнены, даже когда ping уже успешен (не first-wins).
        assert calls == {"ping": 1, "tcp": 1}
        assert signals == {
            "ping_reachable": True,
            "ping_latency_ms": 1.11,
            "ssh_reachable": True,
            "ssh_latency_ms": 2.22,
        }

    async def test_probe_reachability_signals_empty_host(self):
        assert await _reachability.probe_reachability_signals("  ") == {
            "ping_reachable": False,
            "ping_latency_ms": None,
            "ssh_reachable": False,
            "ssh_latency_ms": None,
        }
