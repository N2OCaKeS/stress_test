"""Process-local state для task runner'а.

Держит in-memory набор `task_id` — текущих *работающих* в этом worker'е
impl-функций. Поднимается в `_runner.run_task` (sentinel-add сразу после
mark_running) и опускается в finally при выходе из impl — независимо от
happy/failure/retry.

Graceful shutdown:

* При SIGTERM (`WORKER_SHUTDOWN`) handler ждёт до
  `WORKER_SHUTDOWN_TIMEOUT_SECONDS`, пока множество не опустеет.
* По таймауту: оставшиеся идут в `mark_pending_for_retry` (если
  `attempt < max_attempts`) или `mark_failed("worker_shutdown")` —
  иначе task'и зависают в `status='running'` навсегда.

Per-worker, не shared между процессами — каждый отвечает за свои
running tasks. Cross-worker zombie (pod умер до handler'а) покрывается
sweep'ом `tasks_sweep_orphaned` через `worker_heartbeats`.

Worker identity:

* `get_worker_id()` — стабильный идентификатор replica. Источники в
  приоритете: env `WORKER_ID` → hostname-pid → uuid.
* Используется в `mark_running` (чья task'а) и `worker_heartbeat`
  periodic'е (запись в `worker_heartbeats`).

`asyncio.Lock` не нужен: набор обновляется только из coroutines одного
event-loop'а (taskiq-worker). `add`/`discard` атомарны в одном thread.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)


# Множество task_id, чей impl сейчас выполняется в этом процессе.
# Очищается при graceful shutdown (см. `src/main.py::_drain_running_tasks`).
RUNNING_TASKS: set[str] = set()


# Кеш resolved worker_id — резолвится 1 раз на процесс. None пока
# `get_worker_id()` не позвали; после первой инициализации меняется
# только в тестах через `_reset_worker_id_for_tests`.
_RESOLVED_WORKER_ID: str | None = None


def get_worker_id() -> str:
    """Вернуть стабильный идентификатор этой replica.

    Порядок резолвинга (one-time per process):

    1. Settings.worker_id (`env WORKER_ID`) — если непустой;
    2. `socket.gethostname()` + pid — обычный случай в k8s, hostname
       == pod name, для bare-metal — машинное имя + pid;
    3. fallback на `uuid4().hex[:16]` если hostname лежит / нет
       доступа (запас, на практике не должен срабатывать).

    Кешируется в module-level — sweep, mark_running, heartbeat зовут
    одно и то же значение в рамках процесса. Кеш сбрасывается только
    в тестах через `_reset_worker_id_for_tests()`.
    """
    global _RESOLVED_WORKER_ID
    if _RESOLVED_WORKER_ID is not None:
        return _RESOLVED_WORKER_ID

    # local-import: settings импортируется при первом get_worker_id'е,
    # чтобы import `_runner_state` сам по себе не открыл `src.core.config`.
    from src.core.config import get_settings

    settings = get_settings()
    if settings.worker_id:
        _RESOLVED_WORKER_ID = settings.worker_id[:64]
        return _RESOLVED_WORKER_ID

    try:
        host = socket.gethostname() or "unknown"
    except Exception:  # noqa: BLE001 — defensive, hostname() редко падает
        host = "unknown"
    candidate = f"{host}-{os.getpid()}"
    # tasks.worker_id и worker_heartbeats.worker_id — String(64). hostname
    # в k8s ≤ 63 символа, но truncate всё равно делаем как safety net.
    if len(candidate) > 64:
        candidate = candidate[:64]
    if not candidate or candidate == "-":
        candidate = f"worker-{uuid.uuid4().hex[:16]}"
    _RESOLVED_WORKER_ID = candidate
    return _RESOLVED_WORKER_ID


def _reset_worker_id_for_tests() -> None:
    """Test helper: сбросить кеш worker_id, чтобы следующий тест мог
    переопределить env и получить новое значение."""
    global _RESOLVED_WORKER_ID
    _RESOLVED_WORKER_ID = None


@contextmanager
def track_running_task(task_id: str) -> Iterator[None]:
    """Context manager: add task_id при enter, discard при exit.

    Использование в `_runner.run_task`:

        with track_running_task(task_id):
            ...  # impl + mark_succeeded/failed/pending

    Гарантирует discard даже при exception в impl или mark_*. Если
    runner упадёт до `add` (например, mark_running CAS отказал) —
    задача в множество не попадает, что корректно: shutdown-drain её
    не пытается mark_failed-ить дважды.
    """
    RUNNING_TASKS.add(task_id)
    try:
        yield
    finally:
        RUNNING_TASKS.discard(task_id)


def reset_for_tests() -> None:
    """Test helper: очистить множество (между тестами не должно копиться)."""
    RUNNING_TASKS.clear()
