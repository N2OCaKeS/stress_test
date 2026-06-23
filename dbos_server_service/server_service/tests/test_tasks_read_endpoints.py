"""Tests for GET /api/server/v1/tasks and GET /api/server/v1/tasks/{id}.

`worker_client.list_tasks` / `worker_client.get_task` мочатся через
monkeypatch — cross-DB read из dev_server_worker.tasks не задействуем, на
стороне server_service проверяем permission -> dept-scope -> visibility ->
TaskRead-маппинг -> X-Total-Count.

Серверы (с реальными department_id) создаются через `make_server` в server_db,
чтобы dept-scope (`server_repo.list_ids_in_departments` /
`department_map_for_ids`) работал по настоящим row'ам.
"""

from __future__ import annotations

import pytest

from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1"


def _row(
    *,
    id: str,
    kind: str = "power.on",
    status: str = "succeeded",
    target_server_id: str | None = None,
    target_resource_id: str | None = None,
    attempt: int = 0,
    last_error: str | None = None,
    result=None,
    created_by: str | None = None,
):
    """БД-row task'и (имена колонок dev_server_worker.tasks)."""
    from datetime import datetime, timezone

    now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    return {
        "id": id,
        "task_kind": kind,
        "status": status,
        "target_server_id": target_server_id,
        "target_resource_id": target_resource_id,
        "attempt": attempt,
        "last_error": last_error,
        "result": result,
        "enqueued_at": now,
        "started_at": None,
        "completed_at": None,
        "created_by": created_by,
    }


@pytest.fixture
def fake_worker_read(monkeypatch):
    """Перехватывает `worker_client.list_tasks` / `get_task`.

    Тест кладёт в `state["rows"]` список БД-row'ов (через `_row`). Фейковый
    `list_tasks` применяет тот же фильтр-контракт, что и реальный
    (status/kind/server_ids/include_infra + limit/offset), и возвращает
    `(page, total)`. `get_task` ищет по id.
    """
    state: dict = {"rows": []}

    async def fake_list(*, status=None, task_kind=None, server_ids=None,
                        include_infra=False, created_by=None, limit, offset):
        rows = list(state["rows"])
        if status is not None:
            rows = [r for r in rows if r["status"] == status]
        if task_kind is not None:
            rows = [r for r in rows if r["task_kind"] == task_kind]
        if created_by is not None:
            rows = [r for r in rows if r.get("created_by") == created_by]
        if server_ids is not None:
            allowed = set(server_ids)

            def visible(r):
                sid = r["target_server_id"]
                if sid is None:
                    return include_infra
                return sid in allowed

            rows = [r for r in rows if visible(r)]
        total = len(rows)
        return rows[offset:offset + limit], total

    async def fake_get(task_id_value):
        for r in state["rows"]:
            if r["id"] == task_id_value:
                return r
        return None

    from src.services import worker_client
    monkeypatch.setattr(worker_client, "list_tasks", fake_list)
    monkeypatch.setattr(worker_client, "get_task", fake_get)
    return state


# ── List ─────────────────────────────────────────────────────────────────────

