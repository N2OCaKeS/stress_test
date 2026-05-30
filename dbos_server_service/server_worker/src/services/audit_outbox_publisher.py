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
После `_CB_FAILURE_THRESHOLD` подряд iteration'ов `run_publisher_loop`'а с
хотя бы одним `AuditEmitError` — открываем breaker на exponential
back-off (capped `_CB_MAX_OPEN_SECONDS`). В open-state цикл спит
короткими порциями (≤5s) и не вызывает `flush_outbox` вовсе — снимает
нагрузку с упавшего loging_service. Успешный iteration (или полностью
пустой outbox) сбрасывает счётчик. Outbox-rows при этом остаются в DB
и подхватятся, как только breaker закроется. Breaker не влияет на
inline `_safe_flush_outbox()` из `_runner.py` — там важнее latency
happy-path'а; если loging лежит, тот вызов всё равно бросит
AuditEmitError → outbox-row останется unpublished, background loop
разгребёт.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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

    Три независимых сигнала, которые caller использует по-разному, и три
    позиционных bool'а легко путаются местами — отсюда NamedTuple.

    * `closed` — row завершила свой цикл в этом проходе (опубликована, в
      DLQ или skip'нута breaker'ом). Caller не должен повторно её
      SELECT'ить в рамках текущего `_flush_outbox_once`.
    * `audit_emit_error` — была реальная HTTP-failure от loging_service
      (5xx/timeout/connect). Сигнал loop-level breaker'у.
    * `was_published` — row пометилась `published_at=NOW()` после 2xx. Не
      путать с `closed`: DLQ-row тоже `closed`, но `was_published=False`.
    """

    closed: bool
    audit_emit_error: bool
    was_published: bool


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
    перестаёт мозолить очередь. Override через env для операционных
    тестов и форс-дренажа.
    """
    try:
        return int(os.environ.get("MAX_PUBLISH_ATTEMPTS", "50"))
    except ValueError:
        return 50

# ── Circuit breaker tunables ─────────────────────────────────────────────────
# Сколько подряд iteration'ов с AuditEmitError должно случиться, чтобы
# breaker открылся. 5 = ~10s по умолчанию (poll 2s) — достаточно, чтобы
# реальный transient blip (rolling-restart loging_service) не открыл
# breaker, и достаточно мало, чтобы при серьёзной аварии не успеть
# зафлудить логи.
_CB_FAILURE_THRESHOLD = 5
# Максимум, до которого растёт open-window. 5 мин = разумный потолок: даже
# при долгой аварии loging_service остаётся возможность авто-восстановиться
# без рестарта worker'а.
_CB_MAX_OPEN_SECONDS = 300.0
# В open-state цикл спит порциями ≤ этой — чтобы при «починке» loging'а
# breaker закрылся быстро и можно было корректно остановить worker
# (cancellation на длинном sleep'е работает, но мелкими порциями нагляднее
# в логах).
_CB_SLEEP_CHUNK_SECONDS = 5.0

# ── Circuit breaker state (module-level) ─────────────────────────────────────
# Хранится в процессе worker'а; при рестарте сбрасывается — это приемлемо,
# т.к. outbox-rows остаются в DB, и новый процесс сам увидит fail'ы и
# заново откроет breaker, если loging ещё лежит. Состояние не разделяется
# между replica'ами — каждая replica наблюдает свою долю outbox-rows
# (skip-locked) и держит свой breaker.
_consecutive_failures: int = 0
_circuit_open_until: float = 0.0

# DLQ counter (monotonic, per-process). Считаем все случаи, когда row
# был отравлен (`_maybe_poison`) — и по cap'у attempts, и по 4xx-классу.
# Stub под будущую Prometheus-метрику `audit_outbox_dead_total`; пока
# доступен через `get_dlq_total()` для health-эндпоинтов и тестов.
_dlq_total: int = 0

# Backoff cap. 2^attempts растёт быстро: уже на 10 fail'ах = 1024s
# (~17 минут) между попытками, на 20 — ~12 дней. Cap'аем потолком,
# чтобы row не «уезжал» на месяцы из-за случайно высокого attempts
# (например, после ручного re-attempt'а из DLQ с не-обнулённым счётчиком).
# Cap совпадает с `_CB_MAX_OPEN_SECONDS` — после 5 минут backoff'а
# дальнейшее ожидание не имеет смысла: либо loging уже починили, либо
# proper DLQ-обработка через `internal.outbox_re_attempt`.
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


