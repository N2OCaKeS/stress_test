"""Unit-тесты симметричного шифрования секретов.

Покрытие критичных свойств:
* Round-trip encrypt → decrypt сохраняет plaintext.
* Wire-формат `v<n>$<nonce_b64u>$<ct_b64u>` строго соблюдается.
* Каждый encrypt использует свежий nonce (AES-GCM безопасность).
* Все malformed-варианты дают типизированный AppException, **никогда** не Python-traceback.
* Plaintext НЕ утекает ни в `exc.message`, ни в `exc.details` при ошибке.
* Версионирование ключа: чтение старой версии берётся из `SERVER_ENCRYPTION_KEY__vN`.
* KDF dispatch: v1 → legacy SHA-256, v2+ → HKDF-SHA256.
* `SERVER_ENCRYPTION_KEY` валидируется на `min_length=32`.
"""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError

from src.core.exceptions import AppException
from src.services import secrets_service


# Фиксированный AAD для тестов, не проверяющих swap-attack семантику —
# нам важно только round-trip / KDF / валидация, а не привязка к строке.
_TEST_AAD = b"test-aad-fixed"


def _craft_v1_token(plaintext: str, master_key: str, *, aad: bytes) -> str:
    """Собрать legacy v1-ciphertext напрямую, минуя `encrypt()`.

    Активная запись под v1 запрещена (`server_encryption_key_version` ограничен
    `ge=2`), но исторические v1-токены обязаны оставаться расшифровываемыми.
    Тесты back-compat'а строят такой токен через legacy-KDF + сырой AES-GCM,
    как если бы его написала старая версия сервиса.
    """
    key = secrets_service._derive_key_legacy(master_key)
    nonce = os.urandom(secrets_service._NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return f"v1${secrets_service._b64e(nonce)}${secrets_service._b64e(ciphertext)}"


# ── Round-trip ────────────────────────────────────────────────────────────────

class TestRoundTrip:
    @pytest.mark.parametrize("plaintext", [
        "simple",
        "with space and !@#$%^&*() symbols",
        "юникод и кириллица",
        "x",                       # минимально короткий
        "a" * 4096,                # большой
        "\n\r\t\\\"'",             # escape-символы
        "🔐 emoji + binary-ish",
    ])
    def test_round_trip_preserves_plaintext(self, plaintext: str):
        token = secrets_service.encrypt(plaintext, aad=_TEST_AAD)
        assert secrets_service.decrypt(token, aad=_TEST_AAD) == plaintext

    def test_empty_string_roundtrips(self):
        token = secrets_service.encrypt("", aad=_TEST_AAD)
        assert secrets_service.decrypt(token, aad=_TEST_AAD) == ""

    def test_encrypt_returns_token_with_expected_shape(self):
        token = secrets_service.encrypt("payload", aad=_TEST_AAD)
        version_part, nonce_b64, ct_b64 = token.split("$", 2)
        assert version_part.startswith("v")
        int(version_part[1:])                       # parses as int
        # base64url без паддинга — _b64d должен принять и nonce, и ciphertext
        assert secrets_service._b64d(nonce_b64), "nonce must decode"
        assert secrets_service._b64d(ct_b64), "ciphertext must decode"

    def test_each_encrypt_uses_fresh_nonce(self):
        a, b = (
            secrets_service.encrypt("same-plaintext", aad=_TEST_AAD),
            secrets_service.encrypt("same-plaintext", aad=_TEST_AAD),
        )
        assert a != b, "AES-GCM requires unique nonces; two encrypts of the same plaintext must differ"
        nonce_a = a.split("$", 2)[1]
        nonce_b = b.split("$", 2)[1]
        assert nonce_a != nonce_b


# ── Encrypt input validation ─────────────────────────────────────────────────

class TestEncryptInput:
    def test_none_raises_typed_error(self):
        with pytest.raises(AppException) as exc:
            secrets_service.encrypt(None, aad=_TEST_AAD)  # type: ignore[arg-type]
        assert exc.value.error_code == "ENCRYPT_INPUT_INVALID"


# ── Decrypt input validation ─────────────────────────────────────────────────

class TestDecryptInputValidation:
    @pytest.mark.parametrize("bad_token", [
        "",                                      # пусто
        "not-a-token",                           # без префикса v
        "1$abc$def",                             # префикс не v
    ])
    def test_no_version_prefix_raises(self, bad_token: str):
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(bad_token, aad=_TEST_AAD)
        assert exc.value.error_code == "ENCRYPTED_TOKEN_INVALID"

    def test_none_token_raises(self):
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(None, aad=_TEST_AAD)  # type: ignore[arg-type]
        assert exc.value.error_code == "ENCRYPTED_TOKEN_INVALID"

    @pytest.mark.parametrize("malformed", [
        "v1",                       # ничего кроме версии
        "v1$onlynonce",             # нет ciphertext-сегмента
        "vXYZ$AAAA$BBBB",           # не-числовая версия
    ])
    def test_malformed_token_returns_malformed_code(self, malformed: str):
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(malformed, aad=_TEST_AAD)
        assert exc.value.error_code == "ENCRYPTED_TOKEN_MALFORMED"

    def test_tampered_ciphertext_yields_decrypt_failed(self):
        token = secrets_service.encrypt("hello", aad=_TEST_AAD)
        version, nonce, ct = token.split("$", 2)
        ct_bytes = secrets_service._b64d(ct)
        flipped = bytes([ct_bytes[0] ^ 0x01]) + ct_bytes[1:]
        tampered = f"{version}${nonce}${secrets_service._b64e(flipped)}"
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(tampered, aad=_TEST_AAD)
        assert exc.value.error_code == "DECRYPT_FAILED"

    def test_bad_base64_yields_decrypt_failed(self):
        # Версия и формат правильные, но base64 для nonce невалидный — крипто-движок
        # упадёт с InvalidTag/ValueError, что попадает в общий except → DECRYPT_FAILED.
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt("v2$!!!$@@@", aad=_TEST_AAD)
        assert exc.value.error_code == "DECRYPT_FAILED"


# ── Plaintext-leak protection ────────────────────────────────────────────────

class TestNoPlaintextLeak:
    """Если шифрование/расшифровка падают, plaintext НЕ должен попасть в сообщение
    или details исключения — оттуда оно поедет в audit-логи."""

    def test_encrypt_none_message_does_not_contain_input(self):
        try:
            secrets_service.encrypt(None, aad=_TEST_AAD)  # type: ignore[arg-type]
        except AppException as exc:
            assert "None" not in (exc.details or {}).values().__str__(), \
                "AppException.details must not echo the raw input"

    def test_decrypt_failed_message_does_not_contain_ciphertext(self):
        token = secrets_service.encrypt("secret-plaintext-value", aad=_TEST_AAD)
        version, nonce, ct = token.split("$", 2)
        ct_bytes = secrets_service._b64d(ct)
        flipped = bytes([ct_bytes[0] ^ 0x01]) + ct_bytes[1:]
        tampered = f"{version}${nonce}${secrets_service._b64e(flipped)}"
        try:
            secrets_service.decrypt(tampered, aad=_TEST_AAD)
        except AppException as exc:
            payload = exc.message + str(exc.details or {})
            assert "secret-plaintext-value" not in payload
            # Cryptography раскрывает только класс ошибки, не сами байты.
            assert "InvalidTag" in exc.message or "type" in exc.message.lower()


# ── Key versioning ───────────────────────────────────────────────────────────

class TestKeyVersioning:
    def test_unknown_version_raises_key_missing(self, monkeypatch):
        # Удалим возможные fallback-env'ы, чтобы версия v99 заведомо отсутствовала.
        monkeypatch.delenv("SERVER_ENCRYPTION_KEY__v99", raising=False)
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt("v99$AAAA$BBBB", aad=_TEST_AAD)
        assert exc.value.error_code == "ENCRYPTION_KEY_MISSING"
        # Имя env-переменной должно фигурировать в details — для облегчения операций.
        assert exc.value.details.get("env") == "SERVER_ENCRYPTION_KEY__v99"

    def test_legacy_key_used_when_active_version_bumped(self, monkeypatch):
        """Симуляция ротации: исторический v1-токен всё ещё дешифруется через
        legacy env после того, как активная версия стала v2."""
        from src.core.config import get_settings

        # Исторический v1-токен, написанный старым ключом (новая запись под v1
        # запрещена, поэтому собираем токен напрямую через legacy-KDF).
        original_v1_key = "legacy-v1-master-key-padded-to-32-chars"
        token = _craft_v1_token("legacy-payload", original_v1_key, aad=_TEST_AAD)

        # Активная версия — v2 (по умолчанию), новый активный ключ.
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY", "fresh-v2-master-key-padded-to-32-chars")
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        # Прежний v1 теперь должен читаться из legacy env.
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY__v1", original_v1_key)
        get_settings.cache_clear()  # type: ignore[attr-defined]

        try:
            assert secrets_service.decrypt(token, aad=_TEST_AAD) == "legacy-payload"
        finally:
            get_settings.cache_clear()  # type: ignore[attr-defined]


# ── Helpers (base64url without padding) ───────────────────────────────────────

class TestBase64Helpers:
    @pytest.mark.parametrize("raw", [b"", b"\x00", b"abc", b"\xff" * 32, bytes(range(256))])
    def test_b64_round_trip(self, raw: bytes):
        encoded = secrets_service._b64e(raw)
        assert "=" not in encoded, "base64url here is unpadded by contract"
        assert secrets_service._b64d(encoded) == raw

    def test_b64d_accepts_canonical_padded_input(self):
        # стандартный base64url с паддингом тоже должен декодироваться
        padded = base64.urlsafe_b64encode(b"hello").decode("ascii")
        assert secrets_service._b64d(padded) == b"hello"


# ── KDF dispatch (v1 = legacy SHA-256, v2+ = HKDF) ───────────────────────────


class TestKDFDispatch:
    """Wire-format version doubles as KDF marker.

    * v1 → legacy single-pass SHA-256 (kept only for back-compat decryption of
      historical ciphertexts).
    * v2 (and any future version) → HKDF-SHA256 with fixed salt + per-version
      info; produces a different AES key than legacy SHA-256.
    """

    def test_derive_key_legacy_matches_sha256(self):
        # The legacy derivation is exactly SHA-256(material) — pinned by spec
        # so historical ciphertexts written by older versions of the service
        # remain decryptable bit-for-bit.
        import hashlib

        material = "any-master-key-value"
        assert (
            secrets_service._derive_key_legacy(material)
            == hashlib.sha256(material.encode("utf-8")).digest()
        )

    def test_derive_key_v1_uses_legacy(self):
        material = "another-test-master-key-value-XXX"
        legacy = secrets_service._derive_key_legacy(material)
        assert secrets_service._derive_key(material, version=1) == legacy

    def test_derive_key_v2_uses_hkdf(self):
        # HKDF MUST differ from SHA-256 of the same input — that's the whole point.
        material = "another-test-master-key-value-XXX"
        legacy = secrets_service._derive_key_legacy(material)
        hkdf_v2 = secrets_service._derive_key(material, version=2)
        assert hkdf_v2 != legacy
        assert len(hkdf_v2) == 32  # AES-256

    def test_derive_key_hkdf_is_deterministic(self):
        material = "stable-master-key-material-zzz"
        a = secrets_service._derive_key_hkdf(material, version=2)
        b = secrets_service._derive_key_hkdf(material, version=2)
        assert a == b

    def test_derive_key_hkdf_version_isolates_outputs(self):
        # Same master, different version → different AES key. This is what
        # makes key-version rotation meaningful without re-encrypting.
        material = "stable-master-key-material-zzz"
        v2 = secrets_service._derive_key_hkdf(material, version=2)
        v3 = secrets_service._derive_key_hkdf(material, version=3)
        assert v2 != v3

    def test_encrypt_with_v2_writes_v2_token_and_roundtrips(self, monkeypatch):
        from src.core.config import get_settings

        # Bump the active version to 2 (HKDF) for this test.
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        get_settings.cache_clear()  # type: ignore[attr-defined]
        try:
            token = secrets_service.encrypt("hkdf-payload", aad=_TEST_AAD)
            assert token.startswith("v2$"), f"expected v2 prefix, got {token[:5]!r}"
            assert secrets_service.decrypt(token, aad=_TEST_AAD) == "hkdf-payload"
        finally:
            get_settings.cache_clear()  # type: ignore[attr-defined]

    def test_v1_token_still_decryptable_after_kdf_upgrade(self, monkeypatch):
        """Cross-KDF back-compat: a v1 (legacy) ciphertext stays readable when
        the active version is v2 (HKDF) as long as the original master is still
        available under SERVER_ENCRYPTION_KEY__v1."""
        from src.core.config import get_settings

        # Step 1: a historical v1 (legacy SHA-256) token. New writes under v1
        # are forbidden, so the token is crafted directly via the legacy KDF.
        original_key = "historical-v1-master-key-padded-32xx"
        token_v1 = _craft_v1_token("historical-secret", original_key, aad=_TEST_AAD)
        assert token_v1.startswith("v1$")

        # Step 2: active is v2 (HKDF), v1 master lives under legacy env.
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY", "fresh-active-key-for-v2-hkdf-aaaaaa")
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY__v1", original_key)
        get_settings.cache_clear()  # type: ignore[attr-defined]
        try:
            # Old ciphertext decrypts via legacy SHA-256 path.
            assert secrets_service.decrypt(token_v1, aad=_TEST_AAD) == "historical-secret"
            # New encrypts go out as v2 (HKDF).
            token_v2 = secrets_service.encrypt("new-secret", aad=_TEST_AAD)
            assert token_v2.startswith("v2$")
            assert secrets_service.decrypt(token_v2, aad=_TEST_AAD) == "new-secret"
        finally:
            get_settings.cache_clear()  # type: ignore[attr-defined]

    def test_v2_token_decrypt_fails_under_wrong_master(self, monkeypatch):
        """A v2 ciphertext written under one master MUST NOT decrypt under
        another — sanity-check that HKDF actually uses the master string."""
        from src.core.config import get_settings

        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        get_settings.cache_clear()  # type: ignore[attr-defined]
        try:
            token = secrets_service.encrypt("payload", aad=_TEST_AAD)
        finally:
            get_settings.cache_clear()  # type: ignore[attr-defined]

        # Swap master under the same active version → decrypt must fail.
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY", "a-completely-different-master-key-bbb")
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        get_settings.cache_clear()  # type: ignore[attr-defined]
        try:
            with pytest.raises(AppException) as exc:
                secrets_service.decrypt(token, aad=_TEST_AAD)
            assert exc.value.error_code == "DECRYPT_FAILED"
        finally:
            get_settings.cache_clear()  # type: ignore[attr-defined]


# ── Settings validation: SERVER_ENCRYPTION_KEY min_length ─────────────────────


class TestKeyMinLengthValidator:
    """`SERVER_ENCRYPTION_KEY` must be at least 32 chars — protects against
    operator typos like `SERVER_ENCRYPTION_KEY=x`. HKDF tolerates any input
    structurally, but trivial keys have trivial entropy."""

    def _build_settings(self, **overrides):
        # Construct Settings directly (env_file is irrelevant here — pydantic
        # picks up the field values we pass explicitly).
        from src.core.config import Settings

        base = {
            "database_url": "postgresql+psycopg://u:p@h/d",
            "auth_service_url": "http://auth",
            "server_encryption_key": "x" * 32,
        }
        base.update(overrides)
        return Settings(**base)

    def test_short_key_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            self._build_settings(server_encryption_key="short")
        # Pydantic surfaces field-level errors; we just need to see that the
        # offending field is the encryption key.
        msg = str(exc.value)
        assert "server_encryption_key" in msg

    def test_one_char_below_threshold_is_rejected(self):
        with pytest.raises(ValidationError):
            self._build_settings(server_encryption_key="x" * 31)

    def test_exactly_32_chars_is_accepted(self):
        settings = self._build_settings(server_encryption_key="x" * 32)
        assert settings.server_encryption_key == "x" * 32

    def test_long_key_is_accepted(self):
        long_key = "a" * 128
        settings = self._build_settings(server_encryption_key=long_key)
        assert settings.server_encryption_key == long_key

    def test_default_active_version_is_v2(self, monkeypatch):
        # New deployments must default to HKDF (v2) — v1 (legacy single-pass
        # SHA-256) is no longer a valid active version. Clear the env override
        # to inspect the bare field default.
        monkeypatch.delenv("SERVER_ENCRYPTION_KEY_VERSION", raising=False)
        settings = self._build_settings()
        assert settings.server_encryption_key_version == 2


# ── AAD swap-attack mitigation ────────────────────────────────────────────────


class TestAADSwapAttack:
    """AES-GCM AAD binding: ciphertext привязан к строке-владельцу.

    Без aad злоумышленник с UPDATE-доступом к БД мог бы скопировать
    `password_encrypted` из строки A в строку B и через `internal/view_password`
    раскрыть чужой пароль. С aad подмена даёт `DECRYPT_FAILED` (под капотом
    InvalidTag), что фейлит fetch и оставляет audit-trail.
    """

    def test_decrypt_with_different_aad_fails(self):
        token = secrets_service.encrypt("secret", aad=b"aad-A")
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(token, aad=b"aad-B")
        assert exc.value.error_code == "DECRYPT_FAILED"

    def test_decrypt_with_same_aad_succeeds(self):
        token = secrets_service.encrypt("secret", aad=b"aad-A")
        assert secrets_service.decrypt(token, aad=b"aad-A") == "secret"

    def test_account_aad_isolates_rows(self):
        """Ciphertext, написанный для account_id=A, не должен дешифроваться
        для account_id=B — swap между двумя `server_accounts` строками."""
        aad_a = secrets_service.aad_for_server_account_password("acc_AAAA")
        aad_b = secrets_service.aad_for_server_account_password("acc_BBBB")
        token = secrets_service.encrypt("payload", aad=aad_a)
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(token, aad=aad_b)
        assert exc.value.error_code == "DECRYPT_FAILED"

    def test_ipmi_vs_account_aad_isolates_tables(self):
        """Cross-table swap (ipmi → server_account на тот же id) — DECRYPT_FAILED.
        Helpers кодируют owner_table в aad, поэтому ipmi-токен не подойдёт
        под account-aad даже если идентификаторы случайно совпали бы."""
        aad_account = secrets_service.aad_for_server_account_password("xxx")
        aad_ipmi = secrets_service.aad_for_ipmi_credential("xxx")
        token = secrets_service.encrypt("payload", aad=aad_ipmi)
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(token, aad=aad_account)
        assert exc.value.error_code == "DECRYPT_FAILED"


# ── HKDF salt config ─────────────────────────────────────────────────────────


class TestHkdfSaltConfig:
    """`HKDF_SALT_HEX` Settings-валидация + dev-fallback семантика."""

    def _build_settings(self, **overrides):
        from src.core.config import Settings

        base = {
            "database_url": "postgresql+psycopg://u:p@h/d",
            "auth_service_url": "http://auth",
            "server_encryption_key": "x" * 32,
            "app_env": "local",
        }
        base.update(overrides)
        return Settings(**base)

    def test_valid_hex_salt_accepted(self):
        salt = "a" * 32  # ровно 32 hex
        settings = self._build_settings(hkdf_salt_hex=salt)
        assert settings.hkdf_salt_hex == salt

    def test_long_hex_salt_accepted(self):
        salt = "deadbeef" * 16  # 128 hex char
        settings = self._build_settings(hkdf_salt_hex=salt)
        assert settings.hkdf_salt_hex == salt

    def test_short_hex_salt_rejected(self):
        with pytest.raises(ValidationError) as exc:
            self._build_settings(hkdf_salt_hex="abc")
        assert "HKDF_SALT_HEX" in str(exc.value) or "hkdf_salt_hex" in str(exc.value)

    def test_invalid_hex_rejected(self):
        # 32 chars но не hex — `xyz` за пределами [0-9a-f].
        with pytest.raises(ValidationError):
            self._build_settings(hkdf_salt_hex="x" * 32)

    def test_empty_in_local_accepted(self):
        # local/dev/test разрешают пустоту — secrets_service подсунет fallback.
        settings = self._build_settings(app_env="local", hkdf_salt_hex="")
        assert settings.hkdf_salt_hex == ""

    def test_empty_in_dev_accepted(self):
        settings = self._build_settings(app_env="dev", hkdf_salt_hex="")
        assert settings.hkdf_salt_hex == ""

    def test_empty_in_production_rejected(self):
        with pytest.raises(ValidationError) as exc:
            self._build_settings(app_env="production", hkdf_salt_hex="")
        assert "HKDF_SALT_HEX" in str(exc.value)

    def test_empty_in_staging_rejected(self):
        with pytest.raises(ValidationError) as exc:
            self._build_settings(app_env="staging", hkdf_salt_hex="")
        assert "HKDF_SALT_HEX" in str(exc.value)

    def test_dev_fallback_used_when_empty(self, monkeypatch):
        """В dev/test/local пустой `HKDF_SALT_HEX` → секреты деривируются через
        fallback-константу. Округлая проверка: round-trip работает."""
        from src.core.config import get_settings

        monkeypatch.setenv("APP_ENV", "local")
        monkeypatch.setenv("HKDF_SALT_HEX", "")
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        get_settings.cache_clear()  # type: ignore[attr-defined]
        try:
            assert get_settings().hkdf_salt_hex == ""
            token = secrets_service.encrypt("payload", aad=_TEST_AAD)
            assert token.startswith("v2$")
            assert secrets_service.decrypt(token, aad=_TEST_AAD) == "payload"
        finally:
            get_settings.cache_clear()  # type: ignore[attr-defined]
