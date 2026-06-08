"""Reveal-throttle для credentials.

5-минутное скользящее окно на пару `(actor_id, cred_id)`. Первый reveal в окне
эмитится `tokens.revealed` CRITICAL, последующие — `tokens.revealed_throttled`
INFO с накопленным счётчиком в details.

Бэкенды:

* Redis (preferable) — общий счётчик для всего fleet'а. Ключ
  `secret:reveal:{actor_id}:{cred_id}`, INCR + EXPIRE на окно.
* In-memory fallback — если Redis не сконфигурён или недоступен, держим
  словарь `{(actor, cred): (window_started_at, count)}` per-process. WARN
  в лог при первом fallback'е (один раз на процесс).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Protocol

import redis.asyncio as aioredis

from src.core.config import get_settings

logger = logging.getLogger(__name__)

# 5-минутное окно — фиксировано контрактом README §«Reveal + throttle».
_WINDOW_SECONDS = 300

_KEY_PREFIX = "secret:reveal"


class _RedisLike(Protocol):
    """Узкий протокол под мокинг в тестах."""

    async def incr(self, key: str) -> int: ...
    async def expire(self, key: str, seconds: int) -> bool: ...
    async def get(self, key: str) -> bytes | str | None: ...
    def pipeline(self): ...  # noqa: ANN201


# Module-level Redis-клиент. Поднимается в lifespan'е (lazy, при первом вызове).
_redis_client: _RedisLike | None = None
_redis_init_lock = threading.Lock()
_fallback_warned = False


# In-memory fallback storage. Ключ — (actor_id, cred_id), значение —
# (window_started_monotonic, count).
_inmem_state: dict[tuple[str, str], tuple[float, int]] = {}
_inmem_lock = threading.Lock()


def _get_redis_url() -> str | None:
    """Достаём Redis URL для throttle'а — переиспользуем rate_limit_storage_uri."""
    settings = get_settings()
    uri = (settings.rate_limit_storage_uri or "").strip()
    if uri.startswith("redis://") or uri.startswith("rediss://"):
        return uri
    return None


def _ensure_redis_client() -> _RedisLike | None:
    """Lazy-инициализация Redis-клиента. None если URI не задан или клиент сломан."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    url = _get_redis_url()
    if url is None:
        return None
    with _redis_init_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            _redis_client = aioredis.from_url(url, decode_responses=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reveal_throttle: failed to init redis client (%s); using in-memory fallback",
                exc,
            )
            return None
    return _redis_client


def _key(actor_id: str, cred_id: str) -> str:
    return f"{_KEY_PREFIX}:{actor_id}:{cred_id}"


def _record_inmem(actor_id: str, cred_id: str) -> tuple[bool, int]:
    """In-memory путь: сначала пробуем расширить окно, иначе стартуем новое."""
    now = time.monotonic()
    pair = (actor_id, cred_id)
    with _inmem_lock:
        entry = _inmem_state.get(pair)
        if entry is None or (now - entry[0]) >= _WINDOW_SECONDS:
            _inmem_state[pair] = (now, 1)
            # Best-effort sweep устаревших ключей при miss'е.
            stale_cutoff = now - _WINDOW_SECONDS
            stale = [k for k, (t, _c) in _inmem_state.items() if t < stale_cutoff and k != pair]
            for k in stale:
                _inmem_state.pop(k, None)
            return True, 1
        started_at, count = entry
        new_count = count + 1
        _inmem_state[pair] = (started_at, new_count)
        return False, new_count


def _warn_fallback_once(reason: str) -> None:
    global _fallback_warned
    if _fallback_warned:
        return
    _fallback_warned = True
    logger.warning("reveal_throttle: using in-memory fallback (%s)", reason)


async def record_reveal(actor_id: str, cred_id: str) -> tuple[bool, int]:
    """Зафиксировать reveal. Возвращает `(is_first_in_window, count_in_window)`.

    is_first_in_window=True → caller обязан эмитить `tokens.revealed` CRITICAL.
    False → `tokens.revealed_throttled` INFO с count'ом в details.
    """
    client = _ensure_redis_client()
    if client is None:
        _warn_fallback_once("redis URI not configured")
        return _record_inmem(actor_id, cred_id)

    key = _key(actor_id, cred_id)
    try:
        # INCR + EXPIRE одной транзакцией: при kill процесса между шагами ключ
        # не остаётся без TTL и не «протухает» бессмертным счётчиком, иначе
        # первый reveal следующего окна засчитается как throttled (INFO) и
        # CRITICAL `tokens.revealed` пропадёт.
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, _WINDOW_SECONDS)
        results = await pipe.execute()
        count = int(results[0])
        return count == 1, count
    except Exception as exc:  # noqa: BLE001 — best-effort
        _warn_fallback_once(f"redis error: {exc!r}")
        return _record_inmem(actor_id, cred_id)


def _reset_for_tests() -> None:
    """Снести in-memory state между прогонами."""
    global _redis_client, _fallback_warned
    with _inmem_lock:
        _inmem_state.clear()
    _redis_client = None
    _fallback_warned = False
