"""Тесты `POST /api/server/v1/servers/installed-packages/bulk` — массовый live-запрос.

Bulk-эндпоинт переиспользует per-server путь одиночного `installed_packages.list`:
на каждый видимый prepared-сервер диспатчит отдельную задачу, на остальные
возвращает статус (`not_found`/`decommissioned`/`prepare_required`/`auth_failed`)
вместо того, чтобы уронить весь батч. Реальные пакеты добираются поллингом
`task.result` — здесь проверяем только dispatch shape и per-server статусы
(worker замочен, как и в `test_installed_packages_live.py`).

Покрытие:

* bulk на N серверов — все prepared → N dispatch'ей, status=ok + task_id.
* смешанные статусы — prepared + не-prepared + cross-dept в одном теле.
* фильтр pattern — общий glob пробрасывается в каждый payload.
* дедуп server_ids — повторный id схлопывается в одну задачу/строку.
* лимит серверов — превышение cap'а → 413 BULK_PACKAGES_TOO_LARGE.
* invalid pattern → 400 INVALID_PATTERN, ни одного dispatch'а.
* permission denied — guest без `(server, view)` → 403 на весь батч.
* worker unreachable per-server → status=auth_failed, остальные не страдают.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1"

from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402

URL = f"{BASE}/servers/installed-packages/bulk"


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват `worker_client.dispatch_task_with_hit` в общей dispatch-обвязке.

    bulk-эндпоинт идёт через тот же `dispatch_server_ssh_task`
    (`endpoints/_dispatch.py`), что и одиночный — патчим там, как в
    `test_installed_packages_live.py`.
    """
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
        new_id = f"tsk_pkg_bulk_{len(calls)}"
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
    """Пометить сервер подготовленным — live-probe идёт по ключу."""
    srv.is_managed = True
    srv.management_user = management_user
    await db.flush()
    return srv


def _by_id(results: list[dict]) -> dict[str, dict]:
    return {r["server_id"]: r for r in results}


# ── 1. Bulk на N серверов, все prepared ──────────────────────────────────────


class TestBulkAllPrepared:
    async def test_dispatches_one_task_per_server(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        srv3 = await make_server(department_id="dep_a")
        for s in (srv1, srv2, srv3):
            await _prepared(db, s)

        resp = await client.post(
            URL,
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv1.id, srv2.id, srv3.id], "pattern": "linux-image*"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["pattern"] == "linux-image*"
        assert body["patterns"] == ["linux-image*"]
        assert body["requested"] == 3
        assert body["dispatched"] == 3
        assert len(captured_dispatch) == 3

        by_id = _by_id(body["results"])
        for s in (srv1, srv2, srv3):
            row = by_id[s.id]
            assert row["status"] == "ok"
            assert row["task_id"].startswith("tsk_pkg_bulk_")
            assert row["hostname"] == s.hostname
            assert row["packages"] == []  # пусто на dispatch — пакеты в task.result

        # Каждая задача — installed_packages.list с общими patterns/max_rows.
        for call in captured_dispatch:
            assert call["task_kind"] == "installed_packages.list"
            assert call["payload"]["patterns"] == ["linux-image*"]
            assert call["payload"]["max_rows"] == 10000
            assert call["target_resource_id"] is None

    async def test_result_order_matches_input(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        for s in (srv1, srv2):
            await _prepared(db, s)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv2.id, srv1.id]},
        )
        assert resp.status_code == 202
        ids = [r["server_id"] for r in resp.json()["results"]]
        assert ids == [srv2.id, srv1.id]


# ── 2. Смешанные статусы ─────────────────────────────────────────────────────


