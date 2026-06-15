"""Lazy re-encrypt на read-path: legacy ciphertext перешифровывается
активной версией ключа во время чтения, без блокировки ответа.

Покрывает:

* помечает `DecryptResult.needs_reencrypt=True` при чтении legacy v<N>$ под
  не-активной версией;
* `lazy_reencrypt_owner_column` CAS-UPDATE'ит owner-row и возвращает True;
* concurrent winner / расхождение `old_blob` → 0 rows affected, False, no raise;
* whitelist `_ALLOWED_LAZY_TARGETS` отсекает чужие таблицы/колонки;
* несуществующая id → 0 rows, False;
* read-path в `_reveal_account_password` подменяет ciphertext в БД на v<active>$
  но всё равно отдаёт правильный base64-plaintext.
"""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.core.keystore import get_keystore
from src.services import secrets_service


def _rebootstrap_keystore() -> None:
    """Пересобрать keystore из текущего env после монки-патча ключей.

    KeyStore кэшируется на процесс и держит durable-файл. Тесты, которые
    выставляют `SERVER_ENCRYPTION_KEY__v<N>` уже ПОСЛЕ того, как keystore
    засеялся (например, после `make_account`, который шифрует), обязаны
    пересобрать его — иначе legacy-версия в keystore не появится и decrypt
    упадёт ENCRYPTION_KEY_MISSING. Сносим файл и чистим lru_cache.
    """
    ks_path = os.environ.get("KEYSTORE_PATH")
    if ks_path and os.path.exists(ks_path):
        os.remove(ks_path)
    get_keystore.cache_clear()  # type: ignore[attr-defined]


