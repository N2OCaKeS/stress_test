"""Фоновый per-worker heartbeat-loop.

`worker_heartbeats.last_heartbeat_at` — маркер живости реплики: sweep
`tasks_sweep_orphaned` метит `worker_orphaned` running-задачи воркера, чей
heartbeat протух (старше `worker_heartbeat_stale_seconds`).

Раньше heartbeat был broker-задачей (`worker.heartbeat`, cron `*/1`). Проблема:
задача конкурирует за handler-слоты. Насыщенный воркер (все слоты заняты
длинными create'ами) не берёт свой heartbeat-тик — его подхватывает свободная
реплика и UPSERT'ит СВОЙ worker_id, а насыщенный протухает и его же задачи
метятся orphaned → двойное выполнение. Поэтому heartbeat вынесен в фоновый
asyncio-loop: он крутится на event-loop'е воркера независимо от слотов, поэтому
даже полностью занятый воркер бьётся между IO-await'ами длинных SSH-операций.

Loop never dies: любые ошибки UPSERT'а логируются и глотаются, цикл продолжается.
Интервал — треть stale-окна (минимум 5с), чтобы пережить пару пропущенных тиков.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("server_worker.heartbeat_loop")


def _interval_seconds() -> float:
    from src.core.config import get_settings

    stale = get_settings().worker_heartbeat_stale_seconds
    return max(5.0, stale / 3.0)


async def run_heartbeat_loop() -> None:
    """UPSERT `(worker_id, now())` в `worker_heartbeats` каждые ~stale/3 секунд."""
    from src.db.session import AsyncSessionLocal
    from src.repositories import worker_heartbeat as heartbeat_repo
    from src.tasks._runner_state import get_worker_id
    from src.utils.redaction import redact_error_message

    interval = _interval_seconds()
    wid = get_worker_id()
    logger.info(
        "heartbeat loop started worker_id=%s interval=%.1fs", wid, interval,
    )
    while True:
        try:
            async with AsyncSessionLocal() as session:
                await heartbeat_repo.upsert_heartbeat(session, worker_id=wid)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — loop не должен падать
            logger.warning(
                "heartbeat loop upsert failed worker_id=%s: %s",
                wid, redact_error_message(f"{type(exc).__name__}: {exc}"),
            )
        await asyncio.sleep(interval)
