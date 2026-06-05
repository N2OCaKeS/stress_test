"""Общий Python-каркас для circuit breaker'ов worker'а.

`bmc_circuit_breaker` (per-host BMC) и `audit_publisher_breaker` (channel
worker → loging_service) делят одну Lua-логику (см. `_breaker_lua`) и
одну Python-обвязку: open Redis-клиента → eval Lua → разобрать
`(state, retry_after_seconds)` → закрыть клиент. Раньше эта обвязка
дублировалась полностью; отличались только префикс лог-сообщений, ключи
и host-измерение.

Здесь — runner-функции `eval_check`, `eval_get_state`,
`eval_record_success`, `eval_record_failure`, `eval_reset`. Они
принимают тройку `(failures, state, open_until, probe)`-ключей, пороги,
log-prefix и фабрику Redis-клиента. Фабрика передаётся явно, чтобы
тесты могли monkeypatch'ить `_get_client` в caller-модуле — текущий
паттерн тестов в `tests/unit/_breaker_test_helpers.py` остаётся
рабочим без изменений.

Параметр `close_after_use` (по умолчанию `True`) — для совместимости с
тестовым one-shot-клиентом: фабрика возвращает fresh FakeRedis на каждый
вызов, и мы его aclose'им после eval. Production-фабрика теперь
возвращает singleton из `redis_pool.get_redis()` — этот клиент закрывать
тут нельзя (его держит весь worker), и caller передаёт
`close_after_use=False`.

Fail-open: только транспортные классы Redis (`RedisError` / `OSError` /
`asyncio.TimeoutError`) логируются WARNING'ом и возвращают
`("closed", 0.0)` (для record-функций — None / без эффекта). Остальные
exception'ы (программные баги — `RuntimeError`, `KeyError`,
`AttributeError`) пробрасываются с ERROR-логом — fail-open маскировал бы
их под транспорт, и оператор видел бы «канал прошёл» вместо реальной
ошибки.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from src.services import _breaker_lua


# Транспорт-уровень: то, что breaker'у легитимно «fail-open'ить». Всё
# остальное — program bug, должно пройти наружу с ERROR-логом, чтобы не
# маскироваться под недоступность Redis.
#
# `EOFError` ловит `asyncio.IncompleteReadError` (его base-class) — это
# обрыв TCP-потока внутри asyncio.StreamReader, который redis-py может
# пробросить при разрыве соединения. По смыслу — тот же transport-fail,
# что и OSError/RedisError, не bug в нашем коде.
_TRANSPORT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    RedisError,
    OSError,
    asyncio.TimeoutError,
    EOFError,
)

# Тип фабрики: вызов без аргументов, возвращает корутину с готовым
# Redis-клиентом. Caller-модуль (`bmc_circuit_breaker` /
# `audit_publisher_breaker`) держит свой `_get_client()` функцию,
# которую тесты monkeypatch'ят на FakeRedis.
ClientFactory = Callable[[], Awaitable[aioredis.Redis]]


@dataclass(frozen=True)
class Thresholds:
    """Пороги breaker'а: failure_threshold / window_seconds / cooldown_seconds.

    Один и тот же контракт у BMC и audit-breaker'а; конкретные значения
    каждый модуль достаёт из своих Settings-полей.
    """

    failure_threshold: int
    window_seconds: int
    cooldown_seconds: int


def _decode_state(state_raw: object) -> str:
    """Lua возвращает bytes/str в зависимости от драйвера — нормализуем в str."""
    return state_raw.decode() if isinstance(state_raw, bytes) else str(state_raw)


async def _maybe_close(client: aioredis.Redis, close_after_use: bool) -> None:
    """Закрыть клиент, если caller владеет им (one-shot путь).

    Singleton из `redis_pool.get_redis()` закрывать тут нельзя — его
    делят все breaker'ы и stash-функции, close сделает один общий
    shutdown-хук. Тестовая фабрика отдаёт fresh FakeRedis на каждый
    eval, и его close требуется здесь.
    """
    if not close_after_use:
        return
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001 — close best-effort
        pass


async def eval_check(
    *,
    client_factory: ClientFactory,
    keys: tuple[str, str, str, str],
    cooldown_seconds: int,
    log_prefix: str,
    logger: logging.Logger,
    now: float | None = None,
    close_after_use: bool = True,
) -> tuple[str, float]:
    """Вызвать CHECK_SCRIPT; вернуть `(state, retry_after_seconds)`.

    На транспортной ошибке Redis возвращает `("closed", 0.0)` и
    логирует WARNING с `log_prefix`. Program-bug exception'ы
    (RuntimeError/KeyError/...) пробрасываются с ERROR-логом. `now`
    нужен caller'у для frozen-clock тестов: модуль держит свой `time`
    и передаёт его сюда.
    """
    if now is None:
        now = time.time()
    client = await client_factory()
    try:
        try:
            result = await client.eval(
                _breaker_lua.CHECK_SCRIPT, 4, *keys,
                str(int(now)), str(cooldown_seconds),
            )
        except _TRANSPORT_EXCEPTIONS:
            logger.warning(
                "%s: check failed (transport), defaulting to closed",
                log_prefix, exc_info=True,
            )
            return ("closed", 0.0)
        except Exception:
            logger.error(
                "%s: check raised non-transport exception — propagating",
                log_prefix, exc_info=True,
            )
            raise
    finally:
        await _maybe_close(client, close_after_use)
    state_raw, retry_after_raw = result[0], result[1]
    return (_decode_state(state_raw), float(retry_after_raw))


async def eval_get_state(
    *,
    client_factory: ClientFactory,
    keys: tuple[str, str, str, str],
    log_prefix: str,
    logger: logging.Logger,
    now: float | None = None,
    close_after_use: bool = True,
) -> tuple[str, float]:
    """Pure-read snapshot: `(state, retry_after_seconds)`. Без побочных эффектов."""
    if now is None:
        now = time.time()
    client = await client_factory()
    try:
        try:
            result = await client.eval(
                _breaker_lua.GET_STATE_SCRIPT, 4, *keys,
                str(int(now)),
            )
        except _TRANSPORT_EXCEPTIONS:
            logger.warning(
                "%s: get_state failed (transport), defaulting to closed",
                log_prefix, exc_info=True,
            )
            return ("closed", 0.0)
        except Exception:
            logger.error(
                "%s: get_state raised non-transport exception — propagating",
                log_prefix, exc_info=True,
            )
            raise
    finally:
        await _maybe_close(client, close_after_use)
    state_raw, retry_after_raw = result[0], result[1]
    return (_decode_state(state_raw), float(retry_after_raw))


async def eval_record_success(
    *,
    client_factory: ClientFactory,
    keys: tuple[str, str, str, str],
    log_prefix: str,
    logger: logging.Logger,
    close_after_use: bool = True,
) -> None:
    """RECORD_SUCCESS_SCRIPT: сброс всех четырёх ключей. Best-effort."""
    client = await client_factory()
    try:
        try:
            await client.eval(_breaker_lua.RECORD_SUCCESS_SCRIPT, 4, *keys)
        except _TRANSPORT_EXCEPTIONS:
            logger.warning("%s: record_success failed (transport)", log_prefix, exc_info=True)
        except Exception:
            logger.error(
                "%s: record_success raised non-transport exception — propagating",
                log_prefix, exc_info=True,
            )
            raise
    finally:
        await _maybe_close(client, close_after_use)


async def eval_record_failure(
    *,
    client_factory: ClientFactory,
    keys: tuple[str, str, str, str],
    thresholds: Thresholds,
    log_prefix: str,
    logger: logging.Logger,
    now: float | None = None,
    close_after_use: bool = True,
) -> tuple[str, int] | None:
    """RECORD_FAILURE_SCRIPT; вернуть `(state_after, current_count)`.

    Caller использует это, чтобы залогировать `OPENED` с собственным
    форматом (host для BMC, без host для audit). На транспортной ошибке
    Redis возвращает `None`; program-bug exception'ы пробрасываются.
    """
    if now is None:
        now = time.time()
    client = await client_factory()
    try:
        try:
            result = await client.eval(
                _breaker_lua.RECORD_FAILURE_SCRIPT, 4, *keys,
                str(int(now)),
                str(thresholds.failure_threshold),
                str(thresholds.window_seconds),
                str(thresholds.cooldown_seconds),
            )
        except _TRANSPORT_EXCEPTIONS:
            logger.warning(
                "%s: record_failure failed (transport)", log_prefix, exc_info=True,
            )
            return None
        except Exception:
            logger.error(
                "%s: record_failure raised non-transport exception — propagating",
                log_prefix, exc_info=True,
            )
            raise
    finally:
        await _maybe_close(client, close_after_use)
    state_raw, count_raw = result[0], result[1]
    return (_decode_state(state_raw), int(count_raw))


async def eval_reset(
    *,
    client_factory: ClientFactory,
    keys: tuple[str, str, str, str],
    log_prefix: str,
    logger: logging.Logger,
    close_after_use: bool = True,
) -> None:
    """Сбросить все четыре ключа breaker'а. Используется тестами/оператором."""
    client = await client_factory()
    try:
        try:
            await client.delete(*keys)
        except _TRANSPORT_EXCEPTIONS:
            logger.warning("%s: reset failed (transport)", log_prefix, exc_info=True)
        except Exception:
            logger.error(
                "%s: reset raised non-transport exception — propagating",
                log_prefix, exc_info=True,
            )
            raise
    finally:
        await _maybe_close(client, close_after_use)
