"""Тесты оркестрации очистки сервера после переустановки ОС — POST /servers/{id}/clean.

Покрывает:
* каждый флаг по отдельности (unbind / rerun_prepare / update_os_version /
  run_inventory_sync) + невыбранные → skipped(not_selected);
* комбинация всех четырёх в фиксированном порядке;
* unbind снимает связки в БД + ставит userdel-fanout;
* rerun_prepare ставит server.prepare (manual / account-режим);
* inventory.sync требует prepared-сервер (иначе failed prepare_required);
* update_os_version: reset (None → done), невалидный id → failed;
* RBAC (reader → 403), decommissioned → 409, cross-dept → 404;
* валидация тела (нет действий → 422, rerun_prepare без creds → 422);
* аудит server.clean (CRITICAL).
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import func, select

from src.core.constants import ServerStatus
from src.models import ServerAccountServer

BASE = "/api/server/v1/servers"

from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


_PWD = "Boot1234!StrongPwd"
_MANUAL_PREPARE = {"username_b64": _b64("bootadmin"), "password_b64": _b64(_PWD)}


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client store/dispatch для prepare + inventory + deprovision."""
    class _Recorder(list):
        pass

    calls: _Recorder = _Recorder()
    stored: dict[str, dict] = {}
    stored_calls: list[tuple[str, dict]] = []
    deleted: list[str] = []
    calls.stored_creds_calls = stored_calls  # type: ignore[attr-defined]
    calls.deleted_keys = deleted  # type: ignore[attr-defined]

    async def fake_store(creds_key, creds):
        stored[creds_key] = creds
        stored_calls.append((creds_key, creds))

    async def fake_delete(creds_key):
        deleted.append(creds_key)

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            priority=0, return_hit=False):
        creds_key = payload.get("bootstrap_creds_key")
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
            "stored_creds": stored.get(creds_key) if creds_key else None,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        if return_hit:
            return new_id, False
        return new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "store_prepare_creds", fake_store)
    monkeypatch.setattr(worker_mod, "delete_prepare_creds", fake_delete)
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    return calls


@pytest.fixture
def captured_emits(monkeypatch):
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
    )


async def _link_count(db, server_id: str) -> int:
    res = await db.execute(
        select(func.count()).select_from(ServerAccountServer).where(
            ServerAccountServer.server_id == server_id
        )
    )
    return res.scalar_one()


