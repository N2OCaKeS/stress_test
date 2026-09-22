"""Unit-тесты ensure_provision_credentials и reset_provision_credentials.

Проверяет edge-cases, не покрытые интеграционными тестами:
* sticky по каждому секрету: пароль сохраняется, если уже есть; SSH-ключ
  сохраняется, если уже есть; догенерируется только то, чего не хватает;
* reset_provision_credentials обнуляет все три поля;
* после reset → ensure генерирует новые данные и generated=True;
* idempotency: двойной вызов ensure без reset → generated=False второй раз.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.services import secrets_service
from src.services.server_account import ensure_provision_credentials, reset_provision_credentials
from tests._helpers import next_stand_number


@pytest.fixture
def make_bare_account(db):
    """Фабрика для создания ServerAccount вне транзакции теста (использует session-scope db).

    Возвращает async-callable: await _factory(password=..., ssh_public_key=..., ...).
    """
    from src.models import Server, ServerAccount, ServerAccountServer
    from src.utils.ids import _new_id
    import uuid

    async def _factory(
        *,
        password: str | None = None,
        ssh_public_key: str | None = None,
        ssh_private_key_encrypted: str | None = None,
    ) -> ServerAccount:
        suffix = uuid.uuid4().hex[:6]
        srv_id = _new_id("srv_")
        srv = Server(
            id=srv_id,
            hostname=f"bare-srv-{suffix}",
            ip_address=f"10.99.{int(suffix[:2], 16) % 256}.{int(suffix[2:4], 16) % 256}",
            ssh_port=22,
            department_id="dep_a",
            number=next_stand_number(),
        )
        db.add(srv)
        await db.flush()

        acc_id = _new_id("acc_")
        acc = ServerAccount(
            id=acc_id,
            department_id="dep_a",
            login=f"svc-{suffix}",
            password_encrypted=secrets_service.encrypt(
                password, aad=secrets_service.aad_for_server_account_password(acc_id),
            ) if password is not None else None,
            ssh_public_key=ssh_public_key,
            ssh_private_key_encrypted=ssh_private_key_encrypted,
            has_sudo=False,
            unix_groups=[],
        )
        db.add(acc)
        await db.flush()

        link = ServerAccountServer(
            id=_new_id("acs_"),
            account_id=acc_id,
            server_id=srv_id,
            login=acc.login,
        )
        db.add(link)
        await db.flush()
        await db.refresh(acc)
        return acc

    return _factory


class TestEnsureProvisionCredentials:
    async def test_fully_null_account_generates_both_secrets(
        self, db, make_bare_account,
    ):
        acc = await make_bare_account(password=None, ssh_public_key=None)
        updated, creds, generated = await ensure_provision_credentials(db, acc)
        assert generated is True
        assert creds["password"]
        assert creds["ssh_public_key"].startswith("ssh-ed25519 ")
        assert "OPENSSH PRIVATE KEY" in creds["ssh_private_key"]

    async def test_fully_populated_account_reuses_secrets(
        self, db, make_bare_account,
    ):
        # Сначала сидим пустой аккаунт и генерим оба секрета, чтобы получить
        # валидный ciphertext по обоим полям, потом дёргаем ensure повторно.
        acc = await make_bare_account(password=None, ssh_public_key=None)
        _, first_creds, generated1 = await ensure_provision_credentials(db, acc)
        assert generated1 is True

        # Второй вызов без reset → идемпотентный, всё прежнее.
        _, second_creds, generated2 = await ensure_provision_credentials(db, acc)
        assert generated2 is False
        assert second_creds["password"] == first_creds["password"]
        assert second_creds["ssh_public_key"] == first_creds["ssh_public_key"]
        assert second_creds["ssh_private_key"] == first_creds["ssh_private_key"]

    async def test_account_with_password_only_generates_ssh_keeps_password(
        self, db, make_bare_account,
    ):
        """Пароль есть, SSH — нет (legacy до миграции b9c2e7d4a8f1).
        Догенерируется только SSH-пара, пароль сохраняется."""
        acc = await make_bare_account(password="MyP@ss1234!!", ssh_public_key=None)
        _, creds, generated = await ensure_provision_credentials(db, acc)
        assert generated is True
        # Пароль не перезатёрт — отдан тот, что был.
        assert creds["password"] == "MyP@ss1234!!"
        # SSH-пара сгенерирована.
        assert creds["ssh_public_key"].startswith("ssh-ed25519 ")
        assert "OPENSSH PRIVATE KEY" in creds["ssh_private_key"]
        # И в БД действительно лежит ssh.
        assert acc.ssh_public_key is not None
        assert acc.ssh_private_key_encrypted is not None

    async def test_account_with_ssh_only_generates_password_keeps_ssh(
        self, db, make_bare_account,
    ):
        """SSH-пара есть, пароля нет. Догенерируется только пароль, SSH-пара
        сохраняется. Чтобы получить валидный ciphertext SSH-key, прогоняем
        ensure на чистом аккаунте, потом обнуляем только password_encrypted."""
        acc = await make_bare_account(password=None, ssh_public_key=None)
        _, first_creds, _ = await ensure_provision_credentials(db, acc)
        original_pubkey = first_creds["ssh_public_key"]
        original_private = first_creds["ssh_private_key"]

        # Имитируем legacy: пароля нет, SSH-пара лежит.
        acc.password_encrypted = None
        await db.flush()

        _, creds, generated = await ensure_provision_credentials(db, acc)
        assert generated is True
        # SSH не перезатёрт.
        assert creds["ssh_public_key"] == original_pubkey
        assert creds["ssh_private_key"] == original_private
        # Пароль сгенерирован (новый).
        assert creds["password"]
        assert creds["password"] != first_creds["password"]

    async def test_both_set_no_op(self, db, make_bare_account):
        """Оба секрета есть → ничего не генерируем, generated=False."""
        acc = await make_bare_account(password=None, ssh_public_key=None)
        _, first_creds, generated1 = await ensure_provision_credentials(db, acc)
        assert generated1 is True

        _, creds, generated2 = await ensure_provision_credentials(db, acc)
        assert generated2 is False
        assert creds["password"] == first_creds["password"]
        assert creds["ssh_public_key"] == first_creds["ssh_public_key"]
        assert creds["ssh_private_key"] == first_creds["ssh_private_key"]

    async def test_ssh_keypair_format(self, db, make_bare_account):
        acc = await make_bare_account()
        _, creds, _ = await ensure_provision_credentials(db, acc)
        # Public key — одна строка, без переносов.
        pub = creds["ssh_public_key"]
        assert "\n" not in pub.strip()
        assert pub.startswith("ssh-ed25519 ")
        # Private key — OpenSSH PEM.
        priv = creds["ssh_private_key"]
        assert "-----BEGIN OPENSSH PRIVATE KEY-----" in priv
        assert "-----END OPENSSH PRIVATE KEY-----" in priv


class TestResetProvisionCredentials:
    async def test_reset_clears_all_three_fields(self, db, make_bare_account):
        acc = await make_bare_account(password="MyP@ss1234!!")
        _, _, _ = await ensure_provision_credentials(db, acc)
        # Убеждаемся, что поля установлены.
        assert acc.password_encrypted is not None
        assert acc.ssh_public_key is not None
        assert acc.ssh_private_key_encrypted is not None

        updated = await reset_provision_credentials(db, acc)
        assert updated.password_encrypted is None
        assert updated.ssh_public_key is None
        assert updated.ssh_private_key_encrypted is None

    async def test_ensure_after_reset_generates_fresh_material(
        self, db, make_bare_account,
    ):
        acc = await make_bare_account(password="MyP@ss1234!!")
        _, first_creds, _ = await ensure_provision_credentials(db, acc)
        old_pubkey = first_creds["ssh_public_key"]

        await reset_provision_credentials(db, acc)
        _, second_creds, generated = await ensure_provision_credentials(db, acc)
        assert generated is True
        # Новая пара — другой ключ (с вероятностью 1 - 2^{-255} ≠ старый).
        assert second_creds["ssh_public_key"] != old_pubkey

    async def test_reset_twice_is_idempotent(self, db, make_bare_account):
        acc = await make_bare_account()
        await reset_provision_credentials(db, acc)
        updated = await reset_provision_credentials(db, acc)
        assert updated.password_encrypted is None
        assert updated.ssh_public_key is None
