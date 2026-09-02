"""Симметричное шифрование хранимых credential-secret'ов.

Self-contained token-формат::

    v<key_version>$<base64url_nonce>$<base64url_ciphertext>

`SECRET_ENCRYPTION_KEY` — активный мастер-ключ; старые версии читаются из
`SECRET_ENCRYPTION_KEY__v<N>` env-переменных. Алгоритм — AES-256-GCM:
AEAD с 96-битным nonce, 128-битным auth-tag и AAD-binding'ом ciphertext'а
к owner-row (см. секцию «Associated data» ниже). KDF — HKDF-SHA256 поверх
мастер-ключа. Wire-формат version-prefix'нут, поэтому схема ключей и
алгоритм при необходимости заменяются без re-encrypt'а существующих строк.

Key derivation
--------------

Версия в wire-формате работает и как KDF-marker для back-compat:

* ``v1`` — legacy одношаговый ``SHA-256(master_key)`` (только для
  расшифровки исторических ciphertext'ов; новые encrypt'ы запрещены).
* ``v2`` и выше — HKDF-SHA256 с конфигурируемой service-salt
  (env ``HKDF_SALT_HEX``) и per-version ``info``-меткой, на выходе
  256-битный AES-ключ.

HKDF-salt в production/staging обязателен (Settings отказывается стартовать
с пустым). В dev/test/local допускается fallback на константу
``_FALLBACK_HKDF_SALT`` — это позволяет CI/devcontainer-у не возиться с
секретами, но не даёт случайно унаследовать тот же salt в продакшен.

Associated data
---------------

Каждый encrypt/decrypt ОБЯЗАН передавать `aad` (associated data) —
"identity"-байты строки-владельца. Это binding ciphertext'а к конкретной
строке БД: если злоумышленник скопирует `secret_encrypted` из credential A
в credential B (swap-attack), `decrypt` отбьётся `InvalidTag`, потому что
aad строки A не совпадает с aad строки B.

Хелпер :func:`aad_for_credential` формирует aad по схеме ``"cred:{cred_id}"``
— тот же формат, что описан в README.
"""

import base64
import hashlib
import os
import re
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.core.config import get_settings
from src.core.exceptions import AppException
from src.core.keystore import get_keystore


@dataclass(frozen=True)
class DecryptResult:
    """Результат `decrypt_with_metadata`: plaintext + флаги для lazy re-encrypt.

    `needs_reencrypt` поднимается, когда ciphertext был сделан под версией
    ключа, меньшей, чем текущая активная. Caller, у которого есть row-id и
    запись на БД, может прозрачно перешифровать секрет и обновить строку
    одним CAS-UPDATE'ом.
    """

    plaintext: str
    source_version: int
    needs_reencrypt: bool


_NONCE_BYTES = 12  # стандартный AES-GCM
_AES_KEY_BYTES = 32  # AES-256
_MAX_PLAINTEXT_BYTES = 8192  # симметрично CHECK в схеме credentials
# Fallback HKDF salt — применяется ТОЛЬКО когда `HKDF_SALT_HEX` пуст и
# `APP_ENV` ∈ {dev, test, local}. В production/staging Settings не даст
# стартовать без явного значения.
_FALLBACK_HKDF_SALT = b"dbos-secret-service-credentials-dev-fallback"
_LEGACY_KDF_VERSIONS = frozenset({1})  # v1 — одношаговый SHA-256
# Wire-format whitelist: префикс v<int>, два base64url-сегмента (URL-safe
# алфавит без паддинга). Жёстче, чем split('$', 2): отлавливает мусор вроде
# `v9$abc$abc$xyz` до того, как мы дойдём до AEAD.
_TOKEN_FORMAT_RE = re.compile(r"^v\d+\$[A-Za-z0-9_-]+\$[A-Za-z0-9_-]+$")


def _resolve_hkdf_salt() -> bytes:
    """Достать активную HKDF-salt: env-конфиг или dev-fallback."""
    settings = get_settings()
    raw = (settings.hkdf_salt_hex or "").strip()
    if not raw:
        return _FALLBACK_HKDF_SALT
    return bytes.fromhex(raw)


def _derive_key_legacy(material: str) -> bytes:
    """Legacy одношаговый SHA-256 — только для ``v1`` ciphertext'ов."""
    return hashlib.sha256(material.encode()).digest()


def _derive_key_hkdf(material: str, version: int) -> bytes:
    """HKDF-SHA256, 32 байта на выходе, version-tagged info."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_AES_KEY_BYTES,
        salt=_resolve_hkdf_salt(),
        info=f"v{version}".encode("ascii"),
    )
    return hkdf.derive(material.encode())


def _derive_key(material: str, version: int) -> bytes:
    """Выбрать KDF по wire-формату версии ciphertext'а."""
    if version in _LEGACY_KDF_VERSIONS:
        return _derive_key_legacy(material)
    return _derive_key_hkdf(material, version)


