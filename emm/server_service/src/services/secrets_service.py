"""Симметричное шифрование хранимых паролей.

Self-contained token-формат::

    v<key_version>$<base64url_nonce>$<base64url_ciphertext>

`SERVER_ENCRYPTION_KEY` — активный мастер-ключ; старые версии читаются из
`SERVER_ENCRYPTION_KEY__v<N>` env-переменных. Алгоритм — AES-256-GCM:
AEAD с 96-битным nonce, 128-битным auth-tag и AAD-binding'ом
ciphertext'а к owner-row (см. секцию «Associated data» ниже). KDF —
HKDF-SHA256 поверх мастер-ключа. Wire-формат version-prefix'нут, поэтому
схема ключей и алгоритм при необходимости заменяются без re-encrypt'а
существующих строк.

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
import logging
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.exceptions import AppException
from src.core.keystore import get_keystore

logger = logging.getLogger(__name__)


_NONCE_BYTES = 12  # стандартный AES-GCM
_AES_KEY_BYTES = 32  # AES-256
# Fallback HKDF salt — применяется ТОЛЬКО когда `HKDF_SALT_HEX` пуст
# и `APP_ENV` ∈ {dev, test, local}. В production/staging Settings
# не даст стартовать без явного значения.
_FALLBACK_HKDF_SALT = b"dbos-server-service-secrets-dev-fallback"
_LEGACY_KDF_VERSIONS = frozenset({1})  # v1 использовал одношаговый SHA-256


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
    return hashlib.sha256(material.encode()).digest()


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
    return hkdf.derive(material.encode())


def _derive_key(material: str, version: int) -> bytes:
    """Выбрать KDF по wire-формату версии ciphertext'а."""
    if version in _LEGACY_KDF_VERSIONS:
        return _derive_key_legacy(material)
    return _derive_key_hkdf(material, version)


