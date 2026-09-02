"""Тесты трёх связанных фич по серверным учёткам.

* #2 keygen: резолв кред для prepare/provision у учётки без ssh-ключа генерит и
  сохраняет пару (public + зашифрованный private), идемпотентно;
* #3 apply: set/rotate пароля и ssh-ключа поднимают `credentials_pending_apply`
  и диспатчат `account.update_on_host` на привязанные серверы; ручной
  `POST /apply` диспатчит на все present-сервера; права проверяются;
* #4 previous ssh key: ротация ssh-ключа кладёт старый в
  `previous_ssh_private_key_encrypted`; `GET /previous_ssh_private_key` отдаёт
  его (и 404, когда прежнего нет).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.models import ServerAccount
from src.services import secrets_service

from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/server-accounts"

pytestmark = pytest.mark.asyncio


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client.dispatch_task[_with_hit] — иначе fan-out упрётся в
    недоступный воркер."""
    calls: list[dict] = []
    by_key: dict[str, str] = {}

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            priority=0, return_hit=False):
        if idempotency_key is not None and idempotency_key in by_key:
            existing = by_key[idempotency_key]
            return (existing, True) if return_hit else existing
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "target_resource_id": target_resource_id,
            "payload": payload,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_{len(calls)}"
        if idempotency_key is not None:
            by_key[idempotency_key] = new_id
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


async def _reload(db, account_id: str) -> ServerAccount:
    return (
        await db.execute(select(ServerAccount).where(ServerAccount.id == account_id))
    ).scalar_one()


# ── #2 keygen на резолве кред ────────────────────────────────────────────────


class TestSshKeygenOnResolve:
    async def test_ensure_provision_credentials_generates_key_for_keyless(
        self, db, make_server, make_account,
    ):
        """Учётка с паролем, но без ssh-ключа → ensure_provision_credentials
        генерит и сохраняет пару (pub + зашифрованный priv); priv расшифровывается
        своим AAD."""
        from src.services import server_account as account_svc

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        assert acc.ssh_public_key is None
        assert acc.ssh_private_key_encrypted is None

        _, creds, generated = await account_svc.ensure_provision_credentials(db, acc)
        assert generated is True
        fresh = await _reload(db, acc.id)
        assert fresh.ssh_public_key is not None
        assert fresh.ssh_private_key_encrypted is not None
        priv = secrets_service.decrypt(
            fresh.ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_account_ssh_key(acc.id),
        )
        assert priv == creds["ssh_private_key"]
        assert "OPENSSH PRIVATE KEY" in priv

    async def test_ensure_ssh_keypair_passwordless_keeps_password_null(
        self, db, make_server, make_account,
    ):
        """Discovered без пароля → генерим только ключ, пароль не выдумываем."""
        from src.services import server_account as account_svc

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="disc", password=None)
        assert acc.password_encrypted is None

        _, ssh_creds = await account_svc.ensure_ssh_keypair(db, acc)
        fresh = await _reload(db, acc.id)
        assert fresh.ssh_public_key is not None
        assert fresh.ssh_private_key_encrypted is not None
        assert fresh.password_encrypted is None
        # Идемпотентно: повторный резолв не перегенерит ключ (sticky).
        pub1 = fresh.ssh_public_key
        _, _ = await account_svc.ensure_ssh_keypair(db, fresh)
        fresh2 = await _reload(db, acc.id)
        assert fresh2.ssh_public_key == pub1


# ── #3 auto-apply + manual apply ─────────────────────────────────────────────


