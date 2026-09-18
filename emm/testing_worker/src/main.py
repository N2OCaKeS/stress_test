"""Точка входа testing_worker'а.

`broker` — singleton taskiq broker'а; `taskiq worker src.main:broker` его
запускает. Реальная работа воркера, в отличие от `server_worker`, не идёт
через taskiq task-handler'ы — диспетчер один: `testing_service`'овская
очередь. Вместо этого на `WORKER_STARTUP` поднимается `settings.queue_concurrency`
фоновых asyncio-loop'ов (`src/services/queue_loop.py::run_polling_loop`) —
КОНКУРЕНТНО в одном процессе, не отдельными процессами/подами: каждый в своём
цикле забирает готовое задание (`POST /internal/queue/claim`), исполняет его
по SSH (`src/services/ssh_executor.py`) и отчитывается об исходе (`POST
/internal/queue/{id}/completed`). SSH-сессия почти всё время ждёт сеть, не
грузит CPU, поэтому `asyncio`-конкурентность внутри одного процесса даёт
параллельность по стендам без накладных расходов N интерпретаторов Python —
`claim_next_ready()` на стороне testing_service (`SELECT ... FOR UPDATE
SKIP LOCKED`) уже гарантирует, что два слота не заберут один и тот же item.
Тот же приём, что у `server_worker`'а для `audit_outbox_publisher`/
`heartbeat`/probe-loop'ов — фоновые task'и в `TaskiqState`, а не глобальная
переменная, отменяются на `WORKER_SHUTDOWN`.

`system.ping` остаётся как смоук-задача: подтверждает, что broker слушает
очередь и хендлеры резолвятся, даже когда task-registry иначе был бы пуст.
"""

import asyncio
import logging

from taskiq import TaskiqEvents, TaskiqState

from src.core.config import get_settings
from src.core.broker import broker
from src.core.logging import configure_logging
from src.services import queue_loop

_settings = get_settings()

configure_logging("testing_worker", level=_settings.worker_log_level)

logger = logging.getLogger(__name__)


@broker.task("system.ping")
async def system_ping() -> str:
    """Смоук-задача — подтверждает, что broker слушает очередь и хендлеры резолвятся."""
    return "pong"


# Ключ в `TaskiqState`, под которым лежит список handle'ов фоновых
# polling-тасок (один на слот конкурентности). Через state, а не через
# глобальную переменную — тем же приёмом, что и у `server_worker`'а
# (`_PUBLISHER_TASK_KEY` в его `src/main.py`), чтобы несколько broker'ов в
# одном процессе (например, в тестах) не делили одну ссылку.
_QUEUE_LOOP_TASKS_KEY = "queue_polling_loop_tasks"


def _on_queue_loop_exit(task: asyncio.Task) -> None:
    """Safety-net: `run_polling_loop` сам ловит `Exception` построчно, но
    неожиданный `BaseException`-подкласс (не `CancelledError`) проскочит и
    task молча завершится. Без этого callback'а shutdown-хук просто увидел бы
    уже завершённый task и решил бы, что всё штатно — ERROR-лог здесь даёт
    оператору сигнал в логах. Один упавший слот не останавливает остальные —
    они независимы."""
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.critical("queue polling loop %s exited unexpectedly: %s: %s", task.get_name(), type(exc).__name__, exc)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _start_queue_polling_loop(state: TaskiqState) -> None:
    concurrency = _settings.queue_concurrency
    tasks = []
    for slot in range(concurrency):
        task = asyncio.create_task(queue_loop.run_polling_loop(), name=f"queue_polling_loop_{slot}")
        task.add_done_callback(_on_queue_loop_exit)
        tasks.append(task)
    state[_QUEUE_LOOP_TASKS_KEY] = tasks
    logger.info("queue polling loop scheduled on worker startup (concurrency=%d)", concurrency)


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _stop_queue_polling_loop(state: TaskiqState) -> None:
    tasks: list[asyncio.Task] | None = state.get(_QUEUE_LOOP_TASKS_KEY)
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for task, result in zip(tasks, results):
        if isinstance(result, Exception):
            logger.warning(
                "queue polling loop %s raised on shutdown: %s: %s",
                task.get_name(), type(result).__name__, result,
            )
    try:
        del state[_QUEUE_LOOP_TASKS_KEY]
    except KeyError:
        pass
    logger.info("queue polling loop stopped on worker shutdown")


logger.info("testing_worker starting; queue=%s redis=%s", _settings.taskiq_queue_name, _settings.redis_url)

# Регистрация будущих task-handler'ов (пока пустой пакет).
from src import tasks  # noqa: E402, F401
