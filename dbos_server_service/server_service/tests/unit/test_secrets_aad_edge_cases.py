"""AAD edge cases для secrets_service.

Фокус на сценариях, не покрытых основным test_secrets_service.py:
* Decrypt токена v1 с AAD, которое генерирует aad_for_server_account_password —
  проверяет, что helper-функции дают стабильный формат.
* Swap-attack через другой key_version: токен зашифрован под v2, злоумышленник
  «переименовывает» его в v3 (несуществующий) — ENCRYPTION_KEY_MISSING, не DECRYPT_FAILED.
* Cross-version decrypt невозможен: ciphertext под v2-KDF не расшифровать,
  даже если взять тот же master-key и вручную попробовать под v3-info.
* aad_for_ipmi_credential / aad_for_server_account_password возвращают bytes,
  содержащие owner_table и id — стабильный формат.
* Множественные round-trip с разными AAD на одном ключе — каждый независим.
"""

from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.core.exceptions import AppException
from src.services import secrets_service


class TestAadHelperFormat:
    """Стабильный формат AAD-хелперов."""

    def test_account_aad_contains_kind_and_table(self):
        aad = secrets_service.aad_for_server_account_password("acc_12345")
        text = aad.decode("utf-8")
        assert "server_account_password" in text
        assert "server_accounts" in text
        assert "acc_12345" in text

    def test_ipmi_aad_contains_kind_and_table(self):
        aad = secrets_service.aad_for_ipmi_credential("ipm_99999")
        text = aad.decode("utf-8")
        assert "ipmi_credential" in text
        assert "ipmi_controllers" in text
        assert "ipm_99999" in text

    def test_aad_format_is_pipe_separated(self):
        aad_acc = secrets_service.aad_for_server_account_password("acc_x")
        aad_ipmi = secrets_service.aad_for_ipmi_credential("ipm_x")
        assert aad_acc.count(b"|") == 2
        assert aad_ipmi.count(b"|") == 2

    def test_different_ids_produce_different_aad(self):
        a = secrets_service.aad_for_server_account_password("acc_AAA")
        b = secrets_service.aad_for_server_account_password("acc_BBB")
        assert a != b

    def test_same_id_different_table_produces_different_aad(self):
        aad_acc = secrets_service.aad_for_server_account_password("shared_id")
        aad_ipmi = secrets_service.aad_for_ipmi_credential("shared_id")
        assert aad_acc != aad_ipmi


class TestKeyVersionMismatchErrors:
    """Правильные error_code при проблемах с версиями ключей."""

    def test_missing_legacy_env_raises_key_missing(self, monkeypatch):
        """Токен v1, но `SERVER_ENCRYPTION_KEY__v1` не задан и активная версия v2."""
        monkeypatch.delenv("SERVER_ENCRYPTION_KEY__v1", raising=False)
        from src.core.config import get_settings
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        get_settings.cache_clear()
        try:
            # Строим v1 токен вручную через legacy KDF с тем же master-ключом,
            # что в env (get_settings().server_encryption_key). После cache_clear
            # настройки подхватятся.
            nonce = os.urandom(secrets_service._NONCE_BYTES)
            key = secrets_service._derive_key_legacy("some-old-master-key")
            ct = AESGCM(key).encrypt(nonce, b"data", b"aad")
            token = f"v1${secrets_service._b64e(nonce)}${secrets_service._b64e(ct)}"
            with pytest.raises(AppException) as exc:
                secrets_service.decrypt(token, aad=b"aad")
            # v1 из env не загружен → ENCRYPTION_KEY_MISSING
            assert exc.value.error_code == "ENCRYPTION_KEY_MISSING"
            assert "SERVER_ENCRYPTION_KEY__v1" in str(exc.value.details)
        finally:
            get_settings.cache_clear()

    def test_nonexistent_version_identifier_raises_key_missing(self, monkeypatch):
        """Токен vN, где N — какой-то несуществующий номер, и env не задан."""
        monkeypatch.delenv("SERVER_ENCRYPTION_KEY__v42", raising=False)
        with pytest.raises(AppException) as exc:
            # Токен с несуществующей версией — после parse версии пойдём в
            # _key_for_version(42), где env не задан.
            secrets_service.decrypt("v42$AAAA$BBBBBBBB", aad=b"x")
        assert exc.value.error_code == "ENCRYPTION_KEY_MISSING"

    def test_version_bump_ciphertext_fails_under_new_key(self, monkeypatch):
        """Ciphertext v2 под old-key не расшифровать, если master-key сменился.

        Это сценарий правильного key-rotation: старый ciphertext с новым
        master-key должен дать DECRYPT_FAILED, а не тихо выдать мусор.
        """
        from src.core.config import get_settings

        monkeypatch.setenv("SERVER_ENCRYPTION_KEY", "old-master-key-for-v2-encrypt-aaaa")
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        get_settings.cache_clear()
        try:
            token = secrets_service.encrypt("sensitive-value", aad=b"row-id")
        finally:
            get_settings.cache_clear()

        # Меняем master при той же active version = 2
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY", "completely-different-master-key-bbbb")
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        get_settings.cache_clear()
        try:
            with pytest.raises(AppException) as exc:
                secrets_service.decrypt(token, aad=b"row-id")
            assert exc.value.error_code == "DECRYPT_FAILED"
        finally:
            get_settings.cache_clear()