class TestAutoApplyDispatch:
    async def test_rotate_password_dispatches_update_on_host(
        self, client, admin_token, make_server, make_account, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        kinds = [c["task_kind"] for c in captured_dispatch]
        assert kinds == ["account.update_on_host"], kinds
        # payload несёт apply_password (пароль есть) — воркер резолвит его сам,
        # plaintext в payload не кладётся.
        payload = captured_dispatch[0]["payload"]
        assert payload.get("apply_password") is True
        assert "password_plaintext" not in payload
        fresh = await _reload(db, acc.id)
        assert fresh.credentials_pending_apply is True

    async def test_set_ssh_key_dispatches_update_on_host_and_pending(
        self, client, admin_token, make_server, make_account, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        resp = await client.post(
            f"{BASE}/{acc.id}/ssh_key",
            json={"ssh_mode": "generate"}, headers=_hdr(admin_token),
        )
        assert resp.status_code == 202, resp.text
        kinds = [c["task_kind"] for c in captured_dispatch]
        assert kinds == ["account.update_on_host"], kinds
        payload = captured_dispatch[0]["payload"]
        # SSH-only apply: публичный ключ едет, apply_password не ставится.
        assert payload.get("ssh_public_key") is not None
        assert "apply_password" not in payload
        fresh = await _reload(db, acc.id)
        assert fresh.credentials_pending_apply is True

    async def test_rotate_password_no_present_no_dispatch(
        self, client, admin_token, make_server, make_account, captured_dispatch, db,
    ):
        """Если аккаунт не present ни на одном сервере — apply-fan-out пуст."""
        from src.models import ServerAccountServer

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        link = (
            await db.execute(
                select(ServerAccountServer).where(
                    ServerAccountServer.account_id == acc.id
                )
            )
        ).scalar_one()
        link.present_on_server = False
        await db.commit()

        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert captured_dispatch == []


class TestManualApply:
    async def test_apply_dispatches_to_all_present_servers(
        self, client, admin_token, make_server, make_account, captured_dispatch,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv1.id, srv2.id], login="ops", password="pw1",
        )
        resp = await client.post(f"{BASE}/{acc.id}/apply", headers=_hdr(admin_token))
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert len(body["tasks"]) == 2, body
        kinds = [c["task_kind"] for c in captured_dispatch]
        assert kinds == ["account.update_on_host", "account.update_on_host"], kinds
        for c in captured_dispatch:
            assert c["payload"].get("apply_password") is True

    async def test_apply_forbidden_without_rotate_password(
        self, client, no_role_token_a, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        resp = await client.post(f"{BASE}/{acc.id}/apply", headers=_hdr(no_role_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch == []

    async def test_apply_cross_dept_hidden_404(
        self, client, admin_token, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        # admin_token — dep_a; учётка dep_b скрыта за 404 для держателя права.
        resp = await client.post(f"{BASE}/{acc.id}/apply", headers=_hdr(admin_token))
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")


# ── #4 previous ssh key ──────────────────────────────────────────────────────


class TestPreviousSshKey:
    async def _generate_then_rotate(self, client, token, account_id):
        r1 = await client.post(
            f"{BASE}/{account_id}/ssh_key",
            json={"ssh_mode": "generate"}, headers=_hdr(token),
        )
        assert r1.status_code == 202, r1.text
        priv1 = r1.json()["ssh_private_key"]
        assert priv1
        r2 = await client.post(
            f"{BASE}/{account_id}/rotate_ssh_key", headers=_hdr(token),
        )
        assert r2.status_code == 202, r2.text
        priv2 = r2.json()["ssh_private_key"]
        assert priv2 and priv2 != priv1
        return priv1, priv2

    async def test_rotate_retains_previous_key(
        self, client, admin_token, make_server, make_account, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        priv1, priv2 = await self._generate_then_rotate(client, admin_token, acc.id)

        fresh = await _reload(db, acc.id)
        assert fresh.previous_ssh_private_key_encrypted is not None
        assert fresh.previous_ssh_key_rotated_at is not None
        prev = secrets_service.decrypt(
            fresh.previous_ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_account_ssh_key(acc.id),
        )
        assert prev == priv1

    async def test_reveal_previous_returns_old_key(
        self, client, admin_token, make_server, make_account, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        priv1, priv2 = await self._generate_then_rotate(client, admin_token, acc.id)

        resp = await client.get(
            f"{BASE}/{acc.id}/previous_ssh_private_key", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ssh_private_key"] == priv1

    async def test_reveal_previous_404_when_none(
        self, client, admin_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        resp = await client.get(
            f"{BASE}/{acc.id}/previous_ssh_private_key", headers=_hdr(admin_token),
        )
        assert_error(resp, 404, "ACCOUNT_NO_PREVIOUS_SSH_KEY")

    async def test_reveal_previous_forbidden_without_view_password(
        self, client, make_token, make_server, make_account, captured_dispatch,
    ):
        # Роль только с `update` (не view_password): ставит ключ, но прежний не видит.
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        admin = make_token(
            department_id="dep_a", service_roles={"server_service": ["admin"]},
        )
        await self._generate_then_rotate(client, admin, acc.id)
        # reader обычно без view_password.
        reader = make_token(
            department_id="dep_a", service_roles={"server_service": ["reader"]},
        )
        resp = await client.get(
            f"{BASE}/{acc.id}/previous_ssh_private_key", headers=_hdr(reader),
        )
        assert resp.status_code == 403, resp.text

    async def test_clear_removes_previous(
        self, client, admin_token, make_server, make_account, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        await self._generate_then_rotate(client, admin_token, acc.id)
        fresh = await _reload(db, acc.id)
        assert fresh.previous_ssh_private_key_encrypted is not None

        resp = await client.delete(
            f"{BASE}/{acc.id}/previous_ssh_private_key", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
        cleared = await _reload(db, acc.id)
        assert cleared.previous_ssh_private_key_encrypted is None
        assert cleared.previous_ssh_key_rotated_at is None
        # Прежний ключ забыт → reveal больше не отдаёт его.
        reveal = await client.get(
            f"{BASE}/{acc.id}/previous_ssh_private_key", headers=_hdr(admin_token),
        )
        assert_error(reveal, 404, "ACCOUNT_NO_PREVIOUS_SSH_KEY")

    async def test_clear_idempotent_when_no_previous(
        self, client, admin_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        # Ротации ssh-ключа не было — прежнего ключа нет, но DELETE идемпотентен.
        resp = await client.delete(
            f"{BASE}/{acc.id}/previous_ssh_private_key", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

    async def test_clear_forbidden_without_rotate_password(
        self, client, no_role_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        resp = await client.delete(
            f"{BASE}/{acc.id}/previous_ssh_private_key",
            headers=_hdr(no_role_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_clear_cross_dept_hidden_404(
        self, client, admin_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        resp = await client.delete(
            f"{BASE}/{acc.id}/previous_ssh_private_key", headers=_hdr(admin_token),
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_clear_emits_audit(
        self, client, admin_token, make_server, make_account,
        captured_dispatch, captured_emits, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        await self._generate_then_rotate(client, admin_token, acc.id)
        resp = await client.delete(
            f"{BASE}/{acc.id}/previous_ssh_private_key", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        events = [
            e for e in captured_emits
            if e["action"] == "server_account.clear_previous_ssh_key"
        ]
        assert len(events) == 1, captured_emits
        assert events[0]["status"] == "success"
        assert events[0]["details"]["cleared"] is True


@pytest.fixture
def captured_emits(monkeypatch):
    """Захват audit_service.emit в use-case-слое server_account."""
    from tests._helpers import make_emit_capture

    return make_emit_capture(monkeypatch)


class TestRotateResponseFanout:
    async def test_rotate_response_lists_present_server_tasks(
        self, client, admin_token, make_server, make_account, captured_dispatch,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv1.id, srv2.id], login="ops", password="pw1",
        )
        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["tasks"]) == 2, body
        assert body["skipped"] == [], body
        server_ids = {t["server_id"] for t in body["tasks"]}
        assert server_ids == {srv1.id, srv2.id}
        for t in body["tasks"]:
            assert t["task_id"].startswith("tsk_")
            assert t["status"] == "dispatched"

    async def test_rotate_response_skips_decommissioned(
        self, client, admin_token, make_server, make_account, captured_dispatch, db,
    ):
        from src.core.constants import ServerStatus

        srv_ok = await make_server(department_id="dep_a")
        srv_dead = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv_ok.id, srv_dead.id], login="ops", password="pw1",
        )
        srv_dead.status = ServerStatus.DECOMMISSIONED
        await db.commit()

        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [t["server_id"] for t in body["tasks"]] == [srv_ok.id], body
        assert len(body["skipped"]) == 1, body
        assert body["skipped"][0]["server_id"] == srv_dead.id
        assert body["skipped"][0]["reason"] == "decommissioned"

    async def test_rotate_response_empty_when_no_present(
        self, client, admin_token, make_server, make_account, captured_dispatch, db,
    ):
        from src.models import ServerAccountServer

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        link = (
            await db.execute(
                select(ServerAccountServer).where(
                    ServerAccountServer.account_id == acc.id
                )
            )
        ).scalar_one()
        link.present_on_server = False
        await db.commit()

        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tasks"] == []
        assert body["skipped"] == []
