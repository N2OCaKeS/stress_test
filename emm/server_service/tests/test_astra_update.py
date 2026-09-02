"""Тесты обновления ОС Astra (`server.astra_update`).

Покрытие:

* dispatch success — operator ставит задачу на prepared-сервере: 202,
  `busy_state='updating'`, payload несёт host=IP + os_version_id +
  repositories версии;
* RBAC — reader без `update` → 403;
* каталог: несуществующая версия → 404 OS_VERSION_NOT_FOUND, версия с пустыми
  repositories → 409 OS_VERSION_NO_REPOSITORIES;
* prepared-gate — неподготовленный сервер → 409 PREPARE_REQUIRED;
* busy-lock — пока `busy_state='updating'`, любые операции над сервером
  (inventory / prepare / power) отбиваются 409 SERVER_UPDATING;
* callback success — снимает блокировку, привязывает версию, триггерит
  inventory; callback failed — снимает блокировку, версию не трогает.

Реальный PostgreSQL через сервисный docker-compose.test.yml (см. conftest).
"""

from __future__ import annotations

import pytest

from src.utils.ids import _new_id
from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1"
BASE_INT = "/api/server/v1/internal"


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват `dispatch_task_with_hit` в endpoint-модуле worker_dispatch."""
    calls: list[dict] = []

    async def fake_dispatch_with_hit(
        *, db, task_kind, target_server_id, payload, created_by,
        request_id, target_resource_id=None, idempotency_key=None,
        priority=0, max_attempts=3,
    ):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
            "max_attempts": max_attempts,
        })
        return f"tsk_{len(calls)}", False

    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


@pytest.fixture
def captured_auto_inventory(monkeypatch):
    """Перехват `dispatch_task_with_hit` в auto_inventory (callback-триггер)."""
    calls: list[dict] = []

    async def fake_dispatch_with_hit(
        *, db, task_kind, target_server_id, payload, created_by,
        request_id, target_resource_id=None, idempotency_key=None, priority=0,
    ):
        calls.append({"task_kind": task_kind, "target_server_id": target_server_id})
        return f"tsk_auto_{len(calls)}", False

    monkeypatch.setattr(
        "src.services.auto_inventory.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


async def _make_managed(make_server, db, *, dept="dep_a"):
    srv = await make_server(department_id=dept)
    srv.is_managed = True
    srv.management_user = "dbos"
    await db.flush()
    return srv


async def _make_os_version(db, *, name, repositories):
    from src.models import OsVersion

    osv = OsVersion(id=_new_id("osv_"), name=name, repositories=repositories)
    db.add(osv)
    await db.flush()
    return osv


@pytest.mark.usefixtures("soft_dept_mode")
class TestAstraUpdateDispatch:
    async def test_operator_dispatches_astra_update(
        self, client, operator_token_a, make_server, db, captured_dispatch,
    ):
        srv = await _make_managed(make_server, db)
        osv = await _make_os_version(
            db, name="Astra 1.8 orel",
            repositories=["deb http://repo/orel stable main", "deb http://repo/x s m"],
        )
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/astra-update",
            headers=_hdr(operator_token_a),
            json={"os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "queued"

        assert len(captured_dispatch) == 1
        call = captured_dispatch[0]
        assert call["task_kind"] == "server.astra_update"
        assert call["target_server_id"] == srv.id
        assert call["max_attempts"] == 1
        # host — именно IP сервера (короткие имена не резолвятся из пода воркера).
        assert call["payload"]["host"] == str(srv.ip_address)
        assert call["payload"]["os_version_id"] == osv.id
        assert call["payload"]["repositories"] == [
            "deb http://repo/orel stable main", "deb http://repo/x s m",
        ]

        # Сервер помечен updating.
        got = await client.get(f"{BASE}/servers/{srv.id}", headers=_hdr(operator_token_a))
        assert got.json()["busy_state"] == "updating"

    async def test_reader_cannot_dispatch(
        self, client, reader_token_a, make_server, db, captured_dispatch,
    ):
        srv = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra r", repositories=["deb x y z"])
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/astra-update",
            headers=_hdr(reader_token_a),
            json={"os_version_id": osv.id},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch == []

    async def test_unknown_os_version_404(
        self, client, operator_token_a, make_server, db, captured_dispatch,
    ):
        srv = await _make_managed(make_server, db)
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/astra-update",
            headers=_hdr(operator_token_a),
            json={"os_version_id": "osv_missing"},
        )
        assert_error(resp, 404, "OS_VERSION_NOT_FOUND")
        assert captured_dispatch == []

    async def test_empty_repositories_409(
        self, client, operator_token_a, make_server, db, captured_dispatch,
    ):
        srv = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra empty", repositories=[])
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/astra-update",
            headers=_hdr(operator_token_a),
            json={"os_version_id": osv.id},
        )
        assert_error(resp, 409, "OS_VERSION_NO_REPOSITORIES")
        assert captured_dispatch == []

    async def test_not_prepared_409(
        self, client, operator_token_a, make_server, db, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")  # is_managed=False
        osv = await _make_os_version(db, name="Astra np", repositories=["deb x y z"])
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/astra-update",
            headers=_hdr(operator_token_a),
            json={"os_version_id": osv.id},
        )
        assert_error(resp, 409, "PREPARE_REQUIRED")
        assert captured_dispatch == []


@pytest.mark.usefixtures("soft_dept_mode")
class TestBusyLockDuringUpdate:
    """Пока `busy_state='updating'`, все операции отбиваются SERVER_UPDATING."""

    async def test_inventory_blocked_while_updating(
        self, client, operator_token_a, make_server, db, captured_dispatch,
    ):
        srv = await _make_managed(make_server, db)
        from src.core.constants import BusyState
        srv.busy_state = BusyState.UPDATING
        await db.flush()

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/inventory/sync",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "SERVER_UPDATING")
        assert captured_dispatch == []

    async def test_second_astra_update_blocked_while_updating(
        self, client, operator_token_a, make_server, db, captured_dispatch,
    ):
        srv = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra dup", repositories=["deb x y z"])
        from src.core.constants import BusyState
        srv.busy_state = BusyState.UPDATING
        await db.flush()

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/astra-update",
            headers=_hdr(operator_token_a),
            json={"os_version_id": osv.id},
        )
        assert_error(resp, 409, "SERVER_UPDATING")
        assert captured_dispatch == []


@pytest.mark.usefixtures("soft_dept_mode")
class TestAstraUpdateRace:
    """Гонка перехода в `updating`: без row-lock два astra-update прошли бы оба."""

    async def test_concurrent_astra_update_second_blocked_by_row_lock(
        self, client, operator_token_a, make_server, db, captured_dispatch,
        monkeypatch,
    ):
        srv = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra race", repositories=["deb x y z"])
        from sqlalchemy import select, update as sa_update

        from src.core.constants import BusyState
        from src.models import Server
        from src.services import server as server_svc

        original_get = server_svc.get_server

        async def racy_get(db_, identity, sid):
            obj = await original_get(db_, identity, sid)
            # Параллельный astra-update выставил updating и закоммитил между нашим
            # visibility-чтением и постановкой блокировки. `obj` держит stale
            # busy_state=free; row-lock (get_for_update) обязан перечитать свежее.
            await db_.execute(
                sa_update(Server).where(Server.id == sid).values(
                    busy_state=BusyState.UPDATING,
                    busy_note="Обновление ОС Astra (параллельное)",
                )
            )
            await db_.commit()
            return obj

        monkeypatch.setattr(server_svc, "get_server", racy_get)

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/astra-update",
            headers=_hdr(operator_token_a),
            json={"os_version_id": osv.id},
        )
        # Row-lock перечитал updating → 409, вторая задача не задиспатчена
        # (нет двойного apt/astra-update на боксе).
        assert_error(resp, 409, "SERVER_UPDATING")
        assert captured_dispatch == []

        row = (
            await db.execute(select(Server).where(Server.id == srv.id))
        ).scalar_one()
        assert row.busy_state == BusyState.UPDATING


@pytest.mark.usefixtures("soft_dept_mode")
class TestAstraUpdateStuckRecovery:
    """Залипший `updating` (callback не пришёл) освобождается sweep'ом по TTL."""

    async def test_stuck_updating_recovered_after_ttl(
        self, client, worker_bot_token_a, make_server, db, dept_a,
        captured_auto_inventory, monkeypatch,
    ):
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select

        from src.core.constants import BusyState
        from src.models import Server

        srv = await _make_managed(make_server, db, dept=dept_a)
        # Обновление началось давно, callback `astra-updated` так и не пришёл.
        srv.busy_state = BusyState.UPDATING
        srv.busy_user_id = "usr_op"
        srv.busy_since = datetime.now(timezone.utc) - timedelta(hours=6)
        srv.busy_note = "Обновление ОС Astra"
        await db.flush()

        resp = await client.post(
            f"{BASE_INT}/servers/auto-inventory-sweep",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["stuck_updating_recovered"] == 1

        row = (
            await db.execute(select(Server).where(Server.id == srv.id))
        ).scalar_one()
        assert row.busy_state == BusyState.FREE
        assert row.busy_user_id is None
        assert row.busy_since is None

    async def test_recent_updating_not_recovered(
        self, client, worker_bot_token_a, make_server, db, dept_a,
        captured_auto_inventory,
    ):
        from datetime import datetime, timezone

        from sqlalchemy import select

        from src.core.constants import BusyState
        from src.models import Server

        srv = await _make_managed(make_server, db, dept=dept_a)
        # Обновление идёт прямо сейчас — трогать нельзя.
        srv.busy_state = BusyState.UPDATING
        srv.busy_since = datetime.now(timezone.utc)
        await db.flush()

        resp = await client.post(
            f"{BASE_INT}/servers/auto-inventory-sweep",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["stuck_updating_recovered"] == 0

        row = (
            await db.execute(select(Server).where(Server.id == srv.id))
        ).scalar_one()
        assert row.busy_state == BusyState.UPDATING
        # Sweep-фан-аут пропускает updating (не диспатчит inventory во время апдейта).
        assert captured_auto_inventory == []


@pytest.mark.usefixtures("soft_dept_mode")
class TestAstraUpdateCallback:
    async def test_success_clears_lock_binds_version_triggers_inventory(
        self, client, worker_bot_token_a, make_server, db, dept_a, captured_auto_inventory,
    ):
        srv = await _make_managed(make_server, db, dept=dept_a)
        osv = await _make_os_version(db, name="Astra done", repositories=["deb x y z"])
        from src.core.constants import BusyState
        srv.busy_state = BusyState.UPDATING
        srv.busy_note = "Обновление ОС Astra до Astra done"
        await db.flush()

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/astra-updated",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"os_version_id": osv.id, "succeeded": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Блокировка снята, версия привязана — из ответа callback'а.
        assert body["os_version_id"] == osv.id
        assert body["busy_state"] == "free"
        # inventory.sync + power.status задиспатчены авто-обновлением.
        assert {c["task_kind"] for c in captured_auto_inventory} == {
            "inventory.sync", "power.status",
        }

    async def test_failed_clears_lock_keeps_version_no_inventory(
        self, client, worker_bot_token_a, make_server, db, dept_a, captured_auto_inventory,
    ):
        srv = await _make_managed(make_server, db, dept=dept_a)
        osv = await _make_os_version(db, name="Astra fail", repositories=["deb x y z"])
        from src.core.constants import BusyState
        srv.busy_state = BusyState.UPDATING
        await db.flush()

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/astra-updated",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"os_version_id": osv.id, "succeeded": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Провал: блокировка снята, версию не привязываем, inventory не гоняем.
        assert body["os_version_id"] is None
        assert body["busy_state"] == "free"
        assert captured_auto_inventory == []
