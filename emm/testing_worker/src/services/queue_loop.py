"""Polling-цикл очереди `testing_service` (§5.5 плана миграции).

`run_polling_loop()` — long-running `asyncio` loop, поднимается как фоновая
задача на `WORKER_STARTUP` (см. `src/main.py`), по образцу
`server_worker/src/services/audit_outbox_publisher.py::run_publisher_loop`.
Задачи очереди исполняются не как отдельные taskiq-таски — диспетчер здесь
один: сам `testing_service` решает, какой item готов (`state=ready`),
`claim()` просто забирает следующий.

Один проход:

1. `testing_client.claim()` — если очередь пуста (или testing_service
   недоступен), `item is None` → короткий сон и следующая попытка.
2. Иначе — `ssh_executor.execute(...)` под кредами и хостом из `item`.
3. `testing_client.report_completed(...)` с исходом.
4. Сразу следующая итерация, без сна — под нагрузкой воркер вычерпывает
   очередь максимально быстро; пауза нужна только когда реально нечего делать.

Тело цикла обёрнуто в `try/except Exception`: неожиданная ошибка (баг в
коде, а не транзиентная сетевая недоступность — та уже обработана внутри
`testing_client`) не должна убивать весь loop навсегда. На такой ошибке —
ERROR-лог и сон `queue_poll_interval_seconds`, чтобы систематический баг не
заспамил лог тысячами повторов в секунду.
"""

from __future__ import annotations

import asyncio
import logging

from src.core.config import get_settings
from src.services import ssh_executor, testing_client

logger = logging.getLogger("testing_worker.queue_loop")


async def _run_one_item(item: dict) -> None:
    """Исполнить одно задание из `claim()` и отчитаться `completed`."""
    settings = get_settings()
    queue_item_id = item["queue_item_id"]

    succeeded, exit_code, error = await ssh_executor.execute(
        item["host"],
        item["test_username"],
        item["test_ssh_private_key"],
        item["command"],
        connect_timeout=settings.ssh_connect_timeout_seconds,
        command_timeout=settings.ssh_command_timeout_seconds,
    )

    logger.info(
        "queue item %s finished: succeeded=%s exit_code=%s is_retry=%s debug_mode=%s",
        queue_item_id,
        succeeded,
        exit_code,
        item.get("is_retry"),
        item.get("debug_mode"),
    )

    await testing_client.report_completed(
        queue_item_id,
        succeeded=succeeded,
        exit_code=exit_code,
        error=error,
    )


async def run_polling_loop() -> None:
    """Бесконечный polling loop. Останавливается только через `CancelledError`."""
    settings = get_settings()
    poll_interval = settings.queue_poll_interval_seconds

    logger.info("queue polling loop started (poll_interval=%ss)", poll_interval)

    while True:
        try:
            item = await testing_client.claim()
            if item is None:
                await asyncio.sleep(poll_interval)
                continue

            await _run_one_item(item)
            # Задание обработано — сразу пробуем забрать следующее, без сна.
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — один сбойный проход не должен убивать loop
            logger.exception("queue polling loop: unexpected error in iteration")
            await asyncio.sleep(poll_interval)