class TestMixedStatuses:
    async def test_prepared_and_unprepared(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        """Один prepared (ok), один без prepare (prepare_required)."""
        ok_srv = await make_server(department_id="dep_a")
        await _prepared(db, ok_srv)
        unprepared = await make_server(department_id="dep_a")

        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [ok_srv.id, unprepared.id]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["requested"] == 2
        assert body["dispatched"] == 1
        by_id = _by_id(body["results"])
        assert by_id[ok_srv.id]["status"] == "ok"
        assert by_id[ok_srv.id]["task_id"] is not None
        assert by_id[unprepared.id]["status"] == "prepare_required"
        assert by_id[unprepared.id]["task_id"] is None
        # Только подготовленный сервер реально продиспатчился.
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["target_server_id"] == ok_srv.id

    async def test_cross_dept_and_missing_become_not_found(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        """Cross-dept и несуществующий id → status=not_found, батч живёт."""
        ok_srv = await make_server(department_id="dep_a")
        await _prepared(db, ok_srv)
        foreign = await make_server(department_id="dep_b")

        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [ok_srv.id, foreign.id, "srv_ghost_none"]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["dispatched"] == 1
        by_id = _by_id(body["results"])
        assert by_id[ok_srv.id]["status"] == "ok"
        assert by_id[foreign.id]["status"] == "not_found"
        # Cross-dept сервер не должен светить hostname.
        assert by_id[foreign.id]["hostname"] is None
        assert by_id["srv_ghost_none"]["status"] == "not_found"
        assert len(captured_dispatch) == 1

    async def test_decommissioned_status(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        from src.core.constants import ServerStatus

        ok_srv = await make_server(department_id="dep_a")
        await _prepared(db, ok_srv)
        dead = await make_server(department_id="dep_a")
        await _prepared(db, dead)
        dead.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [ok_srv.id, dead.id]},
        )
        assert resp.status_code == 202, resp.text
        by_id = _by_id(resp.json()["results"])
        assert by_id[ok_srv.id]["status"] == "ok"
        assert by_id[dead.id]["status"] == "decommissioned"
        assert len(captured_dispatch) == 1


# ── 3. Pattern ───────────────────────────────────────────────────────────────


class TestPattern:
    async def test_default_pattern_is_wildcard(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a), json={"server_ids": [srv.id]},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["pattern"] == "*"
        assert body["patterns"] == ["*"]
        assert captured_dispatch[0]["payload"]["patterns"] == ["*"]

    @pytest.mark.parametrize("bad_pattern", ["foo;bar", "foo bar", "foo$bar", "foo'bar"])
    async def test_invalid_pattern_returns_400(
        self, client, operator_token_a, make_server, captured_dispatch, db, bad_pattern,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "pattern": bad_pattern},
        )
        assert_error(resp, 422, "INVALID_PATTERN")
        assert captured_dispatch == []


# ── 3b. Несколько паттернов (patterns) ───────────────────────────────────────


class TestMultiplePatterns:
    async def test_patterns_list_dispatched(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        """`patterns` пробрасывается в каждый payload как есть (OR на воркере)."""
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "patterns": ["ssh*", "bash*", "*libs*"]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["patterns"] == ["ssh*", "bash*", "*libs*"]
        # Back-compat поле — первый паттерн.
        assert body["pattern"] == "ssh*"
        assert captured_dispatch[0]["payload"]["patterns"] == ["ssh*", "bash*", "*libs*"]

    async def test_patterns_wins_over_single_pattern(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        """Если присланы оба — приоритет у `patterns`, одиночный игнорируется."""
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={
                "server_ids": [srv.id],
                "pattern": "ignored*",
                "patterns": ["ssh*", "bash*"],
            },
        )
        assert resp.status_code == 202, resp.text
        assert captured_dispatch[0]["payload"]["patterns"] == ["ssh*", "bash*"]

    async def test_single_pattern_becomes_one_element_list(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        """Back-compat: одиночный pattern → patterns=[pattern] на воркере."""
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "pattern": "htop"},
        )
        assert resp.status_code == 202, resp.text
        assert captured_dispatch[0]["payload"]["patterns"] == ["htop"]

    async def test_duplicate_patterns_collapse(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        """Повторный паттерн схлопывается, порядок сохраняется."""
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "patterns": ["ssh*", "bash*", "ssh*"]},
        )
        assert resp.status_code == 202, resp.text
        assert captured_dispatch[0]["payload"]["patterns"] == ["ssh*", "bash*"]

    @pytest.mark.parametrize("bad", ["foo;bar", "foo bar", "foo$bar", "foo'bar"])
    async def test_invalid_pattern_among_many_returns_422(
        self, client, operator_token_a, make_server, captured_dispatch, db, bad,
    ):
        """Один битый паттерн в списке → 422 INVALID_PATTERN, ни одного dispatch'а."""
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "patterns": ["bash*", bad]},
        )
        assert_error(resp, 422, "INVALID_PATTERN")
        assert captured_dispatch == []

    async def test_empty_patterns_list_rejected(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        """Пустой `patterns` — нарушение схемы (min_length=1) → 422."""
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "patterns": []},
        )
        assert resp.status_code == 422
        assert captured_dispatch == []

    async def test_too_many_patterns_rejected(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        """Больше 20 паттернов — нарушение схемы (max_length=20) → 422."""
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "patterns": [f"pkg{i}*" for i in range(21)]},
        )
        assert resp.status_code == 422
        assert captured_dispatch == []


