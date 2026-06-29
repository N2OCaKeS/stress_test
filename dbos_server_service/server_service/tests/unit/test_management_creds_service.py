"""Unit-тесты per-server управляющих кред (#3): генерация / шифрование / ротация.

Покрывает `services/management_creds.py`:
* `generate_management_material` отдаёт Ed25519-пару + strong-password;
* `ensure_management_credentials` генерит, шифрует (roundtrip через AAD),
  ставит pending_apply, sticky-переиспользует существующий материал;
* swap-attack между privkey/password AAD ловится InvalidTag;
* `rotate_management_credentials` переносит старый ciphertext в previous_*,
  пишет новый и ставит pending_apply.
"""
from __future__ import annotations

import pytest

from src.core.exceptions import AppException
from src.services import management_creds as mgmt
from src.services import secrets_service


class TestGenerateMaterial:
    def test_returns_keypair_and_password(self):
        private_pem, public_openssh, password = mgmt.generate_management_material()
        assert "PRIVATE KEY" in private_pem
        assert public_openssh.startswith("ssh-ed25519 ")
        # strong-password: 24 символа, буква+цифра+символ.
        assert len(password) >= 16
        assert any(c.isalpha() for c in password)
        assert any(c.isdigit() for c in password)


class TestEnsureManagementCredentials:
    async def test_generates_and_encrypts_roundtrip(self, db, make_server):
        srv = await make_server(department_id="dep_a")
        server, creds, generated = await mgmt.ensure_management_credentials(db, srv)

        assert generated is True
        assert server.mgmt_creds_pending_apply is True
        # public хранится открытым и совпадает с отданным воркеру.
        assert server.mgmt_ssh_public_key == creds["public_key"]
        # private/password зашифрованы; расшифровка по своему AAD совпадает.
        assert secrets_service.decrypt(
            server.mgmt_ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_mgmt_ssh_key(server.id),
        ) == creds["private_key"]
        assert secrets_service.decrypt(
            server.mgmt_password_encrypted,
            aad=secrets_service.aad_for_server_mgmt_password(server.id),
        ) == creds["password"]
        # plaintext в ciphertext не светится.
        assert creds["private_key"] not in server.mgmt_ssh_private_key_encrypted
        assert creds["password"] not in server.mgmt_password_encrypted

    async def test_sticky_reuse_on_repeat(self, db, make_server):
        srv = await make_server(department_id="dep_a")
        _, creds1, gen1 = await mgmt.ensure_management_credentials(db, srv)
        _, creds2, gen2 = await mgmt.ensure_management_credentials(db, srv)
        assert gen1 is True
        assert gen2 is False
        assert creds1 == creds2

    async def test_aad_swap_between_key_and_password_fails(self, db, make_server):
        srv = await make_server(department_id="dep_a")
        server, _, _ = await mgmt.ensure_management_credentials(db, srv)
        # privkey-ciphertext под password-AAD → InvalidTag (swap mitigation).
        with pytest.raises(AppException):
            secrets_service.decrypt(
                server.mgmt_ssh_private_key_encrypted,
                aad=secrets_service.aad_for_server_mgmt_password(server.id),
            )


class TestRotateManagementCredentials:
    async def test_moves_previous_and_sets_pending(self, db, make_server):
        srv = await make_server(department_id="dep_a")
        _, creds1, _ = await mgmt.ensure_management_credentials(db, srv)
        srv.mgmt_creds_pending_apply = False
        await db.flush()

        old_priv_ct = srv.mgmt_ssh_private_key_encrypted
        old_pwd_ct = srv.mgmt_password_encrypted
        new_creds = await mgmt.rotate_management_credentials(db, srv)

        assert srv.mgmt_creds_pending_apply is True
        assert new_creds["private_key"] != creds1["private_key"]
        assert new_creds["password"] != creds1["password"]
        # previous держит прежний ciphertext (рабочий на боксе) для анти-локаута.
        assert srv.previous_mgmt_ssh_private_key_encrypted == old_priv_ct
        assert srv.previous_mgmt_password_encrypted == old_pwd_ct
        # previous расшифровывается в старый материал тем же AAD.
        assert secrets_service.decrypt(
            srv.previous_mgmt_ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_mgmt_ssh_key(srv.id),
        ) == creds1["private_key"]
        # current расшифровывается в новый материал.
        assert secrets_service.decrypt(
            srv.mgmt_ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_mgmt_ssh_key(srv.id),
        ) == new_creds["private_key"]
