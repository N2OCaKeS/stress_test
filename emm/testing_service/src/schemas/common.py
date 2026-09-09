"""Общие response- и error-схемы (envelope ответов сервиса)."""

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """Стандартный envelope ошибки — то, что собирает `app_exception_handler`."""

    error: str = Field(description="HTTP-категория: bad_request / forbidden / not_found / ...")
    error_code: str = Field(description="Стабильный машинный код (например, GLOBAL_VARIABLE_NOT_FOUND).")
    message: str = Field(description="Человекочитаемое описание ошибки.")
    details: dict = Field(default_factory=dict, description="Дополнительный контекст (entity_id, hint и т.п.).")
    request_id: str | None = Field(default=None, description="Совпадает с заголовком X-Request-ID.")
    timestamp: datetime | None = Field(default=None, description="UTC-метка времени ответа.")


class OkResponse(BaseModel):
    """Тривиальный {ok: true} для эндпоинтов без полезной нагрузки."""

    ok: bool = True


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic-обёртка для постраничных ответов (limit/offset)."""

    items: list[T] = Field(description="Содержимое страницы.")
    total: int = Field(description="Общее количество записей (без учёта limit/offset).")
    limit: int = Field(description="Размер страницы, переданный в запросе.")
    offset: int = Field(description="Смещение от начала, переданное в запросе.")
