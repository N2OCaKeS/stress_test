"""Shared circuit breaker для канала worker → loging_service /events.

Аналог `bmc_circuit_breaker`, но для outbox-publisher'а. Per-process
breaker в `audit_outbox_publisher` остаётся (он считает iteration'ы и
держит exponential back-off на длину sleep'а в loop'е). Этот модуль —
shared-state guard перед каждым отдельным HTTP POST: при N реплик'ах
каждая до этого молотила по 5 fail'ов прежде чем замкнуть свой
in-memory breaker → ~15 лишних запросов в умирающий loging_service.
С shared-state три реплики разделят один и тот же счётчик и закроют
канал после первых 5 fail'ов суммарно.

Хранение, переходы и атомарность повторяют BMC-вариант:

  * `cb:audit_publisher:failures` — счётчик failure'ов в rolling
    window, TTL = `window_seconds` ставится только на первом INCR.
  * `cb:audit_publisher:state` — `"open"` | `"half_open"`. closed не
    пишем (отсутствие ключа = closed).
  * `cb:audit_publisher:open_until` — unix-ts, до которого breaker
    отбивает запросы.

Lua-скрипты — три атомарных перехода (check / record_success /
record_failure). От BMC-варианта отличаются только префиксом ключей:
канал один (publisher → loging_service), поэтому host-измерения нет,
все три ключа фиксированы.

Fail-open на ошибках Redis: если Redis недоступен, мы не блокируем
HTTP-вызов в loging_service — это менее опасно, чем потерять канал
аудита из-за инцидента в кэше. Симметрично BMC-варианту.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import redis.asyncio as aioredis

from src.core.config import get_settings
from src.core.exceptions import AppException

logger = logging.getLogger(__name__)

# Префикс ключей — отделён и от bmc, и от prepare-creds, чтобы можно было
# `SCAN MATCH cb:audit_publisher:*` глянуть состояние канала отдельно.
_KEY_PREFIX = "cb:audit_publisher"

# Default-параметры breaker'а. Те же 5/60/30, что у BMC: на этих числах
# уже выверена не-flaky реакция на rolling restart loging_service.
DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_WINDOW_SECONDS = 60
DEFAULT_COOLDOWN_SECONDS = 30


@dataclass(frozen=True)
class _Thresholds:
    """Пороги breaker'а для одного вызова — отделены от Settings для тестов."""

    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS


def _thresholds_from_settings() -> _Thresholds:
    """Достаём пороги из Settings; legacy/тесты падают на default'ы."""
    s = get_settings()
    return _Thresholds(
        failure_threshold=getattr(
            s, "audit_publisher_breaker_failure_threshold",
            DEFAULT_FAILURE_THRESHOLD,
        ),
        window_seconds=getattr(
            s, "audit_publisher_breaker_window_seconds",
            DEFAULT_WINDOW_SECONDS,
        ),
        cooldown_seconds=getattr(
            s, "audit_publisher_breaker_cooldown_seconds",
            DEFAULT_COOLDOWN_SECONDS,
        ),
    )


class CircuitBreakerOpenError(AppException):
    """Audit-publisher breaker открыт — POST в loging_service не делаем.

    Семантически отличается от `bmc_circuit_breaker.CircuitBreakerOpenError`
    кодом и сообщением, но наследует тот же `AppException`. Publisher
    ловит её отдельно от `AuditEmitError` и трактует как transient HTTP-fail
    (row остаётся unpublished, attempts инкрементится).
    """

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__(
            error_code="AUDIT_PUBLISHER_CIRCUIT_OPEN",
            message=(
                "Audit publisher circuit breaker is open; "
                f"retry after ~{int(retry_after_seconds)}s"
            ),
            details={
                "retry_after_seconds": int(retry_after_seconds),
            },
        )


# ── Lua-скрипты ─────────────────────────────────────────────────────────────
# Полностью повторяют BMC-вариант. Не переиспользуем через import — каждая
# breaker-реализация держит свои keys/argv-контракт, общий код был бы
# натянут (см. obsidian/reports/ если когда-то решим выделить shared lib).

_CHECK_SCRIPT = """
local now = tonumber(ARGV[1])
local cooldown = tonumber(ARGV[2])
local state = redis.call('GET', KEYS[2])
local open_until = tonumber(redis.call('GET', KEYS[3]) or '0')
if state == 'open' then
  if open_until > now then
    return {state, open_until - now}
  end
  redis.call('SET', KEYS[2], 'half_open', 'EX', cooldown)
  redis.call('DEL', KEYS[3])
  return {'half_open', 0}
end
if state then
  return {state, 0}
end
return {'closed', 0}
"""

