"""Тесты supply-режима SSH-ключа с опциональным приватным ключом.

`ssh_mode='supply'` исторически нёс только public. Теперь клиент может приложить
и приватный ключ (`ssh_private_key_b64`) — тогда server_service шифрует его и
кладёт рядом, и консоль может ходить под аккаунтом по этому ключу. Покрытие:

* create supply + private → приватный шифруется и сохраняется (round-trip через
  reveal);
* create supply без private → как раньше, хранится только public;
* приватный без supply / битый приватный / не-соответствующий public → 422;
* POST /{id}/ssh_key supply + private → приватный сохраняется, в ответе не эхуется.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy import select

from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/server-accounts"

pytestmark = pytest.mark.asyncio


def _keypair() -> tuple[str, str]:
    """Сгенерить Ed25519-пару, вернуть `(public_openssh, private_pem)`."""
    priv = ed25519.Ed25519PrivateKey.generate()
    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    return pub, pem


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client.dispatch_task — иначе ssh_key-fan-out упрётся в
    недоступный воркер (ServiceUnavailable) и его savepoint-rollback заэкспайрит
    объекты сессии."""
    calls: list[dict] = []
    by_key: dict[str, str] = {}

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            priority=0, return_hit=False):
        if idempotency_key is not None and idempotency_key in by_key:
            existing = by_key[idempotency_key]
            return (existing, True) if return_hit else existing
        calls.append({"task_kind": task_kind, "target_server_id": target_server_id})
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
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


class TestCreateSupplyWithPrivateKey:
    async def test_supply_with_private_key_stores_encrypted(
        self, client, admin_token, make_server, db,
    ):
        """supply + приватный → приватный шифруется и расшифровывается обратно."""
        from src.models import ServerAccount
        from src.services import secrets_service

        pub, pem = _keypair()
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={
                "server_ids": [srv.id],
                "login": "consoleuser",
                "ssh_mode": "supply",
                "ssh_public_key": pub,
                "ssh_private_key_b64": _b64(pem),
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Приватный ключ обратно в ответе create не эхуется (его прислал клиент).
        assert body["ssh_private_key"] is None
        assert body["ssh_public_key"] == pub
        # Сырого приватного материала в ответе нет.
        assert "PRIVATE KEY" not in resp.text

        acc = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == body["id"])
        )).scalar_one()
        assert acc.ssh_private_key_encrypted is not None
        decrypted = secrets_service.decrypt(
            acc.ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_account_ssh_key(acc.id),
        )
        assert decrypted == pem

    async def test_supply_private_key_revealable(
        self, client, admin_token, make_server, db,
    ):
        """Приложенный приватный ключ доступен через reveal-ручку (view_password)."""
        pub, pem = _keypair()
        srv = await make_server(department_id="dep_a")
        create = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={
                "server_ids": [srv.id],
                "login": "consoleuser2",
                "ssh_mode": "supply",
                "ssh_public_key": pub,
                "ssh_private_key_b64": _b64(pem),
            },
        )
        assert create.status_code == 201, create.text
        acc_id = create.json()["id"]
        reveal = await client.get(
            f"{BASE}/{acc_id}/ssh_private_key", headers=_hdr(admin_token),
        )
        assert reveal.status_code == 200, reveal.text
        assert reveal.json()["ssh_private_key"] == pem

    async def test_supply_without_private_key_stores_only_public(
        self, client, admin_token, make_server, db,
    ):
        """supply без приватного → хранится только public (поведение как раньше)."""
        from src.models import ServerAccount

        pub, _ = _keypair()
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={
                "server_ids": [srv.id],
                "login": "publiconly",
                "ssh_mode": "supply",
                "ssh_public_key": pub,
            },
        )
        assert resp.status_code == 201, resp.text
        acc = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == resp.json()["id"])
        )).scalar_one()
        assert acc.ssh_public_key == pub
        assert acc.ssh_private_key_encrypted is None


class TestCreateSupplyPrivateKeyValidation:
    async def test_private_key_without_supply_mode_422(
        self, client, admin_token, make_server,
    ):
        """Приватный ключ без ssh_mode='supply' → 422 (не диспатчится)."""
        _, pem = _keypair()
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={
                "server_ids": [srv.id],
                "login": "nomode",
                "ssh_private_key_b64": _b64(pem),
            },
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_private_key_with_generate_mode_422(
        self, client, admin_token, make_server,
    ):
        """Приватный ключ при ssh_mode='generate' → 422."""
        _, pem = _keypair()
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={
                "server_ids": [srv.id],
                "login": "genmode",
                "ssh_mode": "generate",
                "ssh_private_key_b64": _b64(pem),
            },
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_broken_private_key_422(
        self, client, admin_token, make_server,
    ):
        """supply + битый приватный ключ → 422."""
        pub, _ = _keypair()
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={
                "server_ids": [srv.id],
                "login": "broken",
                "ssh_mode": "supply",
                "ssh_public_key": pub,
                "ssh_private_key_b64": _b64("this is not a private key"),
            },
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_private_key_mismatch_422(
        self, client, admin_token, make_server,
    ):
        """supply + приватный из ДРУГОЙ пары (не матчит public) → 422."""
        pub, _ = _keypair()
        _, other_pem = _keypair()
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={
                "server_ids": [srv.id],
                "login": "mismatch",
                "ssh_mode": "supply",
                "ssh_public_key": pub,
                "ssh_private_key_b64": _b64(other_pem),
            },
        )
        assert_error(resp, 422, "VALIDATION_ERROR")


class TestSshKeyEndpointSupplyWithPrivateKey:
    async def test_set_ssh_key_supply_with_private_stores_encrypted(
        self, client, admin_token, make_server, make_account, db, captured_dispatch,
    ):
        """POST /{id}/ssh_key supply + приватный → шифруется, в ответе не эхуется."""
        from src.models import ServerAccount
        from src.services import secrets_service

        pub, pem = _keypair()
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="rotateme")
        resp = await client.post(
            f"{BASE}/{acc.id}/ssh_key",
            headers=_hdr(admin_token),
            json={
                "ssh_mode": "supply",
                "ssh_public_key": pub,
                "ssh_private_key_b64": _b64(pem),
            },
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["ssh_public_key"] == pub
        # Приватный, присланный клиентом, наружу не эхуется.
        assert body["ssh_private_key"] is None

        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        assert refreshed.ssh_private_key_encrypted is not None
        decrypted = secrets_service.decrypt(
            refreshed.ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_account_ssh_key(acc.id),
        )
        assert decrypted == pem
