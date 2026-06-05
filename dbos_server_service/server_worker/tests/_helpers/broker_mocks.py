"""Broker / kicker / task mock builders.

Закрывают паттерн `broker.find_task(kind).kicker().kiq(task_id)`, который
дублируется в durable-retry / dispatch-outbox / w21-recover тестах.
Конкретные сценарии собирают желаемое поведение через kwargs:

* `make_broker(kiq_exc=Exception("boom"))` — kiq падает на любом вызове.
* `make_broker()` — happy-path, kiq просто записывает вызов.
* `make_broker(unknown_kind=True)` — `find_task` возвращает None,
  имитирует deploy-drift / unknown task_kind.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


def make_broker(
    *,
    kiq_exc: Exception | None = None,
    unknown_kind: bool = False,
):
    """Собрать broker mock с заданным поведением `find_task(...).kicker().kiq(...)`.

    Возвращённый объект имеет `_kiq` (AsyncMock) — удобный аксессор для
    проверок `call_args_list` / `call_count` без обхода
    `find_task().kicker().kiq`-цепочки.
    """
    broker = MagicMock()
    if unknown_kind:
        broker.find_task = MagicMock(return_value=None)
        return broker

    kiq = AsyncMock(side_effect=kiq_exc) if kiq_exc else AsyncMock()
    kicker = MagicMock()
    kicker.kiq = kiq
    task = MagicMock()
    task.kicker = MagicMock(return_value=kicker)
    broker.find_task = MagicMock(return_value=task)
    broker._kiq = kiq
    return broker


__all__ = ["make_broker"]
