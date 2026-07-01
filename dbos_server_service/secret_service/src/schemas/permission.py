"""Pydantic-схемы для тип-wide матрицы прав secret_service."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PermissionResponse(BaseModel):
    """Одна строка матрицы entity_permissions в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Permission ID (префикс prm_).")
    entity_type: str = Field(description="Тип сущности: secret.")
    role: str = Field(description="Роль, которой выдан action.")
    action: str = Field(description="Action: read / reveal / write / delete / grant_acl / grant_dept / manage_status.")
    department_id: str | None = Field(
        default=None,
        description="Scope: None — system-wide grant; иначе — per-department.",
    )
    granted_by: str | None = Field(
        default=None,
        description="user_id, создавший grant (NULL для seed-данных).",
    )
    created_at: datetime = Field(description="Когда grant создан.")
    updated_at: datetime = Field(description="Когда grant изменён в последний раз.")


class PermissionGrant(BaseModel):
    """Опциональное тело PUT /permissions/{entity_type}/{role}/{action}.

    Caller обязан либо опустить ``target_department_id``, либо передать
    собственный ``department_id`` — несовпадение даёт 403
    ``DEPARTMENT_ISOLATION``. Платформенный ``account_admin`` — мета-админ
    матрицы, для него ``target_department_id`` задаёт целевой отдел без
    dept-isolation проверки.
    """

    target_department_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "department_id scope'а для grant'а. Department/service admin обязан "
            "либо опустить поле, либо передать собственный department; "
            "несовпадение → 403 DEPARTMENT_ISOLATION."
        ),
    )


class CatalogAction(BaseModel):
    """Действие в каталоге прав: имя, описание и флаг чувствительности."""

    action: str = Field(description="Имя действия.")
    description: str = Field(description="Человеческое описание действия.")
    sensitive: bool = Field(description="Чувствительное действие (аудит CRITICAL).")


class CatalogEntity(BaseModel):
    """Сущность каталога прав с описанием и набором её действий."""

    entity_type: str = Field(description="Тип сущности.")
    description: str = Field(description="Человеческое описание сущности.")
    actions: list[CatalogAction] = Field(description="Действия, доступные для этой сущности.")


class PermissionDescribedResponse(PermissionResponse):
    """Строка матрицы, обогащённая описаниями из каталога (`describe=true`)."""

    entity_description: str = Field(description="Человеческое описание сущности гранта.")
    action_description: str = Field(description="Человеческое описание действия гранта.")
    sensitive: bool = Field(description="Чувствительное ли действие гранта.")


class PermissionListResponse(BaseModel):
    """Envelope для list-эндпоинтов матрицы прав.

    Строки — обычные `PermissionResponse` либо обогащённые
    `PermissionDescribedResponse` (флаг `described`). `total` — длина `items`.
    """

    items: list[PermissionResponse | PermissionDescribedResponse] = Field(
        description="Список grants. Тип строк определяется флагом `described`.",
    )
    total: int = Field(description="Количество записей в `items`.")
    described: bool = Field(
        description="True — строки обогащены описаниями (`describe=true`).",
    )
