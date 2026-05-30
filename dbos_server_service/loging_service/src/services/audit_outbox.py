"""In-memory outbox для self-audit событий loging_service.

Раньше `main._emit_audit` каждый http.*-эмит средневой `audit_access` запускал
через `asyncio.ensure_future(asyncio.to_thread(_emit_audit, ...))`. Каждый
такой taск открывал свой `SessionLocal()` и закрывал на финализации. Под
всплеском admin/reader-трафика (например, шторм 401 от перебора токена) это
насыщало пул SQLAlchemy (`pool_size=10, max_overflow=20`) и блокировало
основные read-эндпоинты на ожидании коннекта.

Outbox-паттерн: `push_nowait()` кладёт payload во внутреннюю `asyncio.Queue`
без обращения к БД. Фоновая корутина `drain_loop()`, поднятая в lifespan'е,
выгребает события батчами и пишет их одной транзакцией под одним pooled
коннектом. Bounded buffer — при переполнении старейший элемент дропается,
счётчик `dropped_total` инкрементится, чтобы факт потери не маскировался.

Sync-однострочник `_emit_audit` остаётся (его дёргают тесты и retention
sweep с собственной сессией), но горячий путь middleware теперь идёт через
очередь.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from src.core.constants import VALID_ACTOR_TYPES

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuditEnvelope:
    """Снапшот self-audit события для очереди.

    Намеренно не `EventCreate`: pydantic-валидация payload'а делается уже на
    drain'е, чтобы push_nowait был полностью non-blocking — middleware не
    должен платить за валидацию в hot-path'е.
    """

    action: str
    actor_id: str | None
    actor_type: str | None
    username: str | None
    emit_status: str
    allowed: bool
    request_id: str | None
    details: dict
    enqueued_at: datetime


class AuditOutbox:
    """Bounded async-очередь self-audit событий + батчевый drain.

    Лайфцикл:
      * `start(loop)` — создаёт `asyncio.Queue(maxsize=max_size)` на указанной
        event loop и поднимает background `drain_loop`-task.
      * `push_nowait(envelope)` — non-blocking enqueue. При переполнении
        дропает старейший элемент и инкрементит `dropped_total`. Может
        вызываться из любого корутинного контекста на той же event loop.
      * `stop(timeout)` — отменяет drain-task'у и финально выгребает буфер
        под выделенным бюджетом времени (graceful shutdown).

    Drain хранит подключение к БД минимально: одна `SessionLocal()` на батч,
    `record_admin_action(commit=False)` под `db.begin_nested()` для каждого
    события, единый `db.commit()` в конце. Битое событие откатывает только
    свой savepoint и инкрементит `_bump_failure`, остальной батч долетает.
    """

    def __init__(
        self,
        *,
        max_size: int,
        batch_size: int,
        poll_interval_seconds: float,
        session_factory: Callable[[], Session],
        writer: Callable[[Session, AuditEnvelope], None],
        bump_failure: Callable[[], int],
    ) -> None:
        if max_size <= 0:
            raise ValueError("audit outbox max_size must be > 0")
        if batch_size <= 0:
            raise ValueError("audit outbox batch_size must be > 0")
        if poll_interval_seconds <= 0:
            raise ValueError("audit outbox poll_interval must be > 0")

        self._max_size = max_size
        self._batch_size = batch_size
        self._poll_interval = poll_interval_seconds
        self._session_factory = session_factory
        self._writer = writer
        self._bump_failure = bump_failure

        self._queue: asyncio.Queue[AuditEnvelope] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._drain_task: asyncio.Task | None = None
        self._stopping = False

        # Счётчики — под `threading.Lock`, чтобы тесты могли читать их с
        # другого треда (ThreadPoolExecutor) без data race на `+=`. Симметрия
        # с `self_audit_failures_total` в `main.py`.
        self._counters_lock = threading.Lock()
        self._dropped_total = 0
        self._drained_total = 0

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Создаёт очередь и поднимает drain-task на event loop'е."""
        if self._queue is not None:
            return
        self._loop = loop or asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self._max_size)
        self._stopping = False
        self._drain_task = self._loop.create_task(
            self._drain_loop(), name="audit_outbox_drain"
        )

    async def stop(self, *, timeout: float) -> None:
        """Останавливает drain, выгребает остаток буфера под бюджет."""
        if self._queue is None:
            return
        self._stopping = True
        task = self._drain_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                # drain-task мог упасть — это уже залогировано внутри loop'а.
                pass
        # Финальный выгреб того, что осталось в очереди.
        await self._drain_remaining(timeout=timeout)
        self._drain_task = None
        self._queue = None
        self._loop = None

    # ── enqueue ──────────────────────────────────────────────────────────

    def push_nowait(self, envelope: AuditEnvelope) -> bool:
        """Non-blocking enqueue. Возвращает True, если событие принято.

        При переполнении буфера сбрасываем самый старый элемент, чтобы
        свежее событие имело шанс попасть на drain. Старейшее обычно
        наименее ценно (его уже могли успеть отдать) — и это лучше, чем
        отбрасывать свежее, которое атакующий тут же мог бы использовать
        как «событие исчезло, audit не сработал». Counter `_dropped_total`
        растёт ровно один раз на каждое потерянное событие.
        """
        queue = self._queue
        if queue is None:
            # Сервис ещё не запустил lifespan (или уже остановил). На случай
            # in-process тестов фолбэчимся в синхронный writer прямо здесь —
            # это та же семантика, что у старого `to_thread(_emit_audit)`.
            db = self._session_factory()
            try:
                try:
                    self._writer(db, envelope)
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    self._bump_failure()
                    logger.error(
                        "audit outbox fallback write failed: %s", exc, exc_info=True
                    )
            finally:
                db.close()
            return False

        try:
            queue.put_nowait(envelope)
            return True
        except asyncio.QueueFull:
            pass

        # Полный буфер — освобождаем место. `get_nowait` гарантированно не
        # блокирует: один-в-один с `QueueFull` означает len == maxsize.
        try:
            queue.get_nowait()
            queue.task_done()
        except asyncio.QueueEmpty:
            # Гонка: drain успел выгрести между нашими ветками. Просто
            # пробуем повторить put.
            pass
        with self._counters_lock:
            self._dropped_total += 1
        try:
            queue.put_nowait(envelope)
            return True
        except asyncio.QueueFull:
            # Совсем не повезло: drain не успевает, или конкурирующий push
            # снова забил слот. Возвращаем False, чтобы caller знал, что
            # событие не сохранено.
            with self._counters_lock:
                self._dropped_total += 1
            return False

    # ── drain ────────────────────────────────────────────────────────────

    async def _drain_loop(self) -> None:
        """Фоновая корутина: ждёт элемент → выгребает батч → пишет."""
        assert self._queue is not None
        try:
            while not self._stopping:
                try:
                    first = await self._queue.get()
                except asyncio.CancelledError:
                    raise
                batch = [first]
                # Добираем до batch_size, не блокируя — то, что пришло
                # одновременно, едет одной транзакцией.
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                try:
                    await self._flush_batch(batch)
                except asyncio.CancelledError:
                    # `stop()` отменил задачу прямо во время flush — батч уже
                    # выдернут из очереди, но в БД может быть не записан
                    # (или записан частично — `_write_batch_sync` либо
                    # коммитнулся целиком, либо ничего; `to_thread` отменить
                    # на полпути нельзя). Возвращаем events обратно в
                    # очередь, чтобы `_drain_remaining` подобрал их под
                    # shutdown-бюджет. Если очередь уже не помещает —
                    # считаем потерянными и инкрементим `_dropped_total`,
                    # чтобы факт потери не маскировался.
                    requeued = 0
                    for envelope in batch:
                        try:
                            self._queue.put_nowait(envelope)
                            requeued += 1
                        except asyncio.QueueFull:
                            break
                    lost = len(batch) - requeued
                    if lost:
                        with self._counters_lock:
                            self._dropped_total += lost
                    raise
                # Маленькая пауза, чтобы не молотить процессор, если queue
                # пуст. `asyncio.Queue.get()` сам await'ит до появления
                # элемента — пауза нужна только под нагрузкой как back-pressure
                # против drain-too-eager (батчи в 1 элемент).
                if self._poll_interval > 0:
                    await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            # Грейсфул-shutdown: остаток допишет `stop()` через
            # `_drain_remaining`. Здесь только тихий выход.
            return
        except Exception as exc:
            # Drain-loop НИКОГДА не должен умереть из-за одного битого
            # события — `_flush_batch` уже ловит per-row. Если мы тут —
            # это сломалась сама очередь или session factory; логируем и
            # выходим, чтобы lifespan мог переподнять при следующем start'е.
            logger.error("audit outbox drain loop crashed: %s", exc, exc_info=True)

    async def _drain_remaining(self, *, timeout: float) -> None:
        """Финальный выгреб очереди в shutdown'е, не дольше `timeout`."""
        if self._queue is None:
            return
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        while True:
            try:
                batch: list[AuditEnvelope] = []
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                if not batch:
                    return
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    lost = len(batch) + self._queue.qsize()
                    if lost:
                        with self._counters_lock:
                            self._dropped_total += lost
                        logger.warning(
                            "audit outbox shutdown drain timed out, %d events lost",
                            lost,
                        )
                    return
                await self._flush_batch(batch)
            except Exception as exc:
                logger.error(
                    "audit outbox shutdown drain failed: %s", exc, exc_info=True
                )
                return

    async def _flush_batch(self, batch: list[AuditEnvelope]) -> None:
        """Пишет батч одной транзакцией в `asyncio.to_thread`."""
        if not batch:
            return
        try:
            succeeded = await asyncio.to_thread(self._write_batch_sync, batch)
            # `succeeded` — сколько savepoint'ов закоммитилось. Failures за
            # отказавшие savepoint'ы уже забампил `_write_batch_sync` через
            # `_bump_failure`, отдельно не считаем. Инвариант:
            # `drained + failures = enqueued`.
            with self._counters_lock:
                self._drained_total += succeeded
        except Exception as exc:
            # Катастрофа на уровне сессии (БД лежит / пул пуст). Все события
            # батча — потерянные, бампим failure-counter за каждое.
            for _ in batch:
                self._bump_failure()
            logger.error(
                "audit outbox batch write failed (%d events): %s",
                len(batch), exc, exc_info=True,
            )

    def _write_batch_sync(self, batch: list[AuditEnvelope]) -> int:
        """Sync-часть flush'а: одна сессия, savepoint на событие, один commit.

        `record_admin_action(commit=False)` живёт в общей транзакции, каждый
        вызов обёрнут в `begin_nested()` — savepoint откатывает только своё
        событие, не валит весь батч. На финале — `db.commit()`. Если падает
        savepoint, бампим self-audit-failure counter (та же семантика, что
        у старого `_emit_audit`).

        Возвращает число успешно записанных событий — caller использует это
        значение для `_drained_total`, чтобы partial-failure не приводил к
        перерасчёту `drained + failures > enqueued`.
        """
        db = self._session_factory()
        succeeded = 0
        try:
            for envelope in batch:
                try:
                    with db.begin_nested():
                        self._writer(db, envelope)
                    succeeded += 1
                except Exception as exc:
                    self._bump_failure()
                    logger.error(
                        "self-audit failed (action=%s): %s",
                        envelope.action, exc, exc_info=True,
                    )
            db.commit()
        finally:
            db.close()
        return succeeded

    # ── introspection ────────────────────────────────────────────────────

    def qsize(self) -> int:
        return self._queue.qsize() if self._queue is not None else 0

    def dropped_total(self) -> int:
        with self._counters_lock:
            return self._dropped_total

    def drained_total(self) -> int:
        with self._counters_lock:
            return self._drained_total

    def reset_counters_for_tests(self) -> None:
        """Только для тестов: обнуляет dropped/drained."""
        with self._counters_lock:
            self._dropped_total = 0
            self._drained_total = 0


