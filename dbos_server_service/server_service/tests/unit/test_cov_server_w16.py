"""Coverage gaps — server_service w16.

Areas:
* _dispatch_power — AuthorizationError branch from get_server (no VIEW):
  emits status=denied, allowed=False, reason=no_view_permission.
* _dispatch_for_server — same AuthorizationError branch for power.status
  and inventory.sync.
* account_rotate_password_dispatch — mode=single + server vanished after
  preflight (load_visible_servers returns None): SERVER_NOT_FOUND 404.
* account_rotate_password_dispatch — mode=all + server not visible:
  skipped with reason=not_found_or_cross_dept.
* fanout_update_on_host — decommissioned server in loop: failure audit +
  continue (not a hard fail).
* fanout_update_on_host — ConflictError / ServiceUnavailableError per
  server: continue loop, failure audit.
* record_provision_status — server is None: server_not_found audit + 404.
* receive_users_inventory — server is None: server_not_found audit + 404.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.constants import ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.schemas.identity import IdentityContext


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехватывает dispatch_task во всех модулях — возвращает список вызовов."""
    calls: list[dict] = []

    async def fake_dispatch(
        *, task_kind, target_server_id, payload,
        created_by, request_id,
        target_resource_id=None, idempotency_key=None,
        return_hit=False,
    ):
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
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.ipmi.worker_client.dispatch_task",
        fake_dispatch,
    )
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.ipmi.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls

BASE = "/api/server/v1"
BASE_INT = "/api/server/v1/internal"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_identity(
    *,
    user_id: str = "usr_test",
    department_id: str | None = "dep_a",
    service_roles: dict | None = None,
    subject_type: str = "user",
) -> IdentityContext:
    return IdentityContext(
        user_id=user_id,
        username="tester",
        department_id=department_id,
        department_name=None,
        allowed_services=["server_service"],
        service_roles=service_roles or {"server_service": ["admin"]},
        is_banned=False,
        platform_role=None,
        subject_type=subject_type,
    )


def _capture_emits(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.ipmi.audit_service.emit",
        fake_emit,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
        fake_emit,
    )
    monkeypatch.setattr(
        "src.services.server.audit_service.emit",
        fake_emit,
    )
    return captured


# ─────────────────────────────────────────────────────────────────────────────
# 1. _dispatch_power — AuthorizationError (no VIEW) branch
# ─────────────────────────────────────────────────────────────────────────────


