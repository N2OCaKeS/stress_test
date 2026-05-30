"""Shared circuit breaker для BMC-вызовов через Redis.

Зачем shared, а не per-process: в k8s worker крутится 2+ репликами. Без общего
state каждая реплика держит свой in-memory счётчик отказов, и один и тот же
контроллер может быть «open» у одной реплики и «closed» у второй. Контроллер
получает ровно ту же нагрузку с retry'ев, breaker фактически не работает.

Состояние хранится в Redis под тремя ключами на каждый host:

  * ``cb:bmc:<host>:failures`` — счётчик failure'ов в rolling window
    (INCR + EXPIRE на ``window_seconds``).
  * ``cb:bmc:<host>:state`` — ``"closed"`` (отсутствует) | ``"open"`` |
    ``"half_open"``. Phantom-key ``closed`` не пишем: отсутствие ключа =
    closed-state, экономит DEL'ы и RAM на Redis.
  * ``cb:bmc:<host>:open_until`` — unix-timestamp, до которого breaker
    отбивает запросы. Read'ится первой проверкой ``check``; expiry задаётся
    тем же значением, чтобы Redis сам подмёл ключ после кулдауна.

Logical states и переходы:

  * **closed** — нет ключей ``state``/``open_until``. ``check`` пропускает,
    ``record_failure`` инкрементит счётчик; когда счётчик ≥ threshold —
    переход в **open** с ``open_until = now + cooldown``.
  * **open** — есть ``state=open`` и ``open_until > now``. ``check``
    бросает ``CircuitBreakerOpenError`` без сетевого вызова BMC.
    ``record_*`` не имеют эффекта (мы внутри open-window).
  * **half_open** — ``state=open`` и ``open_until <= now`` (либо ключ ушёл
    по TTL и Lua вернула нет state'а — тогда `_transition_to_half_open`
    при `check`). Пропускаем ровно один пробный запрос — ставим
    ``state=half_open``. Если он успешен — ``record_success`` сбрасывает
    всё в closed; fail → снова open + reset ``open_until``.

Атомарность переходов — через Lua-скрипты (Redis выполняет их сериально на
одном thread'е, race между реплик'ами невозможна). Альтернатива
WATCH/MULTI потребовала бы retry-loop на каждом вызове.

Отказ Redis: ``check`` и ``record_*`` ловят любую ошибку Redis и логируют
WARNING, **не блокируя BMC-вызов**. Это сознательный fail-open: упавший
Redis не должен ронять power-операции; в худшем случае breaker какое-то
время не работает (тот же сценарий, что и без него).
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

# Префикс ключей. Отдельный от ``dbos:prepare_creds:`` и
# ``dbos:prepared_marker:`` — bmc breaker никак не пересекается с prepare-
# креденшалами, namespace разделён для облегчения мониторинга (`SCAN
# MATCH cb:bmc:*` в operator-debug session).
_KEY_PREFIX = "cb:bmc:"

# Значения параметров breaker'а. Согласованы в TODO: 5 fail/60s → open
# на 30s. Менять только через config / тесты (через override_thresholds).
DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_WINDOW_SECONDS = 60
DEFAULT_COOLDOWN_SECONDS = 30


@dataclass(frozen=True)
class _Thresholds:
    """Конфигурация breaker'а на один вызов.

    Передаётся отдельно, чтобы тесты могли менять пороги без monkeypatch'а
    settings. Production-код всегда берёт значения из ``get_settings()``.
    """

    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS


def _thresholds_from_settings() -> _Thresholds:
    """Достать пороги из ``Settings``; падать на default'ы для legacy-конфигов."""
    s = get_settings()
    return _Thresholds(
        failure_threshold=getattr(
            s, "bmc_breaker_failure_threshold", DEFAULT_FAILURE_THRESHOLD,
        ),
        window_seconds=getattr(
            s, "bmc_breaker_window_seconds", DEFAULT_WINDOW_SECONDS,
        ),
        cooldown_seconds=getattr(
            s, "bmc_breaker_cooldown_seconds", DEFAULT_COOLDOWN_SECONDS,
        ),
    )


class CircuitBreakerOpenError(AppException):
    """BMC breaker открыт — запрос отбит без вызова контроллера.

    Наследует ``AppException`` чтобы `_runner.run_task` штатно положил
    ``error_code`` в ``task.last_error`` и audit, без необходимости
    отдельной ветки в обработчике ошибок. ``BMC_CIRCUIT_OPEN`` — стабильный
    код для SIEM/UI: легко отличить от ``BMC_UNREACHABLE`` (BMC реально
    дёргали и не дождались) и понять, что виновата защита, а не сеть.
    """

    def __init__(self, host: str, retry_after_seconds: float) -> None:
        super().__init__(
            error_code="BMC_CIRCUIT_OPEN",
            message=(
                f"BMC circuit breaker is open for {host}; "
                f"retry after ~{int(retry_after_seconds)}s"
            ),
            details={
                "host": host,
                "retry_after_seconds": int(retry_after_seconds),
            },
        )


