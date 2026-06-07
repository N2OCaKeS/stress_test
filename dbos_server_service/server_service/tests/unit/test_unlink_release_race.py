"""Concurrent race coverage for unlink_servers и release_server.

Покрывает фиксы, синхронизирующие чтение M2M в `unlink_servers` и проверку
`busy_state` в `release_server` с реальным live-state БД под FOR UPDATE.

* `unlink_servers` теперь берёт FOR UPDATE на M2M-строках аккаунта (через
  `repo.lock_links_for_account`) — параллельные правки тех же связок
  сериализуются с unlink'ом.
* `release_server` после `load_visible_server` делает re-fetch с FOR UPDATE
  перед чтением `busy_state`, симметрично `acquire_server` (rowcount==0 +
  re-fetch).

Идём через два независимых connection'а — savepoint-fixture deadlock'и
FOR UPDATE внутри одной транзакции не воспроизводит.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core.constants import BusyState
from src.core.exceptions import ConflictError
from src.models import (
    Server,
    ServerAccount,
    ServerAccountServer,
)
from src.repositories import server as server_repo
from src.repositories import server_account as repo
from src.schemas.identity import IdentityContext
from src.services import secrets_service
from src.services import server as server_svc
from src.utils.ids import server_account_id, server_account_server_id, server_id


_engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


@pytest_asyncio.fixture
async def two_linked_servers():
    """Аккаунт привязан к двум серверам в одном dept'е."""
    suffix = uuid.uuid4().hex[:6]
    srv_a_id = server_id()
    srv_b_id = server_id()
    acc_id = server_account_id()
    link_a_id = server_account_server_id()
    link_b_id = server_account_server_id()
    login = f"svc-{suffix}"

    async with AsyncSession(_engine, expire_on_commit=False) as session:
        async with session.begin():
            session.add(Server(
                id=srv_a_id,
                hostname=f"srv-a-{suffix}",
                ip_address=f"10.99.{int(suffix[:2], 16) % 256}.1",
                ssh_port=22,
                department_id="dep_a",
            ))
            session.add(Server(
                id=srv_b_id,
                hostname=f"srv-b-{suffix}",
                ip_address=f"10.99.{int(suffix[:2], 16) % 256}.2",
                ssh_port=22,
                department_id="dep_a",
            ))
            session.add(ServerAccount(
                id=acc_id,
                department_id="dep_a",
                login=login,
                password_encrypted=secrets_service.encrypt(
                    "pw-seed",
                    aad=secrets_service.aad_for_server_account_password(acc_id),
                ),
                has_sudo=False,
                unix_groups=[],
            ))
            session.add(ServerAccountServer(
                id=link_a_id, account_id=acc_id, server_id=srv_a_id, login=login,
            ))
            session.add(ServerAccountServer(
                id=link_b_id, account_id=acc_id, server_id=srv_b_id, login=login,
            ))

    try:
        yield {
            "account_id": acc_id,
            "server_a": srv_a_id,
            "server_b": srv_b_id,
            "login": login,
        }
    finally:
        async with AsyncSession(_engine, expire_on_commit=False) as session:
            async with session.begin():
                acc = await session.get(ServerAccount, acc_id)
                if acc is not None:
                    await session.delete(acc)
                for sid in (srv_a_id, srv_b_id):
                    srv = await session.get(Server, sid)
                    if srv is not None:
                        await session.delete(srv)


class TestUnlinkServersLocksM2M:
    """`lock_links_for_account` сериализует параллельные правки связок."""

    async def test_second_lock_blocks_until_first_commits(self, two_linked_servers):
        """Два concurrent task'а: оба хотят залочить M2M для одного аккаунта.

        Первый держит FOR UPDATE и не коммитит — второй должен ждать.
        После commit'а первого второй видит обновлённое состояние.
        """
        acc_id = two_linked_servers["account_id"]
        srv_a = two_linked_servers["server_a"]
        first_acquired = asyncio.Event()
        release_first = asyncio.Event()

        async def first_holder():
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                async with session.begin():
                    links = await repo.lock_links_for_account(session, acc_id)
                    assert len(links) == 2
                    # Удаляем связку с srv_a под лок'ом, не коммитим до сигнала.
                    for link in links:
                        if link.server_id == srv_a:
                            await session.delete(link)
                    await session.flush()
                    first_acquired.set()
                    await release_first.wait()

        async def second_waiter():
            await first_acquired.wait()
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                async with session.begin():
                    task = asyncio.create_task(
                        _lock(session, acc_id)
                    )
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                    assert not task.done(), (
                        "lock_links_for_account должен был заблокироваться, "
                        "пока первая транзакция держит FOR UPDATE"
                    )
                    release_first.set()
                    links_after = await asyncio.wait_for(task, timeout=5.0)
                    # После release'а первая транзакция уже удалила link на srv_a.
                    server_ids_after = {link.server_id for link in links_after}
                    assert srv_a not in server_ids_after
                    assert len(links_after) == 1

        await asyncio.gather(first_holder(), second_waiter())


