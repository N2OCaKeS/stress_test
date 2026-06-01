"""Двухфазный creds-drift flag `credentials_pending_apply`.

`ensure_provision_credentials` / `reset_provision_credentials` ставят флаг
в `True` перед dispatch'ем; worker-callback'и (`record_provision_status`,
`record_ipmi_credentials_rotated`) переводят в `False` после успешного
apply'я на боксе/BMC.

Race-сценарий: dispatch успешен, worker применил, но callback не дошёл
(сеть упала / worker рестартанул после apply'я). В этом случае
`credentials_pending_apply` остался `True`, и следующий ручной retry
обязан выйти с `force_replace=True` — иначе ensure_provision_credentials
заберёт ciphertext, уже не совпадающий с тем, что worker положил на бокс.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services import worker_client

BASE = "/api/server/v1/server-accounts"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def stub_redis(monkeypatch):
    storage: dict[str, tuple[str, int | None]] = {}
    pooled = MagicMock()

    async def fake_set(key, value, ex=None):
        storage[key] = (value, ex)

    async def fake_get(key):
        v = storage.get(key)
        return v[0].encode("utf-8") if v else None

    async def fake_delete(key):
        return 1 if storage.pop(key, None) is not None else 0

    pooled.set = AsyncMock(side_effect=fake_set)
    pooled.get = AsyncMock(side_effect=fake_get)
    pooled.delete = AsyncMock(side_effect=fake_delete)
    pooled.aclose = AsyncMock()

    monkeypatch.setattr(worker_client, "_prepare_redis_client", pooled)

    class _Settings:
        server_worker_redis_url = "redis://test:6379/0"
        prepare_creds_ttl_seconds = 900
        dispatch_creds_ttl_seconds = 900

    monkeypatch.setattr(worker_client, "get_settings", lambda: _Settings())
    return storage


@pytest.fixture
def captured_dispatch(monkeypatch):
    calls: list[dict] = []

    async def fake_dispatch(**kwargs):
        calls.append(kwargs)
        return f"tsk_w21_{len(calls)}"

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    return calls


@pytest.mark.asyncio
class TestProvisionPendingApply:
    async def test_first_dispatch_sets_pending_apply_true(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, stub_redis, db,
    ):
        """Свежий provision: после успешного dispatch'а pending_apply=True
        в БД, force_replace=True в payload (creds ещё не были применены)."""
        from src.core.constants import AccountSource
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.commit()
        await db.refresh(acc)

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text

        await db.refresh(acc)
        assert acc.credentials_pending_apply is True
        # На свежем dispatch'е (had_password_before=False) force_replace=True
        assert captured_dispatch[0]["payload"]["force_replace"] is True

    async def test_callback_clears_pending_apply(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, stub_redis, db, worker_bot_token_a,
    ):
        """`provision_status` callback с `present=True` снимает pending_apply."""
        from src.core.constants import AccountSource
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.commit()

        await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        await db.refresh(acc)
        assert acc.credentials_pending_apply is True

        # Worker callback — present_on_server=True, operation=provision.
        cb_url = (
            f"/api/server/v1/internal/servers/{srv.id}/accounts/{acc.id}/"
            f"provision_status"
        )
        cb_headers = _hdr(worker_bot_token_a)
        cb_headers["X-Target-Department-Id"] = srv.department_id
        resp = await client.post(
            cb_url,
            json={"operation": "provision", "present": True},
            headers=cb_headers,
        )
        assert resp.status_code == 200, resp.text

        await db.refresh(acc)
        assert acc.credentials_pending_apply is False

    async def test_retry_after_lost_callback_forces_replace(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, stub_redis, db,
    ):
        """Race: первый dispatch успешен, callback потерян (pending_apply=True
        остался), retry без force_password снова делает force_replace=True —
        БД не подтверждена worker'ом, retry обязан перезаписать на боксе."""
        from src.core.constants import AccountSource
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.commit()

        # Первый dispatch — успешен.
        await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        await db.refresh(acc)
        assert acc.credentials_pending_apply is True
        first_password_ct = acc.password_encrypted
        assert captured_dispatch[0]["payload"]["force_replace"] is True

        # Callback НЕ пришёл (симулируем потерю): pending_apply остаётся True.
        # Retry без force_password (managed-style retry или просто повторный
        # provision-вызов). force_replace должен снова быть True, ciphertext в
        # БД остался тем же (sticky на existing password), на боксе тоже
        # перезапишется через chpasswd.
        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text

        await db.refresh(acc)
        # Sticky-семантика ensure_provision_credentials: ciphertext не
        # переписан, потому что force_password не передан и аккаунт уже
        # имеет password_encrypted после первого dispatch'а.
        assert acc.password_encrypted == first_password_ct
        # Но force_replace TRUE: pending_before=True → retry форсит
        # overwrite на боксе.
        assert captured_dispatch[1]["payload"]["force_replace"] is True

    async def test_managed_account_second_provision_no_force_replace(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, stub_redis, db, worker_bot_token_a,
    ):
        """Managed: pwd уже есть, первый dispatch успешен, callback пришёл
        (pending_apply=False) — повторный provision без force_password
        идёт без force_replace (worker только добавит ключ в authorized_keys)."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="Pwd0nce!XX")
        await db.commit()

        # Первый provision.
        await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        # Callback подтверждает apply на боксе.
        cb_url = (
            f"/api/server/v1/internal/servers/{srv.id}/accounts/{acc.id}/"
            f"provision_status"
        )
        cb_headers = _hdr(worker_bot_token_a)
        cb_headers["X-Target-Department-Id"] = srv.department_id
        await client.post(
            cb_url,
            json={"operation": "provision", "present": True},
            headers=cb_headers,
        )
        await db.refresh(acc)
        assert acc.credentials_pending_apply is False

        # Повторный provision — pwd был, ssh-keypair уже есть после первого,
        # ничего нового не генерим → force_replace=False.
        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text

        assert captured_dispatch[1]["payload"]["force_replace"] is False


@pytest.mark.asyncio
class TestDiscoveredNoPasswordFailFast:
    async def test_discovered_no_password_returns_409(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, stub_redis, db,
    ):
        """Discovered + password_encrypted IS NULL + no force_password →
        409 ACCOUNT_HAS_NO_PASSWORD, dispatch_task не вызывается."""
        from src.core.constants import AccountSource
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ghost", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.commit()

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error_code"] == "ACCOUNT_HAS_NO_PASSWORD"
        # До dispatch'а не дошли
        assert captured_dispatch == []
        # БД не тронута — флаг остаётся False
        await db.refresh(acc)
        assert acc.credentials_pending_apply is False
