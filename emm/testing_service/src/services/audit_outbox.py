"""Staging-сторона audit-outbox'а: как payload попадает в `audit_outbox`.

`audit_service.emit()` — синхронная функция, которую зовут из 100+ мест,
в том числе из except-веток, где транзакция уже развалилась, и из
middleware, где сессии запроса нет вовсе. Поэтому запись в таблицу
устроена в два шага:

1. `stage(payload)` кладёт готовый payload в буфер текущего запроса
   (contextvar, ставится в `main.py::attach_request_id` рядом с
   `audit_context`). Это дешёвая операция без I/O — emit по-прежнему не
   умеет блокировать обработчик и не умеет бросать наружу.
2. `flush_scope()` в finally того же middleware пишет весь буфер одним
   INSERT'ом в своей короткой сессии и коммитит. С этого момента событие
   durable: дальше его судьба — дело drain-loop'а.

Почему не `session.add()` в сессию запроса (буквальный transactional
outbox, как в `server_worker`): подавляющее большинство call-site'ов
зовут `emit()` уже ПОСЛЕ `await db.commit()`, а denied-ветки — вообще на
развалившейся транзакции. Строка, подсаженная в такую сессию, либо
никогда не будет закоммичена, либо уедет в rollback вместе с бизнес-
ошибкой. Плюс любой сбой на нашей строке (несериализуемый payload) ронял
бы чужую бизнес-транзакцию. Граница durability здесь — «запрос
завершился ⇒ событие в outbox», а не «бизнес-строка закоммичена ⇔
событие закоммичено».

Вне запроса (фоновые loop'ы lifespan'а, `statistics_recalc._run_recalc`,
startup-хуки) буфера нет — payload уезжает отдельной task'ой со своей
сессией. Если running loop'а нет вовсе (чистый sync-контекст), событие
остаётся только в логе: писать в async-движок синхронно нечем.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar, Token

from src.repositories import audit_outbox as repo

logger = logging.getLogger("audit")


class OutboxScope:
    """Буфер audit-payload'ов одного запроса.

    `closed` нужен из-за фоновых task'ов: обработчик может стартовать
    `asyncio.create_task(...)` (см. `services/statistics_recalc.py`),
    задача унаследует контекст со ссылкой на этот же буфер и доживёт до
    после flush'а. Складывать туда payload уже поздно — на закрытом
    буфере `stage()` уходит в отдельную task'у, а не в мёртвый список.
    """

    __slots__ = ("payloads", "closed")

    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.closed = False


_scope: ContextVar[OutboxScope | None] = ContextVar("audit_outbox_scope", default=None)

# Task'и отложенной записи вне request-скоупа. Дренируются на shutdown'е
# вместе с остальными pending audit-task'ами (`main.py`).
_pending_persist_tasks: "set[asyncio.Task]" = set()

# События, которые не доехали даже до outbox'а (БД недоступна, sync-контекст
# без event loop'а). Отдаётся наружу в `/ready` — ненулевое значение значит,
# что часть audit-trail'а существует только в логе процесса.
_not_persisted_total: int = 0


def get_not_persisted_total() -> int:
    """Per-process счётчик событий, не доехавших до outbox-таблицы."""
    return _not_persisted_total


def _reset_counters_for_tests() -> None:
    global _not_persisted_total
    _not_persisted_total = 0


def begin_scope() -> Token:
    """Открыть буфер на текущий контекст. Токен вернуть в `reset_scope`."""
    return _scope.set(OutboxScope())


def current_scope() -> OutboxScope | None:
    """Активный буфер либо None."""
    return _scope.get()


def reset_scope(token: Token) -> None:
    """Вернуть contextvar в прежнее состояние."""
    _scope.reset(token)


def stage(payload: dict) -> bool:
    """Поставить payload в очередь на запись в outbox.

    Возвращает True, если payload попал в буфер запроса, False — если
    ушёл отдельной task'ой или не был принят вовсе (нет event loop'а).
    Ошибки наружу не выпускает: аудит не должен ронять бизнес-операцию.
    """
    scope = _scope.get()
    if scope is not None and not scope.closed:
        scope.payloads.append(payload)
        return True
    _persist_detached([payload])
    return False


def _persist_detached(payloads: list[dict]) -> None:
    """Записать payload'ы отдельной task'ой (вне request-скоупа)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Sync-контекст без loop'а: async-движок здесь недоступен. Это
        # startup/CLI-путь, событий там единицы.
        _count_not_persisted(payloads)
        return
    task = loop.create_task(persist(payloads))
    _pending_persist_tasks.add(task)
    task.add_done_callback(_pending_persist_tasks.discard)


async def flush_scope(token: Token | None = None) -> int:
    """Записать буфер текущего запроса в `audit_outbox` и закрыть его.

    Возвращает число записанных строк. Пустой буфер — без похода в БД.
    """
    scope = _scope.get()
    if token is not None:
        _scope.reset(token)
    if scope is None:
        return 0
    scope.closed = True
    payloads = scope.payloads
    scope.payloads = []
    if not payloads:
        return 0
    return await persist(payloads)


async def persist(payloads: list[dict]) -> int:
    """INSERT payload'ов в `audit_outbox` в собственной сессии + commit.

    Единственное место, где staging реально ходит в БД. Любой сбой
    (БД недоступна, битый payload) логируется и глотается — иначе падение
    аудита утащило бы за собой ответ пользователю.
    """
    if not payloads:
        return 0
    from src.db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await repo.add_payloads(db, payloads)
            await db.commit()
        return len(payloads)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — аудит не роняет запрос
        logger.warning(
            "audit_outbox: failed to persist %d event(s): %s", len(payloads), exc,
        )
        _count_not_persisted(payloads)
        return 0


def _count_not_persisted(payloads: list[dict]) -> None:
    """Событие осталось только в логе — посчитать и вывести action в лог."""
    global _not_persisted_total
    _not_persisted_total += len(payloads)
    for payload in payloads:
        logger.info("audit_event_not_persisted %s", payload.get("action"))
