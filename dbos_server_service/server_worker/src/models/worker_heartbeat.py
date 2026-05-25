"""WorkerHeartbeat ORM-model — heartbeat'ы активных worker-replica'ах.

`RUNNING_TASKS` в `_runner_state.py` живёт в памяти одного процесса.
`_drain_running_tasks` на SIGTERM покрывает только graceful shutdown
**своей** replica. Если pod умер OOM-kill / node-failure'ом ДО shutdown
хука — running task'и остались `status='running'` в БД, никто их не
подберёт. Раньше требовался ручной recovery.

Решение — heartbeat-таблица + sweep:

  1. Каждая replica пишет `(worker_id, now())` каждые ~60s через
     periodic-task `worker_heartbeat` (см. `src/main.py`).
  2. Periodic-task `tasks_sweep_orphaned` находит `status='running' AND
     started_at < now() - 30min AND worker_id NOT IN (active workers)` и
     mark_failed("worker_orphaned"). active workers = те, чей
     `last_heartbeat_at > now() - <stale_threshold>`.

`worker_id` берётся из env `WORKER_ID` (default — hostname-pid). В k8s
hostname == pod name, что даёт стабильный идентификатор replica.

Если scheduler выключен (`SCHEDULER_ENABLED=false`) — таблица существует,
но не наполняется и не зачищается. Sweep тоже не запустится. Это OK для
dev/CI — orphaned tasks там не страшны.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class WorkerHeartbeat(Base):
    """ORM-row для одной activity-метки worker-replica'и.

    Жизненный цикл: periodic `worker_heartbeat` task делает UPSERT
    (insert или update existing row по worker_id PK), обновляя
    `last_heartbeat_at`. Sweep-task смотрит, какие worker_id NOT
    активны последние `_WORKER_STALE_AFTER_SECONDS` секунд.
    """

    __tablename__ = "worker_heartbeats"

    # `worker_id` — это «replica identity»: для k8s pod hostname достаточно,
    # для bare-metal — env WORKER_ID или сгенерированное при старте. 64
    # символа — синхронно с тем, что хранится в `tasks.worker_id`.
    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
