"""Dispatch outbox publisher для server_service → taskiq broker.

Закрывает race-окно между commit'ом доменной транзакции в server_service
(SET status='reinstalling', INSERT в tasks через cross-DB, INSERT строки в
dispatch_outbox — всё в одной транзакции) и публикацией сообщения в Redis.
Без outbox'а смерть процесса между commit'ом и `broker.kick` оставляла бы
БД с новым состоянием и пустую очередь — задача терялась.

Поток:

  1. Фоновый `run_publisher_loop` (поднимается из `WORKER_STARTUP` через
     `asyncio.create_task`) раз в `DISPATCH_OUTBOX_POLL_INTERVAL_SECONDS`
     открывает сессию к server_service-БД, делает `SELECT ... FOR UPDATE
     SKIP LOCKED LIMIT N` по `dispatched_at IS NULL AND (next_retry_at IS NULL
     OR next_retry_at <= now())`. Cron-scheduler не подходит — у него
     минимальная гранулярность минута.
  2. Для каждой row'ы — `broker.find_task(task_kind).kicker().kiq(task_id)`.
     Success → UPDATE dispatched_at=now() + `session.commit()` per-row.
     Failure → attempts++, last_error, next_retry_at = now() + 2^attempts
     (cap 5 минут), тоже per-row commit. Без per-row commit'а Redis-transient
     после k успешных kiq откатывал бы все k записей и приводил к их
     передиспатчу — broker.kiq доставил бы тот же task_id повторно.
  3. После `DISPATCH_OUTBOX_MAX_ATTEMPTS` row не закрываем — оставляем с
     attempts=MAX, паркуем `next_retry_at = now() + 24h`, логируем WARNING.
     Парковка нужна, чтобы row не входила в SELECT каждый poll-тик и не
     спамила лог тем же сообщением. Оператор разруливает: либо ручной
     reset attempts/next_retry_at, либо отдельная DLQ-таска в будущем.

Cleanup-task `dispatch_outbox.cleanup_old` раз в сутки удаляет успешно
dispatch'нутые row'ы старше `DISPATCH_OUTBOX_RETENTION_DAYS`. Pending не
трогается — оператор должен решить.

Multi-replica: SKIP LOCKED + per-row короткая транзакция (commit после
каждой kiq-попытки) — две реплики обрабатывают непересекающиеся подмножества.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select

from src.core.backoff import compute_retry_delay
from src.core.config import get_settings
from src.core.constants import LAST_ERROR_MAX_LEN
from src.db import dispatch_outbox_session
from src.models.dispatch_outbox import DispatchOutbox
from src.utils.redaction import redact_error_message

logger = logging.getLogger(__name__)

# Cap на показатель степени в backoff'е — защита от accidental overflow
# attempts (например, ручной reset с не-обнулённым счётчиком). 2^12 = 4096
# секунд уже выше потолка `_BACKOFF_MAX_SECONDS`, дальше всё равно режется.
_BACKOFF_EXPONENT_CAP = 12

# Потолок паузы между ретраями: 5 минут. Дальше row'ы простаивали бы слишком
# долго; реальные операционные проблемы лучше разруливать вручную, а не
# растягивать exponential до часов.
_BACKOFF_MAX_SECONDS = 300.0

# Парковка для row'ы, упёршейся в `attempts >= max_attempts`. Без неё
# `next_retry_at` остаётся в прошлом, и каждый poll-тик снова берёт row в
# SELECT, дёргает broker.find_task / kiq, пишет WARNING с тем же текстом и
# инкрементит `attempts` в бесконечность. Сутки между тиками держат лог
# читаемым (один WARNING в день), но оставляют оператору возможность сбросить
# вручную (UPDATE attempts=0, next_retry_at=NULL) — row продолжает быть
# pending, не уходит в формальный DLQ.
_CAP_REACHED_PARK_SECONDS = 24 * 3600.0


# Monotonic per-process counter row'ов, упёршихся в `attempts >= max_attempts`.
# Stub под Prometheus-метрику `dispatch_outbox_cap_reached_total` (симметрично
# `_dlq_total` / `_audit_dropped_no_api_key_total` в audit_outbox_publisher).
# Растёт обеими ветками парковки (`unknown_task_kind`, `kiq_failed`); сбрасы-
# вается только рестартом процесса либо `_reset_counters_for_tests()`.
_dispatch_outbox_cap_reached_total: int = 0


def get_dispatch_outbox_cap_reached_total() -> int:
    """Сколько row'ов publisher запарковал на +24h из-за исчерпания attempts.

    Health-check `system.heartbeat` снимает значение раз в минуту, что даёт
    оператору grep-able сигнал «park-rate растёт» без обхода БД. Резкий
    рост = либо неизвестный task_kind в server-side dispatch'е (deploy-
    drift), либо длительный сбой broker.kiq — в обоих случаях оператор
    должен разбираться вручную: parked-row сама не разболокируется.
    """
    return _dispatch_outbox_cap_reached_total


def _reset_counters_for_tests() -> None:
    """Test helper: сбросить module-level cap_reached counter."""
    global _dispatch_outbox_cap_reached_total
    _dispatch_outbox_cap_reached_total = 0


def _compute_next_retry_at(attempts: int) -> datetime:
    """Назначить `next_retry_at = now + 2^attempts` секунд, ограниченное cap'ом."""
    delay = compute_retry_delay(
        attempts,
        base=2.0,
        cap_seconds=_BACKOFF_MAX_SECONDS,
        exp_cap=_BACKOFF_EXPONENT_CAP,
    )
    return datetime.now(timezone.utc) + timedelta(seconds=delay)


