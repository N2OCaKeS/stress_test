"""Unit-тесты симметричного шифрования credential-secret'ов.

Покрывает критичные свойства :mod:`src.services.secrets_service`:

* round-trip encrypt → decrypt с тем же AAD;
* swap-attack защита через `aad_for_credential`;
* dispatch на нужную версию ключа (активная vs legacy);
* отказ при отсутствующем ключе версии;
* отказ при сменённой HKDF-salt;
* лимит plaintext;
* отказ на невалидный wire-формат.
"""

from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.core.exceptions import AppException
from src.services import secrets_service


_TEST_AAD = secrets_service.aad_for_credential("cred_test_fixed")


def _craft_v1_token(plaintext: str, master_key: str, *, aad: bytes) -> str:
    """Собрать legacy v1-ciphertext напрямую (новые v1-encrypt'ы запрещены).

    Используется тестами back-compat'а: имитирует ciphertext, написанный
    старой версией сервиса под legacy SHA-256 KDF.
    """
    key = secrets_service._derive_key_legacy(master_key)
    nonce = os.urandom(secrets_service._NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return f"v1${secrets_service._b64e(nonce)}${secrets_service._b64e(ciphertext)}"


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Сбрасываем lru_cache settings до и после каждого теста.

    Тесты массово монкипатчат env, и закэшированный Settings из conftest
    («test»-окружение) подменяться не должен между ними.
    """
    from src.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


# ── Round-trip ────────────────────────────────────────────────────────────────


class TestRoundTrip:
    @pytest.mark.parametrize(
        "plaintext",
        [
            "simple",
            "with space and !@#$%^&*() symbols",
            "юникод и кириллица",
            "x",
            "a" * 4096,
            "\n\r\t\\\"'",
            "🔐 emoji + binary-ish",
            "",
        ],
    )
    def test_round_trip_preserves_plaintext(self, plaintext: str):
        token = secrets_service.encrypt(plaintext, aad=_TEST_AAD)
        assert secrets_service.decrypt(token, aad=_TEST_AAD) == plaintext

    def test_encrypt_returns_token_with_expected_shape(self):
        token = secrets_service.encrypt("payload", aad=_TEST_AAD)
        version_part, nonce_b64, ct_b64 = token.split("$", 2)
        assert version_part.startswith("v")
        int(version_part[1:])
        assert secrets_service._b64d(nonce_b64)
        assert secrets_service._b64d(ct_b64)

    def test_each_encrypt_uses_fresh_nonce(self):
        a = secrets_service.encrypt("same-plaintext", aad=_TEST_AAD)
        b = secrets_service.encrypt("same-plaintext", aad=_TEST_AAD)
        assert a != b
        assert a.split("$", 2)[1] != b.split("$", 2)[1]


# ── AAD swap-attack ───────────────────────────────────────────────────────────


class TestAADSwapAttack:
    def test_credential_aad_isolates_rows(self):
        token = secrets_service.encrypt(
            "secret-A", aad=secrets_service.aad_for_credential("cred_a")
        )
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(
                token, aad=secrets_service.aad_for_credential("cred_b")
            )
        assert exc.value.error_code == "DECRYPT_FAILED"

    def test_same_aad_succeeds(self):
        token = secrets_service.encrypt(
            "secret-A", aad=secrets_service.aad_for_credential("cred_a")
        )
        assert (
            secrets_service.decrypt(
                token, aad=secrets_service.aad_for_credential("cred_a")
            )
            == "secret-A"
        )

    def test_aad_helper_format(self):
        assert secrets_service.aad_for_credential("cred_xyz") == b"cred:cred_xyz"


# ── Key version dispatch ──────────────────────────────────────────────────────


class TestKeyVersionDispatch:
    def test_unknown_version_raises_key_missing(self, monkeypatch):
        monkeypatch.delenv("SECRET_ENCRYPTION_KEY__v99", raising=False)
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(
                "v99$AAAAAAAAAAAAAAAA$BBBBBBBBBBBBBBBB", aad=_TEST_AAD
            )
        assert exc.value.error_code == "ENCRYPTION_KEY_MISSING"
        assert exc.value.details.get("env") == "SECRET_ENCRYPTION_KEY__v99"

    def test_active_version_bump_writes_new_prefix(self, monkeypatch):
        from src.core.config import get_settings

        # Шифруем под v2 (дефолт conftest'а), потом бампим до v3,
        # старый v2-токен должен остаться расшифровываемым через активный
        # мастер (т.к. master тот же; HKDF info=v2 отличается от v3).
        get_settings.cache_clear()  # type: ignore[attr-defined]
        token_v2 = secrets_service.encrypt("v2-payload", aad=_TEST_AAD)
        assert token_v2.startswith("v2$")

        # Бампим активную версию до v3, тот же master доступен и под legacy v2.
        monkeypatch.setenv("SECRET_ENCRYPTION_KEY_VERSION", "3")
        monkeypatch.setenv(
            "SECRET_ENCRYPTION_KEY__v2",
            os.environ["SECRET_ENCRYPTION_KEY"],
        )
        get_settings.cache_clear()  # type: ignore[attr-defined]

        token_v3 = secrets_service.encrypt("v3-payload", aad=_TEST_AAD)
        assert token_v3.startswith("v3$")
        assert secrets_service.decrypt(token_v3, aad=_TEST_AAD) == "v3-payload"
        # Старый v2-токен по-прежнему читается через legacy env.
        assert secrets_service.decrypt(token_v2, aad=_TEST_AAD) == "v2-payload"

    def test_v2_unreadable_when_v2_master_dropped(self, monkeypatch):
        """Симулируем «потеряли master для версии токена»: encrypt под v2 → бамп
        до v3, активный master сменён, legacy v2 НЕ задан → decrypt v2-токена
        падает с ENCRYPTION_KEY_MISSING.
        """
        from src.core.config import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]
        token_v2 = secrets_service.encrypt("legacy-v2", aad=_TEST_AAD)
        assert token_v2.startswith("v2$")

        monkeypatch.setenv("SECRET_ENCRYPTION_KEY_VERSION", "3")
        monkeypatch.setenv(
            "SECRET_ENCRYPTION_KEY",
            "a-completely-different-active-master-key-bbb",
        )
        monkeypatch.delenv("SECRET_ENCRYPTION_KEY__v2", raising=False)
        get_settings.cache_clear()  # type: ignore[attr-defined]

        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(token_v2, aad=_TEST_AAD)
        assert exc.value.error_code == "ENCRYPTION_KEY_MISSING"
        assert exc.value.details.get("env") == "SECRET_ENCRYPTION_KEY__v2"


# ── Legacy v1 read ────────────────────────────────────────────────────────────


class TestLegacyV1Read:
    def test_v1_token_decryptable_via_legacy_env(self, monkeypatch):
        """Исторический v1-ciphertext остаётся читаемым, когда master v1 лежит
        в `SECRET_ENCRYPTION_KEY__v1` (а активная версия — v2)."""
        from src.core.config import get_settings

        original_v1_key = "legacy-v1-master-key-padded-to-32-chars"
        token = _craft_v1_token("v1-historical", original_v1_key, aad=_TEST_AAD)
        assert token.startswith("v1$")

        monkeypatch.setenv("SECRET_ENCRYPTION_KEY__v1", original_v1_key)
        get_settings.cache_clear()  # type: ignore[attr-defined]
        assert secrets_service.decrypt(token, aad=_TEST_AAD) == "v1-historical"


# ── Format validation ────────────────────────────────────────────────────────


class TestFormatValidation:
    @pytest.mark.parametrize(
        "bad_token",
        [
            "",
            "not-a-token",
            "1$abc$def",  # без префикса v
            "v1",  # нет $-сегментов
            "v1$onlynonce",  # нет ciphertext
            "vXYZ$AAAA$BBBB",  # не-числовая версия
            "v2$abc!$def",  # запрещённый символ
            "v2$abc$def$extra",  # лишний сегмент
        ],
    )
    def test_malformed_token_raises_decrypt_failed(self, bad_token: str):
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(bad_token, aad=_TEST_AAD)
        assert exc.value.error_code == "DECRYPT_FAILED"

    def test_none_token_raises(self):
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(None, aad=_TEST_AAD)  # type: ignore[arg-type]
        assert exc.value.error_code == "DECRYPT_FAILED"

    def test_tampered_ciphertext_fails(self):
        token = secrets_service.encrypt("hello", aad=_TEST_AAD)
        version, nonce, ct = token.split("$", 2)
        ct_bytes = secrets_service._b64d(ct)
        flipped = bytes([ct_bytes[0] ^ 0x01]) + ct_bytes[1:]
        tampered = f"{version}${nonce}${secrets_service._b64e(flipped)}"
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(tampered, aad=_TEST_AAD)
        assert exc.value.error_code == "DECRYPT_FAILED"


# ── Plaintext size limit ──────────────────────────────────────────────────────


class TestPlaintextLimit:
    def test_at_limit_ok(self):
        # ровно 8192 UTF-8 байта (ASCII = 1 байт/символ)
        plaintext = "a" * secrets_service._MAX_PLAINTEXT_BYTES
        token = secrets_service.encrypt(plaintext, aad=_TEST_AAD)
        assert secrets_service.decrypt(token, aad=_TEST_AAD) == plaintext

    def test_over_limit_rejected(self):
        plaintext = "a" * (secrets_service._MAX_PLAINTEXT_BYTES + 1)
        with pytest.raises(AppException) as exc:
            secrets_service.encrypt(plaintext, aad=_TEST_AAD)
        assert exc.value.error_code == "PLAINTEXT_TOO_LARGE"
        # plaintext не должен утечь в details
        assert "a" * 16 not in str(exc.value.details)

    def test_encrypt_none_rejected(self):
        with pytest.raises(AppException) as exc:
            secrets_service.encrypt(None, aad=_TEST_AAD)  # type: ignore[arg-type]
        assert exc.value.error_code == "ENCRYPT_INPUT_INVALID"


# ── HKDF salt change ──────────────────────────────────────────────────────────


class TestHkdfSaltChange:
    def test_salt_change_breaks_decrypt(self, monkeypatch):
        """Меняем `HKDF_SALT_HEX` между encrypt и decrypt → DECRYPT_FAILED.

        Это гарантирует, что salt участвует в KDF: иначе деривация дала бы
        тот же AES-ключ и токен расшифровался бы как ни в чём не бывало.
        """
        from src.core.config import get_settings

        # encrypt под salt A
        monkeypatch.setenv("HKDF_SALT_HEX", "aa" * 16)
        get_settings.cache_clear()  # type: ignore[attr-defined]
        token = secrets_service.encrypt("payload", aad=_TEST_AAD)

        # decrypt под salt B
        monkeypatch.setenv("HKDF_SALT_HEX", "bb" * 16)
        get_settings.cache_clear()  # type: ignore[attr-defined]
        with pytest.raises(AppException) as exc:
            secrets_service.decrypt(token, aad=_TEST_AAD)
        assert exc.value.error_code == "DECRYPT_FAILED"

    def test_same_salt_roundtrips(self, monkeypatch):
        from src.core.config import get_settings

        monkeypatch.setenv("HKDF_SALT_HEX", "cc" * 16)
        get_settings.cache_clear()  # type: ignore[attr-defined]
        token = secrets_service.encrypt("payload", aad=_TEST_AAD)
        assert secrets_service.decrypt(token, aad=_TEST_AAD) == "payload"


# ── KDF dispatch internals ────────────────────────────────────────────────────


class TestKDFDispatch:
    def test_v1_uses_legacy_sha256(self):
        import hashlib

        material = "any-master-key-value"
        assert (
            secrets_service._derive_key(material, version=1)
            == hashlib.sha256(material.encode("utf-8")).digest()
        )

    def test_v2_uses_hkdf_not_sha256(self):
        material = "stable-master-key-material-zzz"
        legacy = secrets_service._derive_key_legacy(material)
        hkdf_v2 = secrets_service._derive_key(material, version=2)
        assert hkdf_v2 != legacy
        assert len(hkdf_v2) == secrets_service._AES_KEY_BYTES

    def test_hkdf_version_isolates_outputs(self):
        material = "stable-master-key-material-zzz"
        assert secrets_service._derive_key_hkdf(
            material, version=2
        ) != secrets_service._derive_key_hkdf(material, version=3)
