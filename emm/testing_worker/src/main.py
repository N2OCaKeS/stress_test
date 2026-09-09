"""Точка входа testing_worker'а.

`broker` — singleton taskiq broker'а; `taskiq worker src.main:broker`
его запускает. Импорт `src.tasks` (в самом низу файла) регистрирует все
task-handler'ы на broker'е — сейчас пакет пуст, задачи появятся в волне 5
плана миграции (собственный SSH-worker, исполнение теста под кредами
из `server_service`'овского `prepare-for-test`, §5.1).

Каркасная волна: только broker + logging + пустой task-registry, чтобы
`taskiq worker` реально стартовал и держал соединение с Redis. Никакой
бизнес-логики, аудит-outbox'а или scheduler'а — появятся вместе с первыми
задачами.
"""

import logging

from src.core.config import get_settings
from src.core.broker import broker
from src.core.logging import configure_logging

_settings = get_settings()

configure_logging("testing_worker", level=_settings.worker_log_level)

logger = logging.getLogger(__name__)


@broker.task("system.ping")
async def system_ping() -> str:
    """Смоук-задача — подтверждает, что broker слушает очередь и хендлеры резолвятся.

    Уйдёт, как только появится первая реальная задача (волна 5) — сейчас
    единственная цель: `taskiq worker` не должен стартовать с пустым
    task-registry (некоторые версии брокера тогда сразу падают на пустом
    `find_task`).
    """
    return "pong"


logger.info("testing_worker starting; queue=%s redis=%s", _settings.taskiq_queue_name, _settings.redis_url)

# Регистрация будущих task-handler'ов (пока пустой пакет).
from src import tasks  # noqa: E402, F401
