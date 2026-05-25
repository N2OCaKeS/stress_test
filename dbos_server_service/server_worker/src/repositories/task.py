"""Task repository — CRUD над таблицей `tasks`."""

from datetime import datetime, timezone

from sqlalchemy import delete, exists, not_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import TaskStatus
from src.models import AuditOutbox, Task, WorkerHeartbeat


async def get_by_id(db: AsyncSession, task_id: str) -> Task | None:
    """Подгрузить task по primary key. None если не нашли."""
    stmt = select(Task).where(Task.id == task_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> Task:
    """Создать task. flush() — чтобы выдать id ещё до commit'а вызывающей сессии."""
    obj = Task(**data)
    db.add(obj)
    await db.flush()
    return obj


async def mark_running(
    db: AsyncSession, task: Task, *, worker_id: str | None = None,
) -> Task | None:
    """Compare-and-swap ``queued → running``.

    Возвращает ``Task`` при успешном переходе, ``None`` если переход не
    случился (статус уже не ``queued`` — task в RUNNING / SUCCEEDED /
    FAILED / CANCELLED). Это защита от двойного исполнения:

    * **duplicate enqueue** — если task_id был ``kiq``-нут дважды (по
      ошибке dispatch_task retry в server_service или Redis-replay) и
      первый worker уже завершил его (terminal status), второй worker не
      должен снова перевести его в RUNNING, обнулить ``last_error`` и
      пере-запустить impl.
    * **double-claim в multi-worker** — taskiq-broker гарантирует, что
      одно сообщение получит ровно один consumer, но CAS — defence in
      depth на случай мисконфига очереди (visibility timeout / re-enqueue
      на ack-fail).

    `worker_id` — идентификатор replica, который
    забирает task'у. Записывается в `tasks.worker_id` для cross-replica
    zombie sweep'а (см. `_runner_state.py` и `tasks_sweep_orphaned` в
    `src/main.py`). При successful CAS попутно гасим `scheduled_retry_at`
    — task больше не «ждёт retry», она running.

    Реализация — UPDATE ... WHERE status='queued' RETURNING. Атомарно на
    уровне PostgreSQL, не нужен ни application-lock, ни row-level SELECT
    FOR UPDATE. После UPDATE refresh переменной из БД — иначе SQLAlchemy
    identity-map отдаст устаревшую копию.
    """
    now = datetime.now(timezone.utc)
    next_attempt = task.attempt + 1
    stmt = (
        update(Task)
        .where(Task.id == task.id, Task.status == TaskStatus.QUEUED)
        .values(
            status=TaskStatus.RUNNING,
            attempt=next_attempt,
            started_at=now,
            last_error=None,
            scheduled_retry_at=None,
            worker_id=worker_id,
        )
        .returning(Task.id)
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is None:
        # CAS не сработал — task в нестандартном статусе. НЕ трогаем
        # in-memory объект (не хотим врать вызывающей стороне).
        return None
    # Синхронизируем in-memory копию с БД, чтобы вызывающая сторона видела
    # актуальный attempt/started_at.
    task.status = TaskStatus.RUNNING
    task.attempt = next_attempt
    task.started_at = now
    task.last_error = None
    task.scheduled_retry_at = None
    task.worker_id = worker_id
    await db.flush()
    return task


async def mark_succeeded(db: AsyncSession, task: Task, result: dict | None) -> Task:
    """Terminal success: status=SUCCEEDED, сохранить result, completed_at=now."""
    task.status = TaskStatus.SUCCEEDED
    task.result = result
    task.completed_at = datetime.now(timezone.utc)
    await db.flush()
    return task


async def mark_failed(db: AsyncSession, task: Task, error_message: str) -> Task:
    """Terminal failure: status=FAILED, last_error, completed_at=now.

    `error_message` должно быть уже redact'нутым caller'ом (см.
    `_runner.run_task` → `redact_error_message`).
    """
    task.status = TaskStatus.FAILED
    task.last_error = error_message
    task.completed_at = datetime.now(timezone.utc)
    await db.flush()
    return task


async def mark_pending_for_retry(
    db: AsyncSession,
    task: Task,
    error_message: str,
    *,
    scheduled_retry_at: datetime | None = None,
) -> Task:
    """Сбросить task в ``queued`` для повторной попытки.

    Используется `_runner` когда `attempt < max_attempts` после exception
    в `impl`. Не очищаем ``attempt`` (он уже инкрементирован в
    ``mark_running``); ``last_error`` сохраняем для диагностики, новая
    попытка перезапишет его при success или новом фейле.

    ``started_at`` оставляем — это «последний старт», новый прогон
    обновит. ``completed_at`` НЕ выставляем (task не завершён).

    ``scheduled_retry_at`` — запланированное
    timestamp re-kick'а. Если worker умрёт во время `asyncio.sleep`,
    перезапущенный worker при startup-scan'е найдёт row с
    `status='queued' AND scheduled_retry_at <= now()` и сам сделает
    `kiq`. NULL — синхронное retry без durable scheduling (например,
    graceful shutdown drain зовёт это без планируемого времени, т.к.
    re-kick делает следующий запущенный worker через startup-scan по
    `status='queued'`).

    Сбрасываем ``worker_id`` обратно в NULL — task больше «ничейная»,
    sweep её не должен ловить как orphan, пока следующий mark_running
    не назначит нового владельца.
    """
    task.status = TaskStatus.QUEUED
    task.last_error = error_message
    task.scheduled_retry_at = scheduled_retry_at
    task.worker_id = None
    await db.flush()
    return task


async def enqueue_audit(
    db: AsyncSession, *, task_id: str | None, payload: dict
) -> AuditOutbox:
    """Положить audit-событие в transactional outbox.

    Должно быть вызвано в той же сессии, что меняет статус task'и, и
    закоммичено вместе с ней. После успешного commit'а publisher
    (`audit_outbox_publisher`) подхватит запись и доставит в loging_service.

    `payload` — уже подготовленный dict с ключами, совпадающими с
    параметрами `audit_client.emit()` (action + остальные kwargs).
    """
    row = AuditOutbox(task_id=task_id, payload=payload)
    db.add(row)
    await db.flush()
    return row


async def list_due_scheduled_retries(
    db: AsyncSession, *, now: datetime | None = None
) -> list[Task]:
    """Найти task'и, у которых наступило время retry'я, но `_schedule_retry`
    не сработал (worker умер во время `asyncio.sleep`).

    Используется в startup-recovery (`src/main.py::_recover_scheduled_retries`):
    при старте worker'а зачитываем все `status='queued' AND
    scheduled_retry_at IS NOT NULL AND scheduled_retry_at <= now()` и
    делаем `kiq` для каждого. Это «догоняет» retry'и, потерянные при
    OOM-kill / SIGKILL.

    `with_for_update(skip_locked=True)` — защита от двойного kiq'а при
    concurrent startup двух replica'ах. Без skip-locked обе replica'и
    SELECT'нули бы одни и те же due-row'ы и kiq'нули каждую дважды; CAS
    на `mark_running` отбивает дубль consumer'а, но Redis-очередь успевает
    раздуться. Lock держится до commit'а вызывающей сессии — caller
    (`_recover_scheduled_retries`) после `kiq` коммитит и отпускает.

    Возвращаем полный Task — caller'у нужны `task_kind` (для
    `broker.find_task`) и `id` (для `kiq`).
    """
    threshold = now or datetime.now(timezone.utc)
    stmt = (
        select(Task)
        .where(
            Task.status == TaskStatus.QUEUED,
            Task.scheduled_retry_at.is_not(None),
            Task.scheduled_retry_at <= threshold,
        )
        .order_by(Task.scheduled_retry_at.asc())
        .with_for_update(skip_locked=True)
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_orphaned_running(
    db: AsyncSession,
    *,
    orphan_threshold_seconds: float,
    heartbeat_stale_seconds: float,
    now: datetime | None = None,
) -> list[Task]:
    """Найти orphan running task'и.

    Orphan = task в status='running', чей `started_at` старше
    `orphan_threshold_seconds`, и (a) `worker_id IS NULL` либо (b)
    `worker_id` не имеет «свежего» heartbeat'а — `last_heartbeat_at <
    now - heartbeat_stale_seconds` или отсутствует в `worker_heartbeats`.

    Используется sweep'ом (`tasks_sweep_orphaned` в `src/main.py`):
    результат прогоняется через `mark_failed("worker_orphaned")` +
    audit-row. После этого UI/operator перестаёт видеть «зависшую
    running task'у»; повторно её запустить можно вручную через
    server_service dispatch.

    Считаем `now` параметром, чтобы тесты могли подменять время; в
    production caller передаст `None` → `datetime.now(timezone.utc)`.
    """
    n = now or datetime.now(timezone.utc)
    orphan_cutoff = datetime.fromtimestamp(
        n.timestamp() - orphan_threshold_seconds, tz=timezone.utc,
    )
    heartbeat_cutoff = datetime.fromtimestamp(
        n.timestamp() - heartbeat_stale_seconds, tz=timezone.utc,
    )

    # Фильтр «активных воркеров»: EXISTS WorkerHeartbeat по worker_id
    # с свежим last_heartbeat_at. Если task.worker_id IS NULL — EXISTS
    # вернёт False (NULL != worker_id), задача попадает в orphan
    # автоматически.
    active_heartbeat = (
        select(WorkerHeartbeat.worker_id)
        .where(
            WorkerHeartbeat.worker_id == Task.worker_id,
            WorkerHeartbeat.last_heartbeat_at >= heartbeat_cutoff,
        )
        .correlate(Task)
    )

    stmt = (
        select(Task)
        .where(
            Task.status == TaskStatus.RUNNING,
            Task.started_at.is_not(None),
            Task.started_at < orphan_cutoff,
            not_(exists(active_heartbeat)),
        )
        .order_by(Task.started_at.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def delete_completed_older_than(
    db: AsyncSession,
    *,
    cutoff: datetime,
) -> int:
    """Удалить terminal task'и (SUCCEEDED/FAILED) с `completed_at < cutoff`.

    Periodic `tasks.cleanup_completed_old` зовёт это раз в сутки — без
    cleanup таблица `tasks` растёт линейно по числу dispatch'ей. На стенде
    ~1k task/день это ~30k row'ов в месяц, индекс по `(status, enqueued_at)`
    распухает, history-запросы по target_server_id тормозят.

    CANCELLED намеренно НЕ дропаем — этот терминальный статус сейчас
    нигде в коде не выставляется, на всякий случай оставляем за порогом.
    QUEUED/RUNNING тоже не трогаем, даже если они застряли надолго —
    их разгребает orphan-sweep с правильным audit-trail'ом.

    `completed_at IS NOT NULL` — explicit guard: для всех terminal task'ов
    `mark_succeeded`/`mark_failed` ставит timestamp, но без `IS NOT NULL`
    случайный NULL в этом столбце сделал бы row кандидатом на удаление.

    Возвращает количество удалённых строк. Caller отвечает за commit.
    """
    stmt = delete(Task).where(
        Task.status.in_((TaskStatus.SUCCEEDED, TaskStatus.FAILED)),
        Task.completed_at.is_not(None),
        Task.completed_at < cutoff,
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount or 0
