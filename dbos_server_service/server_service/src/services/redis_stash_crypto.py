"""Envelope-шифрование Redis-stash'а кред между server_service и server_worker.

Зачем отдельный модуль (а не reuse `secrets_service`)
-----------------------------------------------------

`secrets_service.py` шифрует ciphertext'ы, которые ХРАНЯТСЯ в БД сервиса
(`server_accounts.password_encrypted`, `ipmi_controllers.password_encrypted`).
Эти ключи (`SERVER_ENCRYPTION_KEY`) живут только в server_service — worker
не имеет к ним доступа by-design (граница архитектуры).

Redis-stash — другой контур: server_service ПИШЕТ туда plaintext-creds для
dispatch'а (password + ssh_private_key), worker ЧИТАЕТ на каждой попытке.
Обоим сервисам нужен общий symmetric-ключ — поэтому отдельный
`REDIS_STASH_ENCRYPTION_KEY`, симметрично mount'нутый в оба сервиса.

Wire-формат — тот же `v<version>$<nonce_b64>$<ct_b64>` AES-256-GCM + HKDF-SHA256
(см. `secrets_service.py` про детали). AAD обязателен и binding'уется к
`task_id`: stash-токен от task A нельзя подсунуть task B, иначе InvalidTag.
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
# Fallback HKDF salt — применяется ТОЛЬКО когда `HKDF_SALT_HEX` пуст и
# `APP_ENV` ∈ {dev, test, local}. В production/staging Settings не даст
# стартовать без явного значения. Отдельный fallback от secrets_service —
# чтобы случайный reuse одного и того же salt'а между двумя различными
# доменами шифрования (БД и stash) не дал коллизий KDF-output'а.
_FALLBACK_HKDF_SALT = b"dbos-redis-stash-encryption-dev-fallback"


def _resolve_hkdf_salt() -> bytes:
    settings = get_settings()
    raw = (settings.hkdf_salt_hex or "").strip()
    if not raw:
        return _FALLBACK_HKDF_SALT
    return bytes.fromhex(raw)


def _derive_key(material: str, version: int) -> bytes:
    """HKDF-SHA256 → 32 байта. Info-метка изолирует stash от других доменов.

    `info=b"redis_stash|v<N>"` — фиксированный domain-separator: даже если
    кто-то случайно подсунет общий master-key с `secrets_service` (legacy
    misconfig), KDF-output для stash и БД будут разными.
    """
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
            http_status=500,
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


# ── AAD helper ───────────────────────────────────────────────────────────────


def stash_id_from_key(key: str) -> str:
    """Достать stash-id из Redis-ключа (последний сегмент после `:`).

    `dbos:prepare_creds:pcd_abc` → `pcd_abc`; `dbos:dispatch_creds:dcd_xyz` →
    `dcd_xyz`. Если разделителя нет — возвращаем ключ целиком (defense-in-depth
    на случай тестового stash'а без префикса).
    """
    if ":" not in key:
        return key
    return key.rsplit(":", 1)[1]


def aad_for_redis_stash(stash_id: str) -> bytes:
    """AAD для stash-токена: binding'уется к stash-id (последний сегмент ключа).

    Формат — `"redis_stash|<stash_id>"`. Stash_id — это хвост Redis-ключа
    (`dbos:prepare_creds:<pcd_id>` → `pcd_id`, `dbos:dispatch_creds:<dcd_id>`
    → `dcd_id`, либо просто task_id для in-worker rotate-stash'ей). Worker
    знает stash_id по Redis-ключу, который сам и читает — без отдельного
    канала task_id↔stash_id.

    Зачем binding: с write-доступом к Redis злоумышленник мог бы скопировать
    stash-токен от ключа A в ключ B (swap-attack) — decrypt с AAD от ключа B
    поднимет InvalidTag, chpasswd / useradd с чужими кредами не сработает.
    """
    return f"redis_stash|{stash_id}".encode()


# ── Encrypt / Decrypt ────────────────────────────────────────────────────────


def encrypt_stash(plaintext: str, *, aad: bytes) -> str:
    """Зашифровать stash-payload активной версией ключа.

    Возвращает self-contained token `v<version>$<nonce>$<ct>`. Decrypt того же
    token'а с другим `aad` (или без него) поднимет InvalidTag — это swap-attack
    mitigation.
    """
    if plaintext is None:
        raise AppException(
            http_status=422,
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
    """Расшифровать stash-token. `aad` обязан совпадать с encrypt-стороной.

    Классификация ошибок симметрична `secrets_service.decrypt`:

    * 422 STASH_TOKEN_INVALID/MALFORMED/DECRYPT_FAILED — caller прислал
      данные, которые корректный AEAD не принимает (битый base64, чужой
      AAD, подмена ct, неверная версия с известным missing key).
    * 500 STASH_DECRYPT_INTERNAL_ERROR — неожиданный сбой crypto-стека.
    """
    if not token or not token.startswith("v"):
        raise AppException(
            http_status=422,
            error_code="STASH_TOKEN_INVALID",
            message="Stash token has no version prefix",
        )
    try:
        version_part, nonce_b64, ct_b64 = token.split("$", 2)
        version = int(version_part[1:])
    except (ValueError, IndexError) as exc:
        raise AppException(
            http_status=422,
            error_code="STASH_TOKEN_MALFORMED",
            message="Cannot parse stash token",
        ) from exc
    key = _key_for_version(version)
    try:
        nonce_bytes = _b64d(nonce_b64)
        ct_bytes = _b64d(ct_b64)
    except (ValueError, TypeError) as exc:
        raise AppException(
            http_status=422,
            error_code="STASH_DECRYPT_FAILED",
            message=f"Failed to decrypt stash token: {type(exc).__name__}",
        ) from exc
    try:
        plaintext = AESGCM(key).decrypt(nonce_bytes, ct_bytes, aad)
    except (InvalidTag, ValueError) as exc:
        raise AppException(
            http_status=422,
            error_code="STASH_DECRYPT_FAILED",
            message=f"Failed to decrypt stash token: {type(exc).__name__}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise AppException(
            http_status=500,
            error_code="STASH_DECRYPT_INTERNAL_ERROR",
            message=f"Internal stash decrypt error: {type(exc).__name__}",
        ) from exc
    return plaintext.decode("utf-8")
