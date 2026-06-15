"""Рантайм-ротация мастер-ключа secret_service без простоя.

Строится поверх KeyStore + reencrypt-outbox'а, новых таблиц не вводит.
Зеркало `server_service/src/services/key_rotation_service.py`, но крипто
secret_service раздельно (собственный master-key).

Ротация (`rotate`):

1. Новая версия = ``max(known) + 1``.
2. ``KeyStore.set_key(new_version, new_key_b64)`` + ``set_active(new_version)``
   — новые токены сразу под новой версией, старые остаются читаемыми
   (материал старых версий ещё в keystore) → dual-version read без гонки.
3. ``seed_outbox`` публикует pending-задачи на фоновую ре-шифрацию.

Идемпотентность: повтор с уже-активным материалом не плодит версию.

Retire (`retire`): убрать версию из keystore. Только если 0 строк на ней
(`migration_status` histogram) и она не активна.
"""

from __future__ import annotations

import base64
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppException, BadRequestError, ConflictError
from src.core.keystore import get_keystore
from src.services import migration_status_service, reencrypt_outbox_service

logger = logging.getLogger(__name__)

_AES_KEY_BYTES = 32


def _validate_key_b64(key_b64: str) -> None:
    """Проверить, что material — base64 ровно 32 байт (AES-256)."""
    try:
        raw = base64.b64decode(key_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise BadRequestError(
            error_code="ROTATE_KEY_INVALID",
            message="new_key_b64 is not valid base64",
        ) from exc
    if len(raw) != _AES_KEY_BYTES:
        raise BadRequestError(
            error_code="ROTATE_KEY_INVALID",
            message=f"new_key_b64 must decode to {_AES_KEY_BYTES} bytes, got {len(raw)}",
        )


async def rotate(db: AsyncSession, *, new_key_b64: str) -> dict:
    """Ввести новую версию ключа активной и засидить outbox.

    Возвращает ``{new_version, previous_version, seeded, idempotent}``.
    """
    _validate_key_b64(new_key_b64)
    ks = get_keystore()
    previous_version = ks.get_active_version()

    try:
        current_material = ks.get_key(previous_version).decode()
    except AppException:
        current_material = None
    if current_material == new_key_b64:
        return {
            "new_version": previous_version,
            "previous_version": previous_version,
            "seeded": {
                "inserted": 0,
                "scanned": 0,
                "active_version": previous_version,
            },
            "idempotent": True,
        }

    new_version = max(ks.list_versions(), default=previous_version) + 1
    ks.set_key(new_version, new_key_b64)
    ks.set_active(new_version)

    seeded = await reencrypt_outbox_service.seed_outbox(db)
    return {
        "new_version": new_version,
        "previous_version": previous_version,
        "seeded": seeded,
        "idempotent": False,
    }


async def retire(db: AsyncSession, *, version: int) -> dict:
    """Убрать версию из keystore. Только если 0 строк на ней и она не активна.

    Возвращает ``{version, retired, remaining_on_version}``.
    """
    ks = get_keystore()
    active = ks.get_active_version()
    if int(version) == int(active):
        raise ConflictError(
            error_code="KEYSTORE_CANNOT_RETIRE_ACTIVE",
            message=f"Cannot retire active key version v{version}",
            details={"version": version},
        )

    status = await migration_status_service.compute(db, active_version=active)
    remaining = int(status.by_version.get(str(int(version)), 0))
    if remaining > 0:
        raise ConflictError(
            error_code="KEYSTORE_VERSION_IN_USE",
            message=(
                f"Cannot retire v{version}: {remaining} row(s) still encrypted "
                "under it (run reencrypt-outbox to completion first)"
            ),
            details={"version": version, "remaining": remaining},
        )

    if version not in ks.list_versions():
        return {"version": version, "retired": False, "remaining_on_version": 0}

    ks.remove_key(version)
    return {"version": version, "retired": True, "remaining_on_version": 0}
