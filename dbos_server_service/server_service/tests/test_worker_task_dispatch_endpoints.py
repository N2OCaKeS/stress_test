"""Тесты для worker-dispatch endpoints из `endpoints/worker_dispatch.py`.

Все 4 dispatch'ера идут через worker-broker:

* POST `/servers/{id}/power/status`     → `power.status` (live BMC-probe)
* POST `/servers/{id}/inventory/sync`   → `inventory.sync`
* POST `/server-accounts/{id}/rotate`   → `account.rotate_password`
* POST `/ipmi-controllers/{id}/rotate`  → `ipmi.rotate_password`

`worker_client.dispatch_task` мочится через monkeypatch — тесты фиксируют
именно argument shape (task_kind / target_server_id / payload / created_by
/ request_id / idempotency_key), а не реально публикуют в Redis.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехватывает `worker_client.dispatch_task` ВО ВСЕХ дёргающих модулях.

    Патч идёт сразу через `services.worker_client.dispatch_task` (главный
    источник) — endpoint-модули импортят `from src.services import
    worker_client` и обращаются через `worker_client.dispatch_task(...)`,
    поэтому single-source патч достаточен. Дополнительно мочим прямую
    атрибут-ссылку в `endpoints/worker_dispatch` на случай, если importer
    держит локальную копию (defensive).

    Возвращает list записанных call-kwargs.
    """
    calls: list[dict] = []
    _by_key: dict[str, str] = {}

    async def fake_dispatch(*, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None):
        if idempotency_key is not None and idempotency_key in _by_key:
            return _by_key[idempotency_key]
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "target_resource_id": target_resource_id,
            "payload": payload,
            "created_by": created_by,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        if idempotency_key is not None:
            _by_key[idempotency_key] = new_id
        return new_id

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    return calls