class TestTaskListHappy:
    async def test_admin_lists_own_dept_tasks(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="tsk_1", target_server_id=srv.id, status="succeeded"),
            _row(id="tsk_2", target_server_id=srv.id, status="failed",
                 last_error="boom"),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        assert resp.headers["X-Total-Count"] == "2"
        body = resp.json()
        assert {t["id"] for t in body} == {"tsk_1", "tsk_2"}
        t1 = next(t for t in body if t["id"] == "tsk_1")
        assert t1["kind"] == "power.on"
        assert t1["server_id"] == srv.id
        assert t1["department_id"] == "dep_a"
        assert "created_at" in t1
        assert t1["retry_count"] == 0

    async def test_filter_status(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="ok1", target_server_id=srv.id, status="succeeded"),
            _row(id="fail1", target_server_id=srv.id, status="failed"),
        ]
        resp = await client.get(
            f"{BASE}/tasks?status=failed", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200
        assert resp.headers["X-Total-Count"] == "1"
        body = resp.json()
        assert [t["id"] for t in body] == ["fail1"]

    async def test_filter_kind(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="p1", target_server_id=srv.id, kind="power.on"),
            _row(id="i1", target_server_id=srv.id, kind="inventory.sync"),
        ]
        resp = await client.get(
            f"{BASE}/tasks?kind=inventory.sync", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200
        assert [t["id"] for t in resp.json()] == ["i1"]

    async def test_filter_server_id(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="s1", target_server_id=srv1.id),
            _row(id="s2", target_server_id=srv2.id),
        ]
        resp = await client.get(
            f"{BASE}/tasks?server_id={srv1.id}", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200
        assert [t["id"] for t in resp.json()] == ["s1"]

    async def test_filter_server_id_cross_dept_empty(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        """server_id чужого отдела → пустой результат (enumeration-guard)."""
        srv_b = await make_server(department_id="dep_b")
        fake_worker_read["rows"] = [
            _row(id="x1", target_server_id=srv_b.id),
        ]
        resp = await client.get(
            f"{BASE}/tasks?server_id={srv_b.id}", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200
        assert resp.headers["X-Total-Count"] == "0"
        assert resp.json() == []

    async def test_pagination_total_count(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id=f"t{i}", target_server_id=srv.id) for i in range(5)
        ]
        resp = await client.get(
            f"{BASE}/tasks?limit=2&offset=0", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200
        assert resp.headers["X-Total-Count"] == "5"
        assert len(resp.json()) == 2

    async def test_offset_over_max_rejected(
        self, client, admin_role_token_a, fake_worker_read,
    ):
        resp = await client.get(
            f"{BASE}/tasks?offset=100001", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 422

    async def test_result_summarized_in_list(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(
                id="pkg",
                target_server_id=srv.id,
                kind="installed_packages.list",
                result={"packages": [{"name": "vim"}, {"name": "git"}]},
            ),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        t = resp.json()[0]
        # Список усечён до summary с _count, а не полный массив пакетов.
        assert t["result"] == {"packages": {"_count": 2}}


class TestTaskListInfraVisibility:
    async def test_admin_role_sees_infra_tasks(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="srv_task", target_server_id=srv.id),
            _row(id="infra", target_server_id=None, kind="system.heartbeat"),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        ids = {t["id"] for t in resp.json()}
        assert ids == {"srv_task", "infra"}
        infra = next(t for t in resp.json() if t["id"] == "infra")
        assert infra["server_id"] is None
        assert infra["department_id"] is None

    async def test_operator_sees_infra_tasks(
        self, client, operator_token_a, make_server, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="srv_task", target_server_id=srv.id),
            _row(id="infra", target_server_id=None, kind="system.heartbeat"),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(operator_token_a))
        assert resp.status_code == 200
        assert {t["id"] for t in resp.json()} == {"srv_task", "infra"}

    async def test_reader_does_not_see_infra_tasks(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["reader"]},
            user_id="usr_reader_a",
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="srv_task", target_server_id=srv.id, created_by="usr_reader_a"),
            _row(
                id="infra", target_server_id=None, kind="system.heartbeat",
                created_by="usr_reader_a",
            ),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(token))
        assert resp.status_code == 200
        assert {t["id"] for t in resp.json()} == {"srv_task"}

    async def test_dep_admin_does_not_see_infra_tasks(
        self, client, admin_token, make_server, fake_worker_read,
    ):
        """department_admin (platform-роль) видит серверные, но не инфра-задачи."""
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="srv_task", target_server_id=srv.id),
            _row(id="infra", target_server_id=None, kind="system.heartbeat"),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(admin_token))
        assert resp.status_code == 200
        assert {t["id"] for t in resp.json()} == {"srv_task"}


