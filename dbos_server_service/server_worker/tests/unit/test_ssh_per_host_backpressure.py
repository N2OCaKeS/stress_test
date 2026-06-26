"""Per-host SSH backpressure: семафор на (host, port).

Burst задач на один сервер без лимита открыл бы N параллельных коннектов,
упёрся в sshd MaxStartups и амплифицировал ретраи. `SshClient.connect`
сериализует коннекты сверх лимита к одному (host, port); к разным хостам
сессии остаются параллельны.

Тесты НЕ поднимают реальный SSH — `asyncssh.connect` подменяется
AsyncMock'ом, который блокируется на событии, чтобы можно было посчитать,
сколько коннектов реально «в полёте» одновременно.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from src.clients import ssh as ssh_module
from src.clients.ssh import SshClient


@pytest.fixture(autouse=True)
def _clean_host_registry():
    """Сбрасываем module-level реестр семафоров до и после каждого теста —
    он живёт per-process, иначе слоты протекли бы между тестами."""
    ssh_module._HOST_SEMAPHORES.clear()
    ssh_module._HOST_SEMAPHORE_LIMITS.clear()
    yield
    ssh_module._HOST_SEMAPHORES.clear()
    ssh_module._HOST_SEMAPHORE_LIMITS.clear()


class _GatedConnect:
    """Подмена `asyncssh.connect`, которая блокируется до `release()`.

    Считает, сколько коннектов одновременно «застряли» внутри connect, и
    пишет наблюдаемый максимум — это и есть фактический параллелизм, который
    семафор обязан удерживать в рамках лимита.
    """

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = asyncio.Lock()

    async def __call__(self, *args, **kwargs):
        async with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await self.gate.wait()
        finally:
            async with self._lock:
                self.in_flight -= 1
        conn = MagicMock(spec=asyncssh.SSHClientConnection)
        conn.close = MagicMock()
        conn.wait_closed = AsyncMock()
        return conn

    def release(self) -> None:
        self.gate.set()


async def _open_and_close(host: str, *, limit: int, port: int = 22) -> None:
    async with SshClient(
        host=host, username="u", password="p",
        port=port, max_sessions_per_host=limit,
    ):
        # Внутри контекста коннект открыт; ничего не делаем, выход закроет
        # сессию и отпустит per-host слот.
        pass


class TestSameHostSerializes:
    async def test_concurrent_connects_to_one_host_capped_at_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        limit = 2
        gated = _GatedConnect()
        monkeypatch.setattr(asyncssh, "connect", gated)

        # 5 параллельных коннектов к ОДНОМУ хосту при лимите 2.
        tasks = [
            asyncio.create_task(_open_and_close("10.0.0.1", limit=limit))
            for _ in range(5)
        ]
        # Дать корутинам добежать до connect и упереться в gate.
        for _ in range(20):
            await asyncio.sleep(0)
            if gated.in_flight >= limit:
                break

        # Не больше лимита одновременно «в полёте», несмотря на 5 запросов.
        assert gated.in_flight <= limit
        assert gated.max_in_flight <= limit

        gated.release()
        await asyncio.gather(*tasks)
        # Все коннекты в итоге прошли — никто не потерялся.
        assert gated.max_in_flight <= limit

    async def test_slot_released_after_close_lets_next_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Лимит 1: коннекты строго последовательны. После close() слот
        # освобождается и следующий проходит.
        order: list[str] = []

        async def fake_connect(*args, **kwargs):
            order.append("connected")
            conn = MagicMock(spec=asyncssh.SSHClientConnection)
            conn.close = MagicMock()
            conn.wait_closed = AsyncMock()
            return conn

        monkeypatch.setattr(asyncssh, "connect", fake_connect)

        await _open_and_close("10.0.0.9", limit=1)
        await _open_and_close("10.0.0.9", limit=1)
        # Оба прошли — слот переиспользован после первого close.
        assert order == ["connected", "connected"]


class TestDifferentHostsParallel:
    async def test_connects_to_distinct_hosts_run_in_parallel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        limit = 1
        gated = _GatedConnect()
        monkeypatch.setattr(asyncssh, "connect", gated)

        # По одному коннекту к трём РАЗНЫМ хостам при per-host лимите 1.
        # Семафоры независимы → все три должны оказаться в полёте сразу.
        hosts = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
        tasks = [
            asyncio.create_task(_open_and_close(h, limit=limit)) for h in hosts
        ]
        for _ in range(20):
            await asyncio.sleep(0)
            if gated.in_flight >= len(hosts):
                break

        assert gated.max_in_flight == len(hosts)

        gated.release()
        await asyncio.gather(*tasks)

    async def test_same_host_different_port_is_separate_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # (host, port) — ключ семафора. Один host на двух портах = два слота.
        limit = 1
        gated = _GatedConnect()
        monkeypatch.setattr(asyncssh, "connect", gated)

        tasks = [
            asyncio.create_task(_open_and_close("10.0.0.1", limit=limit, port=22)),
            asyncio.create_task(_open_and_close("10.0.0.1", limit=limit, port=2222)),
        ]
        for _ in range(20):
            await asyncio.sleep(0)
            if gated.in_flight >= 2:
                break

        assert gated.max_in_flight == 2
        gated.release()
        await asyncio.gather(*tasks)


class TestSlotReleasedOnConnectFailure:
    async def test_failed_connect_does_not_leak_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Коннект, который падает, обязан отпустить слот — иначе хост
        # забэкпрешерится навсегда.
        calls = {"n": 0}

        async def flaky_connect(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise asyncssh.PermissionDenied(reason="bad creds")
            conn = MagicMock(spec=asyncssh.SSHClientConnection)
            conn.close = MagicMock()
            conn.wait_closed = AsyncMock()
            return conn

        monkeypatch.setattr(asyncssh, "connect", flaky_connect)

        from src.clients.ssh import SshError

        # Первая попытка падает на auth — слот должен освободиться.
        with pytest.raises(SshError):
            await _open_and_close("10.0.0.7", limit=1)

        # Вторая попытка проходит: если бы слот протёк, при лимите 1 она
        # зависла бы навсегда. Оборачиваем в timeout как страховку.
        await asyncio.wait_for(_open_and_close("10.0.0.7", limit=1), timeout=2.0)
        assert calls["n"] == 2