@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает `audit_service.emit` из `endpoints/worker_dispatch`.

    Зеркало паттерна из `test_ipmi_endpoints.py` — патчим и общий модуль,
    и прямого importer'а (чтобы поймать обе формы вызова).
    """
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
        fake_emit,
    )
    # `services.server.get_server` тоже эмитит denied-аудит на cross-dept —
    # патчим и его, чтобы tests могли проверять полный набор.
    monkeypatch.setattr(
        "src.services.server.audit_service.emit", fake_emit,
    )
    return captured


def _events(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


# ── POST /servers/{id}/power/status — live BMC-probe через worker ──────────


class TestPowerStatusDispatch:
    """`power.status` — live BMC-probe, требует IPMI-row.

    Восстановлен после короткого эпизода с inline TCP-пингом — для серверов
    с BMC честнее опрашивать BMC, а не SSH-порт.
    """

    async def test_operator_dispatches_power_status(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["task_id"].startswith("tsk_")
        assert body["status"] == "queued"
        assert len(captured_dispatch) == 1
        call = captured_dispatch[0]
        assert call["task_kind"] == "power.status"
        assert call["target_server_id"] == srv.id
        assert call["payload"] == {
            "server_id": srv.id,
            "host": srv.hostname,
            "ssh_port": srv.ssh_port,
            "target_department_id": "dep_a",
            "is_managed": False,
            "management_user": None,
        }

    async def test_reader_cannot_dispatch_power_status(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        assert captured_dispatch == []

    async def test_cross_dept_returns_404(
        self, client, operator_token_b, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(operator_token_b),
        )
        assert resp.status_code == 404
        assert captured_dispatch == []

    async def test_no_ipmi_returns_404(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """Без IPMI-row power.status не может ходить в BMC.

        Унифицировано с `endpoints/ipmi.py::_dispatch_power`: 404
        NO_IPMI_CONTROLLER (target ресурс отсутствует), а не 409.
        """
        srv = await make_server(department_id="dep_a")  # без ipmi
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "NO_IPMI_CONTROLLER"
        assert captured_dispatch == []

    async def test_decommissioned_returns_409(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        from src.core.constants import ServerStatus

        srv = await make_server(department_id="dep_a", with_ipmi=True)
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409
        assert resp.json().get("error_code") == "SERVER_DECOMMISSIONED"
        assert captured_dispatch == []

    async def test_no_token_returns_401(
        self, client, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(f"{BASE}/servers/{srv.id}/power/status")
        assert resp.status_code == 401
        assert captured_dispatch == []


# ── POST /servers/{id}/inventory/sync ──────────────────────────────────────


class TestInventorySyncDispatch:
    """`inventory.sync` идёт по SSH — IPMI не нужен."""

    async def test_operator_dispatches_without_ipmi(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")  # без ipmi
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/inventory/sync",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        assert len(captured_dispatch) == 1
        call = captured_dispatch[0]
        assert call["task_kind"] == "inventory.sync"
        assert call["target_server_id"] == srv.id
        assert call["payload"] == {
            "server_id": srv.id,
            "host": srv.hostname,
            "ssh_port": srv.ssh_port,
            "target_department_id": "dep_a",
            "is_managed": False,
            "management_user": None,
        }

    async def test_reader_cannot_trigger_inventory(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        """reader не имеет `inventory_trigger` в дефолтных грантах."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/inventory/sync",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        assert captured_dispatch == []

    async def test_decommissioned_returns_409(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        from src.core.constants import ServerStatus

        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/inventory/sync",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409
        assert resp.json().get("error_code") == "SERVER_DECOMMISSIONED"
        assert captured_dispatch == []


# ── POST /server-accounts/{id}/rotate ──────────────────────────────────────


class TestAccountRotateDispatch:
    """`account.rotate_password` — admin-initiated через worker (SSH + storage)."""

    async def test_operator_dispatches_with_account_kwargs(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="appuser")
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        assert len(captured_dispatch) == 1
        call = captured_dispatch[0]
        assert call["task_kind"] == "account.rotate_password"
        assert call["target_server_id"] == srv.id
        assert call["target_resource_id"] == acc.id
        assert call["payload"] == {
            "server_id": srv.id,
            "host": srv.hostname,
            "ssh_port": srv.ssh_port,
            "account_id": acc.id,
            "target_department_id": "dep_a",
            "login": "appuser",
            "is_managed": False,
            "management_user": None,
        }

    async def test_managed_server_propagates_session_hints(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.is_managed = True
        srv.management_user = "dbos"
        await db.flush()
        acc = await make_account(server_id=srv.id, login="appuser")
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert payload["is_managed"] is True
        assert payload["management_user"] == "dbos"

    async def test_reader_cannot_rotate(
        self, client, reader_token_a, make_server, make_account,
        captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        assert captured_dispatch == []

    async def test_cross_dept_returns_404(
        self, client, operator_token_b, make_server, make_account,
        captured_dispatch,
    ):
        """Account из dep_a НЕ виден operator'у из dep_b → 404 без утечки.

        404 одинаков что для несуществующего id, что для cross-dept —
        иначе по разнице ответов утечёт cross-dept enumeration.
        """
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_b),
        )
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "ACCOUNT_NOT_FOUND"
        assert captured_dispatch == []

    async def test_nonexistent_account_returns_404(
        self, client, operator_token_a, captured_dispatch,
    ):
        resp = await client.post(
            f"{BASE}/server-accounts/acc_ghost_xx/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404
        assert captured_dispatch == []

    async def test_decommissioned_blocks_rotation(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, db,
    ):
        from src.core.constants import ServerStatus

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409
        assert resp.json().get("error_code") == "SERVER_DECOMMISSIONED"
        assert captured_dispatch == []


# ── POST /ipmi-controllers/{id}/rotate ─────────────────────────────────────


class TestIpmiControllerRotateDispatch:
    """`ipmi.rotate_password` — worker сейчас disabled, но dispatch публикуется."""

    async def test_admin_role_dispatches(
        self, client, admin_role_token_a, make_server, make_ipmi,
        captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/ipmi-controllers/{ctrl.id}/rotate",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 202, resp.text
        assert len(captured_dispatch) == 1
        call = captured_dispatch[0]
        assert call["task_kind"] == "ipmi.rotate_password"
        assert call["target_server_id"] == srv.id
        assert call["target_resource_id"] == ctrl.id
        assert call["payload"] == {
            "server_id": srv.id,
            "controller_id": ctrl.id,
            "target_department_id": "dep_a",
        }

    async def test_reader_cannot_rotate_ipmi(
        self, client, reader_token_a, make_server, make_ipmi,
        captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/ipmi-controllers/{ctrl.id}/rotate",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        assert captured_dispatch == []

    async def test_cross_dept_returns_404(
        self, client, operator_token_b, make_server, make_ipmi,
        captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/ipmi-controllers/{ctrl.id}/rotate",
            headers=_hdr(operator_token_b),
        )
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "NO_IPMI_CONTROLLER"
        assert captured_dispatch == []

    async def test_nonexistent_controller_returns_404(
        self, client, admin_role_token_a, captured_dispatch,
    ):
        resp = await client.post(
            f"{BASE}/ipmi-controllers/ipm_ghost_xx/rotate",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "NO_IPMI_CONTROLLER"
        assert captured_dispatch == []


# ── Audit emit на success ──────────────────────────────────────────────────


class TestDispatchAuditOnSuccess:
    """Каждый dispatch обязан эмитить success-аудит с task_id + task_kind."""

    async def test_power_status_success_audit(
        self, client, operator_token_a, make_server,
        captured_dispatch, captured_emits,
    ):
        """POST /power/status эмитит `server.power_status` success-audit
        с task_id/task_kind/department_id в details."""
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        successes = [
            e for e in _events(captured_emits, "server.power_status")
            if e.get("status") == "success"
        ]
        assert len(successes) == 1
        ev = successes[0]
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"
        assert ev["details"]["task_kind"] == "power.status"
        assert ev["details"]["department_id"] == "dep_a"

    async def test_inventory_sync_success_audit(
        self, client, operator_token_a, make_server,
        captured_dispatch, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(
            f"{BASE}/servers/{srv.id}/inventory/sync",
            headers=_hdr(operator_token_a),
        )
        successes = [
            e for e in _events(captured_emits, "server.inventory_sync")
            if e.get("status") == "success"
        ]
        assert len(successes) == 1
        assert successes[0]["details"]["task_kind"] == "inventory.sync"

    async def test_account_rotate_success_audit(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="dba")
        await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        successes = [
            e for e in _events(captured_emits, "server_account.rotate_password_dispatch")
            if e.get("status") == "success"
        ]
        assert len(successes) == 1
        ev = successes[0]
        assert ev["target_id"] == acc.id
        assert ev["details"]["mode"] == "all"
        assert ev["details"]["server_ids"] == [srv.id]
        assert ev["details"]["login"] == "dba"

    async def test_ipmi_rotate_success_audit(
        self, client, admin_role_token_a, make_server, make_ipmi,
        captured_dispatch, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        await client.post(
            f"{BASE}/ipmi-controllers/{ctrl.id}/rotate",
            headers=_hdr(admin_role_token_a),
        )
        successes = [
            e for e in _events(captured_emits, "ipmi_controller.rotate_dispatch")
            if e.get("status") == "success"
        ]
        assert len(successes) == 1
        ev = successes[0]
        assert ev["target_id"] == ctrl.id
        assert ev["details"]["task_kind"] == "ipmi.rotate_password"


# ── Audit emit на worker failure (ConflictError / ServiceUnavailableError) ─


class TestDispatchAuditOnWorkerFailure:
    """`worker_client.dispatch_task` может поднять ConflictError /
    ServiceUnavailableError. В обоих случаях endpoint обязан эмитить
    failure-аудит ДО re-raise — иначе попытка теряется в middleware'е
    как generic `http.client_error`.
    """

    async def test_conflict_emits_failure_for_inventory_sync(
        self, client, operator_token_a, make_server, captured_emits, monkeypatch,
    ):
        """Заменил тест conflict-аудита для power.status (тот endpoint больше
        не диспатчит — стал синхронным TCP-пингом). Тестируем тот же контракт
        на inventory.sync — конфликт идемпотентности должен эмитить
        failure-audit до re-raise."""
        from src.core.exceptions import ConflictError

        async def boom(**_kwargs):
            raise ConflictError(
                error_code="TASK_IDEMPOTENT_CONFLICT",
                message="race",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom,
        )
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/inventory/sync",
            headers={**_hdr(operator_token_a), "Idempotency-Key": "is-race"},
        )
        assert resp.status_code == 409
        assert resp.json().get("error_code") == "TASK_IDEMPOTENT_CONFLICT"
        failures = [
            e for e in _events(captured_emits, "server.inventory_sync")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        assert failures[0]["details"]["reason"] == "idempotent_conflict"
        assert failures[0]["details"]["task_kind"] == "inventory.sync"

    async def test_service_unavailable_emits_failure_for_account_rotate(
        self, client, operator_token_a, make_server, make_account,
        captured_emits, monkeypatch,
    ):
        from src.core.exceptions import ServiceUnavailableError

        async def boom(**_kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_REDIS_NOT_CONFIGURED",
                message="redis env missing",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom,
        )
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503
        assert resp.json().get("error_code") == "WORKER_REDIS_NOT_CONFIGURED"
        failures = [
            e for e in _events(captured_emits, "server_account.rotate_password_dispatch")
            if e.get("status") == "failure"
            and e.get("details", {}).get("reason") == "worker_unreachable"
        ]
        assert len(failures) == 1
        assert failures[0]["details"]["task_kind"] == "account.rotate_password"

    async def test_service_unavailable_emits_failure_for_ipmi_rotate(
        self, client, admin_role_token_a, make_server, make_ipmi,
        captured_emits, monkeypatch,
    ):
        from src.core.exceptions import ServiceUnavailableError

        async def boom(**_kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE",
                message="redis down",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom,
        )
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/ipmi-controllers/{ctrl.id}/rotate",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 503
        failures = [
            e for e in _events(captured_emits, "ipmi_controller.rotate_dispatch")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        assert failures[0]["details"]["reason"] == "worker_unreachable"
        assert failures[0]["details"]["task_kind"] == "ipmi.rotate_password"


# ── M2M ротация: точечная (один сервер) vs массовая (все) ───────────────────


class TestAccountRotateModes:
    """`/server-accounts/{id}/rotate` — точечная (?server_id=) и массовая."""

    async def test_mass_dispatches_per_linked_server(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="ops")
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["mode"] == "all"
        assert {t["server_id"] for t in body["tasks"]} == {srv1.id, srv2.id}
        # По задаче на каждый привязанный сервер.
        assert len(captured_dispatch) == 2
        assert {c["target_server_id"] for c in captured_dispatch} == {srv1.id, srv2.id}
        for c in captured_dispatch:
            assert c["task_kind"] == "account.rotate_password"
            assert c["target_resource_id"] == acc.id

    async def test_targeted_dispatches_single_server(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="ops")
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
            params={"server_id": srv2.id},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["mode"] == "single"
        assert [t["server_id"] for t in body["tasks"]] == [srv2.id]
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["target_server_id"] == srv2.id
        assert captured_dispatch[0]["payload"]["server_id"] == srv2.id

    async def test_targeted_unlinked_server_404(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch,
    ):
        srv1 = await make_server(department_id="dep_a")
        other = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv1.id, login="ops")
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
            params={"server_id": other.id},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "ACCOUNT_NOT_FOUND"
        assert captured_dispatch == []

    async def test_mass_idempotency_key_split_per_server(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch,
    ):
        """Один Idempotency-Key не должен схлопнуть массовую ротацию в одну задачу."""
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="ops")
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers={**_hdr(operator_token_a), "Idempotency-Key": "mass-1"},
        )
        assert resp.status_code == 202
        assert len(captured_dispatch) == 2
        keys = {c["idempotency_key"] for c in captured_dispatch}
        assert keys == {f"mass-1:{srv1.id}", f"mass-1:{srv2.id}"}


class TestMassRotatePartialTolerance:
    """Массовая ротация: один битый сервер не валит весь батч."""

    async def test_one_decommissioned_others_dispatched(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, db,
    ):
        """Списанный сервер пропускается, на остальные задачи ставятся."""
        from src.core.constants import ServerStatus

        good1 = await make_server(department_id="dep_a")
        dead = await make_server(department_id="dep_a")
        good2 = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[good1.id, dead.id, good2.id], login="ops",
        )
        dead.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["mode"] == "all"
        # Задачи только на живые серверы.
        assert {t["server_id"] for t in body["tasks"]} == {good1.id, good2.id}
        assert len(captured_dispatch) == 2
        assert {c["target_server_id"] for c in captured_dispatch} == {good1.id, good2.id}
        # Списанный — в skipped.
        assert body["skipped"] == [{"server_id": dead.id, "reason": "decommissioned"}]

    async def test_aggregated_success_audit_with_skips(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, captured_emits, db,
    ):
        """Агрегированный success-аудит эмитится даже при частичном пропуске."""
        from src.core.constants import ServerStatus

        good = await make_server(department_id="dep_a")
        dead = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[good.id, dead.id], login="ops")
        dead.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text

        successes = [
            e for e in _events(captured_emits, "server_account.rotate_password_dispatch")
            if e.get("status") == "success"
        ]
        assert len(successes) == 1
        ev = successes[0]
        assert ev["details"]["mode"] == "all"
        assert ev["details"]["dispatched"] == 1
        assert ev["details"]["skipped_count"] == 1
        assert ev["details"]["server_ids"] == [good.id]
        assert ev["details"]["skipped"] == [
            {"server_id": dead.id, "reason": "decommissioned"}
        ]

    async def test_all_decommissioned_returns_409(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, db,
    ):
        """Если списаны все привязанные серверы — нечего ставить, 409."""
        from src.core.constants import ServerStatus

        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="ops")
        srv1.status = ServerStatus.DECOMMISSIONED
        srv2.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"
        assert captured_dispatch == []

    async def test_idempotent_conflict_skips_one_server(
        self, client, operator_token_a, make_server, make_account,
        captured_emits, monkeypatch, db,
    ):
        """Idempotent-конфликт на одном сервере пропускается, остальные идут."""
        from src.core.exceptions import ConflictError

        good = await make_server(department_id="dep_a")
        busy = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[good.id, busy.id], login="ops")

        async def selective(*, target_server_id, **kwargs):
            if target_server_id == busy.id:
                raise ConflictError(
                    error_code="TASK_IDEMPOTENT_CONFLICT",
                    message="already queued",
                )
            return "tsk_ok"

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            selective,
        )
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert [t["server_id"] for t in body["tasks"]] == [good.id]
        assert body["skipped"] == [
            {"server_id": busy.id, "reason": "idempotent_conflict"}
        ]
        successes = [
            e for e in _events(captured_emits, "server_account.rotate_password_dispatch")
            if e.get("status") == "success"
        ]
        assert len(successes) == 1
        assert successes[0]["details"]["dispatched"] == 1
        assert successes[0]["details"]["skipped_count"] == 1

    async def test_worker_unreachable_aborts_whole_batch(
        self, client, operator_token_a, make_server, make_account,
        monkeypatch, db,
    ):
        """Глобальная недоступность воркера отбивает весь массовый запрос 503."""
        from src.core.exceptions import ServiceUnavailableError

        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="ops")

        async def boom(**_kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_REDIS_NOT_CONFIGURED",
                message="redis env missing",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom,
        )
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503
        assert resp.json()["error_code"] == "WORKER_REDIS_NOT_CONFIGURED"

    async def test_worker_unreachable_midbatch_emits_partial_aggregate(
        self, client, operator_token_a, make_server, make_account,
        captured_emits, monkeypatch, db,
    ):
        """Если воркер падает после K успешных INSERT+RPUSH в батче, SIEM
        должен увидеть агрегат с фактическим dispatched/failed/not_attempted —
        не только per-server failure последнего сервера."""
        from src.core.exceptions import ServiceUnavailableError

        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        srv3 = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv1.id, srv2.id, srv3.id], login="ops",
        )

        call_count = {"n": 0}

        async def boom_after_first(*, target_server_id, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "tsk_ok_1"
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE",
                message="redis down mid-batch",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom_after_first,
        )
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503

        events = _events(
            captured_emits, "server_account.rotate_password_dispatch",
        )
        # Должны быть два failure-emit: per-server (worker_unreachable) и
        # агрегированный (worker_unreachable_partial) с фактическим состоянием.
        partial_aggregates = [
            e for e in events
            if e.get("status") == "failure"
            and e["details"].get("reason") == "worker_unreachable_partial"
        ]
        assert len(partial_aggregates) == 1
        agg = partial_aggregates[0]["details"]
        assert agg["mode"] == "all"
        assert agg["dispatched_count"] == 1
        # Сервер, на котором случился сбой — единственный в `failed`.
        assert len(agg["failed"]) == 1
        # Третий сервер остался непопытанным.
        assert agg["not_attempted"] == [srv3.id] or len(agg["not_attempted"]) == 1