class TestTaskListDeptScope:
    async def test_cross_dept_server_tasks_hidden(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv_a = await make_server(department_id="dep_a")
        srv_b = await make_server(department_id="dep_b")
        fake_worker_read["rows"] = [
            _row(id="mine", target_server_id=srv_a.id),
            _row(id="theirs", target_server_id=srv_b.id),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        assert resp.headers["X-Total-Count"] == "1"
        assert {t["id"] for t in resp.json()} == {"mine"}


class TestTaskListRbac:
    async def test_account_admin_blocked(
        self, client, account_admin_token, fake_worker_read,
    ):
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(account_admin_token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_loging_admin_blocked(
        self, client, loging_admin_token, fake_worker_read,
    ):
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(loging_admin_token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_no_role_denied(
        self, client, no_role_token_a, fake_worker_read,
    ):
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(no_role_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_reader_allowed_read_only(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["reader"]},
            user_id="usr_reader_a",
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="t", target_server_id=srv.id, created_by="usr_reader_a"),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(token))
        assert resp.status_code == 200, resp.text
        assert [t["id"] for t in resp.json()] == ["t"]

    async def test_no_token_401(self, client, fake_worker_read):
        resp = await client.get(f"{BASE}/tasks")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")


# ── Detail ───────────────────────────────────────────────────────────────────

class TestTaskGetHappy:
    async def test_get_full_result_and_error(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(
                id="tsk_detail",
                target_server_id=srv.id,
                status="failed",
                last_error="ssh timeout",
                result={"packages": [{"name": "vim"}, {"name": "git"}]},
            ),
        ]
        resp = await client.get(
            f"{BASE}/tasks/tsk_detail", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == "tsk_detail"
        assert body["last_error"] == "ssh timeout"
        assert body["department_id"] == "dep_a"
        # detail отдаёт полный result, не summary
        assert body["result"] == {"packages": [{"name": "vim"}, {"name": "git"}]}

    async def test_reader_can_get(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["reader"]},
            user_id="usr_reader_a",
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="r", target_server_id=srv.id, created_by="usr_reader_a"),
        ]
        resp = await client.get(f"{BASE}/tasks/r", headers=_hdr(token))
        assert resp.status_code == 200, resp.text


class TestTaskGetNotFound:
    async def test_missing_task_404(
        self, client, admin_role_token_a, fake_worker_read,
    ):
        resp = await client.get(
            f"{BASE}/tasks/tsk_nope", headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 404, "TASK_NOT_FOUND")

    async def test_cross_dept_task_masked_404(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv_b = await make_server(department_id="dep_b")
        fake_worker_read["rows"] = [_row(id="x", target_server_id=srv_b.id)]
        resp = await client.get(f"{BASE}/tasks/x", headers=_hdr(admin_role_token_a))
        assert_error(resp, 404, "TASK_NOT_FOUND")

    async def test_infra_task_hidden_from_reader_404(
        self, client, reader_token_a, fake_worker_read,
    ):
        fake_worker_read["rows"] = [
            _row(id="infra", target_server_id=None, kind="system.heartbeat"),
        ]
        resp = await client.get(f"{BASE}/tasks/infra", headers=_hdr(reader_token_a))
        assert_error(resp, 404, "TASK_NOT_FOUND")

    async def test_infra_task_visible_to_admin_role(
        self, client, admin_role_token_a, fake_worker_read,
    ):
        fake_worker_read["rows"] = [
            _row(id="infra", target_server_id=None, kind="system.heartbeat"),
        ]
        resp = await client.get(
            f"{BASE}/tasks/infra", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200
        assert resp.json()["server_id"] is None


class TestTaskNameResolution:
    async def test_list_resolves_hostname_and_login(
        self, client, admin_role_token_a, make_server, make_account, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a", hostname="test-server-01")
        acc = await make_account(server_id=srv.id, login="tester")
        fake_worker_read["rows"] = [
            _row(
                id="tsk_named",
                target_server_id=srv.id,
                target_resource_id=acc.id,
            ),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        t = resp.json()[0]
        assert t["server_id"] == srv.id
        assert t["server_hostname"] == "test-server-01"
        assert t["account_id"] == acc.id
        assert t["account_login"] == "tester"

    async def test_get_resolves_hostname_and_login(
        self, client, admin_role_token_a, make_server, make_account, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a", hostname="detail-host")
        acc = await make_account(server_id=srv.id, login="svc-user")
        fake_worker_read["rows"] = [
            _row(id="d1", target_server_id=srv.id, target_resource_id=acc.id),
        ]
        resp = await client.get(f"{BASE}/tasks/d1", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["server_hostname"] == "detail-host"
        assert body["account_login"] == "svc-user"

    async def test_deleted_account_resolves_to_none(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        """Ссылка на несуществующий account_id → account_login None (graceful)."""
        srv = await make_server(department_id="dep_a", hostname="ghost-host")
        fake_worker_read["rows"] = [
            _row(
                id="ghost",
                target_server_id=srv.id,
                target_resource_id="acc_deadbeef",
            ),
        ]
        resp = await client.get(f"{BASE}/tasks/ghost", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["account_id"] == "acc_deadbeef"
        assert body["account_login"] is None
        # hostname резолвится, аккаунт — нет
        assert body["server_hostname"] == "ghost-host"

    async def test_no_account_no_server_resolve_none(
        self, client, admin_role_token_a, fake_worker_read,
    ):
        """Инфра-задача без сервера/аккаунта → оба резолв-поля None."""
        fake_worker_read["rows"] = [
            _row(id="infra", target_server_id=None, kind="system.heartbeat"),
        ]
        resp = await client.get(f"{BASE}/tasks/infra", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["server_hostname"] is None
        assert body["account_login"] is None

    async def test_list_batch_resolves_multiple_without_n1(
        self, client, admin_role_token_a, make_server, make_account, fake_worker_read,
    ):
        """Несколько тасок на одни и те же сущности резолвятся одним батчем.

        Дедупликация по уникальным id (см. `list_tasks`): map строится по
        множеству, не по каждой строке — без N+1. Проверяем корректность
        значений на пересекающихся id.
        """
        srv1 = await make_server(department_id="dep_a", hostname="host-1")
        srv2 = await make_server(department_id="dep_a", hostname="host-2")
        acc = await make_account(server_id=srv1.id, login="shared-login")
        fake_worker_read["rows"] = [
            _row(id="a", target_server_id=srv1.id, target_resource_id=acc.id),
            _row(id="b", target_server_id=srv1.id, target_resource_id=acc.id),
            _row(id="c", target_server_id=srv2.id),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        by_id = {t["id"]: t for t in resp.json()}
        assert by_id["a"]["server_hostname"] == "host-1"
        assert by_id["a"]["account_login"] == "shared-login"
        assert by_id["b"]["server_hostname"] == "host-1"
        assert by_id["b"]["account_login"] == "shared-login"
        assert by_id["c"]["server_hostname"] == "host-2"
        assert by_id["c"]["account_login"] is None


class TestTaskGetRbac:
    async def test_account_admin_blocked(
        self, client, account_admin_token, fake_worker_read,
    ):
        resp = await client.get(
            f"{BASE}/tasks/anything", headers=_hdr(account_admin_token),
        )
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_no_role_denied(
        self, client, no_role_token_a, fake_worker_read,
    ):
        resp = await client.get(
            f"{BASE}/tasks/anything", headers=_hdr(no_role_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── Per-user scoping ───────────────────────────────────────────────────────────

class TestTaskListPerUserScope:
    """Reader без admin/operator видит только свои задачи; admin/operator/
    dept_admin — все задачи отдела."""

    async def test_reader_sees_only_own_tasks(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        reader = make_token(
            department_id=dept_a, user_id="usr_reader",
            service_roles={"server_service": ["reader"]},
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="mine", target_server_id=srv.id, created_by="usr_reader"),
            _row(id="theirs", target_server_id=srv.id, created_by="usr_other"),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(reader))
        assert resp.status_code == 200, resp.text
        assert resp.headers["X-Total-Count"] == "1"
        assert {t["id"] for t in resp.json()} == {"mine"}

    async def test_operator_sees_all_dept_tasks(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        operator = make_token(
            department_id=dept_a, user_id="usr_op",
            service_roles={"server_service": ["operator"]},
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="mine", target_server_id=srv.id, created_by="usr_op"),
            _row(id="theirs", target_server_id=srv.id, created_by="usr_other"),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(operator))
        assert resp.status_code == 200, resp.text
        assert {t["id"] for t in resp.json()} == {"mine", "theirs"}

    async def test_admin_role_sees_all_dept_tasks(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        admin = make_token(
            department_id=dept_a, user_id="usr_admin",
            service_roles={"server_service": ["admin"]},
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="mine", target_server_id=srv.id, created_by="usr_admin"),
            _row(id="theirs", target_server_id=srv.id, created_by="usr_other"),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(admin))
        assert resp.status_code == 200, resp.text
        assert {t["id"] for t in resp.json()} == {"mine", "theirs"}

    async def test_dept_admin_sees_all_dept_tasks(
        self, client, admin_token, make_server, fake_worker_read,
    ):
        """department_admin (platform-роль) видит все серверные задачи отдела."""
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="a", target_server_id=srv.id, created_by="usr_x"),
            _row(id="b", target_server_id=srv.id, created_by="usr_y"),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert {t["id"] for t in resp.json()} == {"a", "b"}


class TestTaskGetPerUserScope:
    async def test_reader_gets_own_task(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        reader = make_token(
            department_id=dept_a, user_id="usr_reader",
            service_roles={"server_service": ["reader"]},
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="mine", target_server_id=srv.id, created_by="usr_reader"),
        ]
        resp = await client.get(f"{BASE}/tasks/mine", headers=_hdr(reader))
        assert resp.status_code == 200, resp.text

    async def test_reader_others_task_masked_404(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        reader = make_token(
            department_id=dept_a, user_id="usr_reader",
            service_roles={"server_service": ["reader"]},
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="theirs", target_server_id=srv.id, created_by="usr_other"),
        ]
        resp = await client.get(f"{BASE}/tasks/theirs", headers=_hdr(reader))
        assert_error(resp, 404, "TASK_NOT_FOUND")

    async def test_operator_gets_others_task(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        operator = make_token(
            department_id=dept_a, user_id="usr_op",
            service_roles={"server_service": ["operator"]},
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="theirs", target_server_id=srv.id, created_by="usr_other"),
        ]
        resp = await client.get(f"{BASE}/tasks/theirs", headers=_hdr(operator))
        assert resp.status_code == 200, resp.text


# ── created_by exposure + filter ───────────────────────────────────────────────

class TestTaskCreatedByField:
    """`created_by` отдаётся наружу и в листинге, и в detail."""

    async def test_created_by_in_list(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="t", target_server_id=srv.id, created_by="usr_init"),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        assert resp.json()[0]["created_by"] == "usr_init"

    async def test_created_by_in_detail(
        self, client, admin_role_token_a, make_server, fake_worker_read,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="d", target_server_id=srv.id, created_by="usr_init"),
        ]
        resp = await client.get(f"{BASE}/tasks/d", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        assert resp.json()["created_by"] == "usr_init"

    async def test_created_by_none_for_infra(
        self, client, admin_role_token_a, fake_worker_read,
    ):
        fake_worker_read["rows"] = [
            _row(id="infra", target_server_id=None, kind="system.heartbeat"),
        ]
        resp = await client.get(
            f"{BASE}/tasks/infra", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["created_by"] is None


class TestTaskListCreatedByFilter:
    """`?created_by=` накладывается поверх role-scope, видимость не расширяет."""

    async def test_admin_filter_narrows_to_self(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        admin = make_token(
            department_id=dept_a, user_id="usr_admin",
            service_roles={"server_service": ["admin"]},
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="mine", target_server_id=srv.id, created_by="usr_admin"),
            _row(id="theirs", target_server_id=srv.id, created_by="usr_other"),
        ]
        resp = await client.get(
            f"{BASE}/tasks?created_by=usr_admin", headers=_hdr(admin),
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["X-Total-Count"] == "1"
        assert {t["id"] for t in resp.json()} == {"mine"}

    async def test_admin_filter_other_user(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        """admin вправе видеть все задачи отдела — фильтр по чужому инициатору
        легитимно сужает выдачу до этого инициатора (панель «задачи отдела»)."""
        admin = make_token(
            department_id=dept_a, user_id="usr_admin",
            service_roles={"server_service": ["admin"]},
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="mine", target_server_id=srv.id, created_by="usr_admin"),
            _row(id="theirs", target_server_id=srv.id, created_by="usr_other"),
        ]
        resp = await client.get(
            f"{BASE}/tasks?created_by=usr_other", headers=_hdr(admin),
        )
        assert resp.status_code == 200, resp.text
        assert {t["id"] for t in resp.json()} == {"theirs"}

    async def test_reader_filter_self_ok(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        reader = make_token(
            department_id=dept_a, user_id="usr_reader",
            service_roles={"server_service": ["reader"]},
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="mine", target_server_id=srv.id, created_by="usr_reader"),
            _row(id="theirs", target_server_id=srv.id, created_by="usr_other"),
        ]
        resp = await client.get(
            f"{BASE}/tasks?created_by=usr_reader", headers=_hdr(reader),
        )
        assert resp.status_code == 200, resp.text
        assert {t["id"] for t in resp.json()} == {"mine"}

    async def test_reader_filter_other_does_not_widen(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        """reader, спрашивающий чужой created_by, получает пусто — фильтр не
        может расширить видимость за пределы своих задач."""
        reader = make_token(
            department_id=dept_a, user_id="usr_reader",
            service_roles={"server_service": ["reader"]},
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="mine", target_server_id=srv.id, created_by="usr_reader"),
            _row(id="theirs", target_server_id=srv.id, created_by="usr_other"),
        ]
        resp = await client.get(
            f"{BASE}/tasks?created_by=usr_other", headers=_hdr(reader),
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["X-Total-Count"] == "0"
        assert resp.json() == []

    async def test_no_filter_unchanged(
        self, client, make_token, dept_a, make_server, fake_worker_read,
    ):
        """Без фильтра admin видит все задачи отдела — поведение как прежде."""
        admin = make_token(
            department_id=dept_a, user_id="usr_admin",
            service_roles={"server_service": ["admin"]},
        )
        srv = await make_server(department_id="dep_a")
        fake_worker_read["rows"] = [
            _row(id="mine", target_server_id=srv.id, created_by="usr_admin"),
            _row(id="theirs", target_server_id=srv.id, created_by="usr_other"),
        ]
        resp = await client.get(f"{BASE}/tasks", headers=_hdr(admin))
        assert resp.status_code == 200, resp.text
        assert {t["id"] for t in resp.json()} == {"mine", "theirs"}
