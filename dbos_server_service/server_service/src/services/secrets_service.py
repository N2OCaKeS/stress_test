"""Симметричное шифрование хранимых паролей.

Self-contained token-формат::

    v<key_version>$<base64url_nonce>$<base64url_ciphertext>

`SERVER_ENCRYPTION_KEY` — активный мастер-ключ; старые версии читаются из
`SERVER_ENCRYPTION_KEY__v<N>` env-переменных. Сейчас в качестве алгоритма
используется AES-256-GCM как placeholder под полноценный ГОСТ-Кузнечик MGM —
wire-формат не меняется, алгоритм можно заменить без re-encrypt'а.

Key derivation
--------------

Wire-формат версии работает и как KDF-marker для backward compatibility:

* ``v1`` — legacy одношаговый ``SHA-256(master_key)`` (сохранён только
  для расшифровки исторических ciphertext'ов; НЕ использовать для новых encrypts).
* ``v2`` и выше — HKDF-SHA256 с конфигурируемой service-salt (env
  ``HKDF_SALT_HEX``) и per-version ``info``-меткой, на выходе 256-битный AES-ключ.

HKDF-salt в production/staging обязателен (Settings отказывается стартовать
с пустым). В dev/test/local допускается fallback на константу
``_FALLBACK_HKDF_SALT`` — это позволяет CI/devcontainer-у не возиться с
секретами, но не даёт случайно унаследовать тот же salt в продакшен.

Associated data
---------------

Каждый encrypt/decrypt ОБЯЗАН передавать `aad` (associated data) —
"identity"-байты строки-владельца. Это binding ciphertext'а к конкретной
строке БД: если злоумышленник скопирует `password_encrypted` из аккаунта A
в строку аккаунта B (swap-attack), `decrypt` отбьётся `InvalidTag`, потому
что aad строки A не совпадает с aad строки B. Без aad swap проходил бы
прозрачно — это критическая уязвимость.

Хелперы :func:`aad_for_server_account_password` и
:func:`aad_for_ipmi_credential` формируют aad по схеме
``"{kind}|{owner_table}|{id}"``.
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.core.config import get_settings
from src.core.exceptions import AppException


_NONCE_BYTES = 12  # стандартный AES-GCM
_AES_KEY_BYTES = 32  # AES-256
# Fallback HKDF salt — применяется ТОЛЬКО когда `HKDF_SALT_HEX` пуст
# и `APP_ENV` ∈ {dev, test, local}. В production/staging Settings
# не даст стартовать без явного значения.
_FALLBACK_HKDF_SALT = b"dbos-server-service-secrets-dev-fallback"
_LEGACY_KDF_VERSIONS = frozenset({1})  # v1 использовал одношаговый SHA-256
_DEV_ENVS = frozenset({"dev", "test", "local"})


def _resolve_hkdf_salt() -> bytes:
    """Достать активную HKDF-salt: env-конфиг или dev-fallback.

    Settings уже валидировал, что в production/staging `hkdf_salt_hex`
    непустой. Здесь только разворачиваем hex → bytes; для пустого
    значения (только dev/test/local) подсовываем `_FALLBACK_HKDF_SALT`.
    """
    settings = get_settings()
    raw = (settings.hkdf_salt_hex or "").strip()
    if not raw:
        return _FALLBACK_HKDF_SALT
    return bytes.fromhex(raw)


def _derive_key_legacy(material: str) -> bytes:
    """Legacy одношаговый SHA-256 — только для ``v1`` ciphertext'ов.

    Оставлен исключительно чтобы записи, зашифрованные до HKDF-rollout'а,
    оставались читаемыми. Новые encrypt'ы ОБЯЗАНЫ идти через :func:`_derive_key_hkdf`.
    """
    return hashlib.sha256(material.encode("utf-8")).digest()


def _derive_key_hkdf(material: str, version: int) -> bytes:
    """HKDF-SHA256, 32 байта на выходе, конфигурируемая salt, version-tagged info.

    Один и тот же master-string под разными версиями даёт разные AES-ключи —
    это позволяет ротировать без re-encrypt'а и изолирует KDF-output'ы по
    wire-format-версии.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_AES_KEY_BYTES,
        salt=_resolve_hkdf_salt(),
        info=f"v{version}".encode("ascii"),
    )
    return hkdf.derive(material.encode("utf-8"))


