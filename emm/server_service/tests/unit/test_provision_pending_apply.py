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


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


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

    monkeypatch.setattr(worker_client, "_creds_redis_client", pooled)

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
        return_hit = kwargs.get("return_hit", False)
        new_id = f"tsk_{len(calls)}"
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

    async def test_deprovision_callback_keeps_pending_apply(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, stub_redis, db, worker_bot_token_a,
    ):
        """Deprovision (`present=False`) НЕ снимает pending_apply.

        Race: оператор ротировал пароль (pending_apply=True), параллельно
        userdel на одном из серверов группы присылает deprovision-callback.
        Этот callback ничего не применяет — он не должен закрывать переходный
        период ротации и зануляять удержанный previous_password."""
        import base64

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old-secret1")
        await db.commit()

        # Ротация открывает переходный период.
        await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
            json={"password_b64": base64.b64encode(b"new-secret9").decode()},
        )
        await db.refresh(acc)
        assert acc.credentials_pending_apply is True
        assert acc.previous_password_encrypted is not None

        # Deprovision-callback (present=False) от воркера.
        cb_url = (
            f"/api/server/v1/internal/servers/{srv.id}/accounts/{acc.id}/provision_status"
        )
        cb_headers = _hdr(worker_bot_token_a)
        cb_headers["X-Target-Department-Id"] = srv.department_id
        resp = await client.post(
            cb_url,
            json={"operation": "deprovision", "present": False},
            headers=cb_headers,
        )
        assert resp.status_code == 200, resp.text

        await db.refresh(acc)
        # Флаг и previous остаются — новый пароль ещё никуда не доехал.
        assert acc.credentials_pending_apply is True
        assert acc.previous_password_encrypted is not None

        # Provision-callback (present=True) закрывает период штатно.
        resp = await client.post(
            cb_url,
            json={"operation": "provision", "present": True},
            headers=cb_headers,
        )
        assert resp.status_code == 200, resp.text
        await db.refresh(acc)
        assert acc.credentials_pending_apply is False
        assert acc.previous_password_encrypted is None

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
class TestPreviousPasswordRetention:
    """Удержание прежнего пароля на время переходного периода ротации.

    rotate переносит current→previous и поднимает pending_apply; GET с
    view_password отдаёт оба пароля; без права — оба null; первый
    provision-callback зануляет previous вместе со снятием pending_apply.
    """

    async def test_rotate_moves_current_to_previous(
        self, client, operator_token_a, make_server, make_account, db,
    ):
        from src.models import ServerAccount
        from src.services import secrets_service
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old-secret1")
        await db.commit()

        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
            json={"password_b64": __import__("base64").b64encode(b"new-secret9").decode()},
        )
        assert resp.status_code == 200, resp.text

        row = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        aad = secrets_service.aad_for_server_account_password(row.id)
        assert secrets_service.decrypt(row.password_encrypted, aad=aad) == "new-secret9"
        assert row.previous_password_encrypted is not None
        assert secrets_service.decrypt(row.previous_password_encrypted, aad=aad) == "old-secret1"
        assert row.previous_password_rotated_at is not None
        # Переходный период открыт — apply на серверы ещё не подтверждён.
        assert row.credentials_pending_apply is True

    async def test_get_returns_both_passwords_with_view_password(
        self, client, admin_role_token_a, operator_token_a, make_server, make_account, db,
    ):
        import base64

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old-secret1")
        await db.commit()

        await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
            json={"password_b64": base64.b64encode(b"new-secret9").decode()},
        )

        get = await client.get(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert get.status_code == 200, get.text
        body = get.json()
        assert base64.b64decode(body["password_b64"]).decode() == "new-secret9"
        assert base64.b64decode(body["previous_password_b64"]).decode() == "old-secret1"
        assert body["previous_password_rotated_at"] is not None

    async def test_get_without_view_password_hides_both(
        self, client, reader_token_a, operator_token_a, make_server, make_account, db,
    ):
        import base64

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old-secret1")
        await db.commit()

        await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
            json={"password_b64": base64.b64encode(b"new-secret9").decode()},
        )

        get = await client.get(f"{BASE}/{acc.id}", headers=_hdr(reader_token_a))
        assert get.status_code == 200, get.text
        body = get.json()
        assert body["password_b64"] is None
        assert body["previous_password_b64"] is None
        # Сырой ciphertext старого пароля наружу не светится.
        assert "old-secret1" not in get.text

    async def test_callback_clears_previous_password(
        self, client, operator_token_a, make_server, make_account, db, worker_bot_token_a,
    ):
        import base64

        from src.models import ServerAccount
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old-secret1")
        await db.commit()

        await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
            json={"password_b64": base64.b64encode(b"new-secret9").decode()},
        )
        await db.refresh(acc)
        assert acc.previous_password_encrypted is not None
        assert acc.credentials_pending_apply is True

        # Worker подтвердил apply на сервере — переходный период закрыт.
        cb_url = (
            f"/api/server/v1/internal/servers/{srv.id}/accounts/{acc.id}/provision_status"
        )
        cb_headers = _hdr(worker_bot_token_a)
        cb_headers["X-Target-Department-Id"] = srv.department_id
        resp = await client.post(
            cb_url,
            json={"operation": "update", "present": True},
            headers=cb_headers,
        )
        assert resp.status_code == 200, resp.text

        row = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        assert row.credentials_pending_apply is False
        assert row.previous_password_encrypted is None
        assert row.previous_password_rotated_at is None


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
        assert_error(resp, 409, "ACCOUNT_HAS_NO_PASSWORD")
        # До dispatch'а не дошли
        assert captured_dispatch == []
        # БД не тронута — флаг остаётся False
        await db.refresh(acc)
        assert acc.credentials_pending_apply is False
