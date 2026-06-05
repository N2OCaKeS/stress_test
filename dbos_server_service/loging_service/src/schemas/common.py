"""Общий envelope ошибок и тривиальный Ok-ответ.

Зеркалит `auth_service/src/schemas/common.py` и `server_service/src/schemas/common.py`:
формат строго совпадает — клиенты, написанные под auth, разбирают ошибки loging'а
тем же кодом.

`ErrorResponse` — алиас на `ErrorEnvelope` для обратной совместимости.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class ErrorEnvelope(BaseModel):
    """Стандартный envelope ошибки сервиса.

    Возвращается из `app_exception_handler`, `rate_limit_exception_handler`
    и `validation_exception_handler` в `main.py`, а также из inline-ответов
    middleware (body-size 413/400).

    `error_code` — стабильный машинный код (каталог в `API_ENDPOINTS.md`).
    `message` — человекочитаемый текст. `details` — словарь с дополнительным
    контекстом (entity_id, hint и т.п.).
    """

    error: str = Field(description="HTTP-категория: bad_request / forbidden / not_found / ...")
    error_code: str = Field(description="Стабильный машинный код (например, INVALID_SERVICE_KEY).")
    message: str = Field(description="Человекочитаемое описание ошибки.")
    details: dict = Field(default_factory=dict, description="Дополнительный контекст (entity_id, hint и т.п.).")
    request_id: str | None = Field(default=None, description="Совпадает с заголовком ответа X-Request-ID для корреляции с логами.")
    timestamp: datetime | None = Field(default=None, description="UTC-метка времени ответа.")


# Алиас для совместимости с привычным именованием в других сервисах.
ErrorResponse = ErrorEnvelope


class OkResponse(BaseModel):
    """Тривиальный {ok: true} для эндпоинтов без полезной нагрузки."""

    ok: bool = Field(default=True, description="Маркер успешного выполнения.")
