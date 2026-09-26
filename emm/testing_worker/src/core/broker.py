"""Taskiq broker для testing_worker.

Обычный `ListQueueBroker` (одна Redis-очередь) — в отличие от
`server_worker`, здесь пока нет ни SSH-задач, ни приоритетной очереди
(появятся вместе с реализацией приоритетной очереди). Заводить
priority-broker сейчас преждевременно: усложнение без потребителя.
"""

from __future__ import annotations

from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from src.core.config import get_settings

_settings = get_settings()

broker = ListQueueBroker(
    url=_settings.redis_url,
    queue_name=_settings.taskiq_queue_name,
).with_result_backend(
    RedisAsyncResultBackend(redis_url=_settings.redis_url, result_ex_time=3600)
)
