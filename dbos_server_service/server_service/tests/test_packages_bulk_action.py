"""Тесты `POST /api/server/v1/servers/packages/bulk-action` — массовая мутация.

Эндпоинт диспатчит на каждый видимый prepared-сервер изменяющую задачу
`installed_packages.{install|remove|update}` (worker замочен — фиксируем shape
dispatch'а и per-server статусы, как в `test_installed_packages_bulk.py`).

Покрытие:

* install/remove/update — корректный task_kind на сервер, payload несёт
  `packages`/`operation`.
* reserve-gate — занятый чужим оператором сервер → status=reserved, без dispatch'а.
* prepare-gate — неподготовленный → status=prepare_required.
* RBAC — без `(server, manage_packages)` → 403 на весь батч.
* валидация имён пакетов — инъекция / пустой install → 422.
* update без packages — обновить всё (dispatch проходит).
* cap серверов → 413.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1"

from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402

URL = f"{BASE}/servers/packages/bulk-action"


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват `worker_client.dispatch_task_with_hit` в общей dispatch-обвязке."""
    calls: list[dict] = []

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            priority=0,
                            return_hit=False):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "target_resource_id": target_resource_id,
            "payload": payload,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_pkg_act_{len(calls)}"
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    monkeypatch.setattr(
        "src.api.v1.endpoints._dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints._dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


async def _prepared(db, srv, management_user="dbos"):
    srv.is_managed = True
    srv.management_user = management_user
    await db.flush()
    return srv


def _by_id(results: list[dict]) -> dict[str, dict]:
    return {r["server_id"]: r for r in results}


# ── 1. install / remove / update happy path ─────────────────────────────────


class TestActionDispatch:
    @pytest.mark.parametrize(
        "action,task_kind",
        [
            ("install", "installed_packages.install"),
            ("remove", "installed_packages.remove"),
            ("update", "installed_packages.update"),
        ],
    )
    async def test_dispatches_action_per_server(
        self, client, operator_token_a, make_server, captured_dispatch, db,
        action, task_kind,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        for s in (srv1, srv2):
            await _prepared(db, s)

        resp = await client.post(
            URL,
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv1.id, srv2.id], "action": action, "packages": ["sl", "cowsay"]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["action"] == action
        assert body["packages"] == ["sl", "cowsay"]
        assert body["requested"] == 2
        assert body["dispatched"] == 2
        assert len(captured_dispatch) == 2
        for call in captured_dispatch:
            assert call["task_kind"] == task_kind
            assert call["payload"]["packages"] == ["sl", "cowsay"]
            assert call["payload"]["operation"] == action
            assert call["target_resource_id"] is None
        by_id = _by_id(body["results"])
        for s in (srv1, srv2):
            assert by_id[s.id]["status"] == "ok"
            assert by_id[s.id]["task_id"].startswith("tsk_pkg_act_")

    async def test_update_without_packages_updates_all(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "action": "update"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["dispatched"] == 1
        assert body["packages"] == []
        assert captured_dispatch[0]["payload"]["packages"] == []
        assert captured_dispatch[0]["payload"]["operation"] == "update"


# ── 2. Reserve-gate ──────────────────────────────────────────────────────────


class TestReserveGate:
    async def test_reserved_for_other_returns_reserved_status(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        """Сервер занят (busy) другим пользователем → status=reserved, без dispatch'а."""
        from src.core.constants import BusyState

        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_someone_else"
        await db.flush()

        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "action": "install", "packages": ["sl"]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["dispatched"] == 0
        assert _by_id(body["results"])[srv.id]["status"] == "reserved"
        assert len(captured_dispatch) == 0

    async def test_reserved_for_self_passes(
        self, client, make_token, make_server, captured_dispatch, db, dept_a,
    ):
        """Владелец брони может менять пакеты на своём занятом сервере."""
        from src.core.constants import BusyState, ServiceRole

        token = make_token(
            user_id="usr_owner",
            department_id=dept_a,
            service_roles={"server_service": [ServiceRole.OPERATOR]},
        )
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_owner"
        await db.flush()

        resp = await client.post(
            URL, headers=_hdr(token),
            json={"server_ids": [srv.id], "action": "install", "packages": ["sl"]},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["dispatched"] == 1
        assert len(captured_dispatch) == 1


# ── 3. Prepare / visibility / decommissioned ─────────────────────────────────


class TestPerServerStatuses:
    async def test_unprepared_returns_prepare_required(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        ok = await make_server(department_id="dep_a")
        await _prepared(db, ok)
        unprepared = await make_server(department_id="dep_a")

        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [ok.id, unprepared.id], "action": "install", "packages": ["sl"]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        by_id = _by_id(body["results"])
        assert by_id[ok.id]["status"] == "ok"
        assert by_id[unprepared.id]["status"] == "prepare_required"
        assert body["dispatched"] == 1
        assert len(captured_dispatch) == 1

    async def test_cross_dept_becomes_not_found(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        ok = await make_server(department_id="dep_a")
        await _prepared(db, ok)
        foreign = await make_server(department_id="dep_b")

        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [ok.id, foreign.id], "action": "remove", "packages": ["sl"]},
        )
        assert resp.status_code == 202, resp.text
        by_id = _by_id(resp.json()["results"])
        assert by_id[ok.id]["status"] == "ok"
        assert by_id[foreign.id]["status"] == "not_found"
        assert by_id[foreign.id]["hostname"] is None

    async def test_decommissioned_status(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        from src.core.constants import ServerStatus

        ok = await make_server(department_id="dep_a")
        await _prepared(db, ok)
        dead = await make_server(department_id="dep_a")
        await _prepared(db, dead)
        dead.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [ok.id, dead.id], "action": "update", "packages": ["sl"]},
        )
        assert resp.status_code == 202, resp.text
        by_id = _by_id(resp.json()["results"])
        assert by_id[ok.id]["status"] == "ok"
        assert by_id[dead.id]["status"] == "decommissioned"


# ── 4. RBAC ──────────────────────────────────────────────────────────────────


class TestRbac:
    async def test_guest_without_manage_packages_403(
        self, client, guest_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(guest_token_a),
            json={"server_ids": [srv.id], "action": "install", "packages": ["sl"]},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert len(captured_dispatch) == 0

    async def test_reader_without_manage_packages_403(
        self, client, reader_token_a, make_server, captured_dispatch, db,
    ):
        """reader имеет view (live-список), но НЕ manage_packages."""
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(reader_token_a),
            json={"server_ids": [srv.id], "action": "install", "packages": ["sl"]},
        )
        assert resp.status_code == 403, resp.text
        assert len(captured_dispatch) == 0

    async def test_admin_role_allowed(
        self, client, admin_role_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(admin_role_token_a),
            json={"server_ids": [srv.id], "action": "install", "packages": ["sl"]},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["dispatched"] == 1


# ── 5. Валидация имён пакетов / тела ─────────────────────────────────────────


class TestValidation:
    @pytest.mark.parametrize("bad", [
        "sl; rm -rf /",
        "$(reboot)",
        "`id`",
        "foo bar",
        "foo|cat",
        "*",
        "-malformed",
    ])
    async def test_invalid_package_name_422(
        self, client, operator_token_a, make_server, captured_dispatch, db, bad,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "action": "install", "packages": [bad]},
        )
        assert resp.status_code == 422, resp.text
        assert len(captured_dispatch) == 0

    async def test_empty_packages_install_422(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "action": "install", "packages": []},
        )
        assert resp.status_code == 422, resp.text
        assert len(captured_dispatch) == 0

    async def test_unknown_action_422(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "action": "purge", "packages": ["sl"]},
        )
        assert resp.status_code == 422, resp.text

    async def test_legit_names_accepted(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "action": "install",
                  "packages": ["linux-image-amd64", "g++", "python3.11", "lib32z1"]},
        )
        assert resp.status_code == 202, resp.text
        assert captured_dispatch[0]["payload"]["packages"] == [
            "linux-image-amd64", "g++", "python3.11", "lib32z1",
        ]


# ── 6. Cap серверов ──────────────────────────────────────────────────────────


class TestServerCap:
    async def test_too_many_servers_413(
        self, client, operator_token_a, captured_dispatch, monkeypatch,
    ):
        from src.core.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("INSTALLED_PACKAGES_BULK_MAX_SERVERS", "2")
        get_settings.cache_clear()
        try:
            resp = await client.post(
                URL, headers=_hdr(operator_token_a),
                json={"server_ids": ["s1", "s2", "s3"], "action": "install", "packages": ["sl"]},
            )
            assert_error(resp, 413, "BULK_PACKAGES_TOO_LARGE")
            assert len(captured_dispatch) == 0
        finally:
            get_settings.cache_clear()
