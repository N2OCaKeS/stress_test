"""reveal_throttle: 5-минутное окно per (actor, cred).

Покрытие in-memory ветки: первый reveal — is_first=True, count=1; повторный
в окне — False, count=2; после истечения окна — снова True, count=1.
Redis-путь покрыт логикой `_ensure_redis_client` returning None при отсутствии URI.
"""

from __future__ import annotations

import pytest

from src.services import reveal_throttle


@pytest.fixture(autouse=True)
def _reset_throttle():
    reveal_throttle._reset_for_tests()
    yield
    reveal_throttle._reset_for_tests()


@pytest.mark.asyncio
async def test_first_reveal_marked_as_first():
    is_first, count = await reveal_throttle.record_reveal("usr_a", "cred_a")
    assert is_first is True
    assert count == 1


@pytest.mark.asyncio
async def test_second_reveal_within_window_throttled():
    await reveal_throttle.record_reveal("usr_a", "cred_a")
    is_first, count = await reveal_throttle.record_reveal("usr_a", "cred_a")
    assert is_first is False
    assert count == 2


@pytest.mark.asyncio
async def test_third_reveal_increments_count():
    await reveal_throttle.record_reveal("usr_a", "cred_a")
    await reveal_throttle.record_reveal("usr_a", "cred_a")
    is_first, count = await reveal_throttle.record_reveal("usr_a", "cred_a")
    assert is_first is False
    assert count == 3


@pytest.mark.asyncio
async def test_different_actor_same_cred_independent():
    await reveal_throttle.record_reveal("usr_a", "cred_a")
    is_first, count = await reveal_throttle.record_reveal("usr_b", "cred_a")
    assert is_first is True
    assert count == 1


@pytest.mark.asyncio
async def test_same_actor_different_cred_independent():
    await reveal_throttle.record_reveal("usr_a", "cred_a")
    is_first, count = await reveal_throttle.record_reveal("usr_a", "cred_b")
    assert is_first is True
    assert count == 1


@pytest.mark.asyncio
async def test_window_reset_after_expiry(monkeypatch):
    """Меняем _WINDOW_SECONDS на 0 → каждое окно новое, всегда is_first."""
    monkeypatch.setattr(reveal_throttle, "_WINDOW_SECONDS", 0)
    is_first1, _ = await reveal_throttle.record_reveal("usr_a", "cred_a")
    is_first2, _ = await reveal_throttle.record_reveal("usr_a", "cred_a")
    assert is_first1 is True
    assert is_first2 is True


@pytest.mark.asyncio
async def test_redis_path_falls_back_on_error(monkeypatch):
    """Если Redis-клиент бросает — падаем на in-memory."""
    class _BrokenRedis:
        async def incr(self, key):
            raise RuntimeError("redis down")

        async def expire(self, key, seconds):
            return True

        async def get(self, key):
            return None

    monkeypatch.setattr(reveal_throttle, "_redis_client", _BrokenRedis())
    is_first, count = await reveal_throttle.record_reveal("usr_a", "cred_a")
    assert is_first is True
    assert count == 1


@pytest.mark.asyncio
async def test_no_redis_uri_uses_inmem():
    """rate_limit_storage_uri='memory://' → _ensure_redis_client возвращает None."""
    client = reveal_throttle._ensure_redis_client()
    assert client is None


@pytest.mark.asyncio
async def test_redis_path_first_returns_is_first():
    """Mock Redis-клиент: incr возвращает 1 → is_first=True."""

    class _OkRedis:
        def __init__(self):
            self.counters: dict = {}

        async def incr(self, key):
            self.counters[key] = self.counters.get(key, 0) + 1
            return self.counters[key]

        async def expire(self, key, seconds):
            return True

        async def get(self, key):
            return self.counters.get(key)

    fake = _OkRedis()
    reveal_throttle._redis_client = fake
    try:
        is_first1, count1 = await reveal_throttle.record_reveal("usr_a", "cred_a")
        is_first2, count2 = await reveal_throttle.record_reveal("usr_a", "cred_a")
        assert (is_first1, count1) == (True, 1)
        assert (is_first2, count2) == (False, 2)
    finally:
        reveal_throttle._redis_client = None
