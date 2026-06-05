"""Cross-dept dispatch on `_dispatch_account_on_host` — failure-аудит.

Закрывает дырку в security-trail: `load_visible_server` поднимает
`NotFoundError`, если department сервера изменили out-of-band уже после
линковки аккаунта. Раньше эта ветка молча отдавала 404 без
`audit_service.emit(...)`, security-trail терял evidence.
Фикс: ловим `NotFoundError` после `load_visible_server` и эмитим
`status="failure"` с reason=server_not_found_or_cross_dept (паттерн
`services/server_account.py::get_account`; convention F-W4 — visibility-404
это business-failure, не access-deny).

Сценарий моделируем напрямую через monkeypatch `load_visible_server`:
поднять рассинхронизированное состояние DB (поменять `server.dept` между
линковкой и dispatch'ем) дорого и хрупко, а проверяем мы именно ветку
обработки исключения.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import NotFoundError

from tests._helpers import auth_hdr as _hdr

BASE = "/api/server/v1/server-accounts"


@pytest.fixture
def captured_emits(monkeypatch):
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.services.server.audit_service.emit",
        "src.services.server_account.audit_service.emit",
        "src.services.permission_service.audit_service.emit",
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
    )


@pytest.fixture
def stub_dispatch(monkeypatch):
    """Заглушка `worker_client.dispatch_task` — на этот путь мы доходить не должны."""
    calls: list[dict] = []

    async def fake_dispatch(**kwargs):
        calls.append(kwargs)
        return_hit = kwargs.get("return_hit", False)
        new_id = "tsk_should_not_reach"
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


def _events(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


@pytest.mark.asyncio
class TestDispatchAccountCrossDeptFailureAudit:
    async def test_cross_dept_server_emits_failure_on_provision(
        self,
        client,
        operator_token_a,
        make_server,
        make_account,
        captured_emits,
        stub_dispatch,
        monkeypatch,
    ):
        """Symbol-уровень: `load_visible_server` raise → failure + 404, dispatch не зовётся."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")

        async def boom(db, identity, server_id):
            raise NotFoundError(
                error_code="SERVER_NOT_FOUND", message="Server not found",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.load_visible_server",
            boom,
        )

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404, resp.text

        failures = [
            e for e in _events(captured_emits, "server_account.provision")
            if e.get("status") == "failure"
            and (e.get("details") or {}).get("reason") == "server_not_found_or_cross_dept"
        ]
        assert len(failures) == 1, captured_emits
        ev = failures[0]
        assert ev["target_id"] == acc.id
        assert ev["target_type"] == "server_account"
        assert ev["allowed"] is True
        assert ev["details"]["server_id"] == srv.id
        assert ev["details"]["operation"] == "provision"
        # dispatch_task НЕ должен был дёрнуться
        assert stub_dispatch == []

    async def test_cross_dept_server_emits_failure_on_update_on_host(
        self,
        client,
        operator_token_a,
        make_server,
        make_account,
        captured_emits,
        stub_dispatch,
        monkeypatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")

        async def boom(db, identity, server_id):
            raise NotFoundError(
                error_code="SERVER_NOT_FOUND", message="Server not found",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.load_visible_server",
            boom,
        )

        resp = await client.post(
            f"{BASE}/{acc.id}/update_on_host?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404

        failures = [
            e for e in _events(captured_emits, "server_account.update_on_host")
            if e.get("status") == "failure"
            and (e.get("details") or {}).get("reason") == "server_not_found_or_cross_dept"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["details"]["operation"] == "update"
        assert ev["details"]["server_id"] == srv.id
        assert stub_dispatch == []

    async def test_happy_path_still_emits_success(
        self,
        client,
        operator_token_a,
        make_server,
        make_account,
        captured_emits,
        monkeypatch,
    ):
        """Sanity: cross-dept фикс не сломал нормальный путь — success-аудит на месте."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")

        async def fake_dispatch(**kwargs):
            return "tsk_ok_42"

        async def fake_dispatch_with_hit(**kwargs):
            return ("tsk_ok_42", False)

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
        monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            fake_dispatch,
        )
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
            fake_dispatch_with_hit,
        )

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202

        # Никаких ложных failure на счастливом пути
        failures = [
            e for e in _events(captured_emits, "server_account.provision")
            if e.get("status") == "failure"
            and (e.get("details") or {}).get("reason") == "server_not_found_or_cross_dept"
        ]
        assert failures == []
        success = [
            e for e in _events(captured_emits, "server_account.provision")
            if e.get("status") == "success"
        ]
        assert len(success) == 1