def _select_pending_stmt(limit: int):
    """SELECT неотправленных outbox-row'ов с учётом backoff'а.

    `dispatched_at IS NULL` — row ещё не доставлена.
    `next_retry_at <= now()` OR NULL — backoff истёк (или ещё не выставлен).
    `FOR UPDATE SKIP LOCKED` — multi-replica защита от двойной публикации.
    """
    now = datetime.now(timezone.utc)
    return (
        select(DispatchOutbox)
        .where(
            DispatchOutbox.dispatched_at.is_(None),
            or_(
                DispatchOutbox.next_retry_at.is_(None),
                DispatchOutbox.next_retry_at <= now,
            ),
        )
        .order_by(DispatchOutbox.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


async def poll_once() -> None:
    """Один проход publisher'а: claim → kiq → mark dispatched/retry.

    Вызывается из `run_publisher_loop` каждые
    `DISPATCH_OUTBOX_POLL_INTERVAL_SECONDS`. Если `SERVER_SERVICE_DATABASE_URL`
    пустой — выходим, нечего читать.
    """
    global _dispatch_outbox_cap_reached_total
    # Локальный импорт broker'а — symmetric с `_runner._schedule_retry`,
    # чтобы при импорте `src.tasks.dispatch_outbox` из `src.tasks/__init__.py`
    # не было циклического импорта (broker создаётся в main.py после
    # импорта тасок).
    from src.main import broker

    settings = get_settings()
    factory = dispatch_outbox_session.get_session_factory()
    if factory is None:
        # local/dev сценарий — server_service-БД не сконфигурирована.
        # Periodic должен молча no-op'ить, не крэшить scheduler-loop.
        logger.debug(
            "dispatch_outbox.poll: SERVER_SERVICE_DATABASE_URL not set, skipping"
        )
        return

    limit = settings.dispatch_outbox_batch_size
    max_attempts = settings.dispatch_outbox_max_attempts
    dispatched_total = 0
    failed_total = 0
    cap_reached = 0

    rows_total = 0
    try:
        async with factory() as session:
            stmt = _select_pending_stmt(limit)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            rows_total = len(rows)

            # Per-row commit: каждая успешная kiq + UPDATE фиксируется
            # отдельной транзакцией. Без этого Redis-transient после k
            # успешных kiq откатывал бы все k row'ов и приводил к их
            # передиспатчу при следующем тике (broker.kiq не идемпотентна
            # на уровне consumer'а: тот же task_id ушёл бы в очередь дважды).
            # FOR UPDATE SKIP LOCKED, полученный при SELECT, держится до
            # первого commit'а — после него locks отпускаются и другая
            # реплика теоретически могла бы зацепить ту же row'у. Защита
            # от двойной публикации остаётся на уровне `dispatched_at IS NULL`
            # фильтра в SELECT: после commit'а первой row'ы её
            # `dispatched_at` уже выставлен, и параллельный SELECT её
            # пропустит. Pending row'и из текущего батча остаются
            # уязвимы между commit'ами — это сознательный trade-off ради
            # at-most-once на успешных kiq.
            for row in rows:
                task = broker.find_task(row.task_kind)
                if task is None:
                    # Неизвестный task_kind: либо deploy-drift (worker устарел),
                    # либо row пришла из будущего. Не пытаемся ретраить — это
                    # не починится временем. attempts++, last_error, backoff;
                    # после cap'а — WARNING-pin.
                    row.attempts = (row.attempts or 0) + 1
                    row.last_error = (
                        f"unknown task_kind: {row.task_kind!r}"
                    )[:LAST_ERROR_MAX_LEN]
                    if row.attempts >= max_attempts:
                        cap_reached += 1
                        _dispatch_outbox_cap_reached_total += 1
                        # Парковка до +24h: row остаётся pending, но не входит
                        # в SELECT каждый poll-тик. Оператор увидит её в админке
                        # и решит — ручной reset либо ждать до следующего парка.
                        row.next_retry_at = datetime.now(timezone.utc) + timedelta(
                            seconds=_CAP_REACHED_PARK_SECONDS,
                        )
                        logger.warning(
                            "dispatch_outbox.poll: event=dispatch_park row=%s "
                            "task_id=%s task_kind=%s reason=unknown_task_kind "
                            "attempts_cap=%s — parking next_retry_at=+24h, "
                            "operator must reset",
                            row.id,
                            row.task_id,
                            row.task_kind,
                            max_attempts,
                        )
                    else:
                        row.next_retry_at = _compute_next_retry_at(row.attempts)
                    failed_total += 1
                    try:
                        await session.commit()
                    except Exception as commit_exc:  # noqa: BLE001
                        await session.rollback()
                        redacted = redact_error_message(
                            f"{type(commit_exc).__name__}: {commit_exc}"
                        )
                        logger.warning(
                            "dispatch_outbox.poll: commit failed for row=%s: %s",
                            row.id, redacted,
                        )
                    continue

                try:
                    await task.kicker().kiq(row.task_id)
                except Exception as exc:  # noqa: BLE001 — publisher не должен падать
                    row.attempts = (row.attempts or 0) + 1
                    redacted = redact_error_message(
                        f"{type(exc).__name__}: {exc}"
                    )
                    row.last_error = redacted[:LAST_ERROR_MAX_LEN]
                    if row.attempts >= max_attempts:
                        cap_reached += 1
                        _dispatch_outbox_cap_reached_total += 1
                        row.next_retry_at = datetime.now(timezone.utc) + timedelta(
                            seconds=_CAP_REACHED_PARK_SECONDS,
                        )
                        logger.warning(
                            "dispatch_outbox.poll: event=dispatch_park row=%s "
                            "task_id=%s task_kind=%s reason=kiq_failed "
                            "attempts_cap=%s last_error=%s — parking "
                            "next_retry_at=+24h, operator must reset",
                            row.id,
                            row.task_id,
                            row.task_kind,
                            max_attempts,
                            redacted,
                        )
                    else:
                        row.next_retry_at = _compute_next_retry_at(row.attempts)
                    failed_total += 1
                    try:
                        await session.commit()
                    except Exception as commit_exc:  # noqa: BLE001
                        await session.rollback()
                        redacted_commit = redact_error_message(
                            f"{type(commit_exc).__name__}: {commit_exc}"
                        )
                        logger.warning(
                            "dispatch_outbox.poll: commit failed for row=%s: %s",
                            row.id, redacted_commit,
                        )
                    continue

                row.dispatched_at = datetime.now(timezone.utc)
                # Очищаем backoff/ошибку на случай предыдущих fail-попыток —
                # в логе/админке row'и видна как чистый success.
                row.next_retry_at = None
                row.last_error = None
                try:
                    await session.commit()
                except Exception as commit_exc:  # noqa: BLE001
                    # Commit упал после успешного kiq — broker уже принял
                    # task_id, а БД не зафиксировала dispatched_at. Следующий
                    # тик увидит row pending и kiq'нет повторно. Пишем WARNING
                    # для оператора, но в нашу статистику row числится как
                    # failed, а не dispatched (потому что в БД ничего не легло).
                    await session.rollback()
                    redacted = redact_error_message(
                        f"{type(commit_exc).__name__}: {commit_exc}"
                    )
                    logger.warning(
                        "dispatch_outbox.poll: kiq succeeded but commit failed "
                        "for row=%s task_id=%s — row will be redispatched: %s",
                        row.id, row.task_id, redacted,
                    )
                    failed_total += 1
                    continue
                dispatched_total += 1
    except Exception as exc:  # noqa: BLE001 — periodic не должен крэшить scheduler
        redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
        logger.warning(
            "dispatch_outbox.poll: tick failed: %s", redacted
        )
        return

    if dispatched_total or failed_total:
        logger.info(
            "dispatch_outbox.poll: dispatched=%s failed=%s cap_reached=%s "
            "batch=%s",
            dispatched_total,
            failed_total,
            cap_reached,
            rows_total,
        )


async def run_publisher_loop() -> None:
    """Бесконечный фоновый loop: poll every N seconds.

    Поднимается через `asyncio.create_task` в `WORKER_STARTUP` хуке. Cron
    из taskiq-scheduler'а не подходит — минимальная гранулярность cron
    составляет минуту, а нам нужен ≤2-секундный latency после commit'а
    server_service-транзакции (иначе UI «зависание dispatched task'и»
    видно глазами).

    Cancellation: `asyncio.CancelledError` от worker shutdown пробрасываем,
    остальные exception'ы ловим — loop никогда не должен крэшить процесс.
    """
    settings = get_settings()
    interval = settings.dispatch_outbox_poll_interval_seconds
    logger.info(
        "dispatch_outbox publisher loop started (interval=%ss)", interval
    )
    while True:
        try:
            await poll_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never crash the loop
            redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
            logger.warning(
                "dispatch_outbox publisher loop iteration failed: %s",
                redacted,
            )
        await asyncio.sleep(interval)


async def cleanup_old() -> None:
    """Daily retention: дропаем dispatched outbox-row'ы старше N дней.

    Pending (`dispatched_at IS NULL`) не трогаем — они in-flight либо
    залипли в DLQ-style по `attempts=MAX`; решение по ним за оператором.

    Audit emit пропускаем — административная housekeeping (как у
    `audit_outbox.cleanup_published_old`). Ошибки логируются и НЕ
    пробрасываются.
    """
    settings = get_settings()
    factory = dispatch_outbox_session.get_session_factory()
    if factory is None:
        logger.debug(
            "dispatch_outbox.cleanup_old: SERVER_SERVICE_DATABASE_URL not set, skipping"
        )
        return

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.dispatch_outbox_retention_days
    )
    try:
        async with factory() as session:
            stmt = delete(DispatchOutbox).where(
                DispatchOutbox.dispatched_at.is_not(None),
                DispatchOutbox.dispatched_at < cutoff,
            )
            result = await session.execute(stmt)
            await session.commit()
            deleted = result.rowcount or 0
    except Exception as exc:  # noqa: BLE001
        redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
        logger.warning("dispatch_outbox.cleanup_old failed: %s", redacted)
        return

    if deleted:
        logger.info(
            "dispatch_outbox.cleanup_old: dropped %s dispatched row(s) "
            "older than %s",
            deleted,
            cutoff.isoformat(),
        )