class TestCleanSingleActions:
    async def test_unbind_only(
        self, client, operator_token_a, make_server, make_account, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await make_account(server_id=srv.id, login="a1")
        await make_account(server_id=srv.id, login="a2")
        assert await _link_count(db, srv.id) == 2
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(operator_token_a),
            json={"unbind_accounts": True},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["unbind_accounts"]["status"] == "done"
        assert body["unbind_accounts"]["detail"]["accounts_unbound"] == 2
        assert body["unbind_accounts"]["detail"]["deprovision_dispatched"] == 2
        for other in ("rerun_prepare", "update_os_version", "run_inventory_sync"):
            assert body[other]["status"] == "skipped"
            assert body[other]["reason"] == "not_selected"
        # Связки сняты, userdel-fanout поставлен на оба аккаунта.
        assert await _link_count(db, srv.id) == 0
        deprov = [c for c in captured_dispatch if c["task_kind"] == "account.deprovision"]
        assert len(deprov) == 2

    async def test_rerun_prepare_manual(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(operator_token_a),
            json={"rerun_prepare": True, "prepare": dict(_MANUAL_PREPARE)},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["rerun_prepare"]["status"] == "dispatched"
        assert body["rerun_prepare"]["task_id"].startswith("tsk_")
        prepares = [c for c in captured_dispatch if c["task_kind"] == "server.prepare"]
        assert len(prepares) == 1
        assert prepares[0]["stored_creds"]["bootstrap_login"] == "bootadmin"

    async def test_rerun_prepare_account_mode(
        self, client, admin_role_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="svc", password="LinkedPwd!123456")
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(admin_role_token_a),
            json={"rerun_prepare": True, "prepare": {"account_id": acc.id}},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["rerun_prepare"]["status"] == "dispatched"
        prepares = [c for c in captured_dispatch if c["task_kind"] == "server.prepare"]
        assert prepares[0]["stored_creds"]["bootstrap_login"] == "svc"

    async def test_update_os_version_reset(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(operator_token_a),
            json={"update_os_version": True, "os_version_id": None},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["update_os_version"]["status"] == "done"

    async def test_update_os_version_invalid_id_failed(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(operator_token_a),
            json={"update_os_version": True, "os_version_id": "osv_does_not_exist"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["update_os_version"]["status"] == "failed"
        assert body["update_os_version"]["reason"] == "invalid_os_version"

    async def test_inventory_requires_prepared(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")  # is_managed=False
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(operator_token_a),
            json={"run_inventory_sync": True},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["run_inventory_sync"]["status"] == "failed"
        assert body["run_inventory_sync"]["reason"] == "prepare_required"

    async def test_inventory_dispatched_when_prepared(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.is_managed = True
        srv.management_user = "dbos"
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(operator_token_a),
            json={"run_inventory_sync": True},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["run_inventory_sync"]["status"] == "dispatched"
        assert body["run_inventory_sync"]["task_id"].startswith("tsk_")
        assert any(c["task_kind"] == "inventory.sync" for c in captured_dispatch)


class TestCleanCombined:
    async def test_all_actions(
        self, client, admin_role_token_a, make_server, make_account,
        captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.is_managed = True
        srv.management_user = "dbos"
        await db.flush()
        acc = await make_account(server_id=srv.id, login="svc", password="LinkedPwd!123456")
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(admin_role_token_a),
            json={
                "unbind_accounts": True,
                "rerun_prepare": True,
                "update_os_version": True,
                "run_inventory_sync": True,
                "os_version_id": None,
                # account-режим, но к моменту prepare аккаунт уже отвязан unbind'ом
                # — поэтому используем ручной режим, чтобы prepare не упал.
                "prepare": dict(_MANUAL_PREPARE),
            },
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["unbind_accounts"]["status"] == "done"
        assert body["rerun_prepare"]["status"] == "dispatched"
        assert body["update_os_version"]["status"] == "done"
        assert body["run_inventory_sync"]["status"] == "dispatched"
        # unbind снёс связку до prepare.
        assert await _link_count(db, srv.id) == 0
        _ = acc

    async def test_audit_server_clean_critical(
        self, client, operator_token_a, make_server, captured_dispatch, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(operator_token_a),
            json={"rerun_prepare": True, "prepare": dict(_MANUAL_PREPARE)},
        )
        assert resp.status_code == 202, resp.text
        actions = [e for e in captured_emits if e["action"] == "server.clean"]
        assert actions, "server.clean audit not emitted"
        success = [e for e in actions if e.get("status") == "success"]
        assert success
        # bootstrap-креды не уходят в audit.
        assert _PWD not in str(captured_emits)
        assert "bootadmin" not in str(captured_emits)


class TestCleanGuards:
    async def test_no_action_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(operator_token_a),
            json={},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_rerun_prepare_without_creds_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(operator_token_a),
            json={"rerun_prepare": True},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_reader_forbidden(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(reader_token_a),
            json={"unbind_accounts": True},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_decommissioned_409(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/clean",
            headers=_hdr(operator_token_a),
            json={"unbind_accounts": True},
        )
        assert_error(resp, 409, "SERVER_DECOMMISSIONED")

    async def test_cross_dept_404(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        foreign = await make_server(department_id="dep_b")
        resp = await client.post(
            f"{BASE}/{foreign.id}/clean",
            headers=_hdr(operator_token_a),
            json={"unbind_accounts": True},
        )
        assert resp.status_code == 404, resp.text
