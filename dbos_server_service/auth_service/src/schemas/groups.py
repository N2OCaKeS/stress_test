"""Схемы для пользовательских групп."""

from datetime import datetime

from pydantic import BaseModel, Field


class GroupCreate(BaseModel):
    """Тело `POST /groups`. Группа всегда привязана к отделу."""
    department_id: str = Field(description="ID отдела, к которому привязываем группу.")
    name: str = Field(description="Машинно-читаемое имя (уникально внутри отдела).")
    display_name: str = Field(description="Человеческое название группы.")
    description: str | None = Field(default=None)


class GroupUpdate(BaseModel):
    """Тело `PATCH /groups/{group_id}` — только display_name/description."""
    display_name: str | None = None
    description: str | None = None


class GroupResponse(BaseModel):
    """Группа в ответе list/get эндпоинтов."""
    id: str
    department_id: str
    name: str
    display_name: str
    description: str | None
    is_active: bool
    created_at: datetime
    created_by: str | None

    model_config = {"from_attributes": True}


class MemberAddRequest(BaseModel):
    """Тело `POST /groups/{group_id}/members`."""
    user_id: str = Field(description="ID юзера, которого добавляем в группу.")


class MemberResponse(BaseModel):
    """Member группы — юзер + время вступления."""
    user_id: str
    username: str
    added_at: datetime


class GroupServiceGrantRequest(BaseModel):
    """Тело `POST /groups/{group_id}/services`."""
    service_name: str = Field(description="Сервис, к которому даём group access.")


class GroupServiceAccessResponse(BaseModel):
    """`GroupServiceAccess` в ответе list эндпоинта."""
    service_name: str
    is_active: bool
    granted_at: datetime
    granted_by: str | None

    model_config = {"from_attributes": True}


class GroupRoleAssignRequest(BaseModel):
    """Тело `POST /groups/{group_id}/roles` — replace-семантика."""
    service_name: str
    roles: list[str]


class GroupRoleResponse(BaseModel):
    """Service-роли группы для конкретного сервиса."""
    service_name: str
    roles: list[str]


class UserGroupsResponse(BaseModel):
    """Запись «группа, в которой состоит юзер» — для `GET /users/{id}/groups`."""
    group_id: str
    group_name: str
    display_name: str
    added_at: datetime
