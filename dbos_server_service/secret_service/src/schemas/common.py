"""Общий envelope-формат ответов сервиса."""

from datetime import datetime

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Стандартный envelope ошибки. Возвращается из exception-хендлеров в main.py.

    `error_code` — стабильный машинный код (catalog в API_ENDPOINTS.md /
    AUDIT_EVENTS.md, будет заполнено в следующих фазах). `message` —
    человекочитаемый текст. `details` — словарь с дополнительным контекстом.
    """

    error: str = Field(description="HTTP-категория: bad_request / forbidden / not_found / ...")
    error_code: str = Field(description="Стабильный машинный код (например, SECRET_NOT_FOUND).")
    message: str = Field(description="Человекочитаемое описание ошибки.")
    details: dict = Field(default_factory=dict, description="Дополнительный контекст.")
    request_id: str | None = Field(
        default=None,
        description="Совпадает с заголовком ответа X-Request-ID для корреляции с логами.",
    )
    timestamp: datetime | None = Field(default=None, description="UTC-метка времени ответа.")


class OkResponse(BaseModel):
    """Тривиальный {ok: true} для эндпоинтов без полезной нагрузки."""

    ok: bool = True
