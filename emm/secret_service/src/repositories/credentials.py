"""CRUD для модели Credential.

Все запросы строго scoped: либо по owner_user_id, либо по owner_dept_id.
Сервисный слой выше уже знает, в какой scope идти; репозиторий лишних
join'ов в auth.users / auth.departments не делает — owner-id'ы лежат
soft-FK, реального join'а через БД нет.

Курсорная пагинация на парах `(created_at DESC, id DESC)` — точно такой
же приём, как в `server_service.repositories.server.list_in_departments_after`.
Пара уберегает от пропусков и дублей при одинаковых timestamp'ах.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Credential


async def get_by_id(db: AsyncSession, cred_id: str) -> Credential | None:
    """SELECT строки по PK."""
    stmt = select(Credential).where(Credential.id == cred_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_id_for_update(
    db: AsyncSession, cred_id: str
) -> Credential | None:
    """SELECT по PK с `FOR UPDATE` для сериализации владельца строки.

    Берётся на transfer/recover ownership и в re-encrypt outbox, чтобы два
    параллельных transfer'а (или transfer и фоновый re-encrypt) на одну креду
    не перетёрли друг друга. Lock держится до конца транзакции caller'а.
    Обычный update content идёт через `load_for_action` → `get_by_id` без
    lock'а: там конкуренцию ловит CAS на ciphertext'е.
    """
    stmt = (
        select(Credential)
        .where(Credential.id == cred_id)
        .with_for_update(of=Credential)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def find_active_by_owner_service_name(
    db: AsyncSession,
    *,
    owner_user_id: str | None,
    owner_dept_id: str | None,
    service: str,
    name: str,
) -> Credential | None:
    """Поиск active-кред с тем же (owner, service, name) для UNIQUE-precheck.

    Partial UNIQUE на БД (см. модель) ловит коллизию атомарно; этот запрос —
    дружелюбный 409-NAME_DUPLICATE до flush'а. Точно один из owner'ов должен
    быть задан, второй — None: маппинг scope→owner caller-side.
    """
    stmt = select(Credential).where(
        Credential.status == "active",
        Credential.service == service,
        Credential.name == name,
    )
    if owner_user_id is not None:
        stmt = stmt.where(Credential.owner_user_id == owner_user_id)
    if owner_dept_id is not None:
        stmt = stmt.where(Credential.owner_dept_id == owner_dept_id)
    return (await db.execute(stmt)).scalar_one_or_none()


def _normalize_cursor_ts(value: datetime) -> datetime:
    """Курсорный timestamp → aware-UTC.

    `created_at` в БД — timestamptz (aware). Курсор приходит распарсенным из
    `datetime.fromisoformat(...)`: если в строке не было смещения, получаем
    naive datetime, и сравнение `Credential.created_at < naive` на стороне
    Python (merge в `list_visible`) роняет TypeError, а на стороне БД даёт
    неоднозначный bind. Приводим к UTC до сравнения.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def apply_cursor(stmt, cursor: tuple[datetime, str] | None):
    """Keyset-фильтр на пару `(created_at, id)` под `ORDER BY` обоих DESC.

    `id` — текстовый `cred_<hex>`; лексикографический `<` задаёт полный
    порядок, согласованный с `ORDER BY Credential.id DESC`, поэтому пара
    `(created_at, id)` уникальна и keyset не пропускает/не дублирует строки
    даже при совпадении `created_at`. Источник истины для всех листингов —
    репо и inline-ветка cross-dep в `credential_service` зовут эту же функцию.
    """
    if cursor is None:
        return stmt
    after_created_at, after_id = cursor
    after_created_at = _normalize_cursor_ts(after_created_at)
    return stmt.where(
        or_(
            Credential.created_at < after_created_at,
            and_(
                Credential.created_at == after_created_at,
                Credential.id < after_id,
            ),
        )
    )


# Обратная совместимость для внутренних caller'ов модуля.
_apply_cursor = apply_cursor


def _apply_filters(stmt, *, scope: str | None, status: str | None):
    if scope is not None:
        stmt = stmt.where(Credential.scope == scope)
    if status is not None:
        stmt = stmt.where(Credential.status == status)
    return stmt


async def list_for_user(
    db: AsyncSession,
    user_id: str,
    *,
    scope: str | None = None,
    status: str | None = None,
    limit: int,
    cursor: tuple[datetime, str] | None = None,
) -> list[Credential]:
    """Personal-креды пользователя. Опциональный фильтр по scope/status.

    Курсор — `(created_at, id)` последней строки предыдущей страницы.
    """
    stmt = select(Credential).where(Credential.owner_user_id == user_id)
    stmt = _apply_filters(stmt, scope=scope, status=status)
    stmt = _apply_cursor(stmt, cursor)
    stmt = stmt.order_by(Credential.created_at.desc(), Credential.id.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars())


async def list_for_dept(
    db: AsyncSession,
    dept_id: str,
    *,
    scope: str | None = None,
    status: str | None = None,
    limit: int,
    cursor: tuple[datetime, str] | None = None,
) -> list[Credential]:
    """Department-/cross_department-креды dep'а — где dep_id == owner_dept_id."""
    stmt = select(Credential).where(Credential.owner_dept_id == dept_id)
    stmt = _apply_filters(stmt, scope=scope, status=status)
    stmt = _apply_cursor(stmt, cursor)
    stmt = stmt.order_by(Credential.created_at.desc(), Credential.id.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars())


async def create(db: AsyncSession, **fields) -> Credential:
    """INSERT новой кред. commit — на caller'е."""
    obj = Credential(**fields)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, cred: Credential, **fields) -> Credential:
    """In-place setattr + flush. `updated_at` обновляется через onupdate в модели."""
    for key, value in fields.items():
        setattr(cred, key, value)
    await db.flush()
    return cred


async def cas_update_secret_encrypted(
    db: AsyncSession,
    *,
    cred_id: str,
    expected_blob: str,
    new_blob: str,
) -> bool:
    """Compare-and-swap `secret_encrypted` строки `cred_id`.

    Возвращает True, если ровно одна строка обновилась — значит, race не
    случился и ciphertext перешит под новый ключ. False означает «кто-то
    опередил» (соседний reveal уже мигрировал строку, либо PATCH секрета
    переписал blob): для lazy-миграции это норма — следующий reveal
    либо ничего не сделает (актуальная версия), либо мигрирует свежий blob.
    """
    stmt = (
        sa_update(Credential)
        .where(
            Credential.id == cred_id,
            Credential.secret_encrypted == expected_blob,
        )
        .values(secret_encrypted=new_blob)
    )
    result = await db.execute(stmt)
    return result.rowcount == 1


async def delete(db: AsyncSession, cred: Credential) -> None:
    """Hard DELETE. Каскад FK снесёт role_acls и dept_grants."""
    await db.delete(cred)
    await db.flush()


async def mark_blocked(
    db: AsyncSession, cred: Credential, *, reason: str
) -> Credential:
    """Перевести креду в `status=blocked` с timestamp'ом и reason'ом."""
    cred.status = "blocked"
    cred.blocked_at = datetime.now(timezone.utc)
    cred.blocked_reason = reason
    await db.flush()
    return cred


async def mark_active(db: AsyncSession, cred: Credential) -> Credential:
    """Снять `blocked` — для recover. Зануляет blocked_at/reason."""
    cred.status = "active"
    cred.blocked_at = None
    cred.blocked_reason = None
    await db.flush()
    return cred
