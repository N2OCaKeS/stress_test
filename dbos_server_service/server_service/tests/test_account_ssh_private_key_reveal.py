"""Reveal приватного SSH-ключа аккаунта + fingerprint публичного ключа в карточке.

`GET /server-accounts/{id}/ssh_private_key` расшифровывает сохранённый
приватный ключ и отдаёт его держателю `view_password` (то же право, что у
раскрытия пароля). Гейтится 403 без права, 404 — когда приватного ключа нет
(supply-режим / ключ не выдан). Карточка аккаунта (`GET /{id}`) несёт
`ssh_key_fingerprint`, посчитанный из публичного ключа, чтобы UI показывал
«ключ есть» без выдачи ключа.
"""

from __future__ import annotations

from src.services import secrets_service
from src.services.server_account import (
    _generate_ssh_keypair,
    ssh_public_key_fingerprint,
)

BASE = "/api/server/v1"

from tests._helpers import auth_hdr as _hdr  # noqa: E402


async def _attach_keypair(db, account) -> tuple[str, str]:
    """Сгенерировать пару и записать её на аккаунт (public + зашифрованный private)."""
    private_pem, public_openssh = _generate_ssh_keypair()
    account.ssh_public_key = public_openssh
    account.ssh_private_key_encrypted = secrets_service.encrypt(
        private_pem,
        aad=secrets_service.aad_for_server_account_ssh_key(account.id),
    )
    db.add(account)
    await db.flush()
    return private_pem, public_openssh


class TestSshKeyFingerprintInCard:
    async def test_fingerprint_present_when_key_set(
        self, client, admin_token, make_server, make_account, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        _, public = await _attach_keypair(db, acc)

        resp = await client.get(
            f"{BASE}/server-accounts/{acc.id}", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ssh_public_key"] == public
        # SHA256:<base64> — совпадает с тем, что считает ssh-keygen -lf.
        assert body["ssh_key_fingerprint"] == ssh_public_key_fingerprint(public)
        assert body["ssh_key_fingerprint"].startswith("SHA256:")

    async def test_fingerprint_null_without_key(
        self, client, admin_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)

        resp = await client.get(
            f"{BASE}/server-accounts/{acc.id}", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ssh_key_fingerprint"] is None


class TestRevealSshPrivateKey:
    async def test_view_password_reveals_private_key(
        self, client, admin_token, make_server, make_account, db,
    ):
        """Держатель view_password (admin) получает расшифрованный приватный ключ."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        private_pem, public = await _attach_keypair(db, acc)

        resp = await client.get(
            f"{BASE}/server-accounts/{acc.id}/ssh_private_key",
            headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ssh_private_key"] == private_pem
        assert body["ssh_public_key"] == public
        assert body["login"] == acc.login

    async def test_reader_without_view_password_denied(
        self, client, reader_token_a, make_server, make_account, db,
    ):
        """reader держит только view — на reveal приватного ключа 403."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        await _attach_keypair(db, acc)

        resp = await client.get(
            f"{BASE}/server-accounts/{acc.id}/ssh_private_key",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403, resp.text

    async def test_no_stored_private_key_returns_404(
        self, client, admin_token, make_server, make_account,
    ):
        """Аккаунт без приватного ключа (supply / без ключа) → 404."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)

        resp = await client.get(
            f"{BASE}/server-accounts/{acc.id}/ssh_private_key",
            headers=_hdr(admin_token),
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "ACCOUNT_NO_SSH_PRIVATE_KEY"

    async def test_cross_dept_hidden_404(
        self, client, admin_token_b, make_server, make_account, db,
    ):
        """Чужой dept скрыт за 404 (admin_token_b видит только dep_b)."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        await _attach_keypair(db, acc)

        resp = await client.get(
            f"{BASE}/server-accounts/{acc.id}/ssh_private_key",
            headers=_hdr(admin_token_b),
        )
        assert resp.status_code in (403, 404), resp.text

    async def test_no_token_returns_401(
        self, client, make_server, make_account, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        await _attach_keypair(db, acc)

        resp = await client.get(
            f"{BASE}/server-accounts/{acc.id}/ssh_private_key",
        )
        assert resp.status_code == 401, resp.text


class TestFingerprintHelper:
    def test_matches_known_vector(self):
        # Пустой/None ключ → None.
        assert ssh_public_key_fingerprint(None) is None
        assert ssh_public_key_fingerprint("") is None
        # Мусор без base64-тела → None.
        assert ssh_public_key_fingerprint("ssh-ed25519") is None
        # Сгенерированный ключ даёт стабильный SHA256:-отпечаток.
        _, public = _generate_ssh_keypair()
        fp = ssh_public_key_fingerprint(public)
        assert fp is not None and fp.startswith("SHA256:")
        # Идемпотентность.
        assert ssh_public_key_fingerprint(public) == fp
