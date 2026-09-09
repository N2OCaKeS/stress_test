"""Use cases настроек тестирования отдела (§2.4 плана миграции).

Чтение открыто любому аутентифицированному актору (как `test_definition`/
`test_stand`) — это не секрет, а рабочая конфигурация очереди. Отсутствие
строки в БД не 404: сервис отдаёт дефолты (`DEFAULT_*`), реальная строка
появляется только на первый `PUT`. Запись — под матрицей прав
`(department_test_settings, *, update)`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError
from src.dependencies.auth import Identity
from src.models import DepartmentTestSettings
from src.repositories import department_test_settings as repo
from src.schemas.department_test_settings import DepartmentTestSettingsUpdate
from src.services import audit_service, permissions
from src.utils.ids import department_test_settings_id as new_id

DEFAULT_RETRY_ENABLED = True
DEFAULT_TEST_USERNAME = "u"


async def get_settings_row(db: AsyncSession, department_id: str) -> DepartmentTestSettings | None:
    """SELECT сырой строки, без подстановки дефолтов. `None` — штатный случай."""
    return await repo.get_by_department(db, department_id)


async def get_effective(db: AsyncSession, department_id: str) -> dict:
    """Эффективные настройки отдела — дефолты, если строки ещё нет.

    Используется и API-эндпоинтом (обёртка в `DepartmentTestSettingsResponse`),
    и сервисом очереди (`services/queue.py`), которому нужны только
    `retry_enabled`/`test_username`, без Pydantic-обёртки.
    """
    row = await repo.get_by_department(db, department_id)
    if row is None:
        return {
            "id": None,
            "department_id": department_id,
            "retry_enabled": DEFAULT_RETRY_ENABLED,
            "test_username": DEFAULT_TEST_USERNAME,
            "activity_report_schedule": None,
            "created_at": None,
            "updated_at": None,
        }
    return {
        "id": row.id,
        "department_id": row.department_id,
        "retry_enabled": row.retry_enabled,
        "test_username": row.test_username,
        "activity_report_schedule": row.activity_report_schedule,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def upsert(
    db: AsyncSession,
    identity: Identity,
    department_id: str,
    payload: DepartmentTestSettingsUpdate,
) -> DepartmentTestSettings:
    """PUT — создаёт строку при первом вызове, иначе обновляет заданные поля."""
    try:
        await permissions.require_action(
            db, identity, EntityType.DEPARTMENT_TEST_SETTINGS, Action.UPDATE,
        )
    except AuthorizationError:
        audit_service.emit(
            "department_test_settings.update",
            target_id=department_id, target_type="department_test_settings",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    changes = payload.model_dump(exclude_unset=True, mode="json")
    row = await repo.get_by_department(db, department_id)
    if row is None:
        data = {
            "id": new_id(),
            "department_id": department_id,
            "retry_enabled": changes.pop("retry_enabled", DEFAULT_RETRY_ENABLED),
            "test_username": changes.pop("test_username", DEFAULT_TEST_USERNAME),
            "activity_report_schedule": changes.pop("activity_report_schedule", None),
        }
        row = await repo.create(db, data)
    elif changes:
        await repo.update(db, row, changes)

    await db.commit()
    await db.refresh(row)
    audit_service.emit(
        "department_test_settings.update",
        target_id=department_id, target_type="department_test_settings",
        status="success", allowed=True,
        details={"fields": list(changes.keys())},
    )
    return row
