"""Envelope-расшифровка Redis-stash'а кред, положенного server_service'ом.

Зеркало `server_service/src/services/redis_stash_crypto.py`. См. оригинал
для архитектурного обоснования отдельного master-key от `SERVER_ENCRYPTION_KEY`.

Worker импортирует только `decrypt_stash` + `aad_for_redis_stash` (он
читатель). `encrypt_stash` оставлен для тестов round-trip'а.

Wire-формат — `v<version>$<nonce_b64>$<ct_b64>`, AES-256-GCM + HKDF-SHA256,
`info=b"redis_stash|v<N>"`. Mirror server_service'а — изменение там обязано
быть отражено здесь же. `_FALLBACK_HKDF_SALT` побайтно совпадает с
server_service-копией, иначе dev-env'ы получили бы разный KDF-output на
одном пустом `HKDF_SALT_HEX`.
"""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.core.config import get_settings
from src.core.exceptions import AppException


_NONCE_BYTES = 12
_AES_KEY_BYTES = 32
_FALLBACK_HKDF_SALT = b"dbos-redis-stash-encryption-dev-fallback"


def _resolve_hkdf_salt() -> bytes:
    settings = get_settings()
    raw = (settings.hkdf_salt_hex or "").strip()
    if not raw:
        return _FALLBACK_HKDF_SALT
    return bytes.fromhex(raw)


def _derive_key(material: str, version: int) -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_AES_KEY_BYTES,
        salt=_resolve_hkdf_salt(),
        info=f"redis_stash|v{version}".encode("ascii"),
    )
    return hkdf.derive(material.encode())


def _key_for_version(version: int) -> bytes:
    settings = get_settings()
    if version == settings.redis_stash_encryption_key_version:
        return _derive_key(settings.redis_stash_encryption_key, version)
    env_name = f"REDIS_STASH_ENCRYPTION_KEY__v{version}"
    legacy = os.environ.get(env_name)
    if not legacy:
        raise AppException(
            error_code="REDIS_STASH_KEY_MISSING",
            message=f"No key configured for stash ciphertext version v{version}",
            details={"env": env_name},
        )
    return _derive_key(legacy, version)


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def stash_id_from_key(key: str) -> str:
    """Достать stash-id из Redis-ключа (последний сегмент после `:`).

    Mirror server_service'а. См. docstring там.
    """
    if ":" not in key:
        return key
    return key.rsplit(":", 1)[1]


def aad_for_redis_stash(stash_id: str) -> bytes:
    """AAD для stash-токена. Формат — `"redis_stash|<stash_id>"`.

    Mirror server_service'а. stash_id — хвост Redis-ключа (без префикса
    `dbos:prepare_creds:` / `dbos:dispatch_creds:`). Worker извлекает его
    из ключа, по которому уже идёт чтение, — отдельной координации с
    server_service не нужно.
    """
    return f"redis_stash|{stash_id}".encode()


def encrypt_stash(plaintext: str, *, aad: bytes) -> str:
    """Зеркальный encrypt — нужен только тестам round-trip'а воркера."""
    if plaintext is None:
        raise AppException(
            error_code="STASH_ENCRYPT_INPUT_INVALID",
            message="Cannot encrypt None",
        )
    settings = get_settings()
    version = settings.redis_stash_encryption_key_version
    key = _key_for_version(version)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), aad)
    return f"v{version}${_b64e(nonce)}${_b64e(ciphertext)}"


def decrypt_stash(token: str, *, aad: bytes) -> str:
    """Расшифровать stash-token из Redis. `aad` обязан совпадать с encrypt-стороной.

    Поднимает `AppException`:

    * STASH_TOKEN_INVALID — пустой/без version-префикса (corrupt в Redis
      либо writer накатил plaintext в обход encrypt'а),
    * STASH_TOKEN_MALFORMED — не разбирается на `v<N>$nonce$ct`,
    * STASH_DECRYPT_FAILED — битый base64, чужой AAD, подмена ct,
    * REDIS_STASH_KEY_MISSING — версия в токене не имеет ключа в env,
    * STASH_DECRYPT_INTERNAL_ERROR — неожиданный сбой crypto-стека.

    Caller (handler) ловит AppException и поднимает её через _runner →
    task FAILED с понятным error_code, без silent-fallback на plaintext.
    """
    if not token or not token.startswith("v"):
        raise AppException(
            error_code="STASH_TOKEN_INVALID",
            message="Stash token has no version prefix",
        )
    try:
        version_part, nonce_b64, ct_b64 = token.split("$", 2)
        version = int(version_part[1:])
    except (ValueError, IndexError) as exc:
        raise AppException(
            error_code="STASH_TOKEN_MALFORMED",
            message="Cannot parse stash token",
        ) from exc
    key = _key_for_version(version)
    try:
        nonce_bytes = _b64d(nonce_b64)
        ct_bytes = _b64d(ct_b64)
    except (ValueError, TypeError) as exc:
        raise AppException(
            error_code="STASH_DECRYPT_FAILED",
            message=f"Failed to decrypt stash token: {type(exc).__name__}",
        ) from exc
    try:
        plaintext = AESGCM(key).decrypt(nonce_bytes, ct_bytes, aad)
    except (InvalidTag, ValueError) as exc:
        raise AppException(
            error_code="STASH_DECRYPT_FAILED",
            message=f"Failed to decrypt stash token: {type(exc).__name__}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise AppException(
            error_code="STASH_DECRYPT_INTERNAL_ERROR",
            message=f"Internal stash decrypt error: {type(exc).__name__}",
        ) from exc
    return plaintext.decode("utf-8")