# ── Lua-скрипты ─────────────────────────────────────────────────────────────
# Все переходы атомарны на стороне Redis. KEYS[1]=failures, KEYS[2]=state,
# KEYS[3]=open_until. ARGV — параметры конкретного вызова. Сами скрипты
# живут в `_breaker_lua` — общие с audit_publisher_breaker. Локальные
# алиасы оставлены для обратной совместимости (FakeRedis в тестах
# матчит скрипт по объекту-строке).
_CHECK_SCRIPT = _breaker_lua.CHECK_SCRIPT
_RECORD_SUCCESS_SCRIPT = _breaker_lua.RECORD_SUCCESS_SCRIPT
_RECORD_FAILURE_SCRIPT = _breaker_lua.RECORD_FAILURE_SCRIPT


def _keys(host: str) -> tuple[str, str, str]:
    """Тройка Redis-ключей для одного host'а: failures, state, open_until."""
    base = f"{_KEY_PREFIX}{host}"
    return f"{base}:failures", f"{base}:state", f"{base}:open_until"


async def _get_client() -> aioredis.Redis:
    """Один-shot Redis-клиент.

    Не кэшируем глобально: ``aioredis.Redis`` держит pool, разрыв коннекта
    из-за рестарта Redis тогда придётся отдельно лечить. Open-close
    стоит мало по сравнению с BMC-roundtrip'ом.
    """
    settings = get_settings()
    return aioredis.from_url(settings.redis_url)


async def check(host: str) -> None:
    """Проверить breaker; raise ``CircuitBreakerOpenError`` если open.

    Идемпотентна, безопасна на ошибках Redis (fail-open). Должна вызываться
    перед каждым BMC-roundtrip'ом — обычно прямо в ``_bmc_helpers.get_bmc``.
    """
    if not host:
        return
    thresholds = _thresholds_from_settings()
    client = await _get_client()
    try:
        try:
            keys = _keys(host)
            result = await client.eval(
                _CHECK_SCRIPT, 3, *keys,
                str(int(time.time())), str(thresholds.cooldown_seconds),
            )
        except Exception:  # noqa: BLE001 — fail-open
            logger.warning(
                "bmc_breaker: check failed for host=%s, defaulting to closed",
                host, exc_info=True,
            )
            return
    finally:
        await client.aclose()

    state_raw, retry_after_raw = result[0], result[1]
    state = state_raw.decode() if isinstance(state_raw, bytes) else state_raw
    retry_after = float(retry_after_raw)
    if state == "open":
        logger.warning(
            "bmc_breaker: rejecting call to %s; circuit open for ~%ss",
            host, int(retry_after),
        )
        raise CircuitBreakerOpenError(host, retry_after)


async def record_success(host: str) -> None:
    """Зафиксировать успешный BMC-вызов → reset state + failures.

    Безопасна при ошибках Redis. Обычно дёргается из обработчика после
    успешного завершения работы с клиентом (см. ``_bmc_helpers.aclose_bmc``).
    """
    if not host:
        return
    client = await _get_client()
    try:
        try:
            keys = _keys(host)
            await client.eval(_RECORD_SUCCESS_SCRIPT, 3, *keys)
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning(
                "bmc_breaker: record_success failed for host=%s",
                host, exc_info=True,
            )
    finally:
        await client.aclose()


async def record_failure(host: str) -> None:
    """Зафиксировать BMC-ошибку → INCR failures, при необходимости open.

    Дёргается из обработчика BMC-исключений (см. ``wrap_bmc_error`` /
    ``_bmc_helpers.record_bmc_failure``). Безопасна при ошибках Redis.
    """
    if not host:
        return
    thresholds = _thresholds_from_settings()
    client = await _get_client()
    try:
        try:
            keys = _keys(host)
            result = await client.eval(
                _RECORD_FAILURE_SCRIPT, 3, *keys,
                str(int(time.time())),
                str(thresholds.failure_threshold),
                str(thresholds.window_seconds),
                str(thresholds.cooldown_seconds),
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning(
                "bmc_breaker: record_failure failed for host=%s",
                host, exc_info=True,
            )
            return
    finally:
        await client.aclose()

    state_raw, count_raw = result[0], result[1]
    state = state_raw.decode() if isinstance(state_raw, bytes) else state_raw
    if state == "open":
        logger.error(
            "bmc_breaker: OPENED for host=%s after %s failures in %ss; "
            "cooldown=%ss",
            host, int(count_raw),
            thresholds.window_seconds, thresholds.cooldown_seconds,
        )


async def reset(host: str) -> None:
    """Test/operator helper: очистить состояние breaker'а для host'а.

    Снимает ``state``, ``open_until`` и счётчик ``failures``. Используется
    в тестах вместо TRUNCATE; в проде — operator-команда «отпусти
    контроллер досрочно».
    """
    if not host:
        return
    client = await _get_client()
    try:
        try:
            await client.delete(*_keys(host))
        except Exception:  # noqa: BLE001
            logger.warning(
                "bmc_breaker: reset failed for host=%s", host, exc_info=True,
            )
    finally:
        await client.aclose()