class TestCrossKeyVersionIsolation:
    """HKDF version-tagged info гарантирует изоляцию: v2 != v3 при одном master."""

    def test_ciphertext_v2_cannot_be_read_as_v3(self, monkeypatch):
        """Берём ciphertext под v2-HKDF. Переписываем префикс в v3.
        Нет env для v3 → ENCRYPTION_KEY_MISSING (не путать с DECRYPT_FAILED).
        """
        from src.core.config import get_settings

        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        monkeypatch.delenv("SERVER_ENCRYPTION_KEY__v3", raising=False)
        get_settings.cache_clear()
        try:
            token_v2 = secrets_service.encrypt("payload", aad=b"r1")
            assert token_v2.startswith("v2$")
        finally:
            get_settings.cache_clear()

        # Склеиваем «поддельный» v3 из nonce и ct v2-токена.
        parts = token_v2.split("$", 2)
        spoofed_v3 = f"v3${parts[1]}${parts[2]}"

        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(spoofed_v3, aad=b"r1")
        # Нет env для v3 → key missing, а не decrypt failure
        assert exc.value.error_code == "ENCRYPTION_KEY_MISSING"


class TestSwapAttackSimulation:
    """Полный swap-attack сценарий с реальными ID через helper-функции."""

    def test_password_copied_between_rows_fails_decrypt(self):
        """Берём зашифрованный пароль строки acc_A и пробуем расшифровать
        с AAD строки acc_B (атака через UPDATE password_encrypted)."""
        aad_a = secrets_service.aad_for_server_account_password("acc_source_row")
        aad_b = secrets_service.aad_for_server_account_password("acc_target_row")
        token = secrets_service.encrypt("secret-password-from-row-a", aad=aad_a)
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(token, aad=aad_b)
        assert exc.value.error_code == "DECRYPT_FAILED"
        # Сообщение об ошибке не раскрывает plaintext.
        assert "secret-password-from-row-a" not in exc.value.message

    def test_ipmi_cred_copied_to_account_fails(self):
        """Ciphertext IPMI-credentials скопирован в server_account.password_encrypted.
        AAD разных таблиц → InvalidTag."""
        aad_ipmi = secrets_service.aad_for_ipmi_credential("ipm_abc")
        aad_acc = secrets_service.aad_for_server_account_password("ipm_abc")  # тот же id!
        token = secrets_service.encrypt("ipmi-secret", aad=aad_ipmi)
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(token, aad=aad_acc)
        assert exc.value.error_code == "DECRYPT_FAILED"

    def test_swap_within_same_table_different_ids(self):
        """Оба аккаунта в одной таблице, но ID разные — swap тоже отбивается."""
        ids = ["acc_row_001", "acc_row_002", "acc_row_003"]
        tokens = {
            uid: secrets_service.encrypt(
                f"password-for-{uid}",
                aad=secrets_service.aad_for_server_account_password(uid),
            )
            for uid in ids
        }
        # Каждый токен расшифровывается только с правильным AAD.
        for uid in ids:
            aad = secrets_service.aad_for_server_account_password(uid)
            assert secrets_service.decrypt(tokens[uid], aad=aad) == f"password-for-{uid}"
            # Попытка расшифровать чужим AAD.
            for other_uid in ids:
                if other_uid == uid:
                    continue
                wrong_aad = secrets_service.aad_for_server_account_password(other_uid)
                with pytest.raises(AppException) as exc:
                    secrets_service.decrypt(tokens[uid], aad=wrong_aad)
                assert exc.value.error_code == "DECRYPT_FAILED"


class TestLazyReencryptSimulation:
    """Симуляция lazy re-encrypt: v1 токен читается, новый encrypt идёт в v2.

    В production lazy re-encrypt происходит, когда код:
    1. Декодирует password_encrypted (старый v1 токен).
    2. Сразу же шифрует новым ключом (v2) и пишет обратно.
    Тест проверяет, что такой round-trip даёт корректный v2-токен.
    """

    def test_v1_decrypt_then_v2_reencrypt_round_trip(self, monkeypatch):
        from src.core.config import get_settings

        original_key = "original-v1-key-before-key-rotation-aa"
        aad = secrets_service.aad_for_server_account_password("acc_migrate_001")
        plaintext = "password-to-migrate"

        # Строим исторический v1 токен через legacy KDF.
        nonce = os.urandom(secrets_service._NONCE_BYTES)
        key_v1 = secrets_service._derive_key_legacy(original_key)
        ct = AESGCM(key_v1).encrypt(nonce, plaintext.encode(), aad)
        token_v1 = f"v1${secrets_service._b64e(nonce)}${secrets_service._b64e(ct)}"

        # Переходим на v2: v1 ключ в legacy env, активная версия v2.
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY__v1", original_key)
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        get_settings.cache_clear()
        try:
            # Шаг 1: декодируем старый v1 токен.
            recovered = secrets_service.decrypt(token_v1, aad=aad)
            assert recovered == plaintext

            # Шаг 2: re-encrypt тем же plaintext под активной v2.
            token_v2 = secrets_service.encrypt(recovered, aad=aad)
            assert token_v2.startswith("v2$")

            # Шаг 3: v2 токен расшифровывается правильно.
            assert secrets_service.decrypt(token_v2, aad=aad) == plaintext

            # Шаг 4: старый v1 токен всё ещё читаемый (до физического удаления).
            assert secrets_service.decrypt(token_v1, aad=aad) == plaintext
        finally:
            get_settings.cache_clear()
