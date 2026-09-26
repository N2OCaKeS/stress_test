"""Use cases справочника семейств статистики (D18).

Справочник платформенный, как и `statistics_settings`: сервис статистики один
на всю платформу. Чтение открыто любому аутентифицированному актору (модалка
пересчёта показывается на страницах прогонов и СТП), запись — под тем же
действием матрицы, что настройки статистики и ручной пересчёт:
`(statistics_settings, *, update)`. Отдельного `entity_type` не заводили —
справочник редактируется на той же странице настроек и тем же кругом ролей.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from src.dependencies.auth import Identity
from src.models import StatisticsCategory
from src.repositories import statistics_category as repo
from src.schemas.statistics_category import StatisticsCategoryCreate, StatisticsCategoryUpdate
from src.services import audit_service, permissions
from src.utils.ids import statistics_category_id as new_id

logger = logging.getLogger(__name__)

# Поля, которые в БД NOT NULL: явный `null` в PATCH для них = «не менять».
_NON_NULLABLE_FIELDS = frozenset({
    "label", "path", "title_statistics", "set_of_test_types", "enabled", "sort_order",
})


async def _require_write(db: AsyncSession, identity: Identity, action: str) -> None:
    try:
        await permissions.require_action(db, identity, EntityType.STATISTICS_SETTINGS, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            action,
            target_type="statistics_category",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise


async def list_categories(db: AsyncSession, *, include_disabled: bool = False) -> list[StatisticsCategory]:
    """Справочник в порядке модалки. Read без проверки прав и без аудита."""
    return await repo.list_all(db, enabled_only=not include_disabled)


async def _get_or_404(db: AsyncSession, category_id: str, action: str) -> StatisticsCategory:
    obj = await repo.get_by_id(db, category_id)
    if obj is None:
        audit_service.emit(
            action,
            target_id=category_id, target_type="statistics_category",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="STATISTICS_CATEGORY_NOT_FOUND",
            message="Statistics category not found",
        )
    return obj


async def create_category(
    db: AsyncSession, identity: Identity, payload: StatisticsCategoryCreate,
) -> StatisticsCategory:
    """INSERT семейства. UNIQUE(key) → 409 STATISTICS_CATEGORY_DUPLICATE."""
    await _require_write(db, identity, "statistics_category.create")

    data = payload.model_dump()
    data["id"] = new_id()
    data["created_by"] = identity.user_id
    try:
        obj = await repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на создании statistics_category: %s", type(exc.orig).__name__)
        audit_service.emit(
            "statistics_category.create",
            target_type="statistics_category",
            status="failure", allowed=True,
            details={"reason": "duplicate", "key": payload.key},
        )
        raise ConflictError(
            error_code="STATISTICS_CATEGORY_DUPLICATE",
            message="Statistics category with this key already exists",
            details={"hint": "уникальное поле — key"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "statistics_category.create",
        target_id=obj.id, target_type="statistics_category",
        status="success", allowed=True,
        details={"key": obj.key, "path": obj.path, "enabled": obj.enabled},
    )
    return obj


async def update_category(
    db: AsyncSession, identity: Identity, category_id: str, payload: StatisticsCategoryUpdate,
) -> StatisticsCategory:
    """PATCH семейства. `key` не меняется; пустой диф → возврат без UPDATE."""
    await _require_write(db, identity, "statistics_category.update")
    obj = await _get_or_404(db, category_id, "statistics_category.update")

    changes = {
        field: value
        for field, value in payload.model_dump(exclude_unset=True).items()
        if not (value is None and field in _NON_NULLABLE_FIELDS)
    }
    changes = {field: value for field, value in changes.items() if getattr(obj, field) != value}
    if not changes:
        return obj

    await repo.update(db, obj, changes)
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "statistics_category.update",
        target_id=obj.id, target_type="statistics_category",
        status="success", allowed=True,
        details={"key": obj.key, "fields": sorted(changes)},
    )
    return obj


async def delete_category(db: AsyncSession, identity: Identity, category_id: str) -> None:
    """Hard-delete. Ссылок по FK нет: история в `statistics_recalc_status` — просто строка ключа."""
    await _require_write(db, identity, "statistics_category.delete")
    obj = await _get_or_404(db, category_id, "statistics_category.delete")
    key = obj.key
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "statistics_category.delete",
        target_id=category_id, target_type="statistics_category",
        status="success", allowed=True,
        details={"key": key},
    )
