"""Use cases настроек интеграции отдела с Jira/Zephyr/Confluence (§2.4, §3.5 плана миграции).

Устройство зеркалит `department_test_settings.py`: чтение открыто любому
аутентифицированному актору (отсутствие строки — не 404, а дефолт с пустыми
полями), запись — под матрицей `(department_integration_settings, *, update)`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError
from src.dependencies.auth import Identity
from src.models import DepartmentIntegrationSettings
from src.repositories import department_integration_settings as repo
from src.schemas.department_integration_settings import DepartmentIntegrationSettingsUpdate
from src.services import audit_service, permissions
from src.utils.ids import department_integration_settings_id as new_id


async def get_effective(db: AsyncSession, department_id: str) -> dict:
    """Эффективные настройки отдела — пустые поля, если строки ещё нет."""
    row = await repo.get_by_department(db, department_id)
    if row is None:
        return {
            "id": None,
            "department_id": department_id,
            "credential_id": None,
            "jira_base_url": None,
            "confluence_base_url": None,
            "created_at": None,
            "updated_at": None,
        }
    return {
        "id": row.id,
        "department_id": row.department_id,
        "credential_id": row.credential_id,
        "jira_base_url": row.jira_base_url,
        "confluence_base_url": row.confluence_base_url,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def upsert(
    db: AsyncSession,
    identity: Identity,
    department_id: str,
    payload: DepartmentIntegrationSettingsUpdate,
) -> DepartmentIntegrationSettings:
    """PUT — создаёт строку при первом вызове, иначе обновляет заданные поля."""
    try:
        await permissions.require_action(
            db, identity, EntityType.DEPARTMENT_INTEGRATION_SETTINGS, Action.UPDATE,
        )
    except AuthorizationError:
        audit_service.emit(
            "department_integration_settings.update",
            target_id=department_id, target_type="department_integration_settings",
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
            "credential_id": changes.pop("credential_id", None),
            "jira_base_url": changes.pop("jira_base_url", None),
            "confluence_base_url": changes.pop("confluence_base_url", None),
        }
        row = await repo.create(db, data)
    elif changes:
        await repo.update(db, row, changes)

    await db.commit()
    await db.refresh(row)
    audit_service.emit(
        "department_integration_settings.update",
        target_id=department_id, target_type="department_integration_settings",
        status="success", allowed=True,
        details={"fields": list(changes.keys())},
    )
    return row
