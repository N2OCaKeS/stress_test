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

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppException, BadRequestError, ConflictError
from src.core.keystore import get_keystore
from src.models import ReencryptOutboxEntry, RetiredKeyVersion
from src.services import (
    migration_status_service,
    reencrypt_outbox_service,
    reencrypt_state_service,
)

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


async def rotate(
    db: AsyncSession, *, new_key_b64: str, mode: str = "lazy"
) -> dict:
    """Ввести новую версию ключа активной и засидить outbox.

    `mode`:

    * ``lazy`` (дефолт) — фоновая перешифровка без простоя. self-drain loop
      растащит outbox в троттлинг-режиме;
    * ``force`` — включить maintenance-gate: пока перешифровка не завершена,
      сервис отдаёт 503 на все запросы (кроме status/health/ready), а drain
      идёт плотным циклом. Флаг force персистентный (переживает рестарт).

    Возвращает ``{new_version, previous_version, seeded, idempotent, mode}``.
    """
    if mode not in ("lazy", "force"):
        raise BadRequestError(
            error_code="ROTATE_MODE_INVALID",
            message="mode must be 'lazy' or 'force'",
        )

    _validate_key_b64(new_key_b64)
    ks = get_keystore()
    previous_version = ks.get_active_version()

    try:
        current_material = ks.get_key(previous_version).decode()
    except AppException:
        current_material = None
    if current_material == new_key_b64:
        # Материал уже активен — версию не плодим и force-окно не открываем
        # (перешифровывать нечего).
        return {
            "new_version": previous_version,
            "previous_version": previous_version,
            "seeded": {
                "inserted": 0,
                "scanned": 0,
                "active_version": previous_version,
            },
            "idempotent": True,
            "mode": "lazy",
        }

    new_version = max(ks.list_versions(), default=previous_version) + 1
    ks.set_key(new_version, new_key_b64)
    ks.set_active(new_version)

    seeded = await reencrypt_outbox_service.seed_outbox(db)

    effective_mode = mode
    if mode == "force":
        # Открываем force-окно только если реально есть что перешифровывать —
        # иначе gate заблокировал бы сервис в пустую (drain снял бы флаг на
        # первом же тике, но при выключенном drain'е — навсегда).
        status = await migration_status_service.compute(
            db, active_version=new_version
        )
        if int(status.remaining_legacy) > 0:
            await reencrypt_state_service.enter_force(
                db, remaining=int(status.remaining_legacy)
            )
        else:
            effective_mode = "lazy"

    return {
        "new_version": new_version,
        "previous_version": previous_version,
        "seeded": seeded,
        "idempotent": False,
        "mode": effective_mode,
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
    await _record_tombstone(db, version)
    return {"version": version, "retired": True, "remaining_on_version": 0}


async def _record_tombstone(db: AsyncSession, version: int) -> None:
    """Зафиксировать факт вывода версии в `retired_key_versions`.

    Делает keystore-retire durable: даже если файл keystore исчезнет и
    bootstrap воскресит версию из env, startup-reconcile сверится с этой
    таблицей и снова её вычистит. ON CONFLICT DO NOTHING — повтор retire'а
    той же версии не падает.
    """
    ins = (
        pg_insert(RetiredKeyVersion.__table__)
        .values(version=int(version))
        .on_conflict_do_nothing(index_elements=["version"])
    )
    await db.execute(ins)
    await db.commit()


async def _pending_outbox_for_version(db: AsyncSession, version: int) -> int:
    """Сколько pending outbox-задач seed'илось с этой версии как source."""
    stmt = select(func.count(ReencryptOutboxEntry.id)).where(
        ReencryptOutboxEntry.status == reencrypt_outbox_service.STATUS_PENDING,
        ReencryptOutboxEntry.source_version == int(version),
    )
    return int((await db.execute(stmt)).scalar_one())


async def auto_retire_drained(db: AsyncSession) -> list[dict]:
    """Вывести все не-активные версии, на которых не осталось строк.

    Зовётся после `reencrypt_outbox_service.process_batch`: как только версия
    опустела (0 строк под ней и нет pending-задач reencrypt-outbox'а с этой
    версией как source), её материал больше не нужен — убираем из keystore.
    Гарды наследуются у `retire`: активную версию не трогаем никогда,
    retire'им только при реальном 0 строк.

    Возвращает список `{version, retired}` по фактически выведенным версиям.
    Идемпотентно: после вывода версия уходит из keystore, повторный tick её
    уже не увидит.
    """
    ks = get_keystore()
    active = int(ks.get_active_version())
    status = await migration_status_service.compute(db, active_version=active)

    retired: list[dict] = []
    for version in ks.list_versions():
        if int(version) == active:
            continue
        if int(status.by_version.get(str(int(version)), 0)) > 0:
            continue
        if await _pending_outbox_for_version(db, version) > 0:
            # Версия пуста по строкам, но в очереди ещё висят задачи с ней —
            # дожидаемся, пока worker их закроет, и только потом выводим.
            continue
        result = await retire(db, version=version)
        if result["retired"]:
            retired.append({"version": int(version), "retired": True})
    return retired


async def reconcile_tombstones(db: AsyncSession) -> list[int]:
    """Вычистить из keystore версии, помеченные выведенными в БД.

    Стартовый хук durability: если keystore воскресил выведенную версию из
    env (файл на emptyDir пропал при рестарте пода), сверяемся с
    `retired_key_versions` и убираем материал снова. Активную версию не
    трогаем — это защита от рассинхрона, когда активной зачем-то оказалась
    помеченная версия (тогда оставляем как есть, чтобы не потерять read-path).

    Возвращает список реально вычищенных версий.
    """
    ks = get_keystore()
    active = int(ks.get_active_version())
    rows = (await db.execute(select(RetiredKeyVersion.version))).scalars().all()
    tombstoned = {int(v) for v in rows}

    pruned: list[int] = []
    for version in ks.list_versions():
        if int(version) == active:
            continue
        if int(version) in tombstoned:
            ks.remove_key(version)
            pruned.append(int(version))
    if pruned:
        logger.warning(
            "keystore reconcile: pruned resurrected retired versions %s", pruned,
        )
    return pruned