class TestIdempotencyKeyLength:
    """`Idempotency-Key` header — guard на длину.

    Worker-БД хранит `idempotency_key` в VARCHAR(128). Per-server fan-out
    (mass rotate / `fanout_update_on_host`) дописывает `:<server.id>` —
    без жёсткого лимита длинный клиентский ключ ронял INSERT с DataError
    и валил массовый вызов 503 `WORKER_UNREACHABLE`. Guard режет на входе
    с 400 `IDEMPOTENCY_KEY_TOO_LONG`.
    """

    async def test_oversized_key_rejected_400(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        too_long = "x" * 90  # > 87
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers={**_hdr(operator_token_a), "Idempotency-Key": too_long},
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["error_code"] == "IDEMPOTENCY_KEY_TOO_LONG"
        assert body["details"]["max_length"] == 87
        assert body["details"]["got"] == 90
        # dispatch_task не должен быть вызван — guard срабатывает раньше.
        assert captured_dispatch == []

    async def test_uuid_key_accepted(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """Стандартный UUID-ключ (36 chars) проходит."""
        import uuid

        srv = await make_server(department_id="dep_a", with_ipmi=True)
        key = str(uuid.uuid4())  # 36 chars
        assert len(key) == 36
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers={**_hdr(operator_token_a), "Idempotency-Key": key},
        )
        assert resp.status_code == 202, resp.text
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["idempotency_key"] == key

    async def test_boundary_key_at_max_len_accepted(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """Ровно 87 символов — на границе, пропускаем."""
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        boundary = "k" * 87
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers={**_hdr(operator_token_a), "Idempotency-Key": boundary},
        )
        assert resp.status_code == 202, resp.text
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["idempotency_key"] == boundary

    async def test_mass_rotate_oversized_key_rejected_400(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch,
    ):
        """Mass-rotate с длинным ключом — отдельно, т.к. именно fan-out был
        точкой эксплуатации DoS (per_server_key = key + ":" + server.id
        вылезал за VARCHAR(128) → 503 на весь батч). Guard ловит ещё до
        первого dispatch'а."""
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="ops")
        too_long = "y" * 100
        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers={**_hdr(operator_token_a), "Idempotency-Key": too_long},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "IDEMPOTENCY_KEY_TOO_LONG"
        # Ни одна задача не поставилась — fan-out не запустился.
        assert captured_dispatch == []

    async def test_no_header_accepted(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """Без header'а — dispatch проходит с `idempotency_key=None`."""
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        assert captured_dispatch[0]["idempotency_key"] is None

    async def test_empty_header_treated_as_missing(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """Пустая строка эквивалентна отсутствию header'а (сохранили
        старое поведение `... or None`)."""
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers={**_hdr(operator_token_a), "Idempotency-Key": ""},
        )
        assert resp.status_code == 202, resp.text
        assert captured_dispatch[0]["idempotency_key"] is None


# ── AuthorizationError на VIEW → denied (не failure) ─────────────────────────


class TestDispatchForServerAuthzErrorIsDenied:
    """Когда у caller'а есть action-роль на dispatch (power.status/inventory.sync),
    но нет VIEW на сервер, `get_server` поднимает `AuthorizationError`.
    Это access-deny, не business-failure: audit обязан выйти со status="denied"
    и allowed=False — симметрично канону в `endpoints/ipmi.py::_dispatch_power`.
    """

    async def test_authz_error_on_view_emits_denied_for_power_status(
        self, client, operator_token_a, make_server, captured_emits,
        captured_dispatch, monkeypatch,
    ):
        from src.core.exceptions import AuthorizationError

        srv = await make_server(department_id="dep_a", with_ipmi=True)

        async def deny_view(*_args, **_kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.get_server",
            deny_view,
        )

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 403, resp.text
        assert captured_dispatch == []
        denied = [
            e for e in _events(captured_emits, "server.power_status")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1, captured_emits
        ev = denied[0]
        assert ev["allowed"] is False
        assert ev["details"]["reason"] == "no_view_permission"

    async def test_authz_error_on_view_emits_denied_for_inventory_sync(
        self, client, operator_token_a, make_server, captured_emits,
        captured_dispatch, monkeypatch,
    ):
        from src.core.exceptions import AuthorizationError

        srv = await make_server(department_id="dep_a")

        async def deny_view(*_args, **_kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.get_server",
            deny_view,
        )

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/inventory/sync",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 403, resp.text
        assert captured_dispatch == []
        denied = [
            e for e in _events(captured_emits, "server.inventory_sync")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1
        assert denied[0]["allowed"] is False
        assert denied[0]["details"]["reason"] == "no_view_permission"

    async def test_notfound_on_view_still_emits_failure(
        self, client, operator_token_a, make_server, captured_emits,
        captured_dispatch, monkeypatch,
    ):
        """NotFoundError (visibility-mask) остаётся `failure`/allowed=True —
        caller прошёл permission, цель невидима, это business-not-found."""
        from src.core.exceptions import NotFoundError

        srv = await make_server(department_id="dep_a", with_ipmi=True)

        async def hide(*_args, **_kwargs):
            raise NotFoundError(
                error_code="SERVER_NOT_FOUND",
                message="hidden",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.get_server",
            hide,
        )
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404
        assert captured_dispatch == []
        failures = [
            e for e in _events(captured_emits, "server.power_status")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        assert failures[0]["allowed"] is True
        assert failures[0]["details"]["reason"] == "not_found_or_cross_dept"

