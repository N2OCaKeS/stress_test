"""Publisher для audit outbox.

Фоновый loop, дренирующий таблицу `audit_outbox` → loging_service.

Две точки входа:

  * `flush_outbox(session)` — single pass, дёргается inline из
    `_runner.run_task` сразу после lifecycle-commit'а (best-effort
    just-in-time publish; держит latency низкой в happy-path).
  * `run_publisher_loop()` — long-running async loop, поднимается как
    sidecar-таск (taskiq startup hook или отдельный k8s-sidecar).
    Polls раз в N секунд, retry'ит unpublished rows.

Идемпотентность: publisher выставляет `published_at` *после* успешного
HTTP-ответа. Если процесс умер между emit() и UPDATE, row повторно
эмитится в следующий проход → at-least-once. Дедупликация на стороне
loging_service (см. AUDIT_EVENTS.md).

Failure-mode: HTTP-ошибки логируются, `attempts` инкрементится,
`last_error` пишется, row остаётся unpublished.

Circuit breaker: без него каскад audit-failures DoS'ит loging_service.
Источник истины — shared `audit_publisher_breaker` в Redis: per-row
`check()` отбивает HTTP-call до сети, `record_failure()` копит счётчик,
`record_success()` сбрасывает. `run_publisher_loop` после каждого
прохода читает `get_state()`: если open — спит короткими порциями
(≤5s) до конца cooldown'а вместо обычного poll-interval'а; HTTP всё
равно ушёл бы в `CircuitBreakerOpenError` на `_publish_one.check()`,
но без adaptive-sleep loop бесполезно крутил бы пустые проходы.
Inline `_safe_flush_outbox()` из `_runner.py` идёт через тот же
`_publish_one`, поэтому breaker распространяется и на него: при open
breaker'е inline-вызов получит skip без HTTP-roundtrip'а (row
останется unpublished, background loop разгребёт). Дополнительной
защиты loging_service это не даёт, но симметрия между inline и
background-путями упрощает рассуждения о состоянии очереди.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.constants import LAST_ERROR_MAX_LEN
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox
from src.services import audit_client, audit_publisher_breaker
from src.services.audit_client import AuditEmitError
from src.services.audit_publisher_breaker import CircuitBreakerOpenError
from src.utils.redaction import redact_error_message

logger = logging.getLogger(__name__)

class PublishResult(NamedTuple):
    """Исход одной попытки `_publish_one`.

    Четыре независимых сигнала, которые caller использует по-разному —
    позиционные bool'ы легко путаются местами, отсюда NamedTuple.

    * `closed` — row завершила свой цикл в этом проходе (опубликована, в
      DLQ или skip'нута breaker'ом). Caller не должен повторно её
      SELECT'ить в рамках текущего `_flush_outbox_once`.
    * `audit_emit_error` — была реальная HTTP-failure от loging_service
      (5xx/timeout/connect). Сигнал loop-level breaker'у.
    * `was_published` — row пометилась `published_at=NOW()` после 2xx. Не
      путать с `closed`: DLQ-row тоже `closed`, но `was_published=False`.
    * `breaker_skipped` — `check()` отбил row до HTTP-вызова (canал open).
      Отдельный флаг, потому что DLQ и breaker-skip оба возвращают
      `(closed=True, was_published=False, audit_emit_error=False)`, но
      caller'у с ними нужно разное: DLQ row уже выпала из SELECT'а
      (published_at!=None), а breaker-skip означает «канал глух, дальше
      перебирать row'ы бессмысленно, bail-out из flush-loop'а».
    """

    closed: bool
    audit_emit_error: bool
    was_published: bool
    breaker_skipped: bool = False


# Сколько строк за один проход. Размер выбран маленьким сознательно:
# `_publish_one` делает HTTP-запрос в loging_service на каждую строку, при
# `with_for_update(skip_locked=True)` весь batch держится залоченным до
# финального commit'а. При batch=50 один медленный emit (timeout 5s) тормозит
# остальные 49 даже если loging тут же ответил бы быстро. batch=5 ограничивает
# blast-radius медленных запросов и при этом не убивает throughput на happy-
# path'е — за 2-секундный poll-interval все 5 успевают пройти.
_BATCH_SIZE = 5
# Пауза между проходами фонового loop'а.
_POLL_INTERVAL_SECONDS = 2.0


def _max_publish_attempts() -> int:
    """Soft-cap на attempts: после него row помечается poisoned.

    Без потолка loging_service с permanent-ошибкой (422 malformed payload)
    заставит publisher ретраить row бесконечно — она будет засорять
    SKIP LOCKED выборку и attempts может перевалить за 2^31. Когда
    `attempts >= MAX_PUBLISH_ATTEMPTS`, publisher выставляет
    `published_at=now()` и пишет ERROR — событие потеряно, но row
    перестаёт мозолить очередь.

    Значение читаем через свежий `Settings()` без LRU-кэша: pydantic
    проводит ту же валидацию (`ge=1`, type=int), что и для прочих
    конфиг-полей, и тесты могут менять `MAX_PUBLISH_ATTEMPTS` через
    monkeypatch.setenv без `get_settings.cache_clear()`. Cap читается
    редко (только когда `_publish_one` решает poison'ить row), оверхед
    нового инстанса пренебрежимо мал.
    """
    return Settings().max_publish_attempts

# ── Circuit breaker tunables ─────────────────────────────────────────────────
# В open-state цикл спит порциями ≤ этой — чтобы при «починке» loging'а
# breaker закрылся быстро и можно было корректно остановить worker
# (cancellation на длинном sleep'е работает, но мелкими порциями нагляднее
# в логах). Решение об open/closed принимает shared `audit_publisher_breaker`;
# тут только sleep-стратегия poll-loop'а.
_CB_SLEEP_CHUNK_SECONDS = 5.0

# DLQ counter (monotonic, per-process). Считаем все случаи, когда row
# был отравлен (`_maybe_poison`) — и по cap'у attempts, и по 4xx-классу.
# Stub под будущую Prometheus-метрику `audit_outbox_dead_total`; пока
# доступен через `get_dlq_total()` для health-эндпоинтов и тестов.
_dlq_total: int = 0

# Counter skip'ов по open circuit breaker'у — отдельная метрика от DLQ.
# Растёт при каждом `_publish_one`, где `audit_publisher_breaker.check()`
# отбил row до HTTP-вызова. Полезно для health-эндпоинта: если breaker
# open и счётчик уверенно растёт — loging_service лежит, надо разбираться,
# а не ждать естественного recovery. Stub под Prometheus-метрику
# `audit_outbox_breaker_skips_total`.
_breaker_skips_total: int = 0

# Backoff cap. 2^attempts растёт быстро: уже на 10 fail'ах = 1024s
# (~17 минут) между попытками, на 20 — ~12 дней. Cap'аем потолком,
# чтобы row не «уезжал» на месяцы из-за случайно высокого attempts
# (например, после ручного re-attempt'а из DLQ с не-обнулённым счётчиком).
# 5 минут — потолок, дальше дальше row простаивает слишком долго; реальная
# повторная отправка к этому моменту либо уже отработает через
# `record_success` от другой строки, либо проблему лучше разгребать
# через `internal.outbox_re_attempt`.
_BACKOFF_MAX_SECONDS = 300.0


def get_dlq_total() -> int:
    """Сколько raз publisher отбраковал outbox-row в DLQ за время жизни процесса.

    Растёт при каждом `_maybe_poison()=True` (любая причина — cap по
    attempts, 4xx permanent-fail или missing_action). Сбрасывается только
    рестартом процесса. Health-check `/health` worker'а (когда появится)
    может репортить это значение — резкий рост = poisoned-deploy либо
    несовместимая схема loging_service.
    """
    return _dlq_total


def get_breaker_skips_total() -> int:
    """Сколько row'ов publisher пропустил из-за open circuit breaker'а.

    Растёт при каждом `_publish_one`, где `audit_publisher_breaker.check()`
    отбил row до HTTP-вызова. Сбрасывается только рестартом процесса
    (тестовый `_reset_breaker_state` тоже обнуляет). Health-check может
    репортить значение для алерта на «канал к loging_service глух».
    """
    return _breaker_skips_total


def _reset_breaker_state() -> None:
    """Test helper: сбросить module-level DLQ counter и breaker-skip counter.

    Shared breaker'ом владеет `audit_publisher_breaker`, его state живёт
    в Redis и сбрасывается через `audit_publisher_breaker.reset()` —
    это делает conftest autouse-фикстура. Здесь остаются monotonic
    счётчики DLQ и breaker-skip'ов, их тесты обнуляют сами, когда хотят
    проверить дельту.
    """
    global _dlq_total, _breaker_skips_total
    _dlq_total = 0
    _breaker_skips_total = 0


async def re_attempt_row(row_id: int) -> bool:
    """Operator-команда: вернуть outbox-row из DLQ обратно в очередь.

    Сбрасывает `published_at`, `attempts`, `next_retry_at`, `last_error`
    → publisher увидит row в следующем тике и попытается отправить
    заново. Никаких guard'ов по типу row'и нет: оператор сам решает,
    какие DLQ-причины пересылать (для `permanent_4xx` без правки payload
    повторная попытка тоже даст 4xx, но это его головная боль).

    Возвращает True, если row найден и сброшен; False — если row нет
    или она уже unpublished. Caller отвечает только за вызов; commit
    делает сама функция.
    """
    async with AsyncSessionLocal() as session:
        row = await session.get(AuditOutbox, row_id)
        if row is None:
            logger.warning("outbox_re_attempt: row=%s not found", row_id)
            return False
        if row.published_at is None:
            logger.info(
                "outbox_re_attempt: row=%s already unpublished, noop", row_id
            )
            return False
        row.published_at = None
        row.attempts = 0
        row.next_retry_at = None
        row.last_error = None
        await session.commit()
    logger.info("outbox_re_attempt: row=%s re-queued from DLQ", row_id)
    return True


def _send_to_dlq(row: AuditOutbox, *, reason: str) -> None:
    """Mark row as «дропнут» (DLQ-семантика без отдельной таблицы).

    Ставим `published_at=now()`, чтобы SELECT по `published_at IS NULL`
    его больше не подхватывал. Чтобы оператор по строке мог отличить
    успешную доставку (`published_at` стоит, `last_error` пустой/старый)
    от drop'а (`published_at` стоит, потому что row выбита из очереди),
    префиксуем `last_error` маркером `[DLQ:<reason>]` — при выводе
    DLQ-row в админке/SELECT'е причина видна сразу, без чтения логов.
    Инкрементим module-level counter и пишем ERROR с явным `event=dlq`.

    Причины: `attempts_cap` (cap по attempts), `permanent_4xx` (4xx
    permanent-fail от loging_service), `missing_action` (битый payload
    без обязательного поля).
    """
    global _dlq_total
    row.published_at = datetime.now(timezone.utc)
    dlq_marker = f"[DLQ:{reason}] "
    existing = row.last_error or ""
    if not existing.startswith("[DLQ:"):
        prefixed = dlq_marker + existing
        row.last_error = prefixed[:LAST_ERROR_MAX_LEN]
    _dlq_total += 1
    logger.error(
        "audit_outbox: row sent to DLQ event=dlq reason=%s attempts=%s "
        "row=%s task_id=%s last_error=%s",
        reason,
        row.attempts,
        row.id,
        row.task_id,
        row.last_error,
    )


def _maybe_poison(row: AuditOutbox) -> bool:
    """Если row перевалил cap по attempts — отправить в DLQ.

    Возвращает True, если row был отравлен (publisher после этого
    пропускает обычный warning-лог).
    """
    cap = _max_publish_attempts()
    if row.attempts >= cap:
        _send_to_dlq(row, reason="attempts_cap")
        return True
    return False


# Cap на показатель степени в backoff'е. attempts обычно ≤ MAX_PUBLISH_ATTEMPTS
# (~50), но если cap сорвётся (баг в poison'е, ручной reset attempts с
# сохранением old-value и т.п.) — `2 ** 10000` в Python работает (bigint
# arithmetic), CPU/память съест. Cap'аем сам shift'ом: 2^16 = 65536 секунд
# (~18 часов) уже за пределами любого разумного retry-окна, дальше всё
# равно режется `_BACKOFF_MAX_SECONDS=300`. Дешёвле, чем доверять верхнему
# `min(...)` останавливать рост exp'оненты.
_BACKOFF_EXPONENT_CAP = 16


def _apply_backoff(row: AuditOutbox) -> None:
    """Назначить `next_retry_at` после неуспешной попытки.

    Формула: `now() + min(2^min(attempts, _BACKOFF_EXPONENT_CAP),
    _BACKOFF_MAX_SECONDS)` секунд. `attempts` уже инкрементнут к моменту
    вызова — берём текущее значение. SELECT publisher'а потом не возьмёт
    row, пока время не наступит.

    Двойной cap: внутренний на показатель степени (CPU/memory guard на
    случай accidental overflow attempts), внешний на результат в секундах
    (бизнес-cap: row не должна простаивать дольше 5 минут).

    Не вызывается при поэтапной отбраковке (`missing_action`,
    `permanent_4xx`, `attempts_cap`) — там row уже закрыт через
    `_send_to_dlq` и `next_retry_at` смысла не имеет.
    """
    exponent = min(row.attempts or 0, _BACKOFF_EXPONENT_CAP)
    delay = min(2 ** exponent, _BACKOFF_MAX_SECONDS)
    row.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)


async def _publish_one(
    session: AsyncSession, row: AuditOutbox,
) -> PublishResult:
    """Попытка отправить одну строку.

    Возвращает `PublishResult(closed, audit_emit_error, was_published)`:
      * `closed=True, was_published=True` — успешный 2xx, row помечена published.
      * `closed=True, was_published=False` — row отбракована в DLQ
        (missing_action / permanent_4xx / attempts_cap), либо breaker уже open
        и HTTP-call пропущен. Caller не должен повторно подбирать её на этом
        тике. В счётчик published такая row не идёт.
      * `closed=False, audit_emit_error=True` — `AuditEmitError` (HTTP/transport
        fail на стороне loging_service). Это сигнал для circuit breaker:
        нагружать loging_service дальше смысла нет.
      * `closed=False, audit_emit_error=False` — программная ошибка
        (сериализация и т.п.). Breaker такие НЕ считает — это локальный
        баг, не проблема loging_service.

    Контракт сессии (важно для callers):

    Метод делает `session.flush()` после изменения row (published_at,
    next_retry_at, attempts, DLQ-маркера) — но **commit оставляет на caller'е**.
    Это сделано намеренно: `_flush_outbox_once` берёт SKIP LOCKED-lock на row
    и держит его до commit'а. Раннее commit'нуть внутри `_publish_one` —
    значит отпустить lock до того, как caller успеет залогировать/собрать
    метрики, и потерять контроль над transaction boundary'ём в случае
    multi-row пути в будущем. Caller обязан вызвать `await session.commit()`
    после возврата (или `rollback()` при exception'е выше) — иначе
    `next_retry_at` не приедет в БД и SELECT следующего poll-цикла подберёт
    ту же row снова.
    """
    payload = dict(row.payload)
    action = payload.pop("action", None)
    if not action:
        # Битый payload без action — событие потеряно, ретраить нечего.
        # Не лотим попыток впустую, сразу в DLQ. Breaker НЕ считает это
        # сигналом про loging_service — корень в нашем payload'е.
        row.last_error = "missing_action"
        _send_to_dlq(row, reason="missing_action")
        await session.flush()
        return PublishResult(closed=True, audit_emit_error=False, was_published=False)

    # Shared circuit breaker перед HTTP-вызовом. Если loging_service уже
    # признан недоступным другими репликами — отбиваем запрос без сетевого
    # roundtrip'а. CircuitBreakerOpenError ловится отдельно ниже.
    #
    # Skip от breaker'а — это НЕ попытка доставки: HTTP-call не делался,
    # `attempts` инкрементировать нельзя. Иначе row уезжает в DLQ через
    # `_maybe_poison` после ~50 open-циклов, не сделав ни одного запроса
    # к loging_service. Backoff тоже не выставляем: при закрытии breaker'а
    # row должна сразу попасть в выборку. Логирование уже делается в
    # `_maybe_open_circuit` / `record_failure`, дублировать на каждую row не нужно.
    try:
        await audit_publisher_breaker.check()
    except CircuitBreakerOpenError as exc:
        # was_published=False — событие не доставлено; closed=True — caller
        # не должен повторять row в этом проходе; audit_emit_error=False —
        # это не сигнал loop-level breaker'у, тот ведёт свой счёт по
        # реальным HTTP-failure'ам. breaker_skipped=True — caller bail-out'ит
        # из flush-loop'а, остальные row'ы под open breaker'ом всё равно
        # отскочат без HTTP'а.
        #
        # Параллельно отодвигаем `next_retry_at` до конца cooldown'а
        # breaker'а. Без этого row остаётся eligible для SELECT'а уже на
        # следующем 2-секундном poll-цикле и публикатор обновного крутит
        # check() по той же неработающей строке. С `next_retry_at`
        # background loop (и inline-flush из `_runner`) пропустит row,
        # пока breaker не закроется естественным образом, что снимает
        # spin на закрытом канале.
        global _breaker_skips_total
        _breaker_skips_total += 1
        retry_after = float(exc.details.get("retry_after_seconds", 0) or 0)
        if retry_after > 0:
            row.next_retry_at = datetime.now(timezone.utc) + timedelta(
                seconds=retry_after,
            )
            await session.flush()
        return PublishResult(
            closed=True, audit_emit_error=False, was_published=False,
            breaker_skipped=True,
        )

    try:
        # `audit_client.emit` сам решает, что считать неудачей:
        #   * 4xx из loging_service → `AuditEmitError(status_code=4xx)`
        #     → permanent-fatal, мы сразу в DLQ;
        #   * 5xx из loging_service → `AuditEmitError(status_code=5xx)`
        #     → transient, оставляем unpublished + breaker;
        #   * httpx.HTTPError (timeout/connect/...) →
        #     `AuditEmitError(status_code=None)` → transient, retry;
        #   * успешный 2xx → return None.
        # Раньше emit делал log-and-swallow на HTTP-ошибках — outbox-row
        # помечался published, событие терялось (swallow 4xx/5xx →
        # published-but-not-delivered).
        # Прочие Exception (баги сериализации, неожиданные ошибки) тоже
        # оставляют row unpublished — publisher ретраит.
        await audit_client.emit(action, **payload)
    except AuditEmitError as exc:
        # HTTP-level failure — отдельная семантика для ясности логов.
        # Реальные клиенты (httpx) включают полный URL с basic-auth в
        # repr исключения. Симметрично `_runner.py` (для `task.last_error`)
        # прогоняем через `redact_error_message` до записи в worker-DB.
        row.attempts = (row.attempts or 0) + 1
        error_message = redact_error_message(exc.error_message)
        row.last_error = error_message[:LAST_ERROR_MAX_LEN]

        # Classify 4xx как permanent-fatal — loging_service ответил, что
        # этот конкретный payload неприемлем (плохая схема, dead key,
        # отозванный actor). Retry не починит. Сразу в DLQ. Breaker такие
        # тоже НЕ открывает: loging_service жив (раз ответил 4xx), просто
        # наш payload не годится — нагружать его дальше другими событиями
        # смысла нет. Возвращаем `(True, False)` симметрично missing_action
        # пути: row закрыт (DLQ-помечен), для счётчика breaker'а это не
        # AuditEmitError (loging-канал не виноват).
        if exc.status_code is not None and 400 <= exc.status_code < 500:
            _send_to_dlq(row, reason="permanent_4xx")
            await session.flush()
            return PublishResult(closed=True, audit_emit_error=False, was_published=False)

        if _maybe_poison(row):
            # Cap по attempts — row закрыта в DLQ. Breaker НЕ открываем:
            # последняя ошибка может быть transient'ом, но row сама по
            # себе ядовитая, нет смысла обвинять канал. Если loging
            # реально лежит, следующие row'ы это покажут.
            await session.flush()
            return PublishResult(closed=True, audit_emit_error=False, was_published=False)
        _apply_backoff(row)
        await session.flush()
        # Transient HTTP-failure от loging_service (5xx/timeout/connect) —
        # сигнал shared breaker'у. 4xx не доходят сюда: они уходят в DLQ
        # выше и не нагружают канал в смысле «он лежит».
        await audit_publisher_breaker.record_failure()
        logger.warning(
            "audit_outbox publish HTTP-failed row=%s attempts=%s status=%s next_retry_at=%s",
            row.id,
            row.attempts,
            exc.status_code,
            row.next_retry_at,
        )
        return PublishResult(closed=False, audit_emit_error=True, was_published=False)
    except Exception as exc:  # noqa: BLE001
        # Программные ошибки (сериализация, неожиданные exception'ы) —
        # тоже не маркируем published, publisher повторит на следующем
        # проходе. Поведение идентично HTTP-failure, но логируется иначе
        # — operator должен заметить «не HTTP» в стектрейсе.
        row.attempts = (row.attempts or 0) + 1
        error_message = redact_error_message(f"{type(exc).__name__}: {exc}")
        row.last_error = error_message[:LAST_ERROR_MAX_LEN]
        if _maybe_poison(row):
            await session.flush()
            return PublishResult(closed=True, audit_emit_error=False, was_published=False)
        _apply_backoff(row)
        await session.flush()
        logger.warning(
            "audit_outbox publish failed row=%s attempts=%s err=%s next_retry_at=%s",
            row.id,
            row.attempts,
            type(exc).__name__,
            row.next_retry_at,
        )
        return PublishResult(closed=False, audit_emit_error=False, was_published=False)

    row.published_at = datetime.now(timezone.utc)
    # На успех — обнуляем backoff (был выставлен предыдущей попыткой,
    # но row всё равно уйдёт из выборки по `published_at IS NOT NULL`).
    row.next_retry_at = None
    await session.flush()
    # Закрываем shared breaker: канал отвечает 2xx, дальше работаем штатно.
    # Дёргается на каждый успех — Redis-команда дешёвая (DEL × 3), а
    # симметрия с record_failure упрощает чтение кода.
    await audit_publisher_breaker.record_success()
    return PublishResult(closed=True, audit_emit_error=False, was_published=True)


def _select_unpublished(limit: int):
    """Собрать SELECT-stmt для unpublished outbox-rows.

    Вынесено отдельно, чтобы можно было проверить контракт inspection'ом
    SQL (тесты валидируют наличие `FOR UPDATE SKIP LOCKED` без коннекта
    к настоящей DB).

    `with_for_update(skip_locked=True)` — защита от двойной публикации
    при `replicas > 1`: publisher loop запускается на каждой replica
    воркера (см. `taskiq.TaskiqEvents.WORKER_STARTUP` wiring в
    `src/main.py`). Без skip-locked две replica'и за один проход
    `SELECT ... LIMIT 50 WHERE published_at IS NULL` подхватили бы одну
    и ту же строку → `audit_client.emit` дважды → дубликат события в
    loging_service. Дедупликация на стороне loging_service пока не
    реализована; здесь — safe-default на стороне worker'а.

    Lock держится до commit'а внешней транзакции; commit идёт после
    UPDATE `published_at`, поэтому строки атомарно «исчезают» из
    выборки конкурента до того, как lock будет отпущен.
    """
    now = datetime.now(timezone.utc)
    return (
        select(AuditOutbox)
        .where(
            AuditOutbox.published_at.is_(None),
            or_(
                AuditOutbox.next_retry_at.is_(None),
                AuditOutbox.next_retry_at <= now,
            ),
        )
        .order_by(AuditOutbox.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


def _select_unpublished_excluding(limit: int, exclude_ids: list[int]):
    """То же что `_select_unpublished`, но с exclude по id'шникам.

    Per-row flush требует пропускать row'ы, которые мы УЖЕ пробовали
    опубликовать в этом проходе и которые остались unpublished (5xx /
    transport). Без exclude'а такая row на следующей итерации того же
    `_flush_outbox_once` снова попадёт в SELECT (она по-прежнему
    unpublished), и мы будем долбить тот же неработающий канал 5 раз
    подряд вместо того, чтобы дать шанс соседним row'ам.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(AuditOutbox)
        .where(
            AuditOutbox.published_at.is_(None),
            or_(
                AuditOutbox.next_retry_at.is_(None),
                AuditOutbox.next_retry_at <= now,
            ),
        )
        .order_by(AuditOutbox.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if exclude_ids:
        stmt = stmt.where(AuditOutbox.id.notin_(exclude_ids))
    return stmt


async def _flush_outbox_once(*, limit: int = _BATCH_SIZE) -> tuple[int, int]:
    """Внутренний single-pass: возвращает `(published, audit_emit_errors)`.

    Отличается от публичного `flush_outbox()` только тем, что отдаёт
    отдельный счётчик `AuditEmitError`'ов — это нужно `run_publisher_loop`
    для circuit breaker'а (он не должен реагировать на программные баги,
    только на сигналы про недоступность loging_service).

    Per-row commit:

    Раньше SELECT FOR UPDATE SKIP LOCKED брал `limit` row'ов и держал
    lock на всех до финального commit'а сессии. Один медленный
    `_publish_one` (HTTP timeout 5s × 5 row'ов = 25s) держал четыре
    быстрых row'а в hostage-state — другие replica'и обходили их по
    skip-locked, но «горячая» партиция выглядела залоченной 25 секунд.

    Сейчас обрабатываем по одной row за итерацию: SELECT LIMIT 1 →
    `_publish_one` → commit → release lock → повторить до `limit` раз.
    Это режет blast-radius медленного запроса до одной row, выигрыш в
    lock-fairness и предсказуемости latency остальных replica'ей.
    Throughput не страдает заметно — узкое горло всё равно HTTP к
    loging_service, не Postgres-commit'ы.
    """
    published = 0
    audit_emit_errors = 0
    # Row'ы, которые мы уже пытались опубликовать в этом проходе и
    # которые остались unpublished (5xx/transport). Без exclude'а они
    # снова бы попадали в SELECT по `published_at IS NULL`, и одна и та
    # же неработающая row пожирала бы весь `limit`. Background loop их
    # подхватит на следующем тике.
    failed_ids: list[int] = []
    for _ in range(limit):
        async with AsyncSessionLocal() as session:
            stmt = _select_unpublished_excluding(1, failed_ids)
            select_res = await session.execute(stmt)
            row = select_res.scalars().first()
            if row is None:
                # Очередь пуста (либо все оставшиеся row'ы в `failed_ids`) —
                # выходим, не нужно бить лишний SELECT.
                break
            row_id = row.id
            publish_res = await _publish_one(session, row)
            if publish_res.was_published:
                published += 1
            if not publish_res.closed:
                # Row осталась unpublished — не SELECT'им её повторно в
                # этом проходе. DLQ-row (closed=True, was_published=False)
                # тоже исчезает из выборки (через `published_at`), её не
                # надо отдельно exclude'ить.
                failed_ids.append(row_id)
            if publish_res.audit_emit_error:
                audit_emit_errors += 1
            if publish_res.breaker_skipped:
                # `_publish_one` мог записать `next_retry_at = now + cooldown'
                # на row — чтобы публикатор не дёргал её каждые 2s, пока
                # breaker сидит в open. Поэтому commit'им, а не rollback'аем:
                # без commit'а next_retry_at не приедет в БД, и следующий
                # poll-цикл вернёт ту же строку в выборку.
                # Дальше bail-out из batch'а: остальные row'ы под open
                # breaker'ом всё равно отскочат от check() без HTTP'а, и
                # проход молотил бы SELECT'ы по БД впустую. Background
                # poll-loop вернётся через `_CB_SLEEP_CHUNK_SECONDS` или
                # раньше (adaptive sleep по `get_state()`); к тому моменту
                # канал либо в half_open, либо cooldown ещё не истёк.
                await session.commit()
                break
            # commit отпускает SKIP LOCKED-lock этой одной row'и сразу,
            # не дожидаясь обработки остальных. Другая replica может
            # подхватить следующую row'ю в тот же момент.
            await session.commit()
    return published, audit_emit_errors


async def flush_outbox(*, limit: int = _BATCH_SIZE) -> int:
    """Single-pass: пытаемся опубликовать до `limit` неотправленных строк.

    Используется как «just-in-time» publisher из `_runner.run_task`:
    после commit'а task-lifecycle пробуем сразу довезти audit-event, но
    если не вышло — фоновый loop / следующий task всё равно подхватит.

    SELECT берёт `FOR UPDATE SKIP LOCKED` — два конкурирующих publisher'а
    (multi-replica deploy) обработают непересекающиеся подмножества
    строк. UPDATE `published_at` и `commit()` идут в той же сессии и
    транзакции, что и SELECT — иначе lock отпустится до отметки и
    конкурент перехватит уже отправленную строку.

    Возвращает количество строк, которые удалось опубликовать.

    Inline-вызовы из `_runner._safe_flush_outbox()` идут через ту же
    `_publish_one`, поэтому breaker применяется к ним симметрично с
    background loop'ом: при open breaker'е inline-вызов скипнет row
    без HTTP-call'а и вернёт 0; row останется unpublished, фон
    добьёт после закрытия breaker'а.
    """
    published, _audit_emit_errors = await _flush_outbox_once(limit=limit)
    return published


async def run_publisher_loop(
    *, interval_seconds: float = _POLL_INTERVAL_SECONDS
) -> None:
    """Бесконечный фоновый loop. Поднимать как async-таск на старте процесса.

    Пример::

        async def startup():
            asyncio.create_task(run_publisher_loop())

    Circuit breaker:

      * Решение об open/closed принимает shared `audit_publisher_breaker`
        (Redis). `_publish_one` дёргает `check()` перед каждым HTTP'ом,
        `record_failure()` после 5xx/transport, `record_success()` после
        2xx. Все реплики worker'а делят один счётчик.
      * После прохода loop читает `get_state()`: если open — спит
        порциями ≤ `_CB_SLEEP_CHUNK_SECONDS` до конца cooldown'а вместо
        обычного poll-interval'а. Это адаптивный backoff: нет смысла
        крутить `_flush_outbox_once` каждые 2s, если все row'ы отбьются
        breaker'ом сразу же.
      * Программные ошибки `_publish_one` (сериализация и т.п.) breaker
        НЕ открывают: loging_service тут не виноват, добавлять задержки
        бессмысленно. `audit_emit_errors` оставлен в API проходов
        только для observability/тестов.
    """
    logger.info("audit_outbox publisher loop started (interval=%ss)", interval_seconds)
    while True:
        try:
            _published, _audit_emit_errors = await _flush_outbox_once()
        # `asyncio.CancelledError` в Python 3.8+ наследуется от `BaseException`,
        # не от `Exception`, поэтому `except Exception` его не глотает и
        # graceful shutdown (taskiq WORKER_SHUTDOWN → cancel этой task'и)
        # отрабатывает штатно. Если когда-нибудь поднимется issue про
        # «loop не останавливается на SIGTERM» — проверять надо `_runner`
        # и taskiq-wiring, не этот блок.
        except Exception as exc:  # noqa: BLE001 — never crash the loop
            # Идёт в worker stdout/journald → k8s log-aggregator. Реальные
            # клиенты (httpx/requests/asyncpg) могут зашить в текст ошибки
            # полный URL с basic-auth (`LOGGING_SERVICE_URL`,
            # `DATABASE_URL`) или Bearer-токен. Без redact это утечёт в
            # логи контейнера. Симметрично `_publish_one` (для
            # `last_error`) и `_runner.py` (для `task.last_error`).
            logger.warning(
                "audit_outbox publisher loop iteration failed: %s",
                redact_error_message(f"{type(exc).__name__}: {exc}"),
            )
            # Catastrophic — скорее всего проблема с DB worker'а, не с
            # loging. Спим обычный poll-interval, к breaker'у не лезем.
            await asyncio.sleep(interval_seconds)
            continue

        # Sleep-strategy: если shared breaker open — спим до конца
        # cooldown'а порциями. Так loop не тратит CPU на пустые проходы,
        # которые `_publish_one` всё равно отобьёт через check(). Если
        # closed/half_open — обычный poll-interval. Ошибки Redis в
        # get_state() fail-open'ятся (возвращается closed), это ok.
        state, retry_after = await audit_publisher_breaker.get_state()
        if state == "open" and retry_after > 0:
            sleep_for = min(retry_after, _CB_SLEEP_CHUNK_SECONDS)
            await asyncio.sleep(sleep_for)
        else:
            await asyncio.sleep(interval_seconds)
