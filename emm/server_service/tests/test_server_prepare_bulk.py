"""Тесты массового бутстрапа управления серверами — POST /servers/prepare/bulk.

Покрывает:
* batch на N серверов — все queued, per-server task_id, креды в Redis-stash;
* смешанные исходы — один queued, один skipped (decommissioned / cross-dept),
  один битый сервер не валит весь батч;
* лимит BULK_PREPARE_MAX_SERVERS → 413;
* дубли server_id в теле → 422;
* reader без `update` → 403 на весь запрос, ничего не диспатчится;
* глобальная недоступность воркера → 503;
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


def _item(server_id: str, *, login: str = "bootadmin", pwd: str = _PWD) -> dict:
    return {
        "server_id": server_id,
        "username_b64": _b64(login),
        "password_b64": _b64(pwd),
    }


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client.dispatch_task для bulk-prepare.

    Тот же контракт, что у `test_server_prepare.py::captured_dispatch`:
    fixture — list-подкласс с атрибутами `stored_creds_calls` и
    `deleted_keys`. Каждый элемент несёт `stored_creds` — то, что prepare
    положил бы в Redis под ключ из payload (store замокан).
    """
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
                            priority=0,
                            return_hit=False):
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

    # Endpoint держит свою ссылку на worker_client — патчим и модуль, и неё.
    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "store_prepare_creds", fake_store)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.store_prepare_creds",
        fake_store,
    )
    monkeypatch.setattr(worker_mod, "delete_prepare_creds", fake_delete)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.delete_prepare_creds",
        fake_delete,
    )
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


@pytest.fixture
def captured_emits(monkeypatch):
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
    )