def make_envelope(
    *,
    action: str,
    actor_id: str | None,
    actor_type: str | None,
    username: str | None,
    emit_status: str,
    allowed: bool,
    request_id: str | None,
    details: dict,
) -> AuditEnvelope:
    """Помощник: фиксирует `enqueued_at` в момент создания envelope'а."""
    return AuditEnvelope(
        action=action,
        actor_id=actor_id,
        actor_type=actor_type,
        username=username,
        emit_status=emit_status,
        allowed=allowed,
        request_id=request_id,
        details=details,
        enqueued_at=datetime.now(timezone.utc),
    )


def write_envelope_to_db(db: Session, envelope: AuditEnvelope) -> Any:
    """Writer-функция по умолчанию: envelope → EventCreate → record_admin_action.

    Вынесена сюда, а не в `main.py`, чтобы тесты могли подменить writer без
    импорта `main` (циклы пакетного импорта).
    """
    from src.schemas.events import EventCreate
    from src.services.event_service import record_admin_action

    # `actor_type` whitelist — повторяем семантику старого `_emit_audit`.
    if envelope.actor_type in VALID_ACTOR_TYPES:
        resolved_actor_type = envelope.actor_type
    else:
        resolved_actor_type = "anonymous"

    payload = EventCreate(
        timestamp=envelope.enqueued_at,
        service="loging_service",
        action=envelope.action,
        actor_id=envelope.actor_id,
        actor_type=resolved_actor_type,
        username=envelope.username,
        status=envelope.emit_status,
        allowed=envelope.allowed,
        request_id=envelope.request_id,
        details=envelope.details,
    )
    return record_admin_action(db, payload, commit=False)
