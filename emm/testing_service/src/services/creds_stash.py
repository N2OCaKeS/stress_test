"""Одноразовый Redis-стэш кред тестового пользователя между callback'ом
prepare-for-test и claim'ом `testing_worker`а (§5.1, §5.5 плана миграции).

В Redis лежит не открытый JSON, а токен `v<version>$<nonce>$<ciphertext>`:
AES-256-GCM, ключ выводится через HKDF-SHA256 из `CREDS_STASH_ENCRYPTION_KEY`
(схема та же, что у Redis-стэша server_service). Nonce случайный на каждую
запись. AAD = `creds_stash|<stash_key>`: токен, скопированный под другой
Redis-ключ, не расшифруется. Ключ у этого стэша свой, не
`REDIS_STASH_ENCRYPTION_KEY` server_service: читает и пишет его только
testing_service.

Переиспользуем тот же Redis, что и брокер `testing_worker` (`REDIS_URL`) —
отдельного контейнера под это заводить не стали, ключи различает префикс.

Записи, лежащие в Redis после выкатки этой версии в старом формате
(открытый JSON), нечитаемы: `pop_creds` возвращает None, как для истёкшей
записи, и пишет в лог причину. Вызывающий код (`claim`) переводит такой
элемент очереди в failed «creds stash missing or expired», дальше он идёт
через обычный retry с новым prepare-for-test. Повреждённая запись, запись
под чужим ключом и запись, зашифрованная неизвестным ключом, обрабатываются
так же. Чтение одноразовое: нечитаемая запись удаляется вместе с прочтением.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid

import redis.asyncio as aioredis
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.core.config import get_settings

logger = logging.getLogger("testing_service.creds_stash")

_KEY_PREFIX = "testing:queue_creds:"
_NONCE_BYTES = 12
_AES_KEY_BYTES = 32
# Соль не секретна и общая для всех записей; изоляция доменов держится на info.
_HKDF_SALT = b"dbos-testing-creds-stash-v1"
# Только для dev/test при пустом CREDS_STASH_ENCRYPTION_KEY. В
# production/staging Settings не даёт стартовать без настоящего ключа.
_DEV_FALLBACK_KEY = "dev-creds-stash-encryption-key-do-not-use-in-prod"


class CredsStashError(Exception):
    """Запись стэша не удалось зашифровать или расшифровать."""


def new_stash_key(queue_item_id: str) -> str:
    """Новый одноразовый ключ для конкретного элемента очереди."""
    return f"{_KEY_PREFIX}{queue_item_id}:{uuid.uuid4().hex}"


def _client() -> aioredis.Redis:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    settings = get_settings()
    return aioredis.from_url(settings.redis_url)


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))


def _aad(stash_key: str) -> bytes:
    return f"creds_stash|{stash_key}".encode()


def _derive_key(material: str, version: int) -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_AES_KEY_BYTES,
        salt=_HKDF_SALT,
        info=f"creds_stash|v{version}".encode("ascii"),
    )
    return hkdf.derive(material.encode())


def _key_for_version(version: int) -> bytes:
    settings = get_settings()
    if version == settings.creds_stash_encryption_key_version:
        material = settings.creds_stash_encryption_key
        if not material:
            logger.warning(
                "CREDS_STASH_ENCRYPTION_KEY is empty (%s): using the built-in dev key",
                settings.app_env,
            )
            material = _DEV_FALLBACK_KEY
        return _derive_key(material, version)
    legacy = os.environ.get(f"CREDS_STASH_ENCRYPTION_KEY__v{version}")
    if not legacy:
        raise CredsStashError(f"no key configured for creds stash version v{version}")
    return _derive_key(legacy, version)


def encrypt_creds(creds: dict, *, stash_key: str) -> str:
    """Сериализовать и зашифровать креды активной версией ключа."""
    version = get_settings().creds_stash_encryption_key_version
    nonce = os.urandom(_NONCE_BYTES)
    plaintext = json.dumps(creds).encode()
    ciphertext = AESGCM(_key_for_version(version)).encrypt(nonce, plaintext, _aad(stash_key))
    return f"v{version}${_b64e(nonce)}${_b64e(ciphertext)}"


def decrypt_creds(token: str | bytes, *, stash_key: str) -> dict:
    """Расшифровать токен, прочитанный под `stash_key`.

    Поднимает `CredsStashError` для записи в старом (нешифрованном) формате,
    битого токена, неизвестной версии ключа, неверного ключа и токена,
    сохранённого под другим Redis-ключом.
    """
    if isinstance(token, bytes):
        try:
            token = token.decode("ascii")
        except UnicodeDecodeError as exc:
            raise CredsStashError("creds stash entry is not a valid token") from exc
    if not token.startswith("v"):
        raise CredsStashError("creds stash entry is in the legacy unencrypted format")
    try:
        version_part, nonce_b64, ct_b64 = token.split("$", 2)
        version = int(version_part[1:])
        nonce = _b64d(nonce_b64)
        ciphertext = _b64d(ct_b64)
    except ValueError as exc:
        raise CredsStashError("creds stash entry is malformed") from exc
    key = _key_for_version(version)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(stash_key))
    except (InvalidTag, ValueError) as exc:
        raise CredsStashError(
            "creds stash entry failed authentication (wrong key or entry moved between keys)"
        ) from exc
    try:
        creds = json.loads(plaintext)
    except ValueError as exc:
        raise CredsStashError("creds stash payload is not valid JSON") from exc
    if not isinstance(creds, dict):
        raise CredsStashError("creds stash payload is not an object")
    return creds


async def store_creds(stash_key: str, creds: dict) -> None:
    """Положить зашифрованные креды под `stash_key` с TTL `creds_stash_ttl_seconds`."""
    settings = get_settings()
    token = encrypt_creds(creds, stash_key=stash_key)
    client = _client()
    try:
        await client.set(stash_key, token, ex=settings.creds_stash_ttl_seconds)
    finally:
        await client.aclose()


async def pop_creds(stash_key: str) -> dict | None:
    """Прочитать, расшифровать и удалить креды одной операцией.

    None — ключ не найден/истёк либо запись нечитаема (старый формат, чужой
    ключ, повреждение); во втором случае причина пишется в лог. Запись в
    любом случае удалена.

    Одноразовое чтение — `GETDEL` (Redis >= 6.2, версия здесь `^6.0`, что
    указывает на протокол клиента, не на сервер; предполагается совместимый
    Redis-сервер). Если `GETDEL` недоступен на сервере — fallback на
    GET+DEL, менее атомарный, но для этого сценария (одна очередь, один
    читатель) гонки не создаёт.
    """
    client = _client()
    try:
        try:
            raw = await client.execute_command("GETDEL", stash_key)
        except Exception:  # noqa: BLE001 — старый Redis без GETDEL
            raw = await client.get(stash_key)
            if raw is not None:
                await client.delete(stash_key)
    finally:
        await client.aclose()
    if raw is None:
        return None
    try:
        return decrypt_creds(raw, stash_key=stash_key)
    except CredsStashError as exc:
        logger.error("Discarding unreadable creds stash entry %s: %s", stash_key, exc)
        return None
