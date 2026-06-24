"""Реальный обгон очереди high-priority через `PriorityListQueueBroker`.

Механизм: две Redis-очереди (normal + high). `kick` маршрутизирует сообщение
по label'у `queue_name`; `listen` делает `BRPOP [high, normal]` — Redis отдаёт
элемент из первого непустого ключа, поэтому high реально обгоняет normal.

Тесты гоняют брокер поверх фейкового Redis-соединения (in-memory списки), не
трогая настоящий Redis: проверяем именно маршрутизацию и порядок drain'а.
"""

from __future__ import annotations

import pytest
from taskiq import BrokerMessage

from src.core import priority_broker
from src.core.priority_broker import PriorityListQueueBroker

NORMAL = "taskiq"
HIGH = "taskiq_high"


class _FakeRedisConn:
    """In-memory заглушка под redis.asyncio.Redis для kick/listen.

    Два списка (по имени ключа). `lpush` кладёт слева (как настоящий брокер),
    `brpop` отдаёт справа из первого непустого ключа в порядке аргументов —
    ровно семантика Redis BRPOP, на которой держится обгон.
    """

    def __init__(self, store: dict[str, list[bytes]]):
        self._store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def lpush(self, key: str, value: bytes) -> int:
        self._store.setdefault(key, []).insert(0, value)
        return len(self._store[key])

    async def brpop(self, keys):
        for key in keys:
            lst = self._store.get(key)
            if lst:
                return (key.encode(), lst.pop())
        return None


@pytest.fixture
def fake_store(monkeypatch):
    """Подменяет `priority_broker.Redis` фейковым соединением над общим store."""
    store: dict[str, list[bytes]] = {}

    def _factory(*args, **kwargs):
        return _FakeRedisConn(store)

    monkeypatch.setattr(priority_broker, "Redis", _factory)
    return store


def _make_broker() -> PriorityListQueueBroker:
    b = PriorityListQueueBroker(
        url="redis://localhost:6379/0",
        queue_name=NORMAL,
        high_queue_name=HIGH,
    )
    return b


def _msg(task_id: str, *, high: bool) -> BrokerMessage:
    labels = {"queue_name": HIGH} if high else {}
    return BrokerMessage(
        task_id=task_id,
        task_name="management_user_sync",
        message=task_id.encode(),
        labels=labels,
    )


class TestKickRouting:
    async def test_high_label_goes_to_high_queue(self, fake_store):
        broker = _make_broker()
        await broker.kick(_msg("hi-1", high=True))
        assert fake_store.get(HIGH) == [b"hi-1"]
        assert fake_store.get(NORMAL) in (None, [])

    async def test_no_label_goes_to_normal_queue(self, fake_store):
        broker = _make_broker()
        await broker.kick(_msg("n-1", high=False))
        assert fake_store.get(NORMAL) == [b"n-1"]
        assert fake_store.get(HIGH) in (None, [])


class TestListenOvertake:
    async def test_high_drained_before_normal(self, fake_store):
        broker = _make_broker()
        # Normal встал в очередь ПЕРВЫМ, high — после. Несмотря на это, drain
        # должен отдать high раньше normal'а.
        await broker.kick(_msg("normal-old", high=False))
        await broker.kick(_msg("high-new", high=True))

        drained: list[bytes] = []
        gen = broker.listen()
        drained.append(await gen.__anext__())
        drained.append(await gen.__anext__())
        await gen.aclose()

        assert drained == [b"high-new", b"normal-old"], drained

    async def test_normal_still_delivered_when_high_empty(self, fake_store):
        broker = _make_broker()
        await broker.kick(_msg("normal-only", high=False))

        gen = broker.listen()
        first = await gen.__anext__()
        await gen.aclose()
        assert first == b"normal-only"

    async def test_multiple_high_then_normal_order(self, fake_store):
        broker = _make_broker()
        await broker.kick(_msg("n1", high=False))
        await broker.kick(_msg("h1", high=True))
        await broker.kick(_msg("h2", high=True))

        drained: list[bytes] = []
        gen = broker.listen()
        for _ in range(3):
            drained.append(await gen.__anext__())
        await gen.aclose()

        # Оба high дренируются раньше normal'а. Между собой high — FIFO
        # (h1 положен раньше h2, brpop отдаёт с правого конца).
        assert drained[:2] == [b"h1", b"h2"], drained
        assert drained[2] == b"n1"
