"""ServerAccount-репозиторий — CRUD + апдейт пароля + M2M-линковка серверов."""

from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ServerAccount, ServerAccountServer
from src.utils.ids import server_account_server_id


async def get_by_id(db: AsyncSession, account_id: str) -> ServerAccount | None:
    """SELECT по PK. server_links подтягиваются selectin'ом."""
    stmt = select(ServerAccount).where(ServerAccount.id == account_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def login_map_for_ids(
    db: AsyncSession,
    account_ids: list[str],
) -> dict[str, str]:
    """`{account_id: login}` для набора аккаунтов одним SELECT'ом.

    Нужно для резолва человекочитаемого логина учётки в `TaskRead` без N+1.
    Несуществующие/удалённые id просто отсутствуют в результате — caller
    проставляет None.
    """
    if not account_ids:
        return {}
    stmt = select(ServerAccount.id, ServerAccount.login).where(
        ServerAccount.id.in_(account_ids)
    )
    return {row[0]: row[1] for row in (await db.execute(stmt)).all()}


async def get_for_update(db: AsyncSession, account_id: str) -> ServerAccount | None:
    """SELECT по PK с row-lock'ом на самой строке ServerAccount.

    Берёт `FOR UPDATE OF server_accounts`, чтобы конкурентные `update_account` /
    `link_servers` / `unlink_servers` сериализовались по строке аккаунта и
    fan-out не уходил с mixed snapshot'ом (см. lost-update-сценарий между
    PATCH'ом полей и параллельной linkage-операцией).

    `of=ServerAccount` явно указывает, что блокируется строка аккаунта, а не
    join'ы selectin-relationship'а (`server_links` подтянется отдельным SELECT'ом
    без лока — нам это не нужно, lock на parent'е достаточен, потому что link/unlink
    тоже сначала берут лок на parent'е через эту же функцию).
    """
    stmt = (
        select(ServerAccount)
        .where(ServerAccount.id == account_id)
        .with_for_update(of=ServerAccount)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def linked_server_ids(account: ServerAccount) -> list[str]:
    """Список server_id из связок аккаунта, упорядоченный по (created_at, server_id).

    Tiebreaker по server_id — при bulk INSERT created_at часто совпадает у всех
    линков; без него порядок зависит от selectin-loading и не воспроизводится.
    """
    return [
        link.server_id
        for link in sorted(
            account.server_links, key=lambda link: (link.created_at, link.server_id)
        )
    ]


async def list_for_server(
    db: AsyncSession, server_id: str, limit: int = 100, offset: int = 0
) -> list[ServerAccount]:
    """Список аккаунтов, привязанных к серверу — упорядочен по created_at DESC."""
    stmt = (
        select(ServerAccount)
        .join(ServerAccountServer, ServerAccountServer.account_id == ServerAccount.id)
        .where(ServerAccountServer.server_id == server_id)
        .order_by(ServerAccount.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def list_for_server_after(
    db: AsyncSession,
    server_id: str,
    *,
    limit: int,
    after_created_at: datetime | None,
    after_id: str | None,
) -> list[ServerAccount]:
    """Keyset-страница аккаунтов сервера по `(created_at DESC, id DESC)`.

    Аналогично `server.list_in_departments_after`: пара (created_at, id) уберегает
    от пропусков/дублей на одинаковых timestamp'ах. Фильтр через JOIN на
    `server_account_servers` (M2M).
    """
    stmt = (
        select(ServerAccount)
        .join(ServerAccountServer, ServerAccountServer.account_id == ServerAccount.id)
        .where(ServerAccountServer.server_id == server_id)
        .order_by(ServerAccount.created_at.desc(), ServerAccount.id.desc())
        .limit(limit)
    )
    if after_created_at is not None and after_id is not None:
        stmt = stmt.where(
            or_(
                ServerAccount.created_at < after_created_at,
                and_(
                    ServerAccount.created_at == after_created_at,
                    ServerAccount.id < after_id,
                ),
            )
        )
    return list((await db.execute(stmt)).scalars())


async def count_for_server(db: AsyncSession, server_id: str) -> int:
    """COUNT привязанных к серверу аккаунтов (для пагинации)."""
    stmt = (
        select(func.count(ServerAccountServer.id))
        .where(ServerAccountServer.server_id == server_id)
    )
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict, server_ids: list[str]) -> ServerAccount:
    """INSERT строки аккаунта + связок на каждый сервер. commit — на caller'е."""
    obj = ServerAccount(**data)
    db.add(obj)
    await db.flush()
    for sid in server_ids:
        db.add(
            ServerAccountServer(
                id=server_account_server_id(),
                account_id=obj.id,
                server_id=sid,
                login=obj.login,
            )
        )
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: ServerAccount, changes: dict) -> ServerAccount:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: ServerAccount) -> None:
    """DELETE объекта (связки уходят каскадом). commit — на caller'е."""
    await db.delete(obj)
    await db.flush()


async def lock_links_for_account(
    db: AsyncSession, account_id: str
) -> list[ServerAccountServer]:
    """SELECT всех M2M-связок аккаунта с FOR UPDATE.

    Берётся в `unlink_servers` перед чтением `linked_server_ids` и `remove_servers`,
    чтобы сериализовать с параллельными правками этих же связок. Возвращает
    свежий live-snapshot из БД (а не selectin-кэш на parent'е).
    """
    stmt = (
        select(ServerAccountServer)
        .where(ServerAccountServer.account_id == account_id)
        .with_for_update(of=ServerAccountServer)
    )
    return list((await db.execute(stmt)).scalars())


async def add_servers(
    db: AsyncSession, account: ServerAccount, server_ids: list[str]
) -> None:
    """Привязать аккаунт к новым серверам. Уже привязанные — пропускаем.

    commit/flush — на caller'е (через последующий flush). Дубль по
    (account_id, server_id) отсеивается на стороне Python; новый логин на
    занятом сервере поднимет IntegrityError на uq_server_login.
    """
    existing = {link.server_id for link in account.server_links}
    for sid in server_ids:
        if sid in existing:
            continue
        db.add(
            ServerAccountServer(
                id=server_account_server_id(),
                account_id=account.id,
                server_id=sid,
                login=account.login,
            )
        )
    await db.flush()
    await db.refresh(account)


async def remove_servers(
    db: AsyncSession, account: ServerAccount, server_ids: list[str]
) -> int:
    """Отвязать аккаунт от серверов. Возвращает число снятых связок."""
    targets = set(server_ids)
    removed = 0
    for link in list(account.server_links):
        if link.server_id in targets:
            await db.delete(link)
            removed += 1
    await db.flush()
    await db.refresh(account)
    return removed


async def is_linked(db: AsyncSession, account_id: str, server_id: str) -> bool:
    """Привязан ли аккаунт к конкретному серверу."""
    stmt = select(ServerAccountServer.id).where(
        ServerAccountServer.account_id == account_id,
        ServerAccountServer.server_id == server_id,
    )
    return (await db.execute(stmt)).first() is not None


async def get_link(
    db: AsyncSession, account_id: str, server_id: str
) -> ServerAccountServer | None:
    """Связка аккаунта с сервером (или None)."""
    stmt = select(ServerAccountServer).where(
        ServerAccountServer.account_id == account_id,
        ServerAccountServer.server_id == server_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_links_for_server(
    db: AsyncSession, server_id: str
) -> list[ServerAccountServer]:
    """Все связки сервера (для reconcile инвентаризации)."""
    stmt = select(ServerAccountServer).where(
        ServerAccountServer.server_id == server_id
    )
    return list((await db.execute(stmt)).scalars())


async def list_accounts_only_on_server(
    db: AsyncSession, server_id: str
) -> list[ServerAccount]:
    """Аккаунты, чья единственная связка — этот сервер.

    Удаление сервера каскадит join-строки (`ondelete=CASCADE`), но сам аккаунт
    остаётся. Если других серверов у аккаунта нет, он становится orphan'ом:
    висит с зашифрованным паролем и недостижим (листинг требует server_id).
    Тот же конечный стейт unlink-путь запрещает (ACCOUNT_NO_SERVERS), поэтому
    `delete_server` должен снести именно эти аккаунты сам.

    Считаем суммарное число связок по каждому привязанному к серверу аккаунту
    и оставляем те, у кого total == 1 (т.е. ровно этот сервер). Аккаунты,
    привязанные ещё к каким-то серверам, переживают delete — у них просто
    уходит одна связка каскадом.
    """
    link_counts = (
        select(
            ServerAccountServer.account_id.label("account_id"),
            func.count(ServerAccountServer.id).label("link_count"),
        )
        .where(
            ServerAccountServer.account_id.in_(
                select(ServerAccountServer.account_id).where(
                    ServerAccountServer.server_id == server_id
                )
            )
        )
        .group_by(ServerAccountServer.account_id)
        .having(func.count(ServerAccountServer.id) == 1)
        .subquery()
    )
    stmt = (
        select(ServerAccount)
        .join(link_counts, link_counts.c.account_id == ServerAccount.id)
    )
    return list((await db.execute(stmt)).scalars())


async def get_account_on_server_by_login(
    db: AsyncSession, server_id: str, login: str
) -> ServerAccount | None:
    """Аккаунт с данным login'ом, привязанный к конкретному серверу.

    Инвариант `uq_server_login` гарантирует, что на одном сервере login
    уникален, поэтому возвращаем не более одной строки.
    """
    stmt = (
        select(ServerAccount)
        .join(ServerAccountServer, ServerAccountServer.account_id == ServerAccount.id)
        .where(
            ServerAccountServer.server_id == server_id,
            ServerAccountServer.login == login,
        )
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_accounts_on_server_by_logins(
    db: AsyncSession, server_id: str, logins: list[str]
) -> dict[str, ServerAccount]:
    """Аккаунты с указанными login'ами, привязанные к серверу — батчем.

    Возвращает map ``login → account`` для всех найденных. Отсутствие login'а
    в карте означает «не привязан к этому серверу». Используется reconcile-циклом
    инвентаризации пользователей, чтобы не делать N запросов на N юзеров.
    """
    if not logins:
        return {}
    stmt = (
        select(ServerAccount, ServerAccountServer.login)
        .join(ServerAccountServer, ServerAccountServer.account_id == ServerAccount.id)
        .where(
            ServerAccountServer.server_id == server_id,
            ServerAccountServer.login.in_(logins),
        )
    )
    rows = (await db.execute(stmt)).all()
    return {login: account for account, login in rows}


async def list_links_for_server_by_account_ids(
    db: AsyncSession, server_id: str, account_ids: list[str]
) -> dict[str, ServerAccountServer]:
    """Связки (account_id → link) для конкретного сервера по списку account_id.

    Возвращает map ``account_id → link``. Идёт одним IN-запросом, чтобы
    `receive_users_inventory` не дёргал `get_link` по каждому юзеру отдельно.
    """
    if not account_ids:
        return {}
    stmt = select(ServerAccountServer).where(
        ServerAccountServer.server_id == server_id,
        ServerAccountServer.account_id.in_(account_ids),
    )
    rows = list((await db.execute(stmt)).scalars())
    return {link.account_id: link for link in rows}


async def create_discovered(
    db: AsyncSession, data: dict, server_id: str
) -> ServerAccount:
    """INSERT аккаунта, обнаруженного инвентаризацией (без пароля), + связка.

    Связка сразу помечается присутствующей и со свежим `last_inventory_at`.
    commit — на caller'е.
    """
    obj = ServerAccount(**data)
    db.add(obj)
    await db.flush()
    db.add(
        ServerAccountServer(
            id=server_account_server_id(),
            account_id=obj.id,
            server_id=server_id,
            login=obj.login,
            present_on_server=True,
            last_inventory_at=datetime.now(timezone.utc),
        )
    )
    await db.flush()
    return obj


async def try_create_discovered(
    db: AsyncSession, data: dict, server_id: str
) -> ServerAccount | None:
    """Как `create_discovered`, но безопасный к гонке callback'ов.

    Создаём ServerAccount, затем пробуем INSERT связки с
    `ON CONFLICT (server_id, login) DO NOTHING`. Если конфликт сработал
    (параллельный callback уже завёл discovered-аккаунт со связкой по
    `uq_server_login`), откатываем созданный ServerAccount — иначе
    остался бы висячий аккаунт без линка — и возвращаем None. Caller
    тогда трактует ситуацию как `present` и подтягивает existing-запись.
    """
    account = ServerAccount(**data)
    db.add(account)
    await db.flush()

    link_id = server_account_server_id()
    stmt = (
        pg_insert(ServerAccountServer)
        .values(
            id=link_id,
            account_id=account.id,
            server_id=server_id,
            login=account.login,
            present_on_server=True,
            last_inventory_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(constraint="uq_server_login")
        .returning(ServerAccountServer.id)
    )
    inserted_id = (await db.execute(stmt)).scalar_one_or_none()
    if inserted_id is None:
        # Гонка: линк уже создан параллельным callback'ом. Сносим
        # только что вставленный аккаунт, чтобы не плодить orphan'ов.
        await db.delete(account)
        await db.flush()
        return None
    await db.flush()
    return account


async def mark_link_inventoried(
    db: AsyncSession,
    link: ServerAccountServer,
    *,
    present: bool,
) -> ServerAccountServer:
    """Проставить связке `present_on_server` + `last_inventory_at = now`."""
    link.present_on_server = present
    link.last_inventory_at = datetime.now(timezone.utc)
    await db.flush()
    return link


async def mark_links_inventoried_bulk(
    db: AsyncSession,
    server_id: str,
    account_ids: list[str],
    *,
    present: bool,
) -> int:
    """Bulk-вариант `mark_link_inventoried` — один UPDATE на N связок.

    Reconcile-цикл (`receive_users_inventory`) обходит N юзеров и на каждом
    вызывает `mark_link_inventoried`, которая делает `flush` (один UPDATE
    через ORM dirty-механизм). Bulk-апдейт сворачивает их в один statement,
    что снимает N round-trip'ов до Postgres на больших инвентаризациях.

    Если `account_ids` пуст — no-op, 0. Возвращает число обновлённых строк
    (для метрик, не строго используется).
    """
    if not account_ids:
        return 0
    from sqlalchemy import update as sa_update

    stmt = (
        sa_update(ServerAccountServer)
        .where(
            ServerAccountServer.server_id == server_id,
            ServerAccountServer.account_id.in_(account_ids),
        )
        .values(
            present_on_server=present,
            last_inventory_at=datetime.now(timezone.utc),
        )
        # `fetch` синхронизирует session-identity-map после UPDATE — caller'ы,
        # которые уже подтянули связки (тесты или сама `receive_users_inventory`
        # после `list_links_for_server`), увидят новое состояние без явного
        # `refresh`.
        .execution_options(synchronize_session="fetch")
    )
    result = await db.execute(stmt)
    return result.rowcount or 0


async def set_link_presence(
    db: AsyncSession,
    link: ServerAccountServer,
    *,
    present: bool,
) -> ServerAccountServer:
    """Проставить связке только `present_on_server`.

    В отличие от `mark_link_inventoried`, `last_inventory_at` не трогается —
    provision/deprovision это не инвентаризация, а целевое изменение состояния
    OS-пользователя на боксе.
    """
    link.present_on_server = present
    await db.flush()
    return link


async def rename_login(
    db: AsyncSession, account: ServerAccount, new_login: str
) -> ServerAccount:
    """Переименовать логин аккаунта + синхронно все денормализованные копии на связках.

    `ServerAccountServer.login` хранит копию логина для constraint
    `uq_server_login` (один логин на сервер) — её надо двигать вместе с
    `ServerAccount.login`, иначе join разъедется с аккаунтом. Конфликт по
    (server_id, login) на любом из привязанных серверов поднимет IntegrityError
    (uq_server_login) — caller трактует как 409. commit — на caller'е.
    """
    account.login = new_login
    for link in account.server_links:
        link.login = new_login
    await db.flush()
    await db.refresh(account)
    return account


async def update_ssh_key(
    db: AsyncSession,
    account: ServerAccount,
    *,
    ssh_public_key: str,
    ssh_private_key_encrypted: str | None,
) -> ServerAccount:
    """Записать новый SSH-ключ аккаунта.

    `ssh_private_key_encrypted=None` — режим supply (приватного у нас нет,
    клиент держит его сам); тогда сбрасываем хранимый зашифрованный private,
    чтобы public и private не разъезжались. commit — на caller'е.
    """
    account.ssh_public_key = ssh_public_key
    account.ssh_private_key_encrypted = ssh_private_key_encrypted
    await db.flush()
    return account


async def update_password(
    db: AsyncSession, account: ServerAccount, password_encrypted: str
) -> ServerAccount:
    """Сменить пароль с удержанием прежнего на время переходного периода.

    Текущий ciphertext переезжает в `previous_password_encrypted` (+ timestamp
    из старого `password_rotated_at`, либо now, если его не было), затем
    записывается новый пароль и поднимается `credentials_pending_apply`. Старый
    пароль остаётся доступным, пока новый не раскатан на все привязанные
    серверы. commit — на caller'е.

    Если текущего пароля нет (discovered-аккаунт без сохранённого пароля),
    previous не трогаем — переносить нечего.
    """
    now = datetime.now(timezone.utc)
    if account.password_encrypted is not None:
        account.previous_password_encrypted = account.password_encrypted
        account.previous_password_rotated_at = account.password_rotated_at or now
    account.password_encrypted = password_encrypted
    account.password_rotated_at = now
    account.credentials_pending_apply = True
    await db.flush()
    return account


async def clear_previous_password(
    db: AsyncSession, account: ServerAccount
) -> ServerAccount:
    """Занулить прежний пароль — переходный период ротации закончен.

    Зовётся, когда новый пароль подтверждён на серверах (снят
    `credentials_pending_apply`). Идемпотентна: если previous уже пуст —
    no-op. commit — на caller'е.
    """
    if (
        account.previous_password_encrypted is None
        and account.previous_password_rotated_at is None
    ):
        return account
    account.previous_password_encrypted = None
    account.previous_password_rotated_at = None
    await db.flush()
    return account
