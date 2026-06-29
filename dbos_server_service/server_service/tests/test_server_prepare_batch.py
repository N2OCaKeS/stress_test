"""Тесты массового prepare с per-server выбором кред — POST /servers/prepare-batch.

Покрывает:
* batch ручного режима — все dispatched, batch_id, креды в Redis-stash;
* account-режим — привязанная учётка резолвится, в stash её логин/пароль;
* account-режим без view_password (operator) → per-server failed, не валит батч;
* смешанные исходы (cross-dept / decommissioned) → failed, остальные dispatched;
* reader без `update` → 403 на весь запрос;
* дубли server_id → 422, превышение cap → 413;
* глобальная недоступность воркера → первый failed worker_unreachable,
  остаток not_attempted;
* bootstrap-креды не уходят в audit.
"""

from __future__ import annotations

import base64

import pytest

from src.core.constants import ServerStatus

BASE = "/api/server/v1/servers"

from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


_PWD = "Boot1234!StrongPwd"


def _manual(server_id: str, *, login: str = "bootadmin", pwd: str = _PWD) -> dict:
    return {
        "server_id": server_id,
        "username_b64": _b64(login),
        "password_b64": _b64(pwd),
    }


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client store/dispatch — тот же контракт, что у bulk-prepare."""
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


class TestPrepareBatchManual:
    async def test_all_manual_dispatched(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        s1 = await make_server(department_id="dep_a")
        s2 = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare-batch",
            headers=_hdr(operator_token_a),
            json={"items": [_manual(s1.id), _manual(s2.id)]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["batch_id"].startswith("bat_")
        assert len(body["dispatched"]) == 2
        assert body["failed"] == []
        by_id = {d["server_id"]: d for d in body["dispatched"]}
        for s in (s1, s2):
            assert by_id[s.id]["task_id"].startswith("tsk_")
            assert by_id[s.id]["status"] == "dispatched"
            assert by_id[s.id]["server_name"]
        for call in captured_dispatch:
            assert call["task_kind"] == "server.prepare"
            assert "bootstrap_login" not in call["payload"]
            assert call["stored_creds"]["bootstrap_login"] == "bootadmin"

    async def test_mixed_cross_dept_and_decommissioned(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        ok = await make_server(department_id="dep_a")
        dead = await make_server(department_id="dep_a")
        dead.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        foreign = await make_server(department_id="dep_b")
        resp = await client.post(
            f"{BASE}/prepare-batch",
            headers=_hdr(operator_token_a),
            json={"items": [_manual(ok.id), _manual(dead.id), _manual(foreign.id)]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert [d["server_id"] for d in body["dispatched"]] == [ok.id]
        failed = {f["server_id"]: f["reason"] for f in body["failed"]}
        assert failed[dead.id] == "decommissioned"
        assert failed[foreign.id] == "not_found_or_cross_dept"
        assert len(captured_dispatch) == 1

    async def test_reader_forbidden_whole_batch(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        s = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare-batch",
            headers=_hdr(reader_token_a),
            json={"items": [_manual(s.id)]},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch.stored_creds_calls == []

    async def test_duplicate_server_id_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        s = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare-batch",
            headers=_hdr(operator_token_a),
            json={"items": [_manual(s.id), _manual(s.id)]},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_limit_exceeded_413(
        self, client, operator_token_a, make_server, captured_dispatch, monkeypatch,
    ):
        from src.core import config as config_mod
        settings = config_mod.get_settings()
        monkeypatch.setattr(settings, "bulk_prepare_max_servers", 1)
        s1 = await make_server(department_id="dep_a")
        s2 = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare-batch",
            headers=_hdr(operator_token_a),
            json={"items": [_manual(s1.id), _manual(s2.id)]},
        )
        assert resp.status_code == 413, resp.text
        assert resp.json()["error_code"] == "BULK_PREPARE_TOO_LARGE"
        assert captured_dispatch.stored_creds_calls == []

    async def test_worker_unreachable_aborts_remaining(
        self, client, operator_token_a, make_server, captured_dispatch, monkeypatch,
    ):
        from src.core.exceptions import ServiceUnavailableError

        async def boom(**kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE", message="redis down",
            )

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", boom)

        s1 = await make_server(department_id="dep_a")
        s2 = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare-batch",
            headers=_hdr(operator_token_a),
            json={"items": [_manual(s1.id), _manual(s2.id)]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["dispatched"] == []
        reasons = {f["server_id"]: f["reason"] for f in body["failed"]}
        assert reasons[s1.id] == "worker_unreachable"
        assert reasons[s2.id] == "not_attempted"


class TestPrepareBatchAccountMode:
    async def test_account_mode_resolves_linked_account(
        self, client, admin_role_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="svc", password="LinkedPwd!123456")
        resp = await client.post(
            f"{BASE}/prepare-batch",
            headers=_hdr(admin_role_token_a),
            json={"items": [{"server_id": srv.id, "account_id": acc.id}]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert [d["server_id"] for d in body["dispatched"]] == [srv.id]
        call = captured_dispatch[0]
        assert call["stored_creds"]["bootstrap_login"] == "svc"
        assert call["stored_creds"]["bootstrap_password"] == "LinkedPwd!123456"

    async def test_account_mode_without_view_password_fails_per_server(
        self, client, operator_token_a, make_server, make_account, captured_dispatch,
    ):
        # operator имеет server.update, но НЕ server_account.view_password —
        # account-режим уходит в failed, батч не падает.
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="svc", password="LinkedPwd!123456")
        resp = await client.post(
            f"{BASE}/prepare-batch",
            headers=_hdr(operator_token_a),
            json={"items": [{"server_id": srv.id, "account_id": acc.id}]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["dispatched"] == []
        assert body["failed"][0]["server_id"] == srv.id
        assert body["failed"][0]["reason"] == "permission_denied"
        assert captured_dispatch.stored_creds_calls == []


class TestPrepareBatchAuditSafety:
    async def test_credentials_not_in_audit(
        self, client, operator_token_a, make_server, captured_dispatch, captured_emits,
    ):
        s = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare-batch",
            headers=_hdr(operator_token_a),
            json={"items": [_manual(s.id)]},
        )
        assert resp.status_code == 202, resp.text
        blob = str(captured_emits)
        assert _PWD not in blob
        assert "bootadmin" not in blob
