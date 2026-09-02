"""Pydantic-схемы для permission-матрицы."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PermissionResponse(BaseModel):
    """Одна строка матрицы entity_permissions в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Permission ID (prefix prm_).")
    entity_type: str = Field(description="Тип сущности: server / server_account / ipmi_controller / ...")
    role: str = Field(description="Роль, которой выдан action.")
    action: str = Field(description="Action: view / create / power_on / view_password / ...")
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
    ``DEPARTMENT_ISOLATION``. Platform-роли (``account_admin``/
    ``loging_admin``) сюда не попадают: ``platform_admin_guard`` middleware
    блокирует их 403 ``PLATFORM_ADMIN_BUSINESS_DATA_DENIED`` ещё до
    endpoint'а, так что system-wide grant'ы из публичного API сейчас
    выписать нельзя.
    """

    target_department_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "department_id scope'а для grant'а. Department/service admin "
            "обязан либо опустить поле, либо передать собственный "
            "department; несовпадение → 403 DEPARTMENT_ISOLATION. None "
            "формально означает system-wide grant, но в текущей конфигурации "
            "получить его через API нельзя — platform-роли отбиваются "
            "middleware'ом."
        ),
    )


class EffectiveActionsResponse(BaseModel):
    """Эффективный набор actions для текущего пользователя на entity_type.

    Используется UI'ем чтобы понять, что отрисовать (кнопка power_on
    появляется только если caller имеет power_on).
    """

    entity_type: str = Field(description="Тип сущности.")
    actions: list[str] = Field(description="Union actions, разрешённых caller'у на этом entity_type.")


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

    entity_description: str = Field(description="Человеческое описание сущности грантa.")
    action_description: str = Field(description="Человеческое описание действия грантa.")
    sensitive: bool = Field(description="Чувствительное ли действие грантa.")


class PermissionListResponse(BaseModel):
    """Envelope для list-эндпоинтов матрицы прав.

    Строки могут быть обычными `PermissionResponse` либо обогащёнными
    `PermissionDescribedResponse` (флаг `described`). Поле `total` — длина
    `items`; зарезервировано под будущий offset/limit (сейчас фильтрация
    идёт по `role`/`entity_type`, пагинация на уровне репо не вкручена).
    """

    items: list[PermissionResponse | PermissionDescribedResponse] = Field(
        description="Список grants. Тип строк определяется флагом `described`.",
    )
    total: int = Field(description="Количество записей в `items` (под будущий offset/limit).")
    described: bool = Field(
        description="True — строки обогащены описаниями (`describe=true`); False — обычная `PermissionResponse`.",
    )