def _craft_legacy_token(plaintext: str, version: int, *, aad: bytes) -> str:
    """Собрать legacy `v<version>$...` ciphertext напрямую, используя
    `SERVER_ENCRYPTION_KEY__v<version>` env (если выставлен) или активный
    мастер-key (для тестов с тем же материалом ключа, но другим version-tag'ом).

    Тесты подменяют активную версию через monkeypatch, поэтому материал
    обоих ключей — один и тот же `SERVER_ENCRYPTION_KEY`. Это легитимный
    сценарий: ротация-без-смены-материала встречается на рестарте сервиса
    с bump'ом version.
    """
    env_name = f"SERVER_ENCRYPTION_KEY__v{version}"
    material = os.environ.get(env_name) or os.environ["SERVER_ENCRYPTION_KEY"]
    key = secrets_service._derive_key(material, version)
    nonce = os.urandom(secrets_service._NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return (
        f"v{version}${secrets_service._b64e(nonce)}${secrets_service._b64e(ciphertext)}"
    )


class TestDecryptResultMetadata:
    """`DecryptResult.needs_reencrypt` корректно тегирует legacy-ciphertext'ы."""

    def test_active_version_does_not_need_reencrypt(self):
        token = secrets_service.encrypt("payload", aad=b"aad-1")
        result = secrets_service.decrypt_with_meta(token, aad=b"aad-1")
        assert result.plaintext == "payload"
        assert result.needs_reencrypt is False
        # source_version совпадает с активной из settings.
        from src.core.config import get_settings
        assert result.source_version == get_settings().server_encryption_key_version

    def test_legacy_version_needs_reencrypt(self, monkeypatch):
        from src.core.config import get_settings

        settings = get_settings()
        active = settings.server_encryption_key_version
        # Создаём токен под N=active+1, потом притворяемся, что мы в проде с
        # active=active. Используем материал того же ключа через env.
        future_version = active + 1
        monkeypatch.setenv(
            f"SERVER_ENCRYPTION_KEY__v{future_version}",
            settings.server_encryption_key,
        )
        token = _craft_legacy_token("payload", future_version, aad=b"aad-2")
        result = secrets_service.decrypt_with_meta(token, aad=b"aad-2")
        assert result.plaintext == "payload"
        # `source_version != active` → needs_reencrypt=True.
        assert result.source_version == future_version
        assert result.needs_reencrypt is True

    def test_decrypt_wrapper_still_returns_string(self):
        token = secrets_service.encrypt("legacy-callsite", aad=b"aad-3")
        plain = secrets_service.decrypt(token, aad=b"aad-3")
        assert plain == "legacy-callsite"


class TestLazyReencryptUpdate:
    """`lazy_reencrypt_owner_column` CAS-UPDATE'ит owner-row."""

    @pytest.mark.asyncio
    async def test_updates_row_to_active_version(
        self, db, make_server, make_account, monkeypatch,
    ):
        from src.core.config import get_settings

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="x")
        # Перепишем токен с префиксом v<active+1>$… — формат корректен, но
        # это уже legacy (для перешифровки нам нужна active != source_version).
        original = acc.password_encrypted
        _, nonce_b64, ct_b64 = original.split("$", 2)
        settings = get_settings()
        legacy_version = settings.server_encryption_key_version - 1 or 1
        legacy_blob = f"v{legacy_version}${nonce_b64}${ct_b64}"
        acc.password_encrypted = legacy_blob
        await db.flush()
        await db.commit()

        # plaintext и aad — те, что lazy_reencrypt получает от call-site'а.
        aad = secrets_service.aad_for_server_account_password(acc.id)
        ok = await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="server_accounts",
            column="password_encrypted",
            row_id=acc.id,
            old_blob=legacy_blob,
            plaintext="x",
            aad=aad,
        )
        assert ok is True

        await db.refresh(acc)
        assert acc.password_encrypted is not None
        assert acc.password_encrypted.startswith(
            f"v{settings.server_encryption_key_version}$"
        )

    @pytest.mark.asyncio
    async def test_cas_misses_when_old_blob_stale(
        self, db, make_server, make_account,
    ):
        """Если параллельная ротация ушла раньше — lazy CAS-UPDATE даёт 0 rows."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="x")
        current_blob = acc.password_encrypted
        stale_blob = "v1$AAAA$BBBB"  # никогда не было в БД

        aad = secrets_service.aad_for_server_account_password(acc.id)
        ok = await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="server_accounts",
            column="password_encrypted",
            row_id=acc.id,
            old_blob=stale_blob,
            plaintext="x",
            aad=aad,
        )
        assert ok is False

        # Row не перетёрт — `password_encrypted` остался тем, что был.
        await db.refresh(acc)
        assert acc.password_encrypted == current_blob

    @pytest.mark.asyncio
    async def test_missing_row_returns_false(self, db):
        ok = await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="server_accounts",
            column="password_encrypted",
            row_id="acc_does_not_exist",
            old_blob="v1$AAAA$BBBB",
            plaintext="x",
            aad=b"aad-z",
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_non_whitelisted_target_refused(self, db, caplog):
        """Любые `table`/`column` вне whitelist'а → False + WARNING-лог."""
        caplog.set_level("WARNING")
        ok = await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="users",
            column="password_encrypted",
            row_id="any",
            old_blob="v1$AAAA$BBBB",
            plaintext="x",
            aad=b"aad-z",
        )
        assert ok is False
        assert any(
            "lazy_reencrypt: refusing to UPDATE non-whitelisted target" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_ipmi_controller_target_allowed(
        self, db, make_server, make_ipmi,
    ):
        """`ipmi_controllers.password_encrypted` тоже в whitelist'е."""
        from src.core.config import get_settings

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id, password="ipmi-pwd")
        original = ctrl.password_encrypted
        _, nonce_b64, ct_b64 = original.split("$", 2)
        settings = get_settings()
        legacy_version = settings.server_encryption_key_version - 1 or 1
        legacy_blob = f"v{legacy_version}${nonce_b64}${ct_b64}"
        ctrl.password_encrypted = legacy_blob
        await db.flush()
        await db.commit()

        aad = secrets_service.aad_for_ipmi_credential(ctrl.id)
        ok = await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="ipmi_controllers",
            column="password_encrypted",
            row_id=ctrl.id,
            old_blob=legacy_blob,
            plaintext="ipmi-pwd",
            aad=aad,
        )
        assert ok is True
        await db.refresh(ctrl)
        assert ctrl.password_encrypted.startswith(
            f"v{settings.server_encryption_key_version}$"
        )


