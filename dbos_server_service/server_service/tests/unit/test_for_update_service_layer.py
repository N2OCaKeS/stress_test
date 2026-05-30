"""Тесты FOR UPDATE на уровне сервисного слоя.

`_load_account_visible_for_update` применяется в update_account, link_servers,
unlink_servers. Покрывает:
* cross-dept аккаунт → 404 (dept-isolation через FOR UPDATE путь);
* несуществующий account_id → 404;
* no-op PATCH (пустые изменения) не трогает БД и возвращает пустой applied_fields;
* no-op PATCH (поля уже равны новым значениям) — applied_fields пуст;
* unlink последнего сервера → 409 ACCOUNT_NO_SERVERS;
* unlink несвязанного сервера → 404 ACCOUNT_SERVER_LINK_NOT_FOUND.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.core.constants import Action, EntityType
from src.models import ServerAccount
from src.schemas.identity import IdentityContext
from src.schemas.server_account import ServerAccountServersUpdate, ServerAccountUpdate
from src.services import server_account as svc
from src.core.exceptions import ConflictError, NotFoundError


def _make_identity(*, dept: str, user_id: str | None = None) -> IdentityContext:
    uid = user_id or f"usr_{uuid.uuid4().hex[:8]}"
    return IdentityContext(
        user_id=uid,
        username="tester",
        department_id=dept,
        platform_role=None,
        service_roles={"server_service": ["admin"]},
        allowed_services=["server_service"],
        is_banned=False,
    )


class TestLoadAccountVisibleForUpdateCrossServiceLayer:
    """Dept-isolation через _load_account_visible_for_update из публичных use case'ов."""

    async def test_update_cross_dept_account_raises_404(
        self, db, make_server, make_account,
    ):
        """PATCH аккаунта из чужого dept → NotFoundError."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="root")

        identity_b = _make_identity(dept="dep_b")
        with pytest.raises(NotFoundError) as exc_info:
            await svc.update_account(
                db, identity_b, acc.id,
                ServerAccountUpdate(),  # пустой PATCH
            )
        assert exc_info.value.error_code == "ACCOUNT_NOT_FOUND"

    async def test_update_nonexistent_account_raises_404(self, db):
        identity = _make_identity(dept="dep_a")
        with pytest.raises(NotFoundError) as exc_info:
            await svc.update_account(
                db, identity, "acc_nonexistent_xyz",
                ServerAccountUpdate(),
            )
        assert exc_info.value.error_code == "ACCOUNT_NOT_FOUND"


class TestUpdateAccountNoop:
    """No-op PATCH через сервисный слой."""

    async def test_empty_patch_returns_empty_applied_fields(
        self, db, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="noop_user", has_sudo=False)
        identity = _make_identity(dept="dep_a")

        obj, applied = await svc.update_account(
            db, identity, acc.id,
            ServerAccountUpdate(),  # все поля unset
        )
        assert applied == set()
        assert obj.id == acc.id

    async def test_patch_same_values_is_noop(
        self, db, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="same_user", has_sudo=False)
        identity = _make_identity(dept="dep_a")

        # Устанавливаем то же значение — изменений нет.
        obj, applied = await svc.update_account(
            db, identity, acc.id,
            ServerAccountUpdate(has_sudo=False),
        )
        assert applied == set()

    async def test_unix_groups_same_set_is_noop(
        self, db, make_server, make_account,
    ):
        """unix_groups сравниваются как множества — разный порядок не меняние."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(
            server_id=srv.id, login="groups_user",
            unix_groups=["sudo", "docker"],
        )
        identity = _make_identity(dept="dep_a")

        obj, applied = await svc.update_account(
            db, identity, acc.id,
            ServerAccountUpdate(unix_groups=["docker", "sudo"]),  # тот же набор
        )
        assert "unix_groups" not in applied


class TestUnlinkServersEdgeCases:
    async def test_unlink_last_server_raises_conflict(
        self, db, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="last_link")
        identity = _make_identity(dept="dep_a")

        with pytest.raises(ConflictError) as exc_info:
            await svc.unlink_servers(
                db, identity, acc.id,
                ServerAccountServersUpdate(server_ids=[srv.id]),
            )
        assert exc_info.value.error_code == "ACCOUNT_NO_SERVERS"

    async def test_unlink_not_linked_server_raises_404(
        self, db, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        other = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="one_link")
        identity = _make_identity(dept="dep_a")

        with pytest.raises(NotFoundError) as exc_info:
            await svc.unlink_servers(
                db, identity, acc.id,
                ServerAccountServersUpdate(server_ids=[other.id]),
            )
        assert exc_info.value.error_code == "ACCOUNT_SERVER_LINK_NOT_FOUND"

    async def test_link_servers_cross_dept_raises_404(
        self, db, make_server, make_account,
    ):
        """Линковка аккаунта dep_a к серверу dep_b → 404."""
        srv_a = await make_server(department_id="dep_a")
        srv_b = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv_a.id, login="cross_link")
        identity = _make_identity(dept="dep_a")

        with pytest.raises(NotFoundError):
            await svc.link_servers(
                db, identity, acc.id,
                ServerAccountServersUpdate(server_ids=[srv_b.id]),
            )


class TestForUpdateRepo:
    """get_for_update через репозиторий напрямую — отдельные сессии."""

    async def test_none_for_missing_account(self, db):
        from src.repositories import server_account as repo
        obj = await repo.get_for_update(db, "acc_does_not_exist_xyz")
        assert obj is None

    async def test_returns_account_row(
        self, db, make_server, make_account,
    ):
        from src.repositories import server_account as repo
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="for_update_test")

        obj = await repo.get_for_update(db, acc.id)
        assert obj is not None
        assert obj.id == acc.id
        assert obj.login == "for_update_test"