class TestDispatchPowerNoViewPermission:
    """_dispatch_power: caller имеет POWER_ON, но не VIEW.

    get_server бросает AuthorizationError → audit denied+allowed=False,
    reason=no_view_permission.

    Этот сценарий реализуется через роль с power_on без view (нестандартная
    матрица, в дефолтной матрице operator имеет оба). Проще всего — monkeypatch
    server_svc.get_server в нужном модуле.
    """

    async def test_no_view_power_on_emits_denied_audit(
        self, client, make_server, make_token, dept_a, monkeypatch,
    ):
        """power/on: get_server → AuthorizationError → denied audit с reason=no_view_permission."""
        captured = _capture_emits(monkeypatch)

        srv = await make_server(department_id=dept_a, with_ipmi=True)
        tok = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["operator"]},
        )

        # Имитируем роль: require_action(POWER_ON) проходит, а get_server
        # бросает AuthorizationError из-за отсутствия VIEW.
        async def _raise_authz(*args, **kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW action",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.server_svc.get_server",
            _raise_authz,
        )

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/on",
            headers=_hdr(tok),
        )
        assert resp.status_code == 403

        denied = [
            e for e in captured
            if e["action"] == "server.power_on" and e.get("status") == "denied"
        ]
        assert len(denied) == 1, captured
        ev = denied[0]
        assert ev["allowed"] is False
        assert ev["details"]["reason"] == "no_view_permission"
        assert ev["target_id"] == srv.id

    async def test_no_view_power_off_emits_denied_audit(
        self, client, make_server, make_token, dept_a, monkeypatch,
    ):
        """power/off: то же — denied audit для AuthorizationError ветки."""
        captured = _capture_emits(monkeypatch)

        srv = await make_server(department_id=dept_a, with_ipmi=True)
        tok = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["operator"]},
        )

        async def _raise_authz(*args, **kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW action",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.server_svc.get_server",
            _raise_authz,
        )

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/off",
            headers=_hdr(tok),
        )
        assert resp.status_code == 403

        denied = [
            e for e in captured
            if e["action"] == "server.power_off" and e.get("status") == "denied"
        ]
        assert len(denied) == 1, captured
        ev = denied[0]
        assert ev["allowed"] is False
        assert ev["details"]["reason"] == "no_view_permission"

    async def test_no_view_power_reboot_emits_denied_audit(
        self, client, make_server, make_token, dept_a, monkeypatch,
    ):
        """power/reboot: denied audit для AuthorizationError ветки."""
        captured = _capture_emits(monkeypatch)

        srv = await make_server(department_id=dept_a, with_ipmi=True)
        tok = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["operator"]},
        )

        async def _raise_authz(*args, **kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW action",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.server_svc.get_server",
            _raise_authz,
        )

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/reboot",
            headers=_hdr(tok),
        )
        assert resp.status_code == 403

        denied = [
            e for e in captured
            if e["action"] == "server.power_reboot" and e.get("status") == "denied"
        ]
        assert len(denied) == 1, captured
        ev = denied[0]
        assert ev["allowed"] is False

    async def test_no_view_emits_no_success(
        self, client, make_server, make_token, dept_a, monkeypatch,
    ):
        """AuthorizationError → no success audit."""
        captured = _capture_emits(monkeypatch)

        srv = await make_server(department_id=dept_a, with_ipmi=True)
        tok = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["operator"]},
        )

        async def _raise_authz(*args, **kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.server_svc.get_server",
            _raise_authz,
        )

        await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/on",
            headers=_hdr(tok),
        )

        successes = [
            e for e in captured
            if e["action"] == "server.power_on" and e.get("status") == "success"
        ]
        assert successes == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. _dispatch_for_server — AuthorizationError (no VIEW) branch
# ─────────────────────────────────────────────────────────────────────────────


class TestDispatchForServerNoViewPermission:
    """_dispatch_for_server: get_server → AuthorizationError → denied audit.

    AuthorizationError = access-deny, status=denied + allowed=False. NotFoundError
    = visibility-mask, status=failure + allowed=True (см. F-W16-W1 fix
    в worker_dispatch.py:206-225).
    """

    async def test_power_status_no_view_emits_denied_audit(
        self, client, make_server, make_token, dept_a, monkeypatch,
    ):
        """power/status: AuthorizationError → denied audit, allowed=False."""
        captured = _capture_emits(monkeypatch)

        srv = await make_server(department_id=dept_a, with_ipmi=True)
        tok = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["operator"]},
        )

        async def _raise_authz(*args, **kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.get_server",
            _raise_authz,
        )

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(tok),
        )
        assert resp.status_code == 403

        denied = [
            e for e in captured
            if e["action"] == "server.power_status"
            and e.get("status") == "denied"
        ]
        assert len(denied) == 1, captured
        ev = denied[0]
        assert ev["allowed"] is False
        assert ev["details"]["reason"] == "no_view_permission"

    async def test_inventory_sync_no_view_emits_denied_audit(
        self, client, make_server, make_token, dept_a, monkeypatch,
    ):
        """inventory/sync: AuthorizationError → denied audit, allowed=False."""
        captured = _capture_emits(monkeypatch)

        srv = await make_server(department_id=dept_a)
        tok = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["operator"]},
        )

        async def _raise_authz(*args, **kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.get_server",
            _raise_authz,
        )

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/inventory/sync",
            headers=_hdr(tok),
        )
        assert resp.status_code == 403

        denied = [
            e for e in captured
            if e["action"] == "server.inventory_sync"
            and e.get("status") == "denied"
        ]
        assert len(denied) == 1, captured
        ev = denied[0]
        assert ev["allowed"] is False
        assert ev["details"]["reason"] == "no_view_permission"


# ─────────────────────────────────────────────────────────────────────────────
# 3. account_rotate_password_dispatch — single mode + server vanished
# ─────────────────────────────────────────────────────────────────────────────


