"""Общие response- и error-схемы (envelope ответов сервиса)."""

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """Стандартный envelope ошибки. Возвращается из `app_exception_handler` в main.py.

    `error_code` — стабильный машинный код (catalog в API_ENDPOINTS.md /
    AUDIT_EVENTS.md). `message` — человекочитаемый текст. `details` — словарь
    с дополнительным контекстом (entity_id, hint и т.п.).
    """

    error: str = Field(description="HTTP-категория: bad_request / forbidden / not_found / ...")
    error_code: str = Field(description="Стабильный машинный код (например, SERVER_NOT_FOUND).")
    message: str = Field(description="Человекочитаемое описание ошибки.")
    details: dict = Field(default_factory=dict, description="Дополнительный контекст (entity_id, hint и т.п.).")
    request_id: str | None = Field(default=None, description="Совпадает с заголовком ответа X-Request-ID для корреляции с логами.")
    timestamp: datetime | None = Field(default=None, description="UTC-метка времени ответа.")


class OkResponse(BaseModel):
    """Тривиальный {ok: true} для эндпоинтов без полезной нагрузки."""

    ok: bool = True


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic-обёртка для постраничных ответов (limit/offset).

    Legacy-формат. Для новых интеграций предпочитать `CursorPaginatedResponse`:
    keyset-пагинация устойчива к вставкам в начале списка и не платит за
    глубокий offset.
    """

    items: list[T] = Field(description="Содержимое страницы.")
    total: int = Field(description="Общее количество записей под фильтром (без учёта limit/offset).")
    limit: int = Field(description="Размер страницы, переданный в запросе.")
    offset: int = Field(description="Смещение от начала, переданное в запросе.")


class CursorPaginatedResponse(BaseModel, Generic[T]):
    """Generic-обёртка под keyset-пагинацию (cursor / has_more).

    `next_cursor` — opaque-токен, подставляется в `?after=<cursor>` следующего
    запроса. `null` означает конец списка. `has_more` дублирует флаг для
    клиентов, которым удобнее булеан, чем сравнение с null.
    """

    items: list[T] = Field(description="Содержимое страницы.")
    next_cursor: str | None = Field(default=None, description="Курсор следующей страницы или null в конце.")
    has_more: bool = Field(default=False, description="Есть ли ещё страницы после этой.")
