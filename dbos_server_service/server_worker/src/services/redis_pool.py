"""Долгоживущий aioredis-клиент для всех stash/breaker-операций worker'а.

Симметрично `http_pool.py`: один общий пул соединений вместо `from_url`
на каждый вызов. До этого 16 stash-функций в `tasks/{prepare,users,passwords}.py`
и 2 breaker-фабрики (`bmc_circuit_breaker._get_client`,
`audit_publisher_breaker._get_client`) каждый раз дёргали
`aioredis.from_url(...)`, делая свежий TCP/AUTH/SELECT-handshake. На
rotation-burst (десятки серверов × несколько stash-touch'ей + breaker
check/record) это сотни лишних коннектов в Redis за минуту.

Контракт:

  * `get_redis()` — лениво поднимает singleton `aioredis.Redis` поверх
    общего connection-pool'а с проставленными `socket_timeout` /
    `socket_connect_timeout`. Без них зависший Redis блокирует
    `await client.get(...)` навечно, и worker не реагирует на SIGTERM
    до taskiq hard-timeout'а.
  * `aclose()` — закрыть singleton на graceful shutdown. Идемпотентно;
    после закрытия следующий `get_redis()` снова поднимет новый пул
    (нужно для test-цикла «startup → shutdown → startup» и для
    рестарта broker'а внутри одного процесса).
  * `reset_for_tests()` — синхронный сброс кеш-слота без `aclose`.
    Тесты, которые monkeypatch'ят `aioredis.from_url`, должны звать
    его в фикстуре, иначе закешированный реальный клиент пережил бы
    patch и продолжил ходить в сеть.

Один Redis-клиент через connection pool безопасен в multi-task сценарии
(redis-py async поддерживает concurrent `get`/`set` через pool); все
stash-операции — короткие single-command вызовы, длинных blocking
операций (BLPOP / Pub-Sub subscribe) у worker'а нет.

Breaker-фабрики (`_get_client`) превращаются из «открыть-закрыть на
каждый eval» в `return get_redis()` — выигрываются handshake'и, а
shutdown-close достаётся одной точкой здесь.
"""

from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as aioredis

from src.core.config import get_settings
from src.utils.redaction import redact_error_message

logger = logging.getLogger(__name__)

# Тайминги под Redis-операции. Connect быстрее обычного, чтобы при
# network-partition мы не упирались в полный socket_timeout, прежде чем
# понять, что Redis недоступен. socket_timeout покрывает время одной
# операции (GET/SET/EVAL); breaker-Lua скрипты возвращаются за миллисекунды,
# stash GET/SET — тоже, 5s — щедрый потолок.
_SOCKET_TIMEOUT_SECONDS = 5.0
_SOCKET_CONNECT_TIMEOUT_SECONDS = 3.0

_client: Optional[aioredis.Redis] = None


def get_redis() -> aioredis.Redis:
    """Вернуть singleton aioredis.Redis. Лениво создаётся на первом вызове.

    Возвращаемый клиент держит внутренний `ConnectionPool` и безопасен
    к concurrent-use из разных tasks: redis-py async берёт коннект из
    пула на каждый command. Закрывать в caller'ах НЕ нужно — `aclose()`
    модуля делает это один раз на shutdown.
    """
    global _client
    if _client is None:
        settings = get_settings()
        _client = aioredis.from_url(
            settings.redis_url,
            socket_timeout=_SOCKET_TIMEOUT_SECONDS,
            socket_connect_timeout=_SOCKET_CONNECT_TIMEOUT_SECONDS,
        )
    return _client


async def aclose() -> None:
    """Закрыть singleton на graceful shutdown.

    Идемпотентно; после вызова `_client` снова `None`, и следующий
    `get_redis()` поднимет новый pool (нужно для тестов и рестарта
    broker'а внутри одного процесса).
    """
    global _client
    if _client is None:
        return
    try:
        await _client.aclose()
    except Exception as exc:  # noqa: BLE001 — shutdown best-effort
        logger.warning(
            "redis_pool: failed to close singleton: %s",
            redact_error_message(f"{type(exc).__name__}: {exc}"),
        )
    _client = None


def reset_for_tests() -> None:
    """Сброс закешированного клиента без `aclose` для тестов.

    Тесты, monkeypatch'ящие `aioredis.from_url`, должны звать это из
    фикстуры — иначе уже закешированный реальный клиент пережил бы
    patch и продолжил ходить в сеть.
    """
    global _client
    _client = None
