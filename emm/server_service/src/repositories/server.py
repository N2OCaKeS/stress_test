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


async def list_managed(db: AsyncSession, limit: int) -> list[Server]:
    """SELECT всех подготовленных серверов (`is_managed=true`), capped по limit.

    Платформенный фан-аут синхронизации управляющей учётки бьёт по всем
    серверам платформы независимо от отдела — конфиг управляющей учётки это
    глобальный singleton. Списанные сервера исключаем: worker-операции они не
    принимают. Порядок `created_at` — детерминированный срез при срабатывании
    cap'а (старейшие подготовленные первыми).
    """
    from src.core.constants import ServerStatus

    stmt = (
        select(Server)
        .where(Server.is_managed.is_(True))
        .where(Server.status != ServerStatus.DECOMMISSIONED)
        .order_by(Server.created_at)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())


async def list_all_active(db: AsyncSession, limit: int) -> list[Server]:
    """SELECT всех не-списанных серверов платформы, capped по limit.

    В отличие от `list_managed` — БЕЗ фильтра `is_managed`: живая проба
    достижимости (ping/ssh) и опрос IPMI-питания идут для ЛЮБОГО сервера, в том
    числе неподготовленного, а не только для онбордингнутых. Списанные
    исключаем — worker-операции они не принимают. Порядок `created_at` —
    детерминированный срез при срабатывании cap'а.
    """
    from src.core.constants import ServerStatus

    stmt = (
        select(Server)
        .where(Server.status != ServerStatus.DECOMMISSIONED)
        .order_by(Server.created_at)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())


async def count_all_active(db: AsyncSession) -> int:
    """COUNT не-списанных серверов платформы — для truncated-аудита power-sweep'а."""
    from src.core.constants import ServerStatus

    stmt = (
        select(func.count(Server.id))
        .where(Server.status != ServerStatus.DECOMMISSIONED)
    )
    return int((await db.execute(stmt)).scalar_one())


async def list_stuck_updating(
    db: AsyncSession, cutoff: datetime, limit: int
) -> list[Server]:
    """SELECT серверов, застрявших в `busy_state='updating'` дольше порога.

    Застрявшими считаем те, у кого `busy_since < cutoff` — обновление ОС
    началось давно, а callback `astra-updated`, который должен снять блокировку,
    так и не пришёл. `busy_since IS NULL` не берём: без таймстампа возраст
    блокировки не определить, освобождать вслепую нельзя. Порядок `busy_since` —
    сначала самые старые.
    """
    from src.core.constants import BusyState

    stmt = (
        select(Server)
        .where(Server.busy_state == BusyState.UPDATING)
        .where(Server.busy_since.is_not(None))
        .where(Server.busy_since < cutoff)
        .order_by(Server.busy_since)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())


async def count_managed(db: AsyncSession) -> int:
    """COUNT подготовленных не-списанных серверов — для truncated-аудита."""
    from src.core.constants import ServerStatus

    stmt = (
        select(func.count(Server.id))
        .where(Server.is_managed.is_(True))
        .where(Server.status != ServerStatus.DECOMMISSIONED)
    )
    return int((await db.execute(stmt)).scalar_one())


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


async def list_by_ids(
    db: AsyncSession,
    server_ids: list[str],
    limit: int = 100,
    offset: int = 0,
) -> list[Server]:
    """SELECT серверов из явного набора id, `created_at DESC`, offset/limit.

    Для grant-only листинга: роль без тип-wide `server.view`, но с инстанс-
    грантами на конкретные сервера видит ровно их. Пустой набор → пусто.
    """
    if not server_ids:
        return []
    stmt = (
        select(Server)
        .where(Server.id.in_(server_ids))
        .order_by(Server.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_by_ids(db: AsyncSession, server_ids: list[str]) -> int:
    """COUNT серверов из явного набора id — total для grant-only листинга."""
    if not server_ids:
        return 0
    stmt = select(func.count(Server.id)).where(Server.id.in_(server_ids))
    return int((await db.execute(stmt)).scalar_one())


async def list_by_ids_after(
    db: AsyncSession,
    server_ids: list[str],
    *,
    limit: int,
    after_created_at: datetime | None,
    after_id: str | None,
) -> list[Server]:
    """Keyset-страница серверов из явного набора id по `(created_at, id) DESC`."""
    if not server_ids:
        return []
    stmt = (
        select(Server)
        .where(Server.id.in_(server_ids))
        .order_by(Server.created_at.desc(), Server.id.desc())
        .limit(limit)
    )
    if after_created_at is not None and after_id is not None:
        stmt = stmt.where(
            or_(
                Server.created_at < after_created_at,
                and_(
                    Server.created_at == after_created_at,
                    Server.id < after_id,
                ),
            )
        )
    return list((await db.execute(stmt)).scalars())


async def get_by_id(db: AsyncSession, server_id: str) -> Server | None:
    """SELECT по PK. Visibility-check делает service-layer."""
    stmt = select(Server).where(Server.id == server_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_number(db: AsyncSession, number: int) -> Server | None:
    """SELECT по номеру стенда (глобально уникален в паре servers+vm)."""
    stmt = select(Server).where(Server.number == number)
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


async def list_ids_in_departments(
    db: AsyncSession,
    department_ids: list[str],
) -> list[str]:
    """SELECT id всех серверов перечисленных отделов — без подгрузки самих row'ов.

    Нужно для dept-scope'а task-листинга: собрать множество видимых caller'у
    server_id'ов и отдать его в cross-DB `worker_client.list_tasks` как фильтр
    `target_server_id IN (...)`. Пустой `department_ids` → пусто.
    """
    if not department_ids:
        return []
    stmt = select(Server.id).where(Server.department_id.in_(department_ids))
    return list((await db.execute(stmt)).scalars())


async def department_map_for_ids(
    db: AsyncSession,
    server_ids: list[str],
) -> dict[str, str]:
    """`{server_id: department_id}` для набора серверов одним SELECT'ом.

    Используется при сборке `TaskRead`, чтобы проставить `department_id`
    задачи из `server.department_id` без N+1 на каждую строку листинга.
    Несуществующие/чужие id просто отсутствуют в результате.
    """
    if not server_ids:
        return {}
    stmt = select(Server.id, Server.department_id).where(Server.id.in_(server_ids))
    return {row[0]: row[1] for row in (await db.execute(stmt)).all()}


async def hostname_map_for_ids(
    db: AsyncSession,
    server_ids: list[str],
) -> dict[str, str]:
    """`{server_id: hostname}` для набора серверов одним SELECT'ом.

    Нужно для резолва человекочитаемого имени сервера в `TaskRead` без N+1
    на каждую строку листинга. Несуществующие/удалённые id просто отсутствуют
    в результате — caller проставляет None.
    """
    if not server_ids:
        return {}
    stmt = select(Server.id, Server.hostname).where(Server.id.in_(server_ids))
    return {row[0]: row[1] for row in (await db.execute(stmt)).all()}


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

async def list_distinct_department_ids(db: AsyncSession) -> list[str]:
    """Отделы, у которых есть хотя бы один сервер.

    server_service не держит своей копии таблицы `departments` (та живёт в
    auth_service, и наружу сервис её не проксирует) — единственный источник
    "какие отделы вообще существуют" с точки зрения этого сервиса это отделы,
    у которых заведены сервера. Используется для карточек admin-настроек
    (например `/settings/acs/departments`), где нужен список кандидатов на
    per-department toggle.
    """
    stmt = select(Server.department_id).distinct()
    return [row[0] for row in (await db.execute(stmt)).all()]