class TestAccountRotateDispatchSingleServerVanished:
    """mode=single: сервер был среди привязанных, но load_visible_servers
    его не вернул (dept изменили out-of-band, или сервер удалён) →
    SERVER_NOT_FOUND 404.
    """

    async def test_single_server_vanished_after_preflight_returns_404(
        self, client, operator_token_a, make_server, make_account, monkeypatch,
    ):
        """mode=single: servers_by_id.get(sid) is None → 404 SERVER_NOT_FOUND."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")

        # Возвращаем пустой dict — как будто сервер исчез из visible
        async def _empty_visible(db, identity, target_ids):
            return {}

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.load_visible_servers",
            _empty_visible,
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
            params={"server_id": srv.id},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"

    async def test_single_server_vanished_no_dispatch(
        self, client, operator_token_a, make_server, make_account,
        monkeypatch, captured_dispatch,
    ):
        """При исчезновении сервера в single-mode диспатч не происходит."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")

        async def _empty_visible(db, identity, target_ids):
            return {}

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.load_visible_servers",
            _empty_visible,
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
            params={"server_id": srv.id},
        )
        assert resp.status_code == 404
        assert captured_dispatch == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. account_rotate_password_dispatch — all mode + server not visible → skipped
# ─────────────────────────────────────────────────────────────────────────────


class TestAccountRotateDispatchMassServerNotVisible:
    """mode=all: один из серверов пропал из load_visible_servers →
    пропускается с reason=not_found_or_cross_dept.
    """

    async def test_mass_one_server_vanished_skipped(
        self, client, operator_token_a, make_server, make_account, monkeypatch,
    ):
        """Один сервер пропал — в skipped, другие получают задачи."""
        srv_ok = await make_server(department_id="dep_a")
        srv_gone = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv_ok.id, srv_gone.id], login="ops",
        )

        dispatched: list[dict] = []

        async def _selective_visible(db, identity, target_ids):
            from src.repositories import server as server_repo
            rows = {}
            for sid in target_ids:
                if sid == srv_gone.id:
                    continue
                row = await server_repo.get_by_id(db, sid)
                if row is not None:
                    rows[sid] = row
            return rows

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.load_visible_servers",
            _selective_visible,
        )

        async def _fake_dispatch(*, target_server_id, **kwargs):
            dispatched.append(target_server_id)
            return "tsk_fake"

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            _fake_dispatch,
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["mode"] == "all"
        task_server_ids = {t["server_id"] for t in body["tasks"]}
        assert srv_ok.id in task_server_ids
        assert srv_gone.id not in task_server_ids
        skipped_ids = {s["server_id"] for s in body["skipped"]}
        assert srv_gone.id in skipped_ids
        reason = next(
            s["reason"] for s in body["skipped"] if s["server_id"] == srv_gone.id
        )
        assert reason == "not_found_or_cross_dept"

    async def test_mass_all_servers_vanished_dispatches_none(
        self, client, operator_token_a, make_server, make_account, monkeypatch,
    ):
        """Все серверы пропали — 409 (dispatchable пусто, как all-decommissioned)."""
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="ops")

        async def _empty_visible(db, identity, target_ids):
            return {}

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.load_visible_servers",
            _empty_visible,
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        # dispatchable == [] → same path as all-decommissioned
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"


# ─────────────────────────────────────────────────────────────────────────────
# 5. fanout_update_on_host — decommissioned server in loop
# ─────────────────────────────────────────────────────────────────────────────


