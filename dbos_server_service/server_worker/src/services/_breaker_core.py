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

Fail-open: любая Redis-ошибка возвращает `("closed", 0.0)` (для record-
функций — None / без эффекта) с WARNING-логом, симметрично прежней
реализации.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import redis.asyncio as aioredis

from src.services import _breaker_lua

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


async def eval_check(
    *,
    client_factory: ClientFactory,
    keys: tuple[str, str, str, str],
    cooldown_seconds: int,
    log_prefix: str,
    logger: logging.Logger,
    now: float | None = None,
) -> tuple[str, float]:
    """Вызвать CHECK_SCRIPT; вернуть `(state, retry_after_seconds)`.

    На ошибке Redis возвращает `("closed", 0.0)` и логирует WARNING с
    `log_prefix`. `now` нужен caller'у для frozen-clock тестов: модуль
    держит свой `time` и передаёт его сюда.
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
        except Exception:  # noqa: BLE001 — fail-open
            logger.warning(
                "%s: check failed, defaulting to closed",
                log_prefix, exc_info=True,
            )
            return ("closed", 0.0)
    finally:
        await client.aclose()
    state_raw, retry_after_raw = result[0], result[1]
    return (_decode_state(state_raw), float(retry_after_raw))


async def eval_get_state(
    *,
    client_factory: ClientFactory,
    keys: tuple[str, str, str, str],
    log_prefix: str,
    logger: logging.Logger,
    now: float | None = None,
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
        except Exception:  # noqa: BLE001 — fail-open
            logger.warning(
                "%s: get_state failed, defaulting to closed",
                log_prefix, exc_info=True,
            )
            return ("closed", 0.0)
    finally:
        await client.aclose()
    state_raw, retry_after_raw = result[0], result[1]
    return (_decode_state(state_raw), float(retry_after_raw))


async def eval_record_success(
    *,
    client_factory: ClientFactory,
    keys: tuple[str, str, str, str],
    log_prefix: str,
    logger: logging.Logger,
) -> None:
    """RECORD_SUCCESS_SCRIPT: сброс всех четырёх ключей. Best-effort."""
    client = await client_factory()
    try:
        try:
            await client.eval(_breaker_lua.RECORD_SUCCESS_SCRIPT, 4, *keys)
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("%s: record_success failed", log_prefix, exc_info=True)
    finally:
        await client.aclose()


async def eval_record_failure(
    *,
    client_factory: ClientFactory,
    keys: tuple[str, str, str, str],
    thresholds: Thresholds,
    log_prefix: str,
    logger: logging.Logger,
    now: float | None = None,
) -> tuple[str, int] | None:
    """RECORD_FAILURE_SCRIPT; вернуть `(state_after, current_count)`.

    Caller использует это, чтобы залогировать `OPENED` с собственным
    форматом (host для BMC, без host для audit). На ошибке Redis
    возвращает `None`.
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
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning(
                "%s: record_failure failed", log_prefix, exc_info=True,
            )
            return None
    finally:
        await client.aclose()
    state_raw, count_raw = result[0], result[1]
    return (_decode_state(state_raw), int(count_raw))


async def eval_reset(
    *,
    client_factory: ClientFactory,
    keys: tuple[str, str, str, str],
    log_prefix: str,
    logger: logging.Logger,
) -> None:
    """Сбросить все четыре ключа breaker'а. Используется тестами/оператором."""
    client = await client_factory()
    try:
        try:
            await client.delete(*keys)
        except Exception:  # noqa: BLE001
            logger.warning("%s: reset failed", log_prefix, exc_info=True)
    finally:
        await client.aclose()