class TestBulkPrepare:
    async def test_bulk_all_queued(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        srv3 = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(operator_token_a),
            json={"items": [_item(srv1.id), _item(srv2.id), _item(srv3.id)]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["queued_count"] == 3
        assert body["skipped_count"] == 0
        by_id = {r["server_id"]: r for r in body["results"]}
        for srv in (srv1, srv2, srv3):
            assert by_id[srv.id]["status"] == "queued"
            assert by_id[srv.id]["task_id"].startswith("tsk_")
            assert by_id[srv.id]["reason"] is None
        # Порядок результатов = порядок items.
        assert [r["server_id"] for r in body["results"]] == [srv1.id, srv2.id, srv3.id]
        # Три dispatch'а, каждый со своими кредами в Redis-stash.
        assert len(captured_dispatch) == 3
        for call in captured_dispatch:
            assert call["task_kind"] == "server.prepare"
            assert "bootstrap_login" not in call["payload"]
            assert "bootstrap_password" not in call["payload"]
            assert call["payload"]["bootstrap_creds_key"].startswith("dbos:prepare_creds:")
            assert call["stored_creds"]["bootstrap_login"] == "bootadmin"

    async def test_per_server_creds_distinct(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        # У разных боксов разные креды — каждый stash несёт свой логин/пароль.
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(operator_token_a),
            json={"items": [
                _item(srv1.id, login="rootone", pwd="One!Pwd-Strong123"),
                _item(srv2.id, login="roottwo", pwd="Two!Pwd-Strong123"),
            ]},
        )
        assert resp.status_code == 202, resp.text
        by_target = {c["target_server_id"]: c for c in captured_dispatch}
        assert by_target[srv1.id]["stored_creds"]["bootstrap_login"] == "rootone"
        assert by_target[srv1.id]["stored_creds"]["bootstrap_password"] == "One!Pwd-Strong123"
        assert by_target[srv2.id]["stored_creds"]["bootstrap_login"] == "roottwo"
        assert by_target[srv2.id]["stored_creds"]["bootstrap_password"] == "Two!Pwd-Strong123"

    async def test_mixed_decommissioned_skipped(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        # Один сервер списан → skipped(decommissioned), остальные queued.
        ok = await make_server(department_id="dep_a")
        dead = await make_server(department_id="dep_a")
        dead.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(operator_token_a),
            json={"items": [_item(ok.id), _item(dead.id)]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["queued_count"] == 1
        assert body["skipped_count"] == 1
        by_id = {r["server_id"]: r for r in body["results"]}
        assert by_id[ok.id]["status"] == "queued"
        assert by_id[dead.id]["status"] == "skipped"
        assert by_id[dead.id]["reason"] == "decommissioned"
        assert by_id[dead.id]["task_id"] is None
        # Только один dispatch — на списанный сервер задача не ставилась.
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["target_server_id"] == ok.id

    async def test_mixed_cross_dept_skipped(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        # Сервер чужого отдела не виден → skipped(not_found_or_cross_dept),
        # не раскрываем его существование, батч не падает.
        ok = await make_server(department_id="dep_a")
        foreign = await make_server(department_id="dep_b")
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(operator_token_a),
            json={"items": [_item(ok.id), _item(foreign.id)]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["queued_count"] == 1
        assert body["skipped_count"] == 1
        by_id = {r["server_id"]: r for r in body["results"]}
        assert by_id[ok.id]["status"] == "queued"
        assert by_id[foreign.id]["status"] == "skipped"
        assert by_id[foreign.id]["reason"] == "not_found_or_cross_dept"
        # Креды чужого сервера в Redis НЕ легли.
        assert len(captured_dispatch.stored_creds_calls) == 1

    async def test_unknown_server_skipped(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        ok = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(operator_token_a),
            json={"items": [_item(ok.id), _item("srv_does_not_exist")]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        by_id = {r["server_id"]: r for r in body["results"]}
        assert by_id["srv_does_not_exist"]["status"] == "skipped"
        assert by_id["srv_does_not_exist"]["reason"] == "not_found_or_cross_dept"

    async def test_reader_cannot_bulk_prepare(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(reader_token_a),
            json={"items": [_item(srv.id)]},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        # Право проверяется до загрузки серверов — ничего в Redis.
        assert captured_dispatch.stored_creds_calls == []
        assert captured_dispatch == []

    async def test_limit_exceeded_413(
        self, client, operator_token_a, make_server, captured_dispatch,
        monkeypatch,
    ):
        # Жмём cap до 2, шлём 3 — 413, ни одного dispatch'а.
        from src.core import config as config_mod
        settings = config_mod.get_settings()
        monkeypatch.setattr(settings, "bulk_prepare_max_servers", 2)

        s1 = await make_server(department_id="dep_a")
        s2 = await make_server(department_id="dep_a")
        s3 = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(operator_token_a),
            json={"items": [_item(s1.id), _item(s2.id), _item(s3.id)]},
        )
        assert resp.status_code == 413, resp.text
        assert resp.json()["error_code"] == "BULK_PREPARE_TOO_LARGE"
        assert captured_dispatch.stored_creds_calls == []

    async def test_duplicate_server_id_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(operator_token_a),
            json={"items": [_item(srv.id), _item(srv.id)]},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")
        assert captured_dispatch.stored_creds_calls == []

    async def test_empty_items_422(
        self, client, operator_token_a, captured_dispatch,
    ):
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(operator_token_a),
            json={"items": []},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_weak_bootstrap_password_item_accepted(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        # Слабый bootstrap-пароль — это существующий пароль хоста, не новый:
        # надёжность не проверяется, элемент батча принимается и диспатчится.
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(operator_token_a),
            json={"items": [_item(srv.id, pwd="1")]},
        )
        assert resp.status_code == 202, resp.text
        assert len(captured_dispatch.stored_creds_calls) == 1

    async def test_worker_unreachable_aborts_with_503(
        self, client, operator_token_a, make_server, captured_dispatch,
        monkeypatch,
    ):
        # Глобальная недоступность воркера на первом сервере → 503, батч не
        # продолжается (каждый следующий упал бы идентично).
        from src.core.exceptions import ServiceUnavailableError

        async def boom(*args, **kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE", message="redis down",
            )

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", boom)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
            boom,
        )

        s1 = await make_server(department_id="dep_a")
        s2 = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(operator_token_a),
            json={"items": [_item(s1.id), _item(s2.id)]},
        )
        assert resp.status_code == 503, resp.text
        # Кред'ы первого сервера успели лечь — обязаны быть подчищены.
        assert len(captured_dispatch.stored_creds_calls) == 1
        creds_key, _ = captured_dispatch.stored_creds_calls[0]
        assert creds_key in captured_dispatch.deleted_keys


class TestBulkPrepareAuditSafety:
    async def test_credentials_not_in_audit(
        self, client, operator_token_a, make_server, captured_dispatch,
        captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/prepare/bulk",
            headers=_hdr(operator_token_a),
            json={"items": [_item(srv.id)]},
        )
        assert resp.status_code == 202, resp.text
        blob = str(captured_emits)
        assert _PWD not in blob
        assert "bootadmin" not in blob
