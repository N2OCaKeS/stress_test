"""Pydantic-схемы для permission-матрицы."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PermissionResponse(BaseModel):
    """Одна строка матрицы entity_permissions в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Permission ID (prefix prm_).")
    entity_type: str = Field(description="Тип сущности: global_variable / test_stand / permission / ...")
    role: str = Field(description="Роль, которой выдан action.")
    action: str = Field(description="Action: view / create / update / permission_grant / ...")
    # Nullable scope-дискриминатор: None = system-wide (встроенные роли),
    # строка = per-department (кастомные роли).
    department_id: str | None = Field(default=None, description="Scope: None — system-wide grant; иначе — per-department.")
    granted_by: str | None = Field(default=None, description="user_id, который создал grant (NULL для seed-данных).")
    created_at: datetime = Field(description="Когда grant создан.")
    updated_at: datetime = Field(description="Когда grant изменён в последний раз.")


class PermissionGrant(BaseModel):
    """Опциональное тело PUT /permissions/{entity_type}/{role}/{action}.

    Caller обязан либо опустить ``target_department_id``, либо передать
    собственный ``department_id`` — несовпадение даёт 403
    ``DEPARTMENT_ISOLATION``. Платформенный ``account_admin`` — исключение:
    он может нацелить grant в любой отдел, dept-isolation к нему не
    применяется (см. `services/permission_service.py`).
    """

    target_department_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "department_id scope'а для grant'а. Department/service admin "
            "обязан либо опустить поле, либо передать собственный "
            "department; несовпадение → 403 DEPARTMENT_ISOLATION. "
            "account_admin может передать любой отдел."
        ),
    )


class CatalogAction(BaseModel):
    """Действие в каталоге прав: имя, описание и флаги чувствительности."""

    action: str = Field(description="Имя действия.")
    description: str = Field(description="Человеческое описание действия.")
    sensitive: bool = Field(description="Чувствительное действие (аудит CRITICAL).")
    worker_only: bool = Field(description="Служебный callback воркера; людям обычно не выдаётся.")


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

    Строки могут быть обычными `PermissionResponse` либо обогащёнными
    `PermissionDescribedResponse` (флаг `described`). Поле `total` — длина
    `items`; зарезервировано под будущий offset/limit.
    """

    items: list[PermissionResponse | PermissionDescribedResponse] = Field(
        description="Список grants. Тип строк определяется флагом `described`.",
    )
    total: int = Field(description="Количество записей в `items` (под будущий offset/limit).")
    described: bool = Field(
        description="True — строки обогащены описаниями (`describe=true`); False — обычная `PermissionResponse`.",
    )
