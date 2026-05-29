"""WorkerHeartbeat repository — UPSERT + cleanup.

Используется periodic-task `worker_heartbeat` (см. `src/main.py`) и
sweep-task `tasks_sweep_orphaned` (через `repositories/task.py
::list_orphaned_running`, чтение).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import WorkerHeartbeat


async def upsert_heartbeat(
    db: AsyncSession,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> None:
    """UPSERT `(worker_id, now)` в `worker_heartbeats`.

    PostgreSQL ON CONFLICT (worker_id) DO UPDATE: атомарно — нет окна
    «row есть, но не успели обновить», sweep видит свежий timestamp.

    Caller отвечает за commit (мы только flush()'им). `now` — для
    тестов; в production caller передаёт `None`, и timestamp берётся
    из БД (`func.now()`) — это устраняет clock-skew между подами
    worker'а и сервером Postgres: sweep сравнивает `last_heartbeat_at`
    с серверным `now()` тоже, поэтому единая точка отсчёта важна.
    """
    timestamp: datetime | object
    if now is not None:
        timestamp = now
    else:
        timestamp = func.now()
    stmt = pg_insert(WorkerHeartbeat).values(
        worker_id=worker_id, last_heartbeat_at=timestamp,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[WorkerHeartbeat.worker_id],
        set_={"last_heartbeat_at": timestamp},
    )
    await db.execute(stmt)
    await db.flush()


async def delete_stale_heartbeats(
    db: AsyncSession,
    *,
    cutoff: datetime,
) -> int:
    """Удалить heartbeat-row'ы старше `cutoff`.

    Best-effort cleanup для bounded growth таблицы: replica может
    переименоваться (k8s pod restart с другим suffix'ом), оставив
    «мёртвый» row. Sweep всё равно работает корректно с stale row'ами
    (они не считаются активными), но без cleanup таблица растёт.

    Возвращает количество удалённых строк.
    """
    stmt = delete(WorkerHeartbeat).where(
        WorkerHeartbeat.last_heartbeat_at < cutoff
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount or 0