def _key_for_version(version: int) -> bytes:
    """Достать AES-ключ для версии: master-материал из keystore + KDF по версии.

    KeyStore хранит master-материал (то, что раньше лежало в env'е); KDF —
    HKDF для v2+, legacy SHA-256 для v1 — остаётся здесь и выбирается по
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


def aad_for_server_account_password(account_id: str) -> bytes:
    """AAD для `server_accounts.password_encrypted` строки `account_id`.

    Формат — `"server_account_password|server_accounts|<id>"`. Привязывает
    ciphertext к конкретной строке: swap в другую строку → InvalidTag.
    """
    return f"server_account_password|server_accounts|{account_id}".encode()


def aad_for_server_account_ssh_key(account_id: str) -> bytes:
    """AAD для `server_accounts.ssh_private_key_encrypted` строки `account_id`.

    Формат — `"server_account_ssh_key|server_accounts|<id>"`. Отдельный kind от
    пароля, чтобы swap ciphertext'а password↔private_key в одной и той же
    строке тоже отбивался InvalidTag.
    """
    return f"server_account_ssh_key|server_accounts|{account_id}".encode()


def aad_for_server_mgmt_ssh_key(server_id: str) -> bytes:
    """AAD для `servers.mgmt_ssh_private_key_encrypted` строки `server_id`.

    Формат — `"server_mgmt_ssh_key|servers|<id>"`. Привязывает ciphertext к
    конкретному серверу: swap приватного ключа в другую строку → InvalidTag.
    Отдельный kind от mgmt-пароля, чтобы swap privkey↔password в одной строке
    тоже отбивался.
    """
    return f"server_mgmt_ssh_key|servers|{server_id}".encode()


def aad_for_server_mgmt_password(server_id: str) -> bytes:
    """AAD для `servers.mgmt_password_encrypted` строки `server_id`.

    Формат — `"server_mgmt_password|servers|<id>"`. Привязывает ciphertext к
    конкретному серверу: swap → InvalidTag.
    """
    return f"server_mgmt_password|servers|{server_id}".encode()


def aad_for_vm_snapshot_password(snapshot_id: str) -> bytes:
    """AAD для `vm_snapshots.mgmt_password_encrypted` строки `snapshot_id`.

    Формат — `"vm_snapshot_password|vm_snapshots|<id>"`. Привязывает ciphertext
    к конкретному снимку: swap кред между снимками → InvalidTag.
    """
    return f"vm_snapshot_password|vm_snapshots|{snapshot_id}".encode()


def aad_for_vm_snapshot_ssh_key(snapshot_id: str) -> bytes:
    """AAD для `vm_snapshots.mgmt_ssh_private_key_encrypted` строки `snapshot_id`.

    Формат — `"vm_snapshot_ssh_key|vm_snapshots|<id>"`. Отдельный kind от
    пароля, чтобы swap password↔private_key в одной строке тоже отбивался.
    """
    return f"vm_snapshot_ssh_key|vm_snapshots|{snapshot_id}".encode()


def aad_for_vm_mgmt_ssh_key(vm_id: str) -> bytes:
    """AAD для `vms.mgmt_ssh_private_key_encrypted` строки `vm_id`.

    Формат — `"vm_mgmt_ssh_key|vms|<id>"`. Привязывает ciphertext к конкретной
    ВМ; отдельный kind от mgmt-пароля, чтобы swap privkey↔password в одной
    строке ловился InvalidTag'ом.
    """
    return f"vm_mgmt_ssh_key|vms|{vm_id}".encode()


def aad_for_vm_mgmt_password(vm_id: str) -> bytes:
    """AAD для `vms.mgmt_password_encrypted` строки `vm_id`.

    Формат — `"vm_mgmt_password|vms|<id>"`. Привязывает ciphertext к
    конкретной ВМ: swap между ВМ → InvalidTag.
    """
    return f"vm_mgmt_password|vms|{vm_id}".encode()


def aad_for_acs_password(settings_id: str) -> bytes:
    """AAD для `acs_settings.acs_password_encrypted` строки `settings_id`.

    Формат — `"acs_password|acs_settings|<id>"`. Singleton-таблица (один ряд,
    `settings_id == SINGLETON_ID`), но привязка к id всё равно держит формат
    единообразным с остальными AAD-хелперами и защищает от swap, если singleton
    когда-нибудь перестанет быть единственной строкой.
    """
    return f"acs_password|acs_settings|{settings_id}".encode()


def aad_for_os_version_bootstrap_password(os_version_id: str) -> bytes:
    """AAD для `os_version_bootstrap_passwords.password_encrypted` строки `os_version_id`.

    Формат — `"os_version_bootstrap_password|os_version_bootstrap_passwords|<id>"`.
    Привязывает ciphertext к конкретной версии каталога ОС: swap пароля между
    версиями → InvalidTag.
    """
    return (
        f"os_version_bootstrap_password|os_version_bootstrap_passwords|"
        f"{os_version_id}"
    ).encode()


def aad_for_ipmi_credential(controller_id: str) -> bytes:
    """AAD для `ipmi_controllers.password_encrypted` строки `controller_id`.

    Формат — `"ipmi_credential|ipmi_controllers|<id>"`. Привязывает
    ciphertext к конкретному BMC-row: swap → InvalidTag.
    """
    return f"ipmi_credential|ipmi_controllers|{controller_id}".encode()


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
            http_status=422,
            error_code="ENCRYPT_INPUT_INVALID",
            message="Cannot encrypt None",
        )
    version = get_keystore().get_active_version()
    if version in _LEGACY_KDF_VERSIONS:
        # Legacy SHA-256 KDF — только для расшифровки старых v1-токенов.
        # Активной версией он быть не должен: иначе свежий encrypt молча
        # лёг бы на слабую деривацию. Ловим misconfig (active=v1) явно.
        raise AppException(
            http_status=500,
            error_code="ENCRYPTION_LEGACY_KDF_ACTIVE",
            message=f"Refusing to encrypt with legacy KDF version v{version}",
            details={"version": version},
        )
    key = _key_for_version(version)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), aad)
    return f"v{version}${_b64e(nonce)}${_b64e(ciphertext)}"


@dataclass(frozen=True)
class DecryptResult:
    """Результат расшифровки токена с метаданными о версии ключа.

    Используется call-site'ами, которые хотят lazy re-encrypt: если
    ``needs_reencrypt=True``, ciphertext был зашифрован устаревшей версией
    мастер-ключа и должен быть переписан активной — обычно через
    :func:`lazy_reencrypt_owner_column`.

    * ``plaintext`` — декодированный UTF-8.
    * ``source_version`` — версия ключа, под которой был зашифрован token.
    * ``needs_reencrypt`` — ``source_version != active_version``.
    """

    plaintext: str
    source_version: int
    needs_reencrypt: bool


def decrypt_with_meta(token: str, *, aad: bytes) -> DecryptResult:
    """Расшифровать token и вернуть результат с метаданными о версии.

    Семантика ошибок идентична :func:`decrypt` (тот же набор error_code'ов).
    Использовать там, где нужен сигнал ``needs_reencrypt`` для lazy-миграции.
    """
    if not token or not token.startswith("v"):
        raise AppException(
            http_status=422,
            error_code="ENCRYPTED_TOKEN_INVALID",
            message="Encrypted token has no version prefix",
        )
    try:
        version_part, nonce_b64, ct_b64 = token.split("$", 2)
        version = int(version_part[1:])
    except (ValueError, IndexError) as exc:
        raise AppException(
            http_status=422,
            error_code="ENCRYPTED_TOKEN_MALFORMED",
            message="Cannot parse encrypted token",
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
        # неправильной длины nonce и подобных format-нарушений. Всё это —
        # input-ошибки: AEAD корректно отрабатывает, просто данные не те.
        raise AppException(
            http_status=422,
            error_code="DECRYPT_FAILED",
            message=f"Failed to decrypt token: {type(exc).__name__}",
        ) from exc
    except Exception as exc:  # noqa: BLE001 — фоллбэк для непредвиденных
        # Сюда долетит что-то нестандартное из crypto-стека — например, сбой
        # HSM-провайдера (когда он появится) или неожиданное исключение из
        # backend'а cryptography. Это инфраструктура, не входные данные.
        raise AppException(
            http_status=500,
            error_code="DECRYPT_INTERNAL_ERROR",
            message=f"Internal decrypt error: {type(exc).__name__}",
        ) from exc

    active_version = get_keystore().get_active_version()
    return DecryptResult(
        plaintext=plaintext_bytes.decode("utf-8"),
        source_version=version,
        needs_reencrypt=(version != active_version),
    )


def decrypt(token: str, *, aad: bytes) -> str:
    """Расшифровать token и вернуть plaintext.

    Тонкая обёртка над :func:`decrypt_with_meta` для обратной совместимости.
    Новым call-site'ам, где нужна lazy re-encrypt-логика, использовать
    :func:`decrypt_with_meta`.

    `aad` обязан совпадать с тем, что передавался в :func:`encrypt`. Несовпадение
    или подмена ciphertext'а — `DECRYPT_FAILED` (под капотом InvalidTag).

    Классификация ошибок (http_status):

    * 422 — input-validation: пустой/без префикса token, неразбираемый формат,
      битый base64, InvalidTag (несовпадение AAD / подмена ciphertext / битый
      nonce). Caller прислал данные, которые корректный AEAD не принимает.
    * 500 — настоящая инфраструктурная авария: ключ для версии токена не
      сконфигурирован (`ENCRYPTION_KEY_MISSING` из :func:`_key_for_version`),
      либо неожиданное исключение в crypto-стеке (`DECRYPT_INTERNAL_ERROR`).
    """
    return decrypt_with_meta(token, aad=aad).plaintext


# ── Lazy re-encrypt helper ───────────────────────────────────────────────────


_ALLOWED_LAZY_TARGETS: frozenset[tuple[str, str]] = frozenset({
    ("server_accounts", "password_encrypted"),
    ("server_accounts", "ssh_private_key_encrypted"),
    ("ipmi_controllers", "password_encrypted"),
    ("servers", "mgmt_ssh_private_key_encrypted"),
    ("servers", "mgmt_password_encrypted"),
    ("vms", "mgmt_ssh_private_key_encrypted"),
    ("vms", "mgmt_password_encrypted"),
    ("acs_settings", "acs_password_encrypted"),
    ("os_version_bootstrap_passwords", "password_encrypted"),
})


async def lazy_reencrypt_owner_column(
    db: AsyncSession,
    *,
    table: str,
    column: str,
    row_id: str,
    old_blob: str,
    plaintext: str,
    aad: bytes,
) -> bool:
    """Перешифровать одну ячейку под активный ключ через CAS-UPDATE.

    Используется в read-path call-сайтах сразу после успешного
    :func:`decrypt_with_meta` с ``needs_reencrypt=True``. Поведение:

    * encrypt(plaintext) активной версией ключа;
    * ``UPDATE <table> SET <column>=:new WHERE id=:id AND <column>=:old`` —
      CAS-style на переданной сессии. Параллельный rotate / другой
      lazy-победитель оставляет 0 rows affected, мы выходим тихо;
    * ``db.commit()`` после успешного UPDATE'а: read-сессия в FastAPI
      не делает commit штатно, и без него UPDATE откатится при teardown'е.
      Read-endpoint'ы обычно не держат других pending-мутаций, так что
      commit безопасен; если caller'у важно сохранить контроль над
      транзакцией — он должен не дёргать lazy_reencrypt;
    * любая ошибка БД (lock, connection drop) или encrypt — WARNING-лог,
      ``return False``. **Read-path никогда не блокируется** — caller
      продолжает работу с уже полученным plaintext'ом.

    `table`/`column` whitelist'ятся через :data:`_ALLOWED_LAZY_TARGETS` —
    лишний раз режет любые SQL-injection-векторы, даже несмотря на то,
    что параметры приходят из кода, а не из user input'а.

    Возвращает ``True`` при успешном UPDATE'е (1 row affected), ``False`` в
    остальных случаях (concurrent winner / БД-ошибка / 0 rows).
    """
    if (table, column) not in _ALLOWED_LAZY_TARGETS:
        # Защита от опечатки в caller'е: имена столбцов и таблиц захардкожены
        # в коде, и любая комбинация вне whitelist'а — это bug, а не легитимный
        # input.
        logger.warning(
            "lazy_reencrypt: refusing to UPDATE non-whitelisted target "
            "(table=%r column=%r row_id=%s)",
            table, column, row_id,
        )
        return False
    try:
        new_blob = encrypt(plaintext, aad=aad)
    except Exception as exc:  # noqa: BLE001 — read не должен падать
        logger.warning(
            "lazy_reencrypt: encrypt failed for %s.%s row_id=%s err=%s",
            table, column, row_id, type(exc).__name__,
        )
        return False

    stmt = text(
        f"UPDATE {table} SET {column} = :new "
        f"WHERE id = :id AND {column} = :old"
    )
    try:
        result = await db.execute(
            stmt, {"new": new_blob, "id": row_id, "old": old_blob}
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — read не должен падать
        # БД-lock, connection drop, любой другой сбой. Откатываем, чтобы не
        # утянуть с собой транзакцию caller'а, и идём дальше.
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(
            "lazy_reencrypt: UPDATE failed for %s.%s row_id=%s err=%s",
            table, column, row_id, type(exc).__name__,
        )
        return False

    rowcount = int(result.rowcount or 0)
    if rowcount == 0:
        # Concurrent winner (другой read или outbox-worker уже перешифровал
        # эту строку). Штатное состояние при многопоточной работе.
        logger.debug(
            "lazy_reencrypt: 0 rows affected (concurrent winner) for %s.%s row_id=%s",
            table, column, row_id,
        )
        return False
    return True
