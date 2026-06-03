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
счётчик потерь инкрементится по причине, чтобы факт потери не маскировался
и cause легко разнести в метриках.

Семантика трёх dropped_*_total counter'ов:

* ``dropped_overflow_total`` — буфер переполнен на `push_nowait`. Старейший
  envelope выкидывается, чтобы освободить слот для свежего (свежее событие
  потенциально важнее: атакующий мог бы рассчитывать «event исчез, audit не
  сработал»). Признак того, что drain не успевает за входящим трафиком:
  либо drain-loop повис на медленной БД, либо batch_size/poll_interval
  настроены слишком консервативно, либо app под DoS-нагрузкой.
* ``dropped_cancel_total`` — drain-task отменён (`stop()`-call или
  CancelledError на event loop'е), батч уже выдернут из очереди, но БД не
  успела принять commit. То, что не закоммитилось и не вернулось в очередь,
  уходит сюда. Если очередь во время requeue была переполнена — события
  идут в ``dropped_overflow_total``, потому что это про насыщение буфера,
  а не про cancel.
* ``dropped_shutdown_total`` — финальный `_drain_remaining` не уложился в
  бюджет `stop(timeout=...)`. Признак того, что shutdown grace period
  слишком короткий для текущего буфера, либо БД-writes стали в разы
  медленнее обычного. SIEM по росту именно этого counter'а отличает
  «не успели на graceful shutdown» от «не успеваем под нагрузкой».

Геттеры `dropped_overflow_total()` / `dropped_cancel_total()` /
`dropped_shutdown_total()` — публичные; ожидаются к экспозу через будущий
`/metrics`, до тех пор читаются тестами напрямую через инстанс outbox'а.
Агрегата нет специально: SIEM по одному числу не отличит DoS-перегрузку от
рваного shutdown'а — суммирование делается на стороне дашборда.

Sync-однострочник `_emit_audit` остаётся (его дёргают тесты и retention
sweep с собственной сессией), но горячий путь middleware теперь идёт через
очередь.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from src.core.constants import VALID_ACTOR_TYPES

logger = logging.getLogger(__name__)


class _BatchCommitFailed(Exception):
    """Маркер: outer-tx упала ПОСЛЕ того, как `_write_batch_sync` уже
    вызвал `_bump_failure` по одному разу на каждое событие батча.

    `_flush_batch` ловит этот тип отдельно, чтобы не бампить failure-counter
    второй раз: иначе savepoint-failed envelope получает 2 bump'а (один — на
    savepoint-rollback, второй — в общем `for _ in batch`), и метрики
    self-audit_failures + DLQ-threshold двоятся.
    """


@dataclass(frozen=True, slots=True)
class AuditEnvelope:
    """Снапшот self-audit события для очереди.

    Намеренно не `EventCreate`: pydantic-валидация payload'а делается уже на
    drain'е, чтобы push_nowait был полностью non-blocking — middleware не
    должен платить за валидацию в hot-path'е.

    `idempotency_key` — UUID4, генерится в `make_envelope`. Используется как
    DB-level dedup-якорь, чтобы graceful-shutdown / requeue не записал
    envelope дважды: партиционный UNIQUE индекс
    `uq_audit_events_service_idempotency_key` отбрасывает второй INSERT,
    даже если `_drain_remaining` после crash-loop повторно дёрнет writer.
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
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)


class AuditOutbox:
    """Bounded async-очередь self-audit событий + батчевый drain.

    Лайфцикл:
      * `start(loop)` — создаёт `asyncio.Queue(maxsize=max_size)` на указанной
        event loop и поднимает background `drain_loop`-task.
      * `push_nowait(envelope)` — non-blocking enqueue. При переполнении
        дропает старейший элемент и инкрементит `dropped_overflow_total`.
        Может вызываться из любого корутинного контекста на той же event loop.
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
        #
        # Потери разнесены по причинам: overflow — буфер переполнен,
        # cancel — drain отменён уже забравшим батчем, shutdown — финальный
        # drain не уложился в бюджет `stop(timeout=...)`. Из warning-логов
        # cause раньше восстанавливался руками.
        self._counters_lock = threading.Lock()
        self._dropped_overflow_total = 0
        self._dropped_cancel_total = 0
        self._dropped_shutdown_total = 0
        self._drained_total = 0

        # Сериализуем sync-writer'ы между `_drain_loop` и `_drain_remaining`.
        # Тонкость: `await asyncio.to_thread(...)` при `CancelledError` отдаёт
        # управление наверх, НО поток-исполнитель в ThreadPoolExecutor
        # продолжает крутить `_write_batch_sync` до конца — `db.commit()` ещё
        # может выстрелить. Если в этот момент `stop()` уже зовёт
        # `_drain_remaining`, тот открывает свою сессию и пишет в ту же
        # таблицу параллельно. Партиционный UNIQUE по
        # `(service, idempotency_key)` от прямого дубля защитит, но конкурентные
        # INSERT'ы из двух сессий всё равно дают шанс на serialization-failure
        # и на partial-commit, который ломает учёт `committed_keys`.
        # threading.Lock держится внутри sync-target'а на всё время сессии,
        # так что второй writer ждёт даже если async-await первого уже отдал
        # CancelledError наверх.
        self._writer_lock = threading.Lock()

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
        как «событие исчезло, audit не сработал». Counter
        `_dropped_overflow_total` растёт ровно один раз на каждое потерянное
        событие из-за переполнения буфера.
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

        # Полный буфер — пробуем освободить слот. Если drain опередил
        # eviction (QueueEmpty), реальной потери не было: повторный put
        # должен пройти, и счётчик трогать не нужно — иначе over-count.
        evicted = False
        try:
            queue.get_nowait()
            queue.task_done()
            evicted = True
        except asyncio.QueueEmpty:
            pass

        if evicted:
            with self._counters_lock:
                self._dropped_overflow_total += 1

        try:
            queue.put_nowait(envelope)
            return True
        except asyncio.QueueFull:
            # Конкурирующий push занял освобождённый слот; нашего envelope'а
            # не приняли. Бампим один раз именно за этот промах.
            with self._counters_lock:
                self._dropped_overflow_total += 1
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
                # `committed_keys` — общий между корутиной и thread-target'ом
                # set. `_write_batch_sync` ПОСЛЕ успешного `db.commit()` кладёт
                # туда `envelope.idempotency_key` каждого закоммитнутого
                # события. Если в этот момент прилетает `CancelledError`
                # (между returning из `to_thread` и `_drained_total +=`),
                # мы по этому set'у узнаём, какие envelope'ы УЖЕ в БД, и не
                # дублируем их при requeue. Идэмпотенси-ключ предпочтительнее
                # `id(envelope)` — он переживает GC и совпадает с DB-level
                # dedup-якорем (партиционный UNIQUE по
                # `(service, idempotency_key)`), что делает инвариант
                # "не пишем тот же ключ дважды" более явным.
                committed_keys: set[str] = set()
                try:
                    await self._flush_batch(batch, committed_keys)
                except asyncio.CancelledError:
                    # `stop()` отменил задачу прямо во время flush. Cancel —
                    # сам по себе НЕ повод считать потерю: что мы успели
                    # закоммитить, лежит в `committed_keys`, а всё остальное
                    # пробуем вернуть в очередь под `_drain_remaining`.
                    # Requeue вынесен в `_account_cancelled_batch`, чтобы
                    # QueueFull-при-requeue (растит `_dropped_overflow_total`,
                    # счётчик `overflow_lost`) и чистая cancel-потеря
                    # (`_dropped_cancel_total`) не пересекались в одном
                    # except-блоке — иначе перегруз буфера маскировался под
                    # «грубый shutdown».
                    self._account_cancelled_batch(batch, committed_keys)
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

    def _account_cancelled_batch(
        self,
        batch: list[AuditEnvelope],
        committed_keys: set[str],
    ) -> None:
        """Учёт батча, отменённого `CancelledError` во время flush.

        Что закоммитилось (по `committed_keys`) — учитываем в `drained_total`,
        как при нормальном пути. Остальное возвращаем в очередь через
        `put_nowait`; QueueFull здесь означает, что буфер уже забит
        конкурирующими push'ами (`push_nowait` продолжает принимать события
        прямо до конца cancel'а) — это потеря по причине overflow, не cancel,
        бампим `_dropped_overflow_total`. Истинный cancel-loss (события, не
        закоммитнутые и НЕ влезшие в overflow-учёт) уезжает в
        `_dropped_cancel_total` — резерв на случай, если в будущем появится
        ветка «пропустить envelope без requeue».
        """
        assert self._queue is not None
        requeued = 0
        overflow_lost = 0
        skipped_committed = 0
        for envelope in batch:
            if envelope.idempotency_key in committed_keys:
                skipped_committed += 1
                continue
            try:
                self._queue.put_nowait(envelope)
                requeued += 1
            except asyncio.QueueFull:
                overflow_lost += 1
        if skipped_committed:
            logger.warning(
                "audit outbox requeue skipped %d envelope(s) already committed",
                skipped_committed,
            )
        cancel_lost = len(batch) - skipped_committed - requeued - overflow_lost
        if overflow_lost > 0 or cancel_lost > 0:
            with self._counters_lock:
                if overflow_lost > 0:
                    self._dropped_overflow_total += overflow_lost
                if cancel_lost > 0:
                    self._dropped_cancel_total += cancel_lost
        if skipped_committed:
            with self._counters_lock:
                self._drained_total += skipped_committed

    async def _drain_remaining(self, *, timeout: float) -> None:
        """Финальный выгреб очереди в shutdown'е, не дольше `timeout`.

        Deadline-check сделан ПОСЛЕ выдёргивания батча, но ДО `_flush_batch`:
        если бюджет уже исчерпан, события уезжают в `_dropped_shutdown_total`
        вместо того, чтобы инициировать flush, который заведомо не успеет
        и упадёт в `CancelledError` ветку. Это даёт детерминированный учёт
        потерь по причине «таймаут» отдельно от «жёсткий cancel» — SIEM по
        этим двум counter'ам отличает «выделили мало времени» от «forced
        kill во время flush'а». Цена: один уже выдёрнутый из очереди батч
        (до `batch_size` events) считается потерянным, даже если БД мгновенно
        бы его приняла — но мы и так в shutdown'е, дополнительные ~poll_interval
        мс держать процесс смысла нет.
        """
        if self._queue is None:
            return
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        while True:
            batch: list[AuditEnvelope] = []
            # Per-batch shared set, в который `_write_batch_sync` положит
            # `envelope.idempotency_key` каждого events, для которого
            # `db.commit()` прошёл успешно. Если `CancelledError` прилетит
            # между `to_thread` returning и обновлением счётчиков, мы по
            # этому set'у узнаём, какие envelope'ы УЖЕ в БД, и не считаем
            # их как `_dropped_shutdown_total`.
            committed_keys: set[str] = set()
            try:
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
                            self._dropped_shutdown_total += lost
                        logger.warning(
                            "audit outbox shutdown drain timed out, %d events lost",
                            lost,
                        )
                    return
                await self._flush_batch(batch, committed_keys)
            except asyncio.CancelledError:
                # Force-cancel во время финального drain'а: батч уже выдернут
                # из очереди и в `_flush_batch` мог не дойти до commit'а. Без
                # явного учёта эти события молча терялись бы, нарушая инвариант
                # `enqueued = drained + failures + dropped`. `CancelledError`
                # это `BaseException`, generic `except Exception` ниже его не
                # ловит — отдельная ветка ОБЯЗАТЕЛЬНА перед re-raise.
                #
                # Если sync-target успел `db.commit()` до отмены, эти envelope'ы
                # уже в `committed_keys`: вычитаем их из `lost`, иначе тот же
                # row уехал бы и в `drained_total` (через `_flush_batch`), и
                # в `dropped_shutdown_total` — двойной учёт ломал бы инвариант
                # выше.
                lost = len(batch) - len(committed_keys)
                if self._queue is not None:
                    lost += self._queue.qsize()
                if lost > 0:
                    with self._counters_lock:
                        self._dropped_shutdown_total += lost
                    logger.warning(
                        "audit outbox shutdown drain cancelled, %d events lost",
                        lost,
                    )
                raise
            except Exception as exc:
                logger.error(
                    "audit outbox shutdown drain failed: %s", exc, exc_info=True
                )
                return

    async def _flush_batch(
        self,
        batch: list[AuditEnvelope],
        committed_keys: set[str] | None = None,
    ) -> None:
        """Пишет батч одной транзакцией в `asyncio.to_thread`.

        `committed_keys` — опциональный shared set, который `_write_batch_sync`
        заполняет `envelope.idempotency_key`'ами ПОСЛЕ `db.commit()`. Нужен
        `_drain_loop`'у, чтобы при `CancelledError` между `to_thread`
        returning и `_drained_total +=` не requeue'ить уже закоммитнутые
        события.
        """
        if not batch:
            return
        if committed_keys is None:
            committed_keys = set()
        try:
            succeeded = await asyncio.to_thread(
                self._write_batch_sync, batch, committed_keys
            )
            # `succeeded` — сколько savepoint'ов закоммитилось. Failures за
            # отказавшие savepoint'ы уже забампил `_write_batch_sync` через
            # `_bump_failure`, отдельно не считаем. Инвариант:
            # `drained + failures = enqueued`.
            with self._counters_lock:
                self._drained_total += succeeded
        except _BatchCommitFailed as exc:
            # outer-tx упала; `_write_batch_sync` уже забампил `_bump_failure`
            # ровно по одному разу на каждое событие батча (savepoint-failed
            # + остальные в commit-fail ветке). Здесь — только лог, без
            # повторного bump'а, иначе дублируем метрику и DLQ-threshold.
            logger.error(
                "audit outbox batch write failed (%d events): %s",
                len(batch), exc.__cause__, exc_info=exc.__cause__,
            )
        except Exception as exc:
            # Катастрофа ДО commit'а (session_factory упала, to_thread сам
            # не смог стартовать) — bump'ов в sync-части не было, бампим тут.
            for _ in batch:
                self._bump_failure()
            logger.error(
                "audit outbox batch write failed (%d events): %s",
                len(batch), exc, exc_info=True,
            )

    def _write_batch_sync(
        self,
        batch: list[AuditEnvelope],
        committed_keys: set[str],
    ) -> int:
        """Sync-часть flush'а: одна сессия, savepoint на событие, один commit.

        `record_admin_action(commit=False)` живёт в общей транзакции, каждый
        вызов обёрнут в `begin_nested()` — savepoint откатывает только своё
        событие, не валит весь батч. На финале — `db.commit()`. Если падает
        savepoint, бампим self-audit-failure counter (та же семантика, что
        у старого `_emit_audit`).

        Возвращает число успешно записанных событий — caller использует это
        значение для `_drained_total`, чтобы partial-failure не приводил к
        перерасчёту `drained + failures > enqueued`. Дополнительно после
        успешного `db.commit()` кладёт `envelope.idempotency_key` каждого
        закоммитнутого события в shared `committed_keys` — это нужно
        `_drain_loop`'у, чтобы корректно отработать `CancelledError`,
        прилетевший уже после commit'а.

        Внутри держим set `bumped_ids` — id() envelope'ов, за которые уже
        вызывался `_bump_failure`. Без него падение `db.commit()` на outer-tx
        приводило бы к двойному учёту: savepoint-failed envelope получал
        первый bump на свой `begin_nested()`-rollback, а потом второй — при
        фолбэке в except-ветке коммита, который ходит по всему батчу.
        """
        # `_writer_lock` сериализует sync-writer'ы между drain-loop'ом и
        # `_drain_remaining`. Acquire — синхронный, в треде; если предыдущий
        # batch ещё пишет (например, async-await его cancel'нули, но
        # `db.commit()` ещё в полёте), второй writer тут подождёт.
        with self._writer_lock:
            return self._write_batch_locked(batch, committed_keys)

    def _write_batch_locked(
        self,
        batch: list[AuditEnvelope],
        committed_keys: set[str],
    ) -> int:
        db = self._session_factory()
        succeeded_envs: list[AuditEnvelope] = []
        bumped_ids: set[int] = set()
        try:
            for envelope in batch:
                try:
                    with db.begin_nested():
                        self._writer(db, envelope)
                    succeeded_envs.append(envelope)
                except Exception as exc:
                    self._bump_failure()
                    bumped_ids.add(id(envelope))
                    logger.error(
                        "self-audit failed (action=%s): %s",
                        envelope.action, exc, exc_info=True,
                    )
            try:
                db.commit()
            except Exception as commit_exc:
                # outer-tx упала — все события, прошедшие savepoint, тоже
                # потеряны (но bump'нуть нужно только тех, кого ещё не
                # бампили). Savepoint-failed envelope'ы уже в `bumped_ids`,
                # их пропускаем, чтобы не считать одно событие дважды.
                for env in batch:
                    if id(env) in bumped_ids:
                        continue
                    self._bump_failure()
                # Пробрасываем под маркером, чтобы `_flush_batch` отличил
                # «commit упал, sync уже забампил всех» от «session_factory
                # упала на старте, никто не бампил».
                raise _BatchCommitFailed() from commit_exc
            # ВАЖНО: помечаем commit-success ДО возврата, чтобы async-caller
            # мог отличить «to_thread вернулся, commit прошёл» от «cancelled
            # до commit'а» даже если `CancelledError` прилетит между этим
            # моментом и returning из `to_thread`.
            for env in succeeded_envs:
                committed_keys.add(env.idempotency_key)
        finally:
            db.close()
        return len(succeeded_envs)

    # ── introspection ────────────────────────────────────────────────────

    def qsize(self) -> int:
        return self._queue.qsize() if self._queue is not None else 0

    def dropped_overflow_total(self) -> int:
        """События, выброшенные из-за переполнения bounded buffer'а."""
        with self._counters_lock:
            return self._dropped_overflow_total

    def dropped_cancel_total(self) -> int:
        """События, потерянные на cancellation активного drain'а."""
        with self._counters_lock:
            return self._dropped_cancel_total

    def dropped_shutdown_total(self) -> int:
        """События, не уложившиеся в бюджет финального drain'а в shutdown'е."""
        with self._counters_lock:
            return self._dropped_shutdown_total

    def drained_total(self) -> int:
        with self._counters_lock:
            return self._drained_total

    def reset_counters_for_tests(self) -> None:
        """Только для тестов: обнуляет все cause-counters и drained."""
        with self._counters_lock:
            self._dropped_overflow_total = 0
            self._dropped_cancel_total = 0
            self._dropped_shutdown_total = 0
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
    idempotency_key: str | None = None,
) -> AuditEnvelope:
    """Помощник: фиксирует `enqueued_at` и привязывает уникальный dedup-ключ.

    Если caller не передал `idempotency_key` явно, генерится UUID4. Этот
    ключ становится `audit_events.idempotency_key` при записи и гарантирует,
    что shutdown-requeue / повторный `_drain_remaining` не запишет один и
    тот же envelope дважды — partial UNIQUE на `(service, idempotency_key)`
    отбросит retry на DB-уровне.
    """
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
        idempotency_key=idempotency_key or uuid.uuid4().hex,
    )


