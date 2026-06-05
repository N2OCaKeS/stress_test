"""Общие схемы — error response и Ok-обёртка."""

from datetime import datetime

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Стандартный формат ошибки. Возвращается обработчиком исключений."""

    error: str = Field(description='Категория ошибки (`forbidden`, `not_found`, ...).')
    error_code: str = Field(description="Стабильный машинный код (`INVALID_CREDENTIALS`, `PERMISSION_DENIED`, ...).")
    message: str = Field(description="Человекочитаемое описание ошибки.")
    details: dict = Field(default_factory=dict, description="Доп. структурированные данные (опционально).")
    request_id: str | None = Field(default=None, description="ID запроса для корреляции в логах.")
    timestamp: datetime | None = Field(default=None, description="UTC timestamp возникновения ошибки.")


class OkResponse(BaseModel):
    """Тривиальный успешный ответ для эндпоинтов, которым нечего возвращать."""
    ok: bool = Field(default=True, description="Всегда True. Маркер успешного выполнения.")


class HealthResponse(BaseModel):
    """Ответ liveness-пробы `/health`."""
    status: str = Field(description="`ok` если процесс жив.")
    service: str = Field(description="Имя сервиса (auth_service).")


class ReadyResponse(BaseModel):
    """Ответ readiness-пробы `/ready` — успех (200) или 503 с reason."""
    status: str = Field(description="`ready` (200) или `not_ready` (503).")
    service: str = Field(description="Имя сервиса (auth_service).")
    reason: str | None = Field(default=None, description="Причина not_ready (например, `db_unreachable`).")
