"""Use cases настроек интеграции отдела с Jira/Zephyr/Confluence (§2.4, §3.5 плана миграции).

Устройство зеркалит `department_test_settings.py`: чтение — своему отделу
(отсутствие строки — не 404, а дефолт с пустыми полями), запись —
department-scoped матрица `(department_integration_settings, *, update)`.
Строка несёт ссылки на кредентиалы отдела в secret_service, поэтому cross-
department доступ закрыт с обеих сторон.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError, DomainValidationError
from src.dependencies.auth import Identity
from src.models import DepartmentIntegrationSettings
from src.repositories import department_integration_settings as repo
from src.schemas.department_integration_settings import DepartmentIntegrationSettingsUpdate
from src.services import audit_service, permissions, secret_client
from src.utils.ids import department_integration_settings_id as new_id


_NULLABLE_FIELDS = (
    "credential_id",
    "jira_base_url",
    "confluence_base_url",
    "confluence_credential_id",
    "bitbucket_base_url",
    "bitbucket_project_key",
    "bitbucket_repo_slug",
    "bitbucket_credential_id",
    "jira_board_id",
    "tempo_team_id",
    "confluence_report_page_space",
    "confluence_report_parent_page_title",
    "stp_matrix_confluence_space",
    "stp_matrix_confluence_root_page_title",
)

# Поля-ссылки на secret_service, которые нужно провалидировать на PUT (C4):
# credential_id (Jira/Zephyr/Tempo и Confluence-fallback), confluence_credential_id,
# bitbucket_credential_id.
_CREDENTIAL_LINK_FIELDS = ("credential_id", "confluence_credential_id", "bitbucket_credential_id")


async def _validate_credential_links(token: str, changes: dict) -> None:
    """Проверяет заново заданные ссылки на credential в secret_service (C4).

    Форвардим bearer вызывающего (не bot-токен сервиса) — решение о
    видимости должно приниматься по ЕГО правам в secret_service, иначе отдел
    мог бы сослаться на чужую credential, которую сам никогда бы не увидел.
    `NotFoundError`/`AuthorizationError` от `get_credential_metadata`
    пробрасываются как есть (404/403); здесь дополнительно отсекается
    scope=personal — такая credential department-несовместима, даже если
    вызывающий на неё смотрит как владелец.

    Канал не настроен в этом окружении (`SECRET_SERVICE_URL` пуст) — молча
    пропускаем, тот же best-effort, что у `reveal_credential` при публикации.
    """
    if not secret_client.is_configured():
        return
    for field in _CREDENTIAL_LINK_FIELDS:
        cred_id = changes.get(field)
        if not cred_id:
            continue
        metadata = await secret_client.get_credential_metadata(token, cred_id)
        scope = metadata.get("scope")
        if scope == "personal":
            raise DomainValidationError(
                error_code="CREDENTIAL_SCOPE_INVALID",
                message=f"{field} must reference a department-scoped credential, not a personal one",
                details={"field": field, "credential_id": cred_id, "scope": scope},
            )


async def get_effective(db: AsyncSession, department_id: str) -> dict:
    """Эффективные настройки отдела — пустые поля, если строки ещё нет."""
    row = await repo.get_by_department(db, department_id)
    if row is None:
        return {
            "id": None,
            "department_id": department_id,
            "created_at": None,
            "updated_at": None,
            **{field: None for field in _NULLABLE_FIELDS},
        }
    return {
        "id": row.id,
        "department_id": row.department_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        **{field: getattr(row, field) for field in _NULLABLE_FIELDS},
    }


async def get_effective_for(db: AsyncSession, identity: Identity, department_id: str) -> dict:
    """То же, что `get_effective`, но для HTTP-чтения — с гейтом по отделу.

    Отдельная функция по той же причине, что и в `department_test_settings`:
    внутренние потребители (СТП-публикация, HR-отчёт) идут без
    пользовательского контекста.
    """
    permissions.require_own_department(identity, department_id)
    return await get_effective(db, department_id)


async def upsert(
    db: AsyncSession,
    identity: Identity,
    department_id: str,
    payload: DepartmentIntegrationSettingsUpdate,
    token: str,
) -> DepartmentIntegrationSettings:
    """PUT — создаёт строку при первом вызове, иначе обновляет заданные поля."""
    try:
        await permissions.require_department_action(
            db, identity, department_id, EntityType.DEPARTMENT_INTEGRATION_SETTINGS, Action.UPDATE,
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
    await _validate_credential_links(token, changes)
    row = await repo.get_by_department(db, department_id)
    if row is None:
        data = {
            "id": new_id(),
            "department_id": department_id,
            **{field: changes.pop(field, None) for field in _NULLABLE_FIELDS},
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