def _key_for_version(version: int) -> bytes:
    """Достать AES-ключ для версии: master-материал из keystore + KDF по версии.

    KeyStore хранит master-материал (то, что раньше лежало в env'е); KDF —
    HKDF для v2+, legacy SHA-256 для v1 — остаётся здесь, выбирается по
    версии из wire-префикса. `get_key` поднимает `ENCRYPTION_KEY_MISSING`
    (500), если для версии нет материала.
    """
    material = get_keystore().get_key(version).decode()
    return _derive_key(material, version)


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


# ── AAD helpers ──────────────────────────────────────────────────────────────


def aad_for_credential(cred_id: str) -> bytes:
    """AAD для `credentials.secret_encrypted` строки `cred_id`.

    Формат — `"cred:{cred_id}"`, как зафиксировано в README. Привязывает
    ciphertext к конкретному credential-row: swap → InvalidTag.
    """
    return f"cred:{cred_id}".encode()


# ── Encrypt / Decrypt ────────────────────────────────────────────────────────


def encrypt(plaintext: str, *, aad: bytes) -> str:
    """Зашифровать активной версией ключа. Возвращает self-contained token.

    `aad` — обязательный keyword-only байтовый идентификатор строки-владельца
    (см. :func:`aad_for_credential`). Без правильного aad decrypt того же
    token'а отдаст `DECRYPT_FAILED` — это и есть swap-attack mitigation.

    Лимит plaintext — 8192 байта в UTF-8: симметрично CHECK
    `length(secret_encrypted) < 8192` в схеме `credentials`.
    """
    if plaintext is None:
        raise AppException(
            http_status=422,
            error_code="ENCRYPT_INPUT_INVALID",
            message="Cannot encrypt None",
        )
    plaintext_bytes = plaintext.encode("utf-8")
    if len(plaintext_bytes) > _MAX_PLAINTEXT_BYTES:
        raise AppException(
            http_status=422,
            error_code="PLAINTEXT_TOO_LARGE",
            message=f"Plaintext exceeds {_MAX_PLAINTEXT_BYTES} bytes",
            details={"limit": _MAX_PLAINTEXT_BYTES, "got": len(plaintext_bytes)},
        )
    version = get_keystore().get_active_version()
    key = _key_for_version(version)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext_bytes, aad)
    return f"v{version}${_b64e(nonce)}${_b64e(ciphertext)}"


def decrypt_with_metadata(token: str, *, aad: bytes) -> DecryptResult:
    """Расшифровать token и вернуть plaintext вместе с метаданными версии.

    Поле `needs_reencrypt=True` означает, что ciphertext был зашифрован под
    версией ключа меньшей, чем активная: caller с доступом на запись может
    выполнить lazy re-encrypt одним UPDATE'ом и постепенно мигрировать БД
    под новый мастер-ключ без планового простоя.

    Семантика ошибок — ровно та же, что в :func:`decrypt`. Эта функция —
    суперсет: `decrypt` остался как тонкая обёртка для legacy call-site'ов,
    которым плевать на версию.
    """
    if not token or not _TOKEN_FORMAT_RE.match(token):
        raise AppException(
            http_status=422,
            error_code="DECRYPT_FAILED",
            message="Encrypted token format invalid",
        )
    version_part, nonce_b64, ct_b64 = token.split("$", 2)
    try:
        version = int(version_part[1:])
    except ValueError as exc:
        raise AppException(
            http_status=422,
            error_code="DECRYPT_FAILED",
            message="Cannot parse encrypted token version",
        ) from exc
    key = _key_for_version(version)
    try:
        nonce_bytes = _b64d(nonce_b64)
        ct_bytes = _b64d(ct_b64)
    except (ValueError, TypeError) as exc:
        # Битый base64 в nonce/ciphertext — это форма malformed-input'а, а не
        # криптофейл. AEAD до этого даже не доходит.
        raise AppException(
            http_status=422,
            error_code="DECRYPT_FAILED",
            message=f"Failed to decrypt token: {type(exc).__name__}",
        ) from exc
    try:
        plaintext_bytes = AESGCM(key).decrypt(nonce_bytes, ct_bytes, aad)
    except (InvalidTag, ValueError) as exc:
        # InvalidTag — auth tag не сошёлся (подмена ciphertext'а, неправильный
        # aad, не тот ключ); ValueError — `cryptography` его поднимает для
        # неправильной длины nonce и подобных format-нарушений.
        raise AppException(
            http_status=422,
            error_code="DECRYPT_FAILED",
            message=f"Failed to decrypt token: {type(exc).__name__}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise AppException(
            http_status=500,
            error_code="DECRYPT_INTERNAL_ERROR",
            message=f"Internal decrypt error: {type(exc).__name__}",
        ) from exc

    active_version = get_keystore().get_active_version()
    return DecryptResult(
        plaintext=plaintext_bytes.decode("utf-8"),
        source_version=version,
        needs_reencrypt=version != active_version,
    )


def decrypt(token: str, *, aad: bytes) -> str:
    """Расшифровать ранее зашифрованный token.

    Тонкая обёртка вокруг :func:`decrypt_with_metadata` для call-site'ов,
    которым не нужны метаданные версии (нет доступа на запись, либо плановая
    миграция выключена). Семантика ошибок неизменна — см. docstring новой
    функции.
    """
    return decrypt_with_metadata(token, aad=aad).plaintext