def _derive_key(material: str, version: int) -> bytes:
    """Выбрать KDF по wire-формату версии ciphertext'а."""
    if version in _LEGACY_KDF_VERSIONS:
        return _derive_key_legacy(material)
    return _derive_key_hkdf(material, version)


def _key_for_version(version: int) -> bytes:
    """Достать ключ для указанной версии — текущий или legacy из env."""
    settings = get_settings()
    if version == settings.server_encryption_key_version:
        return _derive_key(settings.server_encryption_key, version)
    # Старые версии — из `SERVER_ENCRYPTION_KEY__v<N>` env.
    env_name = f"SERVER_ENCRYPTION_KEY__v{version}"
    legacy = os.environ.get(env_name)
    if not legacy:
        raise AppException(
            http_status=500,
            error_code="ENCRYPTION_KEY_MISSING",
            message=f"No key configured for ciphertext version v{version}",
            details={"env": env_name},
        )
    return _derive_key(legacy, version)


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


# ── AAD helpers ──────────────────────────────────────────────────────────────


def aad_for_server_account_password(account_id: str) -> bytes:
    """AAD для `server_accounts.password_encrypted` строки `account_id`.

    Формат — `"server_account_password|server_accounts|<id>"`. Привязывает
    ciphertext к конкретной строке: swap в другую строку → InvalidTag.
    """
    return f"server_account_password|server_accounts|{account_id}".encode("utf-8")


def aad_for_ipmi_credential(controller_id: str) -> bytes:
    """AAD для `ipmi_controllers.password_encrypted` строки `controller_id`.

    Формат — `"ipmi_credential|ipmi_controllers|<id>"`. Привязывает
    ciphertext к конкретному BMC-row: swap → InvalidTag.
    """
    return f"ipmi_credential|ipmi_controllers|{controller_id}".encode("utf-8")


# ── Encrypt / Decrypt ────────────────────────────────────────────────────────


def encrypt(plaintext: str, *, aad: bytes) -> str:
    """Зашифровать активной версией ключа. Возвращает self-contained token.

    `aad` — обязательный keyword-only байтовый идентификатор строки-владельца
    (см. :func:`aad_for_server_account_password` / :func:`aad_for_ipmi_credential`).
    Без правильного aad decrypt того же token'а отдаст InvalidTag — это и есть
    swap-attack mitigation.
    """
    if plaintext is None:
        raise AppException(
            http_status=500,
            error_code="ENCRYPT_INPUT_INVALID",
            message="Cannot encrypt None",
        )
    settings = get_settings()
    version = settings.server_encryption_key_version
    key = _key_for_version(version)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return f"v{version}${_b64e(nonce)}${_b64e(ciphertext)}"


def decrypt(token: str, *, aad: bytes) -> str:
    """Расшифровать ранее зашифрованный token.

    `aad` обязан совпадать с тем, что передавался в :func:`encrypt`. Несовпадение
    или подмена ciphertext'а — `DECRYPT_FAILED` (под капотом InvalidTag).
    """
    if not token or not token.startswith("v"):
        raise AppException(
            http_status=500,
            error_code="ENCRYPTED_TOKEN_INVALID",
            message="Encrypted token has no version prefix",
        )
    try:
        version_part, nonce_b64, ct_b64 = token.split("$", 2)
        version = int(version_part[1:])
    except (ValueError, IndexError) as exc:
        raise AppException(
            http_status=500,
            error_code="ENCRYPTED_TOKEN_MALFORMED",
            message="Cannot parse encrypted token",
        ) from exc
    key = _key_for_version(version)
    try:
        plaintext = AESGCM(key).decrypt(_b64d(nonce_b64), _b64d(ct_b64), aad)
    except Exception as exc:  # noqa: BLE001 — cryptography поднимает разные subclasses
        raise AppException(
            http_status=500,
            error_code="DECRYPT_FAILED",
            message=f"Failed to decrypt token: {type(exc).__name__}",
        ) from exc
    return plaintext.decode("utf-8")