def _resolve_actor_type(actor_type: str | None) -> str:
    """Whitelist-резолв `actor_type`. Неизвестное → `anonymous`.

    Единая точка резолва — используется и здесь, и в `main._emit_audit`.
    Раньше код дублировался в двух местах: правка whitelist'а требовала
    держать обе ветки в синхроне руками.
    """
    if actor_type in VALID_ACTOR_TYPES:
        return actor_type
    return "anonymous"


def _coerce_details_keys(details: Any) -> Any:
    """Рекурсивно приводит ключи dict'а к `str`.

    `EventCreate._details_shadow_keys` проверяет только `isinstance(k, str)` —
    int/tuple/прочие ключи проходят мимо guard'а. Внешние source'ы (middleware
    `audit_access` строит details руками — там всё str, но fallback-paths и
    record_admin_action могут получить смешанный dict, если контракт нарушен).
    Приводим заранее, чтобы JSONB-сериализация и shadow-keys-валидатор
    работали с однородным dict'ом.
    """
    if isinstance(details, dict):
        return {str(k): _coerce_details_keys(v) for k, v in details.items()}
    if isinstance(details, list):
        return [_coerce_details_keys(v) for v in details]
    return details


def write_envelope_to_db(db: Session, envelope: AuditEnvelope) -> Any:
    """Writer-функция по умолчанию: envelope → EventCreate → record_admin_action.

    Вынесена сюда, а не в `main.py`, чтобы тесты могли подменить writer без
    импорта `main` (циклы пакетного импорта).
    """
    from src.schemas.events import EventCreate
    from src.services.event_service import record_admin_action

    payload = EventCreate(
        timestamp=envelope.enqueued_at,
        service="loging_service",
        action=envelope.action,
        actor_id=envelope.actor_id,
        actor_type=_resolve_actor_type(envelope.actor_type),
        username=envelope.username,
        status=envelope.emit_status,
        allowed=envelope.allowed,
        request_id=envelope.request_id,
        details=_coerce_details_keys(envelope.details),
        idempotency_key=envelope.idempotency_key,
    )
    return record_admin_action(db, payload, commit=False)
