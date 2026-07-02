"""Подсчёт прогресса lazy re-encrypt'а под активную версию мастер-ключа.

Endpoint `/internal/migration_status` дёргает :func:`compute` и отдаёт
ротационному скрипту счётчики по wire-версии ciphertext'а. Главное свойство —
`remaining_legacy == 0` означает, что все строки уже под текущим ключом и
старый `SECRET_ENCRYPTION_KEY__v<N>` можно дропнуть.

Запрос идёт одной SQL-агрегатной: `regexp_match` на префиксе `v<N>$`,
GROUP BY по версии. CHECK на формат на БД гарантирует, что префикс есть
у каждой строки — `regexp_match` не вернёт NULL.
"""

from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.keystore import get_keystore
from src.models import ReencryptOutboxEntry
from src.schemas.internal import MigrationStatus


# Postgres regexp_match возвращает text[] — берём первую группу и кастим к bigint
# одной expression'ой, чтобы caller'у не пришлось парсить строку. bigint, а не
# int: ::int (INTEGER, max 2^31-1) переполнится на ciphertext с версией
# v9999999999$ или выше; bigint держит до 2^63-1. Регулярка ловит любое
# количество цифр, поэтому ограничения сверху на размер версии нет.
_VERSION_HISTOGRAM_SQL = text(
    """
    SELECT
        (regexp_match(secret_encrypted, '^v(\\d+)\\$'))[1]::bigint AS version,
        count(*) AS rows
    FROM credentials
    GROUP BY 1
    """
)


async def _outbox_pending_count(db: AsyncSession) -> int:
    """Сколько proactive-задач в очереди ожидают обработки.

    Финализация ротации требует и `remaining_legacy == 0`, и
    `outbox_pending_count == 0` — иначе worker дозакроет row уже под
    дропнутым мастер-ключом и упадёт ENCRYPTION_KEY_MISSING.
    """
    stmt = select(func.count(ReencryptOutboxEntry.id)).where(
        ReencryptOutboxEntry.status == "pending"
    )
    return int((await db.execute(stmt)).scalar_one())


async def compute(
    db: AsyncSession, *, active_version: int
) -> MigrationStatus:
    """Собрать гистограмму версий ciphertext'а и посчитать legacy-остаток."""
    result = await db.execute(_VERSION_HISTOGRAM_SQL)
    by_version: dict[str, int] = {}
    total = 0
    remaining_legacy = 0
    for version, rows in result.all():
        # version приходит из БД как int (см. cast в SQL), но на пустой
        # таблице regexp_match не зовётся; защищаемся на NULL, чтобы не
        # уронить endpoint на edge-case'ах с битыми данными.
        if version is None:
            continue
        v_str = str(int(version))
        by_version[v_str] = int(rows)
        total += int(rows)
        if int(version) < active_version:
            remaining_legacy += int(rows)
    if total == 0:
        migrated_pct = 100.0
    else:
        migrated_pct = round((total - remaining_legacy) * 100.0 / total, 2)
    outbox_pending = await _outbox_pending_count(db)

    # Версии в keystore + снапшот force-режима. Read-only, состояние гейта
    # берём из общей singleton-строки (лениво, чтобы не тащить цикл импорта).
    from src.services import reencrypt_state_service

    versions_in_keystore = get_keystore().list_versions()
    state = await reencrypt_state_service.get_state(db)

    return MigrationStatus(
        active_version=active_version,
        total_rows=total,
        by_version=by_version,
        remaining_legacy=remaining_legacy,
        migrated_pct=migrated_pct,
        outbox_pending_count=outbox_pending,
        versions_in_keystore=versions_in_keystore,
        # process_batch клеймит и финализирует строку в одной транзакции —
        # отдельного статуса 'processing' у outbox'а нет, поэтому 0.
        outbox_processing=0,
        mode=state.mode,
        force_active=state.force_active,
        eta_seconds=state.eta_seconds,
        throughput=state.throughput,
    )
