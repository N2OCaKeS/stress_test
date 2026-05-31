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
  * `cb:audit_publisher:probe` — in-flight half_open marker. SETNX
    в `CHECK_SCRIPT` пропускает в half_open ровно одну реплику;
    остальные продолжают видеть open до результата пробного запроса.

Lua-скрипты — три атомарных перехода (check / record_success /
record_failure). От BMC-варианта отличаются только префиксом ключей:
канал один (publisher → loging_service), поэтому host-измерения нет,
все четыре ключа фиксированы.

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
from src.services import _breaker_lua

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
# Общие с bmc_circuit_breaker, лежат в `_breaker_lua`. Алиасы оставлены —
# тестовый FakeRedis матчит скрипты по объекту-строке.
_CHECK_SCRIPT = _breaker_lua.CHECK_SCRIPT
_RECORD_SUCCESS_SCRIPT = _breaker_lua.RECORD_SUCCESS_SCRIPT
_RECORD_FAILURE_SCRIPT = _breaker_lua.RECORD_FAILURE_SCRIPT


def _keys() -> tuple[str, str, str]:
    """Тройка ключей: failures, state, open_until. Канал один, host-измерения нет.

    Probe-ключ (in-flight half_open marker) — отдельный, см. ``_probe_key``.
    """
    return (
        f"{_KEY_PREFIX}:failures",
        f"{_KEY_PREFIX}:state",
        f"{_KEY_PREFIX}:open_until",
    )


def _probe_key() -> str:
    """In-flight half_open marker; SETNX-захват в ``CHECK_SCRIPT``."""
    return f"{_KEY_PREFIX}:probe"


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
            keys = (*_keys(), _probe_key())
            result = await client.eval(
                _CHECK_SCRIPT, 4, *keys,
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


async def get_state() -> tuple[str, float]:
    """Read-only snapshot breaker'а: `(state, retry_after_seconds)`.

    Имя misleading — метод НЕ pure-read: тот же `_CHECK_SCRIPT`
    атомарно транзитит `open → half_open`, если cooldown истёк, и
    может захватить probe-slot. Это сознательный компромисс: отдельный
    «истинно read-only» Lua пришлось бы поддерживать параллельно с
    `_CHECK_SCRIPT`, и они бы разъезжались по логике переходов.
    Caller'у (`run_publisher_loop`) переход в half_open приемлем: он
    всё равно сделает publish-попытку следующей итерацией, успех её
    закроет breaker, fail — оставит open. Если когда-нибудь понадобится
    действительно неинвазивный peek (например, для метрик) — придётся
    делать отдельный скрипт.

    Возвращает то же что вернул бы `check()` Lua-скрипт, но без raise'а
    при open. Нужен `run_publisher_loop` — он хочет узнать «надо ли
    спать длиннее обычного», без отдельного raise/catch.

    Если Redis недоступен — fail-open: возвращаем `("closed", 0.0)`.
    """
    thresholds = _thresholds_from_settings()
    client = await _get_client()
    try:
        try:
            keys = (*_keys(), _probe_key())
            result = await client.eval(
                _CHECK_SCRIPT, 4, *keys,
                str(int(time.time())), str(thresholds.cooldown_seconds),
            )
        except Exception:  # noqa: BLE001 — fail-open
            logger.warning(
                "audit_publisher_breaker: get_state failed, defaulting to closed",
                exc_info=True,
            )
            return ("closed", 0.0)
    finally:
        await client.aclose()

    state_raw, retry_after_raw = result[0], result[1]
    state = state_raw.decode() if isinstance(state_raw, bytes) else state_raw
    return (state, float(retry_after_raw))


async def record_success() -> None:
    """Зафиксировать успешный POST → reset state + failures.

    Безопасна при ошибках Redis. Дёргается из `_publish_one` после 2xx.
    """
    client = await _get_client()
    try:
        try:
            keys = (*_keys(), _probe_key())
            await client.eval(_RECORD_SUCCESS_SCRIPT, 4, *keys)
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
            keys = (*_keys(), _probe_key())
            result = await client.eval(
                _RECORD_FAILURE_SCRIPT, 4, *keys,
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
    """Operator/test helper: очистить state breaker'а досрочно.

    Сносит все четыре ключа (failures, state, open_until, probe).
    """
    client = await _get_client()
    try:
        try:
            await client.delete(*_keys(), _probe_key())
        except Exception:  # noqa: BLE001
            logger.warning(
                "audit_publisher_breaker: reset failed", exc_info=True,
            )
    finally:
        await client.aclose()
