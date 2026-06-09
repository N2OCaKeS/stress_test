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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.internal import MigrationStatus


# Postgres regexp_match возвращает text[] — берём первую группу и кастим к int
# одной expression'ой, чтобы caller'у не пришлось парсить строку.
_VERSION_HISTOGRAM_SQL = text(
    """
    SELECT
        (regexp_match(secret_encrypted, '^v(\\d+)\\$'))[1]::int AS version,
        count(*) AS rows
    FROM credentials
    GROUP BY 1
    """
)


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
    return MigrationStatus(
        active_version=active_version,
        total_rows=total,
        by_version=by_version,
        remaining_legacy=remaining_legacy,
        migrated_pct=migrated_pct,
    )