_RECORD_SUCCESS_SCRIPT = """
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[3])
return 1
"""

_RECORD_FAILURE_SCRIPT = """
local now = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local window = tonumber(ARGV[3])
local cooldown = tonumber(ARGV[4])
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], window)
end
if count >= threshold then
  redis.call('SET', KEYS[2], 'open', 'EX', cooldown)
  redis.call('SET', KEYS[3], tostring(now + cooldown), 'EX', cooldown)
  redis.call('DEL', KEYS[1])
  return {'open', count}
end
return {'closed', count}
"""


def _keys() -> tuple[str, str, str]:
    """Тройка ключей: failures, state, open_until. Канал один, host-измерения нет."""
    return (
        f"{_KEY_PREFIX}:failures",
        f"{_KEY_PREFIX}:state",
        f"{_KEY_PREFIX}:open_until",
    )


async def _get_client() -> aioredis.Redis:
    """One-shot Redis-клиент. Не кэшируем — см. комментарий в bmc_circuit_breaker."""
    settings = get_settings()
    return aioredis.from_url(settings.redis_url)


async def check() -> None:
    """Проверить breaker; raise CircuitBreakerOpenError если open.

    Вызывать перед каждым POST в loging_service. Безопасна на ошибках
    Redis (fail-open: пропускаем HTTP, breaker «как будто closed»).
    """
    thresholds = _thresholds_from_settings()
    client = await _get_client()
    try:
        try:
            keys = _keys()
            result = await client.eval(
                _CHECK_SCRIPT, 3, *keys,
                str(int(time.time())), str(thresholds.cooldown_seconds),
            )
        except Exception:  # noqa: BLE001 — fail-open
            logger.warning(
                "audit_publisher_breaker: check failed, defaulting to closed",
                exc_info=True,
            )
            return
    finally:
        await client.aclose()

    state_raw, retry_after_raw = result[0], result[1]
    state = state_raw.decode() if isinstance(state_raw, bytes) else state_raw
    retry_after = float(retry_after_raw)
    if state == "open":
        logger.warning(
            "audit_publisher_breaker: rejecting POST; circuit open for ~%ss",
            int(retry_after),
        )
        raise CircuitBreakerOpenError(retry_after)


async def record_success() -> None:
    """Зафиксировать успешный POST → reset state + failures.

    Безопасна при ошибках Redis. Дёргается из `_publish_one` после 2xx.
    """
    client = await _get_client()
    try:
        try:
            keys = _keys()
            await client.eval(_RECORD_SUCCESS_SCRIPT, 3, *keys)
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning(
                "audit_publisher_breaker: record_success failed",
                exc_info=True,
            )
    finally:
        await client.aclose()


async def record_failure() -> None:
    """Зафиксировать неуспех POST → INCR failures, при необходимости open.

    Безопасна при ошибках Redis. Дёргается из `_publish_one` в except
    после AuditEmitError (5xx/transport — 4xx это permanent-fail row'и,
    канал не виноват, breaker такие не считает).
    """
    thresholds = _thresholds_from_settings()
    client = await _get_client()
    try:
        try:
            keys = _keys()
            result = await client.eval(
                _RECORD_FAILURE_SCRIPT, 3, *keys,
                str(int(time.time())),
                str(thresholds.failure_threshold),
                str(thresholds.window_seconds),
                str(thresholds.cooldown_seconds),
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning(
                "audit_publisher_breaker: record_failure failed",
                exc_info=True,
            )
            return
    finally:
        await client.aclose()

    state_raw, count_raw = result[0], result[1]
    state = state_raw.decode() if isinstance(state_raw, bytes) else state_raw
    if state == "open":
        logger.error(
            "audit_publisher_breaker: OPENED after %s failures in %ss; cooldown=%ss",
            int(count_raw),
            thresholds.window_seconds,
            thresholds.cooldown_seconds,
        )


async def reset() -> None:
    """Operator/test helper: очистить state breaker'а досрочно."""
    client = await _get_client()
    try:
        try:
            await client.delete(*_keys())
        except Exception:  # noqa: BLE001
            logger.warning(
                "audit_publisher_breaker: reset failed", exc_info=True,
            )
    finally:
        await client.aclose()
