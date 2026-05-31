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

BASE = "/api/server/v1/server-accounts"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def captured_emits(monkeypatch):
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    for path in (
        "src.services.server.audit_service.emit",
        "src.services.server_account.audit_service.emit",
        "src.services.permission_service.audit_service.emit",
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
    ):
        try:
            monkeypatch.setattr(path, fake_emit)
        except (AttributeError, ImportError):
            pass
    return captured


@pytest.fixture
def stub_dispatch(monkeypatch):
    """Заглушка `worker_client.dispatch_task` — на этот путь мы доходить не должны."""
    calls: list[dict] = []

    async def fake_dispatch(**kwargs):
        calls.append(kwargs)
        return "tsk_should_not_reach"

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
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

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            fake_dispatch,
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