class TestLazyReencryptRace:
    """Конкурентные lazy-вызовы на одной row → не больше одного успешного UPDATE'а.

    CAS на `WHERE column = :old` гарантирует: после первого выигрышного UPDATE'а
    `old` уже не совпадает с актуальным значением, второй UPDATE даёт 0 rows.
    """

    @pytest.mark.asyncio
    async def test_concurrent_calls_only_one_winner(
        self, db, make_server, make_account,
    ):
        from src.core.config import get_settings

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="x")
        original = acc.password_encrypted
        _, nonce_b64, ct_b64 = original.split("$", 2)
        settings = get_settings()
        legacy_version = settings.server_encryption_key_version - 1 or 1
        legacy_blob = f"v{legacy_version}${nonce_b64}${ct_b64}"
        acc.password_encrypted = legacy_blob
        await db.flush()
        await db.commit()

        aad = secrets_service.aad_for_server_account_password(acc.id)

        # Два одновременных вызова с одним и тем же `old_blob`. Один из них
        # выиграет CAS, второй увидит уже-изменённый ciphertext.
        async def call():
            return await secrets_service.lazy_reencrypt_owner_column(
                db,
                table="server_accounts",
                column="password_encrypted",
                row_id=acc.id,
                old_blob=legacy_blob,
                plaintext="x",
                aad=aad,
            )

        # Sequential here — async with same session нельзя гонять параллельно,
        # но семантика CAS-проверки `column = :old` всё равно проявится: после
        # первого UPDATE'а второй найдёт другой blob.
        first = await call()
        second = await call()
        # Один и только один UPDATE прошёл (rows affected == 1).
        assert (first, second) == (True, False) or (first, second) == (False, True)


class TestRevealLazyReencryptIntegration:
    """`_reveal_account_password` после чтения legacy-ciphertext'а сам прописывает
    новый ciphertext активной версией. Read-path не блокируется.
    """

    @pytest.mark.asyncio
    async def test_reveal_rewrites_legacy_to_active(
        self, make_server, make_account, db, monkeypatch,
    ):
        from src.core.config import get_settings
        from src.services import server_account as sa_service
        from src.services import audit_context

        settings = get_settings()
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="reveal-pwd")
        active = settings.server_encryption_key_version

        # legacy = active+1 (фейк-«будущая» версия) с тем же материалом ключа,
        # выставленным через `SERVER_ENCRYPTION_KEY__v<N>` env. Это
        # эквивалент сценария: запустились с active=N+1, на диске остались
        # row'ы под N — но фактически чтобы decrypt прошёл, материал должен
        # быть в env'е.
        legacy_version = active + 1
        monkeypatch.setenv(
            f"SERVER_ENCRYPTION_KEY__v{legacy_version}",
            settings.server_encryption_key,
        )
        # `make_account` уже засеял keystore (через encrypt) до того, как мы
        # выставили legacy-версию в env — пересобираем, чтобы reveal-decrypt
        # нашёл материал v{legacy_version}.
        _rebootstrap_keystore()
        aad = secrets_service.aad_for_server_account_password(acc.id)
        legacy_blob = _craft_legacy_token("reveal-pwd", legacy_version, aad=aad)
        acc.password_encrypted = legacy_blob
        await db.flush()
        await db.commit()

        # Прямой вызов _reveal_account_password обходит endpoint-permission'ы
        # (нам важна именно lazy-логика, а не reveal-аудит).
        audit_context.set_context(
            audit_context.AuditContext(
                actor_id="u_actor",
                username="actor",
                department_id="dep_a",
                request_id="r1",
                ip_address="127.0.0.1",
            )
        )
        revealed = await sa_service._reveal_account_password(db, acc)
        assert revealed is not None
        # base64 расшифрован обратно — тот же plaintext.
        assert base64.b64decode(revealed).decode() == "reveal-pwd"

        # Row в БД должна быть переписана активной версией.
        # `_reveal_*` не expire'ит ORM-объект, надо тянуть свежее значение.
        from sqlalchemy import select
        from src.models import ServerAccount
        fresh = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        await db.refresh(fresh)
        assert fresh.password_encrypted.startswith(f"v{active}$")


class TestLazyReencryptNonblocking:
    """При сбое UPDATE'а (фейк) read-path не должен поднимать исключение."""

    @pytest.mark.asyncio
    async def test_db_error_returns_false_does_not_raise(self, db):
        """Подсовываем session-stub с execute, который падает RuntimeError —
        lazy_reencrypt ловит исключение и возвращает False.
        """

        class _BoomSession:
            async def execute(self, *args, **kwargs):
                raise RuntimeError("simulated db lock")

            async def commit(self):  # pragma: no cover — до сюда не дойдём
                pass

            async def rollback(self):
                # Симулируем чистый rollback: не падаем, чтобы lazy_reencrypt
                # мог записать WARNING и выйти спокойно.
                pass

        ok = await secrets_service.lazy_reencrypt_owner_column(
            _BoomSession(),  # type: ignore[arg-type]
            table="server_accounts",
            column="password_encrypted",
            row_id="anything",
            old_blob="v1$AAAA$BBBB",
            plaintext="x",
            aad=b"aad-y",
        )
        assert ok is False