class TestFanoutUpdateOnHostDecommissioned:
    """fanout_update_on_host: сервер в DECOMMISSIONED состоянии во время
    PATCH → failure audit с reason=decommissioned + source=edit_fanout,
    сервер пропускается (не hard-fail).
    """

    async def test_patch_decommissioned_server_emits_failure_audit(
        self, client, admin_role_token_a, make_server, make_account, db,
        monkeypatch,
    ):
        """PATCH с OS-managed полем: decommissioned сервер → failure audit, не 409."""
        from sqlalchemy import select
        from src.models import ServerAccountServer

        captured = _capture_emits(monkeypatch)

        srv_ok = await make_server(department_id="dep_a")
        srv_dead = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv_ok.id, srv_dead.id], login="fanout_test",
        )
        # Пометить оба сервера как present_on_server=True
        for sid in [srv_ok.id, srv_dead.id]:
            link = (await db.execute(
                select(ServerAccountServer).where(
                    ServerAccountServer.account_id == acc.id,
                    ServerAccountServer.server_id == sid,
                )
            )).scalar_one()
            link.present_on_server = True
        await db.flush()

        srv_dead.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        # Патчим dispatch, чтобы не трогать worker
        async def _noop_dispatch(**kwargs):
            return "tsk_noop"

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            _noop_dispatch,
        )

        resp = await client.patch(
            f"{BASE}/server-accounts/{acc.id}",
            headers=_hdr(admin_role_token_a),
            json={"has_sudo": True},
        )
        assert resp.status_code == 200, resp.text

        fanout_failures = [
            e for e in captured
            if e["action"] == "server_account.update_on_host"
            and e.get("status") == "failure"
            and (e.get("details") or {}).get("reason") == "decommissioned"
        ]
        assert len(fanout_failures) >= 1, captured
        ev = fanout_failures[0]
        assert ev["allowed"] is True
        assert ev["details"]["server_id"] == srv_dead.id
        assert ev["details"]["source"] == "edit_fanout"

    async def test_patch_decommissioned_does_not_abort_other_servers(
        self, client, admin_role_token_a, make_server, make_account, db,
        monkeypatch,
    ):
        """PATCH: decommissioned сервер пропускается, живой получает задачу."""
        from sqlalchemy import select
        from src.models import ServerAccountServer

        dispatched: list[dict] = []

        async def _fake_dispatch(*, target_server_id, **kwargs):
            dispatched.append({"target_server_id": target_server_id})
            return "tsk_fanout"

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            _fake_dispatch,
        )

        srv_ok = await make_server(department_id="dep_a")
        srv_dead = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv_ok.id, srv_dead.id], login="two_srv",
        )
        for sid in [srv_ok.id, srv_dead.id]:
            link = (await db.execute(
                select(ServerAccountServer).where(
                    ServerAccountServer.account_id == acc.id,
                    ServerAccountServer.server_id == sid,
                )
            )).scalar_one()
            link.present_on_server = True
        await db.flush()

        srv_dead.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        resp = await client.patch(
            f"{BASE}/server-accounts/{acc.id}",
            headers=_hdr(admin_role_token_a),
            json={"shell": "/bin/bash"},
        )
        assert resp.status_code == 200, resp.text

        server_ids = {d["target_server_id"] for d in dispatched}
        assert srv_ok.id in server_ids
        assert srv_dead.id not in server_ids


# ─────────────────────────────────────────────────────────────────────────────
# 6. fanout_update_on_host — ConflictError / ServiceUnavailableError per server
# ─────────────────────────────────────────────────────────────────────────────


