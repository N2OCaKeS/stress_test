"""Drain-сторона audit-outbox'а: доставка накопленных событий в loging_service.

Порт publisher'а из `server_worker` (`services/audit_outbox_publisher.py`)
на реалии testing_service: здесь нет собственного taskiq-воркера, поэтому
loop поднимается обычным `asyncio.create_task` в lifespan'е FastAPI —
ровно как `_log_rotation_loop` и `_activity_report_auto_generate_loop` в
`main.py`.

Механика прохода (`flush_outbox_once`):

* по одной строке за итерацию — `SELECT ... LIMIT 1 FOR UPDATE SKIP
  LOCKED` → отправка → commit → отпустили lock. Один медленный HTTP не
  держит в заложниках остальной batch, а в multi-replica деплое реплики
  разбирают непересекающиеся строки;
* порядок — `(created_at, id)`, то есть хронологический;
* строка, которую в этом проходе отправить не удалось, попадает в
  `exclude_ids` и больше в текущем batch'е не берётся: иначе одна
  нерабочая запись съедала бы весь лимит;
* успех — `published_at = now()`, `next_retry_at = NULL`;
* transient-сбой — `attempts++`, `last_error`, `next_retry_at = now() +
  2^attempts` (cap 300s), строка остаётся в очереди;
* `permanent_4xx` / `missing_action` / `attempts_cap` — DLQ: ставим
  `published_at = now()` и префиксуем `last_error` маркером
  `[DLQ:<reason>]`, чтобы строка ушла из выборки, а оператор отличил её
  от честно доставленной. ERROR в лог + счётчик `get_dlq_total()`.

Разница с `server_worker`: там перед каждым HTTP'ом дёргается shared
circuit breaker на Redis, общий на все реплики. Здесь его нет — у
testing_service нет своего breaker-модуля, а заводить его ради одного
потребителя в этой волне избыточно. Роль «не долбить лежачий сервис»
играет per-row backoff: после пары неудач строки разъезжаются по времени
и пустые проходы стоят один SELECT.

Ordering: at-least-once без строгой глобальной упорядоченности. Строки
берутся по `created_at`, но повторная попытка уводит строку в конец по
времени retry'я, поэтому событие, упавшее один раз, может доехать позже
события, эмитнутого после него. Для audit-trail это приемлемо —
`timestamp` в payload'е проставлен в момент `emit()`, loging сортирует по
нему, а не по времени приёма.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import AuditOutbox
from src.repositories import audit_outbox as repo
from src.services import audit_service
from src.services.audit_service import AuditDeliveryError

logger = logging.getLogger("audit")

# Потолок длины `last_error` — от пухлых трейсбеков в БД.
LAST_ERROR_MAX_LEN = 512

_BACKOFF_MAX_SECONDS = 300.0
# Cap показателя степени: `2 ** attempts` при сорвавшемся cap'е attempts
# сожрал бы CPU/память на bigint-арифметике. 2^16 уже далеко за
# `_BACKOFF_MAX_SECONDS`.
_BACKOFF_EXPONENT_CAP = 16

_dlq_total: int = 0


class PublishResult(NamedTuple):
    """Итог одной попытки: строка закрыта для этого прохода / реально отправлена."""

    closed: bool
    was_published: bool


def get_dlq_total() -> int:
    """Per-process счётчик строк, выброшенных в DLQ."""
    return _dlq_total


def _reset_state_for_tests() -> None:
    global _dlq_total
    _dlq_total = 0


def _max_publish_attempts() -> int:
    """Cap попыток из настроек, с безопасным фолбэком."""
    try:
        return int(get_settings().audit_outbox_max_publish_attempts)
    except Exception:  # noqa: BLE001 — конфиг не должен останавливать drain
        return 50


def _send_to_dlq(row: AuditOutbox, *, reason: str) -> None:
    """Пометить строку выброшенной: `published_at=now()` + `[DLQ:<reason>]`.

    Отдельной DLQ-таблицы нет — хватает маркера в `last_error`: строка
    уходит из выборки (по `published_at IS NOT NULL`), а причина видна и
    в ERROR-логе, и обычным SELECT'ом.
    """
    global _dlq_total
    row.published_at = datetime.now(timezone.utc)
    if not (row.last_error or "").startswith("[DLQ:"):
        row.last_error = (f"[DLQ:{reason}] " + (row.last_error or ""))[:LAST_ERROR_MAX_LEN]
    _dlq_total += 1
    logger.error(
        "audit_outbox: row sent to DLQ event=dlq reason=%s attempts=%s row=%s action=%s last_error=%s",
        reason, row.attempts, row.id, row.action, row.last_error,
    )


def _maybe_poison(row: AuditOutbox) -> bool:
    """Строка перевалила cap по attempts → DLQ. True, если выброшена."""
    if (row.attempts or 0) >= _max_publish_attempts():
        _send_to_dlq(row, reason="attempts_cap")
        return True
    return False


def _apply_backoff(row: AuditOutbox, *, hint_seconds: float | None = None) -> None:
    """`next_retry_at = now() + min(2^attempts, 300s)`, но не раньше `Retry-After`."""
    exponent = min(row.attempts or 0, _BACKOFF_EXPONENT_CAP)
    delay = min(float(2 ** exponent), _BACKOFF_MAX_SECONDS)
    if hint_seconds is not None:
        delay = max(delay, hint_seconds)
    row.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)


async def _publish_one(db: AsyncSession, row: AuditOutbox) -> PublishResult:
    """Одна попытка доставки. Commit оставляет на вызывающем.

    `closed=True` значит «в этом проходе строку больше не трогаем»: либо
    доставлена, либо выброшена в DLQ. `closed=False` — строка осталась
    unpublished, вызывающий добавляет её в exclude на этот батч.
    """
    payload = dict(row.payload or {})
    if not payload.get("action"):
        # Битый payload — ретраить нечего.
        row.last_error = "missing_action"
        _send_to_dlq(row, reason="missing_action")
        await db.flush()
        return PublishResult(closed=True, was_published=False)

    try:
        await audit_service.deliver(payload)
    except asyncio.CancelledError:
        raise
    except AuditDeliveryError as exc:
        row.attempts = (row.attempts or 0) + 1
        row.last_error = exc.message[:LAST_ERROR_MAX_LEN]
        if exc.permanent:
            # loging ответил 4xx: тем же телом ретраить бессмысленно.
            _send_to_dlq(row, reason="permanent_4xx")
            await db.flush()
            return PublishResult(closed=True, was_published=False)
        if _maybe_poison(row):
            await db.flush()
            return PublishResult(closed=True, was_published=False)
        _apply_backoff(row, hint_seconds=exc.retry_after)
        await db.flush()
        logger.warning(
            "audit_outbox: publish failed row=%s action=%s attempts=%s status=%s next_retry_at=%s",
            row.id, row.action, row.attempts, exc.status_code, row.next_retry_at,
        )
        return PublishResult(closed=False, was_published=False)
    except Exception as exc:  # noqa: BLE001 — программный сбой, не вина loging
        row.attempts = (row.attempts or 0) + 1
        row.last_error = f"{type(exc).__name__}: {exc}"[:LAST_ERROR_MAX_LEN]
        if _maybe_poison(row):
            await db.flush()
            return PublishResult(closed=True, was_published=False)
        _apply_backoff(row)
        await db.flush()
        logger.warning(
            "audit_outbox: publish errored row=%s action=%s attempts=%s err=%s",
            row.id, row.action, row.attempts, type(exc).__name__,
        )
        return PublishResult(closed=False, was_published=False)

    row.published_at = datetime.now(timezone.utc)
    row.next_retry_at = None
    await db.flush()
    return PublishResult(closed=True, was_published=True)


async def flush_outbox_once(*, limit: int | None = None) -> int:
    """Один проход: доставить до `limit` строк. Возвращает число доставленных."""
    if limit is None:
        try:
            limit = int(get_settings().audit_outbox_batch_size)
        except Exception:  # noqa: BLE001
            limit = 20

    from src.db.session import AsyncSessionLocal

    published = 0
    failed_ids: list[int] = []
    for _ in range(limit):
        async with AsyncSessionLocal() as db:
            row_id: int | None = None
            try:
                stmt = repo.select_publishable(
                    1, now=datetime.now(timezone.utc), exclude_ids=failed_ids,
                )
                row = (await db.execute(stmt)).scalars().first()
                if row is None:
                    break
                row_id = row.id
                result = await _publish_one(db, row)
                # Commit отпускает SKIP LOCKED-lock этой строки сразу, не
                # дожидаясь обработки остальных.
                await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — один сбой не валит проход
                try:
                    await db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                logger.warning("audit_outbox: flush iteration failed err=%s", exc)
                if row_id is not None:
                    failed_ids.append(row_id)
                continue
        if result.was_published:
            published += 1
        if not result.closed and row_id is not None:
            failed_ids.append(row_id)
    return published


async def cleanup_published(*, retention_hours: int | None = None) -> int:
    """Снести доставленные/DLQ-строки старше retention'а. Возвращает число удалённых."""
    if retention_hours is None:
        try:
            retention_hours = int(get_settings().audit_outbox_retention_hours)
        except Exception:  # noqa: BLE001
            retention_hours = 24

    from src.db.session import AsyncSessionLocal

    cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
    async with AsyncSessionLocal() as db:
        deleted = await repo.delete_published_older_than(db, cutoff=cutoff)
        await db.commit()
    return deleted


async def run_drain_loop(
    interval_seconds: float,
    *,
    cleanup_interval_seconds: float = 3600.0,
) -> None:
    """Бесконечный drain-loop. Поднимается `asyncio.create_task` в lifespan'е.

    Тот же приём, что у `_log_rotation_loop`: своего брокера/шедулера у
    testing_service нет, а точность тика здесь не нужна — задержка
    доставки аудита измеряется секундами.

    Сначала спим, потом работаем: на старте процесса очередь либо пуста,
    либо это хвост прошлого запуска, который подождёт один интервал.
    """
    logger.info("audit_outbox drain loop started (interval=%ss)", interval_seconds)
    loop = asyncio.get_running_loop()
    last_cleanup = loop.time()
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            await flush_outbox_once()
            if loop.time() - last_cleanup >= cleanup_interval_seconds:
                last_cleanup = loop.time()
                deleted = await cleanup_published()
                if deleted:
                    logger.info("audit_outbox: cleaned up %d delivered row(s)", deleted)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 — периодическая job не роняет процесс
            logger.warning("audit_outbox drain loop failed: %s", exc)
