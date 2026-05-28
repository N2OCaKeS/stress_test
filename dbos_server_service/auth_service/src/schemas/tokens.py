"""Схемы для Personal Access Tokens (PAT)."""

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field, model_validator


class PATCreate(BaseModel):
    """Тело `POST /tokens`.

    Поддерживается два эквивалентных способа задать срок жизни:
    `expires_at` (абсолютный момент) или `ttl_seconds` (offset от now).
    Если задан `ttl_seconds`, он конвертируется в `expires_at = now + ttl`
    ещё до доменной валидации, чтобы дальше код работал только с
    абсолютной датой. Оба сразу — 422.
    """
    name: str = Field(min_length=1, max_length=256, description="Имя токена (для метаданных, не secret).")
    expires_at: datetime | None = Field(default=None, description="TTL токена. None = бессрочно.")
    ttl_seconds: int | None = Field(
        default=None,
        ge=1,
        description="Альтернатива expires_at: offset в секундах от текущего момента.",
    )
    allowed_services: list[str] = Field(
        default_factory=list,
        description="Scope токена — список сервисов, куда им можно ходить.",
    )

    @model_validator(mode="after")
    def _ttl_to_expires_at(self) -> "PATCreate":
        if self.ttl_seconds is not None:
            if self.expires_at is not None:
                raise ValueError("expires_at and ttl_seconds are mutually exclusive")
            self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
            self.ttl_seconds = None
        return self


class PATCreateResponse(BaseModel):
    """Возвращается один раз при создании — содержит raw-значение токена."""
    token_id: str = Field(description="ID записи токена (для revoke).")
    token: str = Field(description="Raw-токен с префиксом `dbos_pat_…`. Сохрани сейчас — больше не покажем.")
    name: str
    expires_at: datetime | None


class PATListItem(BaseModel):
    """Элемент списка `GET /tokens` — только метаданные."""
    token_id: str
    name: str
    token_prefix: str
    allowed_services: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}