def _reset_breaker_state() -> None:
    """Test helper: вернуть breaker в closed-state + сбросить DLQ counter.

    Сбрасывает module-level globals `_consecutive_failures`,
    `_circuit_open_until` и `_dlq_total`, чтобы тесты не наследовали
    состояние от предыдущих прогонов. Альтернатива monkeypatch'у этих
    переменных.
    """
    global _consecutive_failures, _circuit_open_until, _dlq_total
    _consecutive_failures = 0
    _circuit_open_until = 0.0
    _dlq_total = 0


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
    его больше не подхватывал. last_error остаётся последним
    redacted-сообщением. Инкрементим module-level counter и пишем ERROR
    с явным `event=dlq` + причиной — operator увидит и в логах, и в
    будущей метрике `audit_outbox_dead_total`.

    Причины: `attempts_cap` (cap по attempts), `permanent_4xx` (4xx
    permanent-fail от loging_service), `missing_action` (битый payload
    без обязательного поля).
    """
    global _dlq_total
    row.published_at = datetime.now(timezone.utc)
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


def _apply_backoff(row: AuditOutbox) -> None:
    """Назначить `next_retry_at` после неуспешной попытки.

    Формула: `now() + min(2^attempts, _BACKOFF_MAX_SECONDS)` секунд.
    `attempts` уже инкрементнут к моменту вызова — берём текущее
    значение. SELECT publisher'а потом не возьмёт row, пока время не
    наступит.

    Не вызывается при поэтапной отбраковке (`missing_action`,
    `permanent_4xx`, `attempts_cap`) — там row уже закрыт через
    `_send_to_dlq` и `next_retry_at` смысла не имеет.
    """
    delay = min(2 ** row.attempts, _BACKOFF_MAX_SECONDS)
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
    except CircuitBreakerOpenError:
        # was_published=False — событие не доставлено; closed=True — caller
        # не должен повторять row в этом проходе; audit_emit_error=False —
        # это не сигнал loop-level breaker'у, тот ведёт свой счёт по
        # реальным HTTP-failure'ам.
        return PublishResult(closed=True, audit_emit_error=False, was_published=False)

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

    Inline-вызовы из `_runner._safe_flush_outbox()` намеренно НЕ проходят
    через circuit breaker — happy-path latency важнее, чем защита
    loging_service от случайного лишнего запроса; background loop сам
    открывает breaker, когда видит устойчивые fail'ы.
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

      * После `_CB_FAILURE_THRESHOLD` подряд iteration'ов, где хоть один
        publish упал с `AuditEmitError` (т.е. loging_service отверг
        событие или сеть до него лежит) — breaker открывается на
        `min(2 ** failures, 300)` секунд.
      * Пока breaker open — цикл не вызывает `_flush_outbox_once`, спит
        порциями по `_CB_SLEEP_CHUNK_SECONDS` (или меньше — если до окна
        осталось меньше).
      * Любой iteration, где не было `AuditEmitError` (включая пустой
        outbox), сбрасывает счётчик в 0 — breaker остаётся/возвращается
        в closed.
      * Программные ошибки `_publish_one` (сериализация и т.п.) breaker
        НЕ открывают: loging_service тут не виноват, добавлять задержки
        бессмысленно.
    """
    global _consecutive_failures, _circuit_open_until

    logger.info("audit_outbox publisher loop started (interval=%ss)", interval_seconds)
    while True:
        # ── Circuit breaker check ────────────────────────────────────────
        now = time.monotonic()
        if now < _circuit_open_until:
            # Breaker open — пропускаем iteration, не нагружаем loging.
            remaining = _circuit_open_until - now
            sleep_for = min(remaining, _CB_SLEEP_CHUNK_SECONDS)
            await asyncio.sleep(sleep_for)
            continue

        try:
            _published, audit_emit_errors = await _flush_outbox_once()
        except Exception as exc:  # noqa: BLE001 — never crash the loop
            # Идёт в worker stdout/journald → k8s log-aggregator. Реальные
            # клиенты (httpx/requests/asyncpg) могут зашить в текст ошибки
            # полный URL с basic-auth (`LOGGING_SERVICE_URL`,
            # `DATABASE_URL`) или Bearer-токен. Без redact это утечёт в
            # логи контейнера. Симметрично `_publish_one` (для
            # `last_error`) и `_runner.py` (для `task.last_error`).
            logger.warning(
                "audit_outbox publisher loop iteration failed: %s: %s",
                type(exc).__name__,
                redact_error_message(str(exc)),
            )
            # Catastrophic — это скорее всего проблема с DB worker'а
            # (audit_outbox недоступен), а не с loging. Breaker рассчитан
            # именно на loging_service-аварию, поэтому счётчик не трогаем;
            # просто спим обычный poll-interval.
            await asyncio.sleep(interval_seconds)
            continue

        # ── Update breaker state по результату прохода ───────────────────
        if audit_emit_errors > 0:
            _consecutive_failures += 1
            if _consecutive_failures >= _CB_FAILURE_THRESHOLD:
                # Open breaker: exponential back-off с потолком.
                back_off = min(2 ** _consecutive_failures, _CB_MAX_OPEN_SECONDS)
                _circuit_open_until = time.monotonic() + back_off
                logger.warning(
                    "audit_outbox publisher circuit breaker OPEN: "
                    "consecutive_failures=%s back_off=%ss",
                    _consecutive_failures,
                    back_off,
                )
        else:
            # Iteration без AuditEmitError → loging либо доступен, либо
            # просто очередь пуста. В обоих случаях нет повода держать
            # breaker.
            if _consecutive_failures > 0:
                logger.info(
                    "audit_outbox publisher circuit breaker RESET "
                    "(was at %s consecutive failures)",
                    _consecutive_failures,
                )
            _consecutive_failures = 0
            _circuit_open_until = 0.0

        await asyncio.sleep(interval_seconds)
