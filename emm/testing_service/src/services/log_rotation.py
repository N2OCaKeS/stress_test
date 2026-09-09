"""Ротация логов прогонов (§8.5 плана миграции).

Три независимых механизма:

* `recompute_protection_for_branch()` — вызывается сразу после того, как
  появляется новый `test_logs` с известным `os_version_major` (см.
  `services/test_log.py::get_or_create_log`), и подстраховочно раз в сутки
  для всех веток из фонового цикла в `main.py`. "Глобальная ветка" трактуется
  практически как `os_version_major` (major-линия релиза, например "1.8") —
  внутри неё разные `rc` (конкретные РЦ/сборки) сравниваются по максимальному
  `created_at` среди принадлежащих им логов, 2 самых свежих помечаются
  `protected=true`, остальные (и логи вовсе без `rc`) — `false`. Пересчёт
  делается на всю ветку целиком, не инкрементально — дёшево при ожидаемом
  объёме логов.
* `enforce_monthly_retention()` — периодическая (раз в сутки, см. `main.py`
  lifespan) чистка: удаляет `protected=false` логи старше
  `settings.log_retention_days`.
* `rotate_duplicate_if_any()` — вызывается синхронно из `services/queue.py::
  enqueue()`. "Тот же test_id+launch_context" трактуется практически как
  совпадение `(test_id, rc, kernel)` — это единственные поля launch_context,
  которые снэпшотятся на `test_logs` (см. `models/test_log.py`); полный
  `launch_context` на логе не хранится, поэтому побайтовое сравнение всего
  словаря недостижимо и не нужно — rc+kernel уже однозначно определяют "тот
  же прогон" в терминах того, что осталось от launch_context к этому моменту.

Удаление лога в обоих сценариях (`rotate_duplicate_if_any`/
`enforce_monthly_retention`) аудируется одним и тем же действием
`test_log.rotated` — это не пользовательское действие, но это необратимое
удаление данных, наблюдаемость важнее шума в audit-канале.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories import test_log as test_log_repo
from src.services import audit_service

logger = logging.getLogger(__name__)


async def recompute_protection_for_branch(db: AsyncSession, os_version_major: str) -> None:
    """2 самых свежих `rc` в этой ветке — protected=true, остальные — false."""
    ranked = await test_log_repo.list_distinct_rc_by_latest(db, os_version_major)
    protected_rcs = {rc for rc, _ in ranked[:2]}
    await test_log_repo.set_protected_for_branch(db, os_version_major, protected_rcs)


async def rotate_duplicate_if_any(db: AsyncSession, test_id: str, launch_context: dict[str, str]) -> int:
    """Немедленная ротация: снести старый незащищённый лог с тем же (test_id, rc, kernel).

    Не коммитит, если кандидатов не нашлось — вызывающий (`queue.enqueue`)
    делает это в рамках своей же транзакции постановки в очередь.
    """
    rc = (launch_context or {}).get("RC")
    kernel = (launch_context or {}).get("KERNEL")
    if not rc or not kernel:
        return 0
    duplicates = await test_log_repo.find_unprotected_duplicates(db, test_id=test_id, rc=rc, kernel=kernel)
    for log in duplicates:
        audit_service.emit(
            "test_log.rotated",
            target_id=log.id, target_type="test_log",
            status="success", allowed=True,
            details={"reason": "duplicate_relaunch", "test_id": test_id, "rc": rc, "kernel": kernel},
        )
        await test_log_repo.delete(db, log)
    return len(duplicates)


async def enforce_monthly_retention(db: AsyncSession, retention_days: int) -> int:
    """Удалить `protected=false` логи старше `retention_days`. Возвращает число удалённых."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    stale = await test_log_repo.find_stale_unprotected(db, cutoff)
    for log in stale:
        audit_service.emit(
            "test_log.rotated",
            target_id=log.id, target_type="test_log",
            status="success", allowed=True,
            details={"reason": "monthly_retention", "created_at": log.created_at.isoformat()},
        )
        await test_log_repo.delete(db, log)
    if stale:
        await db.commit()
    return len(stale)


async def recompute_all_branches(db: AsyncSession) -> None:
    """Подстраховочный полный пересчёт `protected` по всем веткам сразу.

    Используется фоновым суточным циклом (`main.py`) как safety-net поверх
    пересчёта "на лету" при создании лога (`services/test_log.py::
    get_or_create_log`) — на случай, если тот вызов когда-то не отработал
    (сбойный процесс между insert'ом и recompute) или данные попали в БД
    в обход обычного пути (ручной бэкфилл/восстановление). При штатной
    работе результат совпадает с уже выставленными флагами — идемпотентно.
    """
    branches = await test_log_repo.list_distinct_branches(db)
    for branch in branches:
        await recompute_protection_for_branch(db, branch)
    if branches:
        await db.commit()
