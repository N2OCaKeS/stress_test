"""Provision-dispatch генерит пароль + Ed25519-ключ, кладёт зашифрованным в БД
и отдаёт plaintext воркеру.

Покрывает три кейса:
* NULL-аккаунт (discovered): генерим оба секрета, force_replace=True;
* существующий аккаунт с паролем и ключом: переиспользуем, force_replace=False;
* схема: ssh_public_key + ssh_private_key_encrypted колонки прочитаны после
  ensure_provision_credentials и читаются обратно через secrets_service.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.password_policy import is_strong
from src.models import ServerAccount
from src.services import secrets_service

BASE = "/api/server/v1/server-accounts"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def captured_dispatch(monkeypatch):
    calls: list[dict] = []

    async def fake_dispatch(*, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "target_resource_id": target_resource_id,
            "payload": payload,
        })
        return f"tsk_{task_kind.replace('.', '_')}_{len(calls)}"

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    return calls


class TestProvisionGeneratesCredentials:
    async def test_null_account_generates_pwd_and_keypair(
        self, client, operator_token_a, make_server, make_account, captured_dispatch, db,
    ):
        # Discovered-сценарий: аккаунт без пароля и без SSH-ключа.
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        assert acc.password_encrypted is None
        assert acc.ssh_public_key is None
        assert acc.ssh_private_key_encrypted is None

        # Discovered-аккаунт без пароля требует явного force_password=true —
        # без него endpoint отбивает 422 (см. `TestProvisionForcePasswordGuard`).
        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        # Все четыре поля кред в payload, force_replace=True.
        assert "password_plaintext" in payload
        assert "ssh_public_key" in payload
        assert "ssh_private_key_plaintext" in payload
        assert payload["force_replace"] is True
        # Пароль удовлетворяет усиленной политике.
        assert is_strong(payload["password_plaintext"])
        # Public-ключ — однострочный Ed25519.
        assert payload["ssh_public_key"].startswith("ssh-ed25519 ")
        assert "\n" not in payload["ssh_public_key"]
        # Private — OpenSSH PEM.
        assert "OPENSSH PRIVATE KEY" in payload["ssh_private_key_plaintext"]

        # БД-строка обновлена ciphertext'ами, plaintext совпадает с payload.
        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        assert refreshed.password_encrypted is not None
        assert refreshed.ssh_public_key == payload["ssh_public_key"]
        assert refreshed.ssh_private_key_encrypted is not None
        decrypted_pwd = secrets_service.decrypt(
            refreshed.password_encrypted,
            aad=secrets_service.aad_for_server_account_password(acc.id),
        )
        assert decrypted_pwd == payload["password_plaintext"]
        decrypted_pk = secrets_service.decrypt(
            refreshed.ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_account_ssh_key(acc.id),
        )
        assert decrypted_pk == payload["ssh_private_key_plaintext"]

    async def test_existing_credentials_reused_force_replace_false(
        self, client, operator_token_a, make_server, make_account, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        # Симулируем предыдущий provision: ssh-ключ уже есть, password — тоже.
        from src.services import server_account as account_svc
        await account_svc.ensure_provision_credentials(db, acc)
        await db.commit()
        await db.refresh(acc)
        stored_pubkey = acc.ssh_public_key
        stored_priv_cipher = acc.ssh_private_key_encrypted
        stored_pwd_cipher = acc.password_encrypted

        resp = await client.post(
            f"{BASE}/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert payload["force_replace"] is False
        assert payload["ssh_public_key"] == stored_pubkey
        # Plaintext-пароль декриптится из того же ciphertext'а.
        expected_pwd = secrets_service.decrypt(
            stored_pwd_cipher,
            aad=secrets_service.aad_for_server_account_password(acc.id),
        )
        assert payload["password_plaintext"] == expected_pwd

        # БД не перезаписана — те же ciphertext'ы.
        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        assert refreshed.password_encrypted == stored_pwd_cipher
        assert refreshed.ssh_private_key_encrypted == stored_priv_cipher
        assert refreshed.ssh_public_key == stored_pubkey

    async def test_update_on_host_does_not_inject_creds(
        self, client, operator_token_a, make_server, make_account, captured_dispatch,
    ):
        # `update_on_host` — это usermod, без выдачи кред. Воркеру не нужны
        # ни пароль, ни ssh-ключ в payload'е.
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="pw1")
        resp = await client.post(
            f"{BASE}/{acc.id}/update_on_host?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert "password_plaintext" not in payload
        assert "ssh_public_key" not in payload
        assert "ssh_private_key_plaintext" not in payload
        assert "force_replace" not in payload
