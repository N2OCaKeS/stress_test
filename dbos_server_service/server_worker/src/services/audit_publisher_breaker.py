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

import redis.asyncio as aioredis

from src.core.config import get_settings
from src.core.exceptions import AppException
from src.services import _breaker_core, _breaker_lua
from src.services._breaker_core import Thresholds as _Thresholds

logger = logging.getLogger(__name__)

# Префикс ключей — отделён и от bmc, и от prepare-creds, чтобы можно было
# `SCAN MATCH cb:audit_publisher:*` глянуть состояние канала отдельно.
_KEY_PREFIX = "cb:audit_publisher"

# Default-параметры breaker'а. Те же 5/60/30, что у BMC: на этих числах
# уже выверена не-flaky реакция на rolling restart loging_service.
DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_WINDOW_SECONDS = 60
DEFAULT_COOLDOWN_SECONDS = 30


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
_GET_STATE_SCRIPT = _breaker_lua.GET_STATE_SCRIPT
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


def _all_keys() -> tuple[str, str, str, str]:
    """Все четыре ключа breaker'а: (failures, state, open_until, probe)."""
    return (*_keys(), _probe_key())


async def _get_client() -> aioredis.Redis:
    """One-shot Redis-клиент. Не кэшируем — см. комментарий в bmc_circuit_breaker.

    Подменяется в тестах через `install_fake_redis(monkeypatch, audit_cb)` —
    `_breaker_core` дёргает эту функцию как client_factory.
    """
    settings = get_settings()
    return aioredis.from_url(settings.redis_url)


async def check() -> None:
    """Проверить breaker; raise CircuitBreakerOpenError если open.

    Вызывать перед каждым POST в loging_service. Безопасна на ошибках
    Redis (fail-open: пропускаем HTTP, breaker «как будто closed»).
    """
    thresholds = _thresholds_from_settings()
    state, retry_after = await _breaker_core.eval_check(
        client_factory=_get_client,
        keys=_all_keys(),
        cooldown_seconds=thresholds.cooldown_seconds,
        log_prefix="audit_publisher_breaker",
        logger=logger,
        now=time.time(),
    )
    if state == "open":
        logger.warning(
            "audit_publisher_breaker: rejecting POST; circuit open for ~%ss",
            int(retry_after),
        )
        raise CircuitBreakerOpenError(retry_after)


async def get_state() -> tuple[str, float]:
    """Pure-read snapshot breaker'а: `(state, retry_after_seconds)`.

    Под капотом крутится отдельный `_GET_STATE_SCRIPT`, который НЕ
    транзитит state и не трогает probe-ключ. Безопасно дёргать из
    observability/metrics-путей и из adaptive-sleep'а в `run_publisher_loop`
    — конкуренция за probe-slot между репликами разрешается только в
    `check()` перед реальным POST'ом.

    Возвращаемые значения:

    * `("closed", 0.0)` — канал свободен, обычный poll-interval;
    * `("open", retry_after)` — breaker открыт; `retry_after` > 0 —
      cooldown ещё идёт; `retry_after` == 0 — cooldown истёк, но
      transition в half_open сделает первый же `check()`. Caller в loop'е
      использует это как сигнал спать дольше обычного poll-interval'а.
    * Half_open state снаружи виден как `("open", ttl_probe)` —
      пробный запрос у кого-то в полёте, остальным дёргать `check()`
      бессмысленно, ttl probe-ключа = верхняя граница ожидания.

    Если Redis недоступен — fail-open: возвращаем `("closed", 0.0)`.
    """
    return await _breaker_core.eval_get_state(
        client_factory=_get_client,
        keys=_all_keys(),
        log_prefix="audit_publisher_breaker",
        logger=logger,
        now=time.time(),
    )


async def record_success() -> None:
    """Зафиксировать успешный POST → reset state + failures.

    Безопасна при ошибках Redis. Дёргается из `_publish_one` после 2xx.
    """
    await _breaker_core.eval_record_success(
        client_factory=_get_client,
        keys=_all_keys(),
        log_prefix="audit_publisher_breaker",
        logger=logger,
    )


async def record_failure() -> None:
    """Зафиксировать неуспех POST → INCR failures, при необходимости open.

    Безопасна при ошибках Redis. Дёргается из `_publish_one` в except
    после AuditEmitError (5xx/transport — 4xx это permanent-fail row'и,
    канал не виноват, breaker такие не считает).
    """
    thresholds = _thresholds_from_settings()
    result = await _breaker_core.eval_record_failure(
        client_factory=_get_client,
        keys=_all_keys(),
        thresholds=thresholds,
        log_prefix="audit_publisher_breaker",
        logger=logger,
        now=time.time(),
    )
    if result is None:
        return
    state, count = result
    if state == "open":
        logger.error(
            "audit_publisher_breaker: OPENED after %s failures in %ss; cooldown=%ss",
            count,
            thresholds.window_seconds,
            thresholds.cooldown_seconds,
        )


async def reset() -> None:
    """Operator/test helper: очистить state breaker'а досрочно.

    Сносит все четыре ключа (failures, state, open_until, probe).
    """
    await _breaker_core.eval_reset(
        client_factory=_get_client,
        keys=_all_keys(),
        log_prefix="audit_publisher_breaker",
        logger=logger,
    )
