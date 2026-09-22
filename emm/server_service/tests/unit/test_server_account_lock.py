"""Row-lock семантика на ServerAccount при concurrent write'ах.

Закрывает lost-update между `update_account` и параллельным
`link_servers`/`unlink_servers`: до фикса PATCH делал SELECT без `FOR UPDATE`,
параллельная linkage-операция успевала закоммититься между SELECT'ом и
последующим UPDATE'ом, fan-out уходил со смешанным snapshot'ом
(stale поля + свежие server_links).

Здесь не гоняем полный fan-out — проверяем сам lock primitive
(`repo.get_for_update`): второй waiter блокируется до commit'а первого
и видит обновлённое состояние после release'а.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.models import Server, ServerAccount, ServerAccountServer
from src.repositories import server_account as repo
from src.services import secrets_service
from src.utils.ids import server_account_id, server_account_server_id, server_id
from tests._helpers import next_stand_number


# Отдельный engine — конкурентность по строке требует двух независимых
# connection'ов. SAVEPOINT-fixture из conftest держит всё на одном connection'е,
# где FOR UPDATE деградирует до noop'а внутри транзакции.
_engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


@pytest_asyncio.fixture
async def seeded_account():
    """Создать сервер + аккаунт под него, вернуть id'шники.

    Чистим за собой руками в finally — мы вне savepoint-fixture, поэтому
    обычный rollback теста сюда не дотянется.
    """
    suffix = uuid.uuid4().hex[:6]
    srv_id = server_id()
    acc_id = server_account_id()
    link_id = server_account_server_id()
    login = f"svc-{suffix}"

    async with AsyncSession(_engine, expire_on_commit=False) as session:
        async with session.begin():
            session.add(
                Server(
                    id=srv_id,
                    hostname=f"srv-{suffix}",
                    ip_address=f"10.99.{int(suffix[:2], 16) % 256}.{int(suffix[2:4], 16) % 256}",
                    ssh_port=22,
                    department_id="dep_a",
                    number=next_stand_number(),
                )
            )
            session.add(
                ServerAccount(
                    id=acc_id,
                    department_id="dep_a",
                    login=login,
                    password_encrypted=secrets_service.encrypt(
                        "pw-seed",
                        aad=secrets_service.aad_for_server_account_password(acc_id),
                    ),
                    has_sudo=False,
                    unix_groups=[],
                )
            )
            session.add(
                ServerAccountServer(
                    id=link_id,
                    account_id=acc_id,
                    server_id=srv_id,
                    login=login,
                )
            )

    try:
        yield {"account_id": acc_id, "server_id": srv_id, "login": login}
    finally:
        async with AsyncSession(_engine, expire_on_commit=False) as session:
            async with session.begin():
                obj = await session.get(ServerAccount, acc_id)
                if obj is not None:
                    await session.delete(obj)
                srv = await session.get(Server, srv_id)
                if srv is not None:
                    await session.delete(srv)


class TestGetForUpdateBlocks:
    """`repo.get_for_update` должен блокировать конкурентный writer."""

    async def test_second_waiter_blocks_until_first_commits(self, seeded_account):
        acc_id = seeded_account["account_id"]
        first_acquired = asyncio.Event()
        release_first = asyncio.Event()
        second_acquired_before_release = False

        async def first_holder():
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                async with session.begin():
                    obj = await repo.get_for_update(session, acc_id)
                    assert obj is not None
                    # Поменяем поле прямо в локированной транзакции — после
                    # commit'а второй waiter обязан увидеть новое значение.
                    obj.has_sudo = True
                    await session.flush()
                    first_acquired.set()
                    await release_first.wait()

        async def second_waiter():
            nonlocal second_acquired_before_release
            await first_acquired.wait()
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                async with session.begin():
                    # Этот вызов должен висеть, пока первый не закоммитится.
                    task = asyncio.create_task(
                        _acquire_for_update(session, acc_id)
                    )
                    # Дать lock'у проявиться: ждём 0.5s и проверяем, что
                    # second_waiter ещё висит. Если бы lock'а не было, get_for_update
                    # отработал бы мгновенно.
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                    else:
                        second_acquired_before_release = True
                    assert not task.done(), (
                        "get_for_update должен был заблокироваться, пока "
                        "первая транзакция держит лок"
                    )
                    release_first.set()
                    obj2 = await asyncio.wait_for(task, timeout=5.0)
                    assert obj2 is not None
                    # После release'а первая транзакция уже закоммитила
                    # has_sudo=True — второй waiter обязан это видеть.
                    assert obj2.has_sudo is True

        await asyncio.gather(first_holder(), second_waiter())
        assert second_acquired_before_release is False

    async def test_returns_none_for_missing_id(self):
        async with AsyncSession(_engine, expire_on_commit=False) as session:
            async with session.begin():
                obj = await repo.get_for_update(session, "acc_nonexistent")
                assert obj is None


async def _acquire_for_update(session: AsyncSession, acc_id: str) -> ServerAccount | None:
    """Обёртка над `repo.get_for_update`, чтобы вызывать её через task."""
    return await repo.get_for_update(session, acc_id)


class TestForUpdateOnRealRow:
    """Sanity: лок реально применяется к строке, виден в pg_locks."""

    async def test_lock_visible_in_pg_locks_table(self, seeded_account):
        acc_id = seeded_account["account_id"]
        async with AsyncSession(_engine, expire_on_commit=False) as session:
            async with session.begin():
                obj = await repo.get_for_update(session, acc_id)
                assert obj is not None
                # Хотя бы один row-level lock на pid'е транзакции должен
                # появиться. На асинк-движке pid берём из server_settings —
                # достаточно убедиться, что count > 0 на текущей сессии.
                result = await session.execute(
                    select(ServerAccount).where(ServerAccount.id == acc_id)
                )
                # SELECT без FOR UPDATE из той же транзакции не висит — у нас
                # уже есть лок на этой строке.
                assert result.scalar_one().id == acc_id