class TestFanoutUpdateOnHostDispatchFailures:
    """fanout_update_on_host: ConflictError/ServiceUnavailableError per сервер →
    failure audit + continue (не hard-fail весь PATCH).
    """

    async def test_conflict_on_one_server_continues_others(
        self, client, admin_role_token_a, make_server, make_account, db,
        monkeypatch,
    ):
        """ConflictError на первом сервере → идёт дальше, второй получает задачу."""
        from sqlalchemy import select
        from src.models import ServerAccountServer

        dispatched: list[str] = []
        call_order: list[str] = []

        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="cf_test")
        for sid in [srv1.id, srv2.id]:
            link = (await db.execute(
                select(ServerAccountServer).where(
                    ServerAccountServer.account_id == acc.id,
                    ServerAccountServer.server_id == sid,
                )
            )).scalar_one()
            link.present_on_server = True
        await db.flush()

        async def _selective(*, target_server_id, **kwargs):
            call_order.append(target_server_id)
            if target_server_id == srv1.id:
                raise ConflictError(
                    error_code="TASK_IDEMPOTENT_CONFLICT",
                    message="already queued",
                )
            dispatched.append(target_server_id)
            return "tsk_ok"

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            _selective,
        )

        captured = _capture_emits(monkeypatch)

        resp = await client.patch(
            f"{BASE}/server-accounts/{acc.id}",
            headers=_hdr(admin_role_token_a),
            json={"has_sudo": True},
        )
        assert resp.status_code == 200, resp.text

        assert srv2.id in dispatched

        fanout_failures = [
            e for e in captured
            if e["action"] == "server_account.update_on_host"
            and e.get("status") == "failure"
            and (e.get("details") or {}).get("reason") == "idempotent_conflict"
        ]
        assert len(fanout_failures) >= 1, captured
        ev = fanout_failures[0]
        assert ev["details"]["server_id"] == srv1.id
        assert ev["details"]["source"] == "edit_fanout"

    async def test_worker_unreachable_on_one_server_continues_others(
        self, client, admin_role_token_a, make_server, make_account, db,
        monkeypatch,
    ):
        """ServiceUnavailableError на одном сервере → continue, второй dispatched."""
        from sqlalchemy import select
        from src.models import ServerAccountServer

        dispatched: list[str] = []

        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="svc_test")
        for sid in [srv1.id, srv2.id]:
            link = (await db.execute(
                select(ServerAccountServer).where(
                    ServerAccountServer.account_id == acc.id,
                    ServerAccountServer.server_id == sid,
                )
            )).scalar_one()
            link.present_on_server = True
        await db.flush()

        async def _selective(*, target_server_id, **kwargs):
            if target_server_id == srv1.id:
                raise ServiceUnavailableError(
                    error_code="WORKER_UNREACHABLE",
                    message="redis down",
                )
            dispatched.append(target_server_id)
            return "tsk_ok"

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            _selective,
        )

        captured = _capture_emits(monkeypatch)

        resp = await client.patch(
            f"{BASE}/server-accounts/{acc.id}",
            headers=_hdr(admin_role_token_a),
            json={"has_sudo": True},
        )
        assert resp.status_code == 200, resp.text

        assert srv2.id in dispatched

        fanout_failures = [
            e for e in captured
            if e["action"] == "server_account.update_on_host"
            and e.get("status") == "failure"
            and (e.get("details") or {}).get("reason") == "worker_unreachable"
        ]
        assert len(fanout_failures) >= 1, captured
        ev = fanout_failures[0]
        assert ev["details"]["server_id"] == srv1.id
        assert ev["details"]["source"] == "edit_fanout"


# ─────────────────────────────────────────────────────────────────────────────
# 7. record_provision_status — server is None
# ─────────────────────────────────────────────────────────────────────────────


class TestRecordProvisionStatusServerNotFound:
    """record_provision_status: server не найден по server_id → server_not_found audit + 404."""

    @pytest.mark.asyncio
    async def test_server_not_found_returns_404(self, monkeypatch, db):
        """server_repo.get_by_id → None → NotFoundError ACCOUNT_NOT_FOUND."""
        from src.services import internal_service

        async def _no_server(db_s, sid):
            return None

        async def _require_ok(db_s, ident, et, ac):
            return None

        monkeypatch.setattr(internal_service.server_repo, "get_by_id", _no_server)
        monkeypatch.setattr(
            internal_service.permissions, "require_action", _require_ok,
        )

        captured: list[dict] = []

        def fake_emit(action, **kwargs):
            captured.append({"action": action, **kwargs})

        monkeypatch.setattr(internal_service.audit_service, "emit", fake_emit)

        identity = _make_identity()
        from src.schemas.internal import ProvisionStatusRequest

        payload = ProvisionStatusRequest(
            operation="provision",
            present=True,
        )

        with pytest.raises(NotFoundError) as exc_info:
            await internal_service.record_provision_status(
                db, identity, "srv_missing", "acc_test", payload,
                target_department_id="dep_a",
            )

        assert exc_info.value.error_code == "ACCOUNT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_server_not_found_emits_server_not_found_audit(
        self, monkeypatch, db,
    ):
        """server is None → audit emit с reason=server_not_found."""
        from src.services import internal_service

        async def _no_server(db_s, sid):
            return None

        async def _require_ok(db_s, ident, et, ac):
            return None

        monkeypatch.setattr(internal_service.server_repo, "get_by_id", _no_server)
        monkeypatch.setattr(
            internal_service.permissions, "require_action", _require_ok,
        )

        captured: list[dict] = []

        def fake_emit(action, **kwargs):
            captured.append({"action": action, **kwargs})

        monkeypatch.setattr(internal_service.audit_service, "emit", fake_emit)

        identity = _make_identity()
        from src.schemas.internal import ProvisionStatusRequest

        payload = ProvisionStatusRequest(operation="provision", present=True)

        with pytest.raises(NotFoundError):
            await internal_service.record_provision_status(
                db, identity, "srv_gone", "acc_xyz", payload,
                target_department_id="dep_a",
            )

        server_not_found = [
            e for e in captured
            if e["action"] == "server_account.provision_status"
            and (e.get("details") or {}).get("reason") == "server_not_found"
        ]
        assert len(server_not_found) == 1, captured
        ev = server_not_found[0]
        assert ev["target_id"] == "acc_xyz"
        assert ev["target_type"] == "server_account"
        assert ev.get("status") == "failure"
        assert ev["details"]["server_id"] == "srv_gone"