# ── 4. Дедуп + лимит серверов ────────────────────────────────────────────────


class TestDedupAndLimit:
    async def test_duplicate_server_ids_collapse(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id, srv.id, srv.id]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["requested"] == 1
        assert body["dispatched"] == 1
        assert len(body["results"]) == 1
        assert len(captured_dispatch) == 1

    async def test_too_many_servers_returns_413(
        self, client, operator_token_a, make_server, captured_dispatch, db, monkeypatch,
    ):
        """Превышение cap'а → 413, ни одной задачи не диспатчится."""
        from src.core.config import get_settings

        # Урезаем cap до 2, чтобы не плодить 50+ серверов.
        monkeypatch.setattr(
            get_settings(), "installed_packages_bulk_max_servers", 2, raising=False,
        )
        s1 = await make_server(department_id="dep_a")
        s2 = await make_server(department_id="dep_a")
        s3 = await make_server(department_id="dep_a")
        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [s1.id, s2.id, s3.id]},
        )
        assert_error(resp, 413, "BULK_PACKAGES_TOO_LARGE")
        assert captured_dispatch == []

    async def test_empty_server_ids_rejected(
        self, client, operator_token_a, captured_dispatch,
    ):
        """Пустой server_ids — нарушение схемы (min_length=1) → 422."""
        resp = await client.post(
            URL, headers=_hdr(operator_token_a), json={"server_ids": []},
        )
        assert resp.status_code == 422
        assert captured_dispatch == []


# ── 5. Permissions ───────────────────────────────────────────────────────────


class TestPermissions:
    async def test_no_token_returns_401(self, client, make_server, captured_dispatch, db):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(URL, json={"server_ids": [srv.id]})
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")
        assert captured_dispatch == []

    async def test_no_role_without_view_returns_403(
        self, client, no_role_token_a, make_server, captured_dispatch, db,
    ):
        """Субъект без `(server, view)` — 403 на весь батч (это про caller'а)."""
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            URL, headers=_hdr(no_role_token_a), json={"server_ids": [srv.id]},
        )
        assert resp.status_code == 403
        assert captured_dispatch == []


# ── 6. Worker unreachable per-server ─────────────────────────────────────────


class TestWorkerFailures:
    async def test_worker_unreachable_per_server_is_auth_failed(
        self, client, operator_token_a, make_server, monkeypatch, db,
    ):
        """Broker недоступен при dispatch'е одного сервера → auth_failed,
        остальные серверы продолжают диспатчиться."""
        from src.core.exceptions import ServiceUnavailableError

        ok_srv = await make_server(department_id="dep_a")
        bad_srv = await make_server(department_id="dep_a")
        for s in (ok_srv, bad_srv):
            await _prepared(db, s)

        async def selective_dispatch(*, target_server_id, **kwargs):
            if target_server_id == bad_srv.id:
                raise ServiceUnavailableError(
                    error_code="WORKER_UNREACHABLE",
                    message="broker down",
                )
            return (f"tsk_ok_{target_server_id}", False)

        monkeypatch.setattr(
            "src.api.v1.endpoints._dispatch.worker_client.dispatch_task_with_hit",
            selective_dispatch,
        )

        resp = await client.post(
            URL, headers=_hdr(operator_token_a),
            json={"server_ids": [ok_srv.id, bad_srv.id]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["dispatched"] == 1
        by_id = _by_id(body["results"])
        assert by_id[ok_srv.id]["status"] == "ok"
        assert by_id[bad_srv.id]["status"] == "auth_failed"
        assert by_id[bad_srv.id]["task_id"] is None