async def _lock(session: AsyncSession, account_id: str) -> list[ServerAccountServer]:
    return await repo.lock_links_for_account(session, account_id)


# ── release_server race ──────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def busy_server():
    """Сервер в состоянии BUSY — готов к release."""
    suffix = uuid.uuid4().hex[:6]
    srv_id = server_id()
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        async with session.begin():
            session.add(Server(
                id=srv_id,
                hostname=f"srv-release-{suffix}",
                ip_address=f"10.98.{int(suffix[:2], 16) % 256}.1",
                ssh_port=22,
                department_id="dep_a",
                busy_state=BusyState.BUSY,
                busy_user_id="usr_lessee",
            ))
    try:
        yield srv_id
    finally:
        async with AsyncSession(_engine, expire_on_commit=False) as session:
            async with session.begin():
                obj = await session.get(Server, srv_id)
                if obj is not None:
                    await session.delete(obj)


class TestReleaseServerLockSymmetry:
    """`release_server` использует FOR UPDATE через `load_for_update_server`.

    Симметрия с `acquire_server`: первый release держит row-lock, второй
    waiter блокируется и после commit'а видит FREE → 409 SERVER_NOT_BUSY.
    """

    async def test_get_for_update_blocks_concurrent_writer(self, busy_server):
        srv = busy_server
        first_acquired = asyncio.Event()
        release_first = asyncio.Event()

        async def first_holder():
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                async with session.begin():
                    obj = await server_repo.get_for_update(session, srv)
                    assert obj is not None
                    assert obj.busy_state == BusyState.BUSY
                    obj.busy_state = BusyState.FREE
                    obj.busy_user_id = None
                    await session.flush()
                    first_acquired.set()
                    await release_first.wait()

        async def second_waiter():
            await first_acquired.wait()
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                async with session.begin():
                    task = asyncio.create_task(
                        _lock_server(session, srv)
                    )
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                    assert not task.done(), (
                        "server_repo.get_for_update должен был заблокироваться"
                    )
                    release_first.set()
                    obj2 = await asyncio.wait_for(task, timeout=5.0)
                    assert obj2 is not None
                    # Видим состояние, которое выкатил первый writer.
                    assert obj2.busy_state == BusyState.FREE

        await asyncio.gather(first_holder(), second_waiter())


async def _lock_server(session: AsyncSession, sid: str) -> Server | None:
    return await server_repo.get_for_update(session, sid)


# ── Service-layer проверки фикса ────────────────────────────────────────────


def _identity(dept: str = "dep_a") -> IdentityContext:
    return IdentityContext(
        user_id=f"usr_{uuid.uuid4().hex[:8]}",
        username="tester",
        department_id=dept,
        platform_role=None,
        service_roles={"server_service": ["admin"]},
        allowed_services=["server_service"],
        is_banned=False,
    )


