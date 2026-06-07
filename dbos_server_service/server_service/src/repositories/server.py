"""Server-репозиторий — сырой CRUD против таблицы `servers`."""

from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Server


async def list_in_departments(
    db: AsyncSession,
    department_ids: list[str] | None,
    limit: int = 100,
    offset: int = 0,
) -> list[Server]:
    """SELECT серверов, опционально ограниченный набором отделов.

    `department_ids=None` → все сервера (зарезервировано под будущие
    operator-сценарии; service-layer сейчас всегда передаёт явный список).
    `department_ids=[]` → пусто (caller без department).
    """
    stmt = select(Server).order_by(Server.created_at.desc()).limit(limit).offset(offset)
    if department_ids is not None:
        if not department_ids:
            return []
        stmt = stmt.where(Server.department_id.in_(department_ids))
    return list((await db.execute(stmt)).scalars())


async def count_in_departments(
    db: AsyncSession,
    department_ids: list[str] | None,
) -> int:
    """COUNT под тем же фильтром, что и list — для total в pagination."""
    stmt = select(func.count(Server.id))
    if department_ids is not None:
        if not department_ids:
            return 0
        stmt = stmt.where(Server.department_id.in_(department_ids))
    return int((await db.execute(stmt)).scalar_one())


async def list_in_departments_after(
    db: AsyncSession,
    department_ids: list[str] | None,
    *,
    limit: int,
    after_created_at: datetime | None,
    after_id: str | None,
) -> list[Server]:
    """Keyset-страница серверов по `(created_at DESC, id DESC)`.

    Без `after_*` — отдаёт первую страницу. С указанной парой выдаёт строки
    строго «после» неё в выбранном порядке — `(created_at, id) < (after_created_at, after_id)`.
    Тай-брейк по `id` нужен, потому что `created_at` с миллисекундной точностью
    на одном INSERT batch'е может совпадать у нескольких строк, и иначе курсор
    «соскользнул» бы либо пропустив строку, либо выдав её дважды.

    `department_ids=None` — все отделы (зарезервировано); `[]` — пусто.
    """
    if department_ids is not None and not department_ids:
        return []
    stmt = (
        select(Server)
        .order_by(Server.created_at.desc(), Server.id.desc())
        .limit(limit)
    )
    if department_ids is not None:
        stmt = stmt.where(Server.department_id.in_(department_ids))
    if after_created_at is not None and after_id is not None:
        # `(created_at, id) < (?, ?)` — стандартный keyset; tuple_-сравнение
        # SQLAlchemy переводит в портабельный SQL, который Postgres исполняет
        # как лексикографическое сравнение пары.
        stmt = stmt.where(
            or_(
                Server.created_at < after_created_at,
                and_(
                    Server.created_at == after_created_at,
                    Server.id < after_id,
                ),
            )
        )
        # OR-разворачивание вместо tuple_-сравнения — на некоторых диалектах
        # tuple-сравнение не поддерживается, а такой WHERE работает везде.
        # Postgres план выходит тот же.
    return list((await db.execute(stmt)).scalars())


async def get_by_id(db: AsyncSession, server_id: str) -> Server | None:
    """SELECT по PK. Visibility-check делает service-layer."""
    stmt = select(Server).where(Server.id == server_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_for_update(db: AsyncSession, server_id: str) -> Server | None:
    """SELECT по PK с FOR UPDATE row-lock.

    `release_server` использует это, чтобы re-fetch'нуть `busy_state` перед
    pre-check'ом 409 SERVER_NOT_BUSY. Без лока caller мог бы прочитать stale
    BUSY из чужой session-cache; параллельный release сериализуется здесь.
    """
    stmt = select(Server).where(Server.id == server_id).with_for_update(of=Server)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_many_by_ids(
    db: AsyncSession, server_ids: list[str]
) -> dict[str, Server]:
    """SELECT всех серверов из списка одним `WHERE id IN (...)` запросом.

    Возвращает словарь по id (несуществующие просто отсутствуют). Для массовых
    fan-out'ов (ротация, update_on_host), где иначе на каждый server_id шёл
    бы свой round-trip в БД.
    """
    if not server_ids:
        return {}
    stmt = select(Server).where(Server.id.in_(server_ids))
    rows = list((await db.execute(stmt)).scalars())
    return {srv.id: srv for srv in rows}


async def get_by_hostname(db: AsyncSession, hostname: str) -> Server | None:
    """SELECT по hostname (UNIQUE). Используется опционально под дедупликацию."""
    stmt = select(Server).where(Server.hostname == hostname)
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> Server:
    """INSERT новой строки. commit делает caller."""
    obj = Server(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: Server, changes: dict) -> Server:
    """In-place setattr по словарю изменений + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: Server) -> None:
    """DELETE объекта. Каскад на child-таблицы — через ondelete=CASCADE."""
    await db.delete(obj)
    await db.flush()
