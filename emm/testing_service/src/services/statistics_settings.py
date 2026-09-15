"""Use cases платформенных настроек внешнего сервиса статистики (§2.7, §9.3 плана миграции).

Singleton, тот же паттерн, что `AcsSettings` в server_service — один сервис
статистики (ветка `statistics`, `statistics/main_api.py`) на всю платформу,
не per-department. Чтение открыто любому аутентифицированному актору (как
`department_test_settings`), запись — под матрицей `(statistics_settings, *,
update)`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError
from src.dependencies.auth import Identity
from src.models.statistics_settings import StatisticsSettings
from src.repositories import statistics_settings as repo
from src.schemas.statistics_settings import StatisticsSettingsUpdate
from src.services import audit_service, permissions


def _to_dict(row: StatisticsSettings | None) -> dict:
    if row is None:
        return {"enabled": False, "base_url": None}
    return {"enabled": row.enabled, "base_url": row.base_url}


async def get_effective(db: AsyncSession) -> dict:
    """Текущие настройки. Строки ещё нет → дефолты (выключено, ничего не задано)."""
    row = await repo.get_singleton(db)
    return _to_dict(row)


async def update_settings(
    db: AsyncSession, identity: Identity, payload: StatisticsSettingsUpdate,
) -> dict:
    """Upsert настроек (частичное слияние). Аудит `statistics_settings.update`."""
    try:
        await permissions.require_action(db, identity, EntityType.STATISTICS_SETTINGS, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "statistics_settings.update",
            target_type="statistics_settings",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    changes: dict = {}
    if payload.enabled is not None:
        changes["enabled"] = payload.enabled
    if payload.base_url is not None:
        stripped = payload.base_url.strip()
        changes["base_url"] = stripped or None

    row = await repo.upsert(db, changes)
    await db.commit()
    await db.refresh(row)

    audit_service.emit(
        "statistics_settings.update",
        target_type="statistics_settings",
        status="success", allowed=True,
        details={"enabled": row.enabled, "base_url_set": bool(row.base_url)},
    )
    return _to_dict(row)