# ─────────────────────────────────────────────────────────────────────────────
# 8. receive_users_inventory — server is None
# ─────────────────────────────────────────────────────────────────────────────


class TestReceiveUsersInventoryServerNotFound:
    """receive_users_inventory: server is None → server_not_found audit + 404."""

    @pytest.mark.asyncio
    async def test_server_not_found_returns_not_found_error(
        self, monkeypatch, db,
    ):
        """server_repo.get_by_id → None → NotFoundError SERVER_NOT_FOUND."""
        from src.services import internal_service

        async def _no_server(db_s, sid):
            return None

        async def _require_ok(db_s, ident, et, ac):
            return None

        monkeypatch.setattr(internal_service.server_repo, "get_by_id", _no_server)
        monkeypatch.setattr(
            internal_service.permissions, "require_action", _require_ok,
        )

        captured: list[dict] = []

        def fake_emit(action, **kwargs):
            captured.append({"action": action, **kwargs})

        monkeypatch.setattr(internal_service.audit_service, "emit", fake_emit)

        identity = _make_identity()
        from src.schemas.internal import UsersInventoryCallbackRequest

        payload = UsersInventoryCallbackRequest(users=[])

        with pytest.raises(NotFoundError) as exc_info:
            await internal_service.receive_users_inventory(
                db, identity, "srv_missing_ui", payload,
                target_department_id="dep_a",
            )

        assert exc_info.value.error_code == "SERVER_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_server_not_found_emits_server_not_found_audit(
        self, monkeypatch, db,
    ):
        """server is None → audit emit reason=server_not_found."""
        from src.services import internal_service

        async def _no_server(db_s, sid):
            return None

        async def _require_ok(db_s, ident, et, ac):
            return None

        monkeypatch.setattr(internal_service.server_repo, "get_by_id", _no_server)
        monkeypatch.setattr(
            internal_service.permissions, "require_action", _require_ok,
        )

        captured: list[dict] = []

        def fake_emit(action, **kwargs):
            captured.append({"action": action, **kwargs})

        monkeypatch.setattr(internal_service.audit_service, "emit", fake_emit)

        identity = _make_identity()
        from src.schemas.internal import UsersInventoryCallbackRequest

        payload = UsersInventoryCallbackRequest(users=[])

        with pytest.raises(NotFoundError):
            await internal_service.receive_users_inventory(
                db, identity, "srv_gone_ui", payload,
                target_department_id="dep_a",
            )

        server_not_found = [
            e for e in captured
            if e["action"] == "server_account.users_inventory_received"
            and (e.get("details") or {}).get("reason") == "server_not_found"
        ]
        assert len(server_not_found) == 1, captured
        ev = server_not_found[0]
        assert ev["target_id"] == "srv_gone_ui"
        assert ev["target_type"] == "server"
        assert ev.get("status") == "failure"
        assert ev.get("allowed") is True
