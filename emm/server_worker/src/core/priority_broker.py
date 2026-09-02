"""Priority-aware ListQueueBroker для taskiq поверх двух Redis-списков.

`ListQueueBroker` из taskiq_redis работает с одним Redis-списком: `kick`
делает LPUSH в `queue_name`, `listen` — блокирующий BRPOP по нему же. Этого
достаточно для FIFO, но high-priority задача в общей очереди ждёт всех, кто
встал раньше, — реального обгона нет.

`PriorityListQueueBroker` держит два списка: normal и high. Минимально
инвазивно — та же библиотека, тот же Redis:

* `kick` смотрит label `queue_name` сообщения. dispatch_outbox publisher
  ставит его в `high_queue_name` для задач с priority >= high-порога; всё
  остальное (в т.ч. scheduled retries, periodic-таски) идёт в normal.
* `listen` делает `BRPOP high normal 0` — один атомарный блокирующий pop по
  двум ключам сразу. Redis проверяет ключи слева направо и отдаёт элемент из
  первого непустого: пока в high что-то есть, normal не трогается. Это и есть
  обгон. Голодания normal-очереди при пустом high нет — BRPOP сразу падает
  на normal.

Доставку normal-задач не меняем: при незаданном/normal-label'е поведение
идентично базовому `ListQueueBroker`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from taskiq import BrokerMessage
from taskiq_redis import ListQueueBroker


class PriorityListQueueBroker(ListQueueBroker):
    """ListQueueBroker с отдельной high-priority Redis-очередью.

    `high_queue_name` — имя второго списка. По умолчанию совпадает с
    `<queue_name>_high`, но в worker'е задаётся явно из настроек.
    """

    def __init__(self, *args, high_queue_name: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.high_queue_name = high_queue_name or f"{self.queue_name}_high"

    async def kick(self, message: BrokerMessage) -> None:
        """LPUSH сообщения в нужный список.

        Целевая очередь берётся из label'а `queue_name` сообщения (его ставит
        publisher через `kicker().with_labels(queue_name=...)`). Если label
        указывает на high-очередь — пишем туда, иначе в normal. Незаданный
        label → normal (базовое поведение).
        """
        requested = message.labels.get("queue_name")
        if requested == self.high_queue_name:
            target_queue = self.high_queue_name
        else:
            target_queue = self.queue_name
        async with Redis(connection_pool=self.connection_pool) as redis_conn:
            await redis_conn.lpush(target_queue, message.message)  # type: ignore[arg-type]

    async def listen(self) -> AsyncGenerator[bytes, None]:
        """Блокирующий drain high-очереди перед normal через `BRPOP high normal`.

        Redis отдаёт элемент из первого непустого ключа в списке аргументов
        BRPOP. Пока high-очередь непустая, normal не трогается — high реально
        обгоняет. На транзиентном ConnectionError повторяем (как базовый
        `ListQueueBroker.listen`).
        """
        brpop_value_position = 1
        while True:
            try:
                async with Redis(connection_pool=self.connection_pool) as redis_conn:
                    popped = await redis_conn.brpop(  # type: ignore[misc]
                        [self.high_queue_name, self.queue_name],
                    )
                    yield popped[brpop_value_position]
            except RedisConnectionError:
                continue