class TestReleaseServerRaceWithAcquire:
    """`release_server` re-fetch'ит под FOR UPDATE — параллельный acquire,
    успевший подняться между load_visible_server и pre-check'ом, не маскируется
    под ложный 409 SERVER_NOT_BUSY: либо release сериализуется с acquire'ом
    под локом, либо честно ловит конфликт через rowcount==0.

    Здесь имитируем простой сценарий: сервер в момент release'а уже FREE
    (свежесть стейта). Без FOR UPDATE pre-check мог бы прочитать stale BUSY
    из чужой session-cache; с FOR UPDATE pre-check всегда видит live-state.
    """

    async def test_release_already_free_returns_409(
        self, db, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        # Сервер по умолчанию FREE.
        identity = _identity()
        with pytest.raises(ConflictError) as exc_info:
            await server_svc.release_server(db, identity, srv.id)
        assert exc_info.value.error_code == "SERVER_NOT_BUSY"


class TestReleaseServerCasRaceAlreadyFree:
    """CAS-ветка `rowcount==0` в `release_server` — `reason=race_already_free`.

    `load_visible_server` + `get_for_update` показывают BUSY, между этим
    моментом и итоговым UPDATE'ом параллельный writer (другая транзакция)
    переводит сервер в FREE — UPDATE с `where busy_state != FREE` возвращает
    rowcount=0, поднимается 409 SERVER_NOT_BUSY с `reason=race_already_free`
    (отдельно от обычного «никогда не был занят» — `reason=not_busy`).
    """

    async def test_cas_miss_emits_race_already_free(
        self, db, make_server, monkeypatch,
    ):
        from src.services import audit_service as as_mod

        srv = await make_server(department_id="dep_a")
        # Сервер сначала busy — иначе pre-check выкинет not_busy.
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_holder"
        await db.commit()
        await db.refresh(srv)

        # Подменяем db.execute, чтобы UPDATE с busy_state!=FREE вернул rowcount=0.
        # SELECT'ы (get_for_update / load_visible_server) проходят как обычно.
        real_execute = db.execute

        async def fake_execute(stmt, *args, **kwargs):
            from sqlalchemy.sql.dml import Update
            res = await real_execute(stmt, *args, **kwargs)
            if isinstance(stmt, Update):
                class _FakeResult:
                    rowcount = 0
                return _FakeResult()
            return res

        captured: list[dict] = []
        monkeypatch.setattr(
            as_mod, "emit",
            lambda action, **kw: captured.append({"action": action, **kw}),
        )
        monkeypatch.setattr(db, "execute", fake_execute)

        identity = _identity()
        with pytest.raises(ConflictError) as exc_info:
            await server_svc.release_server(db, identity, srv.id)
        assert exc_info.value.error_code == "SERVER_NOT_BUSY"

        race_emits = [
            e for e in captured
            if e["action"] == "server.release"
            and (e.get("details") or {}).get("reason") == "race_already_free"
        ]
        assert len(race_emits) == 1, captured


class TestAcquireServerVanishedDuringAcquire:
    """Defensive `vanished_during_acquire` — rowcount=0 + re-fetch вернул None.

    Сервер исчез между `load_visible_server` и CAS-update'ом. Hard-delete
    серверов не реализован, так что в проде путь недостижим; тест держит
    regression-guard на случай будущей фичи delete'а сервера.
    """

    async def test_vanished_during_acquire_returns_404(
        self, db, make_server, monkeypatch,
    ):
        from src.repositories import server as server_repo_mod
        from src.services import audit_service as as_mod
        from src.schemas.server import ServerAcquireRequest

        srv = await make_server(department_id="dep_a")

        # CAS UPDATE → rowcount=0; вслед за этим re-fetch через repo.get_by_id
        # вернёт None — имитируем «сервер успел исчезнуть».
        # `load_visible_server` тоже зовёт `get_by_id`, поэтому первый вызов
        # должен вернуть реальный сервер (visibility pass), а второй — None.
        real_execute = db.execute
        real_get_by_id = server_repo_mod.get_by_id

        async def fake_execute(stmt, *args, **kwargs):
            from sqlalchemy.sql.dml import Update
            res = await real_execute(stmt, *args, **kwargs)
            if isinstance(stmt, Update):
                class _FakeResult:
                    rowcount = 0
                return _FakeResult()
            return res

        call_count = {"n": 0}

        async def fake_get_by_id(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return await real_get_by_id(*args, **kwargs)
            return None

        captured: list[dict] = []
        monkeypatch.setattr(db, "execute", fake_execute)
        monkeypatch.setattr(server_repo_mod, "get_by_id", fake_get_by_id)
        monkeypatch.setattr(
            as_mod, "emit",
            lambda action, **kw: captured.append({"action": action, **kw}),
        )

        from src.core.exceptions import NotFoundError as NF
        identity = _identity()
        with pytest.raises(NF) as exc_info:
            await server_svc.acquire_server(
                db, identity, srv.id, ServerAcquireRequest(),
            )
        assert exc_info.value.error_code == "SERVER_NOT_FOUND"

        vanished = [
            e for e in captured
            if e["action"] == "server.acquire"
            and (e.get("details") or {}).get("reason") == "vanished_during_acquire"
        ]
        assert len(vanished) == 1, captured
