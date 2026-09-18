"""Точка входа testing_worker'а.

`broker` — singleton taskiq broker'а; `taskiq worker src.main:broker` его
запускает. Реальная работа воркера, в отличие от `server_worker`, не идёт
через taskiq task-handler'ы — диспетчер один: `testing_service`'овская
очередь. Вместо этого на `WORKER_STARTUP` поднимается фоновый asyncio-loop
(`src/services/queue_loop.py::run_polling_loop`), который в цикле забирает
готовые задания (`POST /internal/queue/claim`), исполняет их по SSH
(`src/services/ssh_executor.py`) и отчитывается об исходе (`POST
/internal/queue/{id}/completed`). Параллельность по стендам живёт ВНУТРИ
этого одного loop'а (каждый item — своя `asyncio.Task`, `run_polling_loop`
не ждёт её перед следующим `claim`, см. докстринг `queue_loop.py`) — здесь
поднимается ровно один процесс-диспетчер, а не несколько. Тот же приём, что
у `server_worker`'а для `audit_outbox_publisher`/`heartbeat`/probe-loop'ов —
фоновая task в `TaskiqState`, а не глобальная переменная, отменяется на
`WORKER_SHUTDOWN`.

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


# Ключ в `TaskiqState`, под которым лежит handle фоновой polling-таски.
# Через state, а не через глобальную переменную — тем же приёмом, что и у
# `server_worker`'а (`_PUBLISHER_TASK_KEY` в его `src/main.py`), чтобы
# несколько broker'ов в одном процессе (например, в тестах) не делили одну
# ссылку.
_QUEUE_LOOP_TASK_KEY = "queue_polling_loop_task"


def _on_queue_loop_exit(task: asyncio.Task) -> None:
    """Safety-net: `run_polling_loop` сам ловит ожидаемые сбои, но неожиданный
    `BaseException`-подкласс (не `CancelledError`) проскочит и task молча
    завершится. Без этого callback'а shutdown-хук просто увидел бы уже
    завершённый task и решил бы, что всё штатно — ERROR-лог здесь даёт
    оператору сигнал в логах."""
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.critical("queue polling loop exited unexpectedly: %s: %s", type(exc).__name__, exc)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _start_queue_polling_loop(state: TaskiqState) -> None:
    task = asyncio.create_task(queue_loop.run_polling_loop(), name="queue_polling_loop")
    task.add_done_callback(_on_queue_loop_exit)
    state[_QUEUE_LOOP_TASK_KEY] = task
    logger.info("queue polling loop scheduled on worker startup")


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _stop_queue_polling_loop(state: TaskiqState) -> None:
    task: asyncio.Task | None = state.get(_QUEUE_LOOP_TASK_KEY)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001 — shutdown-хук не должен падать
        logger.warning("queue polling loop raised on shutdown: %s: %s", type(exc).__name__, exc)
    finally:
        try:
            del state[_QUEUE_LOOP_TASK_KEY]
        except KeyError:
            pass
    logger.info("queue polling loop stopped on worker shutdown")


logger.info("testing_worker starting; queue=%s redis=%s", _settings.taskiq_queue_name, _settings.redis_url)

# Регистрация будущих task-handler'ов (пока пустой пакет).
from src import tasks  # noqa: E402, F401
