"""Рантайм-ротация мастер-ключа шифрования без простоя.

Строится поверх KeyStore + reencrypt-outbox'а, новых таблиц не вводит.

Ротация (`rotate`):

1. Вычисляем новую версию = ``max(known) + 1`` (монотонно растёт).
2. ``KeyStore.set_key(new_version, new_key_b64)`` — материал доступен для
   деривации.
3. ``KeyStore.set_active(new_version)`` — новые токены сразу пишутся под
   новой версией; старые остаются читаемыми (их версия в wire-префиксе,
   материал ещё в keystore). Это и есть dual-version read без гонки.
4. ``seed_outbox`` — публикуем pending-задачи на лёгкую ре-шифрацию старых
   строк фоном.

Идемпотентность: повторный вызов с тем же ``new_key_b64``, что уже лежит под
активной версией, не плодит новую версию — возвращает текущее состояние.

Retire (`retire`): убрать старую версию из keystore. Разрешён, только если в
БД 0 строк на этой версии (`status().by_version`) и она не активна.
"""

from __future__ import annotations

import base64
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppException
from src.core.keystore import get_keystore
from src.services import audit_service, secrets_migration_service

logger = logging.getLogger(__name__)

_AES_KEY_BYTES = 32


def _validate_key_b64(key_b64: str) -> None:
    """Проверить, что material — base64 ровно 32 байт (AES-256)."""
    try:
        raw = base64.b64decode(key_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise AppException(
            http_status=422,
            error_code="ROTATE_KEY_INVALID",
            message="new_key_b64 is not valid base64",
        ) from exc
    if len(raw) != _AES_KEY_BYTES:
        raise AppException(
            http_status=422,
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

    # Идемпотентность: если активный материал уже совпадает с присланным —
    # ротация уже состоялась (retryrotation-runner'а), не плодим версию.
    try:
        current_material = ks.get_key(previous_version).decode()
    except AppException:
        current_material = None
    if current_material == new_key_b64:
        return {
            "new_version": previous_version,
            "previous_version": previous_version,
            "seeded": {"inserted": 0, "scanned": 0, "active_version": previous_version},
            "idempotent": True,
        }

    new_version = max(ks.list_versions(), default=previous_version) + 1
    ks.set_key(new_version, new_key_b64)
    ks.set_active(new_version)

    seeded = await secrets_migration_service.seed_outbox(db)
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
        raise AppException(
            http_status=409,
            error_code="KEYSTORE_CANNOT_RETIRE_ACTIVE",
            message=f"Cannot retire active key version v{version}",
            details={"version": version},
        )

    by_version = await secrets_migration_service._count_by_version(db)
    remaining = int(by_version.get(int(version), 0))
    if remaining > 0:
        raise AppException(
            http_status=409,
            error_code="KEYSTORE_VERSION_IN_USE",
            message=(
                f"Cannot retire v{version}: {remaining} row(s) still encrypted "
                "under it (run reencrypt-outbox to completion first)"
            ),
            details={"version": version, "remaining": remaining},
        )

    if version not in ks.list_versions():
        # Уже убрана — идемпотентно.
        return {"version": version, "retired": False, "remaining_on_version": 0}

    ks.remove_key(version)
    return {"version": version, "retired": True, "remaining_on_version": 0}


async def auto_retire_drained_versions(
    db: AsyncSession, *, candidate_versions: list[int] | None = None
) -> list[int]:
    """Автоматически вывести не-активные версии ключа, опустевшие после ре-шифрации.

    Версия выводится только когда на ней не осталось owner-строк И нет
    незакрытых (pending/processing) reencrypt-outbox-row'ов. Активная версия
    не трогается никогда. Сам вывод идёт через :func:`retire` — те же гарды
    (не-активная + 0 строк), плюс дополнительная проверка пустого outbox'а.

    ``candidate_versions`` сужает проверку до конкретных версий (хук из
    finalize_done передаёт версию только что закрытой строки); ``None`` —
    проверить все версии keystore (legacy sync-путь после батча).

    Идемпотентно: уже выведенная версия пропадает из ``ks.list_versions()`` и
    на повторном тике не рассматривается. Возвращает список реально
    выведенных версий.

    Retire durable: keystore пишет removal в backing store (файл 0600 или k8s
    Secret), который на рестарте перечитывается, а не bootstrap'ится из env —
    выведенная версия не воскресает.

    Audit: `encryption.auto_retire` (CRITICAL, actor_type=system).
    """
    ks = get_keystore()
    active = int(ks.get_active_version())
    known = ks.list_versions()
    if candidate_versions is None:
        candidates = list(known)
    else:
        wanted = {int(v) for v in candidate_versions}
        candidates = [v for v in known if int(v) in wanted]

    retired: list[int] = []
    for version in candidates:
        v = int(version)
        if v == active:
            continue
        if await secrets_migration_service.has_rows_on_version(db, v):
            continue
        if await secrets_migration_service.has_open_outbox_on_version(db, v):
            continue
        try:
            result = await retire(db, version=v)
        except AppException:
            # Гонка между нашим EXISTS и пере-счётом в retire (новая строка на
            # версии писаться не может — пишут активной — но остаёмся
            # консервативны): пропускаем, вернёмся на следующем тике.
            continue
        if result.get("retired"):
            audit_service.emit(
                "encryption.auto_retire",
                actor_id=None,
                actor_type="system",
                target_id=None,
                target_type="secret",
                status="success",
                allowed=True,
                details={"version": v, "active_version": active},
            )
            logger.info("auto-retired drained key version v%s", v)
            retired.append(v)
    return retired
