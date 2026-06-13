"""Схемы для управления отделами и их доступом к сервисам."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class DepartmentCreate(BaseModel):
    """Тело `POST /departments`."""
    name: str = Field(min_length=1, max_length=128, description="Человеческое имя отдела (уникально).")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name не может быть пустым")
        return v


class DepartmentUpdateRequest(BaseModel):
    """Тело `PATCH /departments/{id}`. Оба поля опциональны; пустое тело → 422."""
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Человеческое имя отдела (уникально).",
    )
    description: str | None = Field(
        default=None,
        max_length=1024,
        description="Свободно-форматный текст-пояснение для UI-карточки.",
    )


class DepartmentResponse(BaseModel):
    """Отдел в ответе list/get эндпоинтов."""
    department_id: str
    name: str
    description: str | None = None
    is_active: bool
    created_at: datetime
    user_count: int = Field(
        default=0,
        description="Число пользователей, привязанных к отделу (все по department_id, без разбивки по is_active; боты не считаются).",
    )

    model_config = {"from_attributes": True}


class GrantServiceAccessRequest(BaseModel):
    """Тело `POST /departments/{department_id}/services`."""
    service_name: str = Field(description="Сервис, к которому даём отделу access.")


class ServiceAccessResponse(BaseModel):
    """Ответ grant-эндпоинта — текущее состояние связи (dept, service)."""
    department_id: str
    service_name: str
    enabled: bool
    granted_at: datetime | None = None


class HardDeleteDepartmentRequest(BaseModel):
    """Тело `DELETE /departments/{department_id}`. `reason` обязателен для compliance-аудита."""
    reason: str = Field(
        min_length=1,
        max_length=256,
        description="Человекочитаемое обоснование hard-delete'а (compliance).",
    )
