"""Pydantic-схемы инстанс-уровневого ACL (resource_role_permissions)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ResourcePermissionResponse(BaseModel):
    """Одна строка инстанс-ACL в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Permission ID (prefix rrp_).")
    resource_type: str = Field(description="Тип ресурса: server / server_account.")
    resource_id: str = Field(description="ID конкретного ресурса (srv_ / acc_).")
    role: str = Field(description="Роль, которой выдан action на этот ресурс.")
    action: str = Field(description="Action: view / power_on / view_password / ...")
    department_id: str | None = Field(
        default=None,
        description="Scope: None — system-wide; иначе — per-department (отдел ресурса).",
    )
    granted_by: str | None = Field(
        default=None, description="user_id, создавший грант (NULL для seed)."
    )
    created_at: datetime = Field(description="Когда грант создан.")
    updated_at: datetime = Field(description="Когда грант изменён в последний раз.")


class ResourcePermissionListResponse(BaseModel):
    """Envelope списка инстанс-грантов."""

    items: list[ResourcePermissionResponse] = Field(description="Список инстанс-грантов.")
    total: int = Field(description="Количество записей в `items`.")


class ResourcePermissionPropagateRequest(BaseModel):
    """Тело POST propagate: копирование инстанс-грантов с образца на цели.

    Образец и цели — ресурсы одного типа (`resource_type` берётся из пути
    образца). `mode`:

    * ``merge`` (дефолт) — добавить на каждую цель недостающие `(role, action)`
      образца; существующие на цели не трогать;
    * ``mirror`` — привести набор инстанс-грантов цели к точной копии образца:
      добавить недостающие И удалить те, которых у образца нет.
    """

    target_resource_ids: list[str] = Field(
        min_length=1,
        max_length=500,
        description="Ресурсы-цели (того же типа, что и образец). Без самого образца.",
    )
    mode: str = Field(
        default="merge",
        pattern="^(merge|mirror)$",
        description="merge — только добавить недостающее; mirror — привести к точной копии образца.",
    )


class ResourcePropagateTargetSummary(BaseModel):
    """Сводка по одной цели propagate."""

    resource_id: str = Field(description="ID цели.")
    added: int = Field(description="Сколько (role, action) добавлено.")
    removed: int = Field(description="Сколько удалено (только mirror; merge=0).")


class ResourcePropagateResponse(BaseModel):
    """Ответ propagate: образец, режим, сводка по целям."""

    source_resource_id: str = Field(description="ID образца.")
    resource_type: str = Field(description="Тип ресурса.")
    mode: str = Field(description="merge | mirror.")
    source_grant_count: int = Field(description="Сколько инстанс-грантов у образца.")
    targets: list[ResourcePropagateTargetSummary] = Field(
        description="Сводка по каждой цели."
    )
