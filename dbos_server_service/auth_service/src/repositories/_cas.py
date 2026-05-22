"""Compare-and-swap helper для repository-уровня.

Один SQL-запрос вида
``UPDATE <table> SET <values> WHERE <id_col>=:id AND <expected> RETURNING <id_col>``
позволяет двум concurrent worker'ам безопасно гонкаться на одной строке без
``SELECT ... FOR UPDATE``: Postgres сам сериализует row-lock, RETURNING
получает только winner.

Использование (см. `bans.py`, `sessions.py`, `oauth_clients.py`):

    won = await atomic_transition(
        session,
        Ban,
        id_column="id",
        id_value=ban.id,
        where_clause=Ban.is_active.is_(True),
        update_values={"is_active": False, "unbanned_at": now},
    )
    if not won:
        return False  # другой worker уже переключил

``update_values`` принимает и литералы, и колоночные выражения
(`Session.token_generation + 1`) — это просто ``.values(**update_values)``.
"""

from typing import Any, Mapping, Type

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.db.base import Base


async def atomic_transition(
    session: AsyncSession,
    model: Type[Base],
    *,
    id_column: str,
    id_value: Any,
    where_clause: ColumnElement,
    update_values: Mapping[str, Any],
) -> bool:
    """Атомарный CAS-переход строки `model` с id=:id_value.

    Возвращает ``True``, если caller выиграл переход (строка соответствовала
    `where_clause` И была обновлена), ``False`` если строка уже не в исходном
    состоянии (другой worker первым выполнил тот же переход — или строки нет).

    Параметры:
        session: AsyncSession для выполнения UPDATE.
        model: ORM-класс таблицы.
        id_column: имя primary-key атрибута (`"id"` в большинстве случаев).
        id_value: значение PK строки.
        where_clause: дополнительный SA-предикат на исходное состояние —
            та часть CAS, которая «ожидаемое». Например
            ``Ban.is_active.is_(True)`` или
            ``Session.refresh_token_hash == expected_hash``.
        update_values: kwargs для ``.values()`` — что записать в случае
            успешного перехода.

    ``synchronize_session=False`` намеренно — caller сам мутирует in-memory
    ORM-инстанс под winner-ветку (loser-ветка ORM-инстанс не трогает,
    что и есть сигнал «не делай side-effects»).
    """
    pk = getattr(model, id_column)
    stmt = (
        update(model)
        .where(pk == id_value, where_clause)
        .values(**update_values)
        .returning(pk)
        .execution_options(synchronize_session=False)
    )
    winner = await session.scalar(stmt)
    return winner is not None
