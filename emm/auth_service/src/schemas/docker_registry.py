"""Схемы для Docker registry token-auth protocol и конфига per-department."""

from datetime import datetime

from pydantic import BaseModel, Field


PULL_POLICY_ALL = "all"
PULL_POLICY_RESTRICTED = "restricted"


class DockerRegistryConfigCreate(BaseModel):
    """Создать или заменить Docker registry конфиг отдела."""
    pull_policy: str = Field(
        default=PULL_POLICY_ALL,
        pattern="^(all|restricted)$",
        description='"all" — pull открыт всей платформе и анонимам (не только отделу); "restricted" — только из pull_user_ids.',
    )
    # Обязателен для pull_policy = "restricted"; игнорируется для "all"
    pull_user_ids: list[str] = Field(
        default_factory=list,
        description='Whitelist юзеров для pull (требуется при pull_policy="restricted").',
    )
    push_user_ids: list[str] = Field(
        default_factory=list,
        description="Whitelist юзеров для push. Push всегда restricted.",
    )


class DockerRegistryConfigUpdate(BaseModel):
    """Тело PATCH — частичный апдейт конфига."""
    pull_policy: str | None = Field(default=None, pattern="^(all|restricted)$")
    pull_user_ids: list[str] | None = None
    push_user_ids: list[str] | None = None
    is_enabled: bool | None = Field(default=None, description="Включить/выключить registry для отдела.")


class DockerRegistryConfigResponse(BaseModel):
    """Конфиг registry в ответе get/put эндпоинтов."""
    department_id: str
    is_enabled: bool
    pull_policy: str
    pull_user_ids: list[str]
    push_user_ids: list[str]
    created_at: datetime
    updated_at: datetime


class DockerTokenResponse(BaseModel):
    """Ответ `GET /docker/token` — scoped JWT по Docker token-auth protocol."""
    token: str = Field(description="RS256-подписанный JWT с access-claims.")
    access_token: str = Field(description="То же значение что и `token`. Docker принимает оба имени поля.")
    expires_in: int = Field(description="TTL токена в секундах (default 5 мин).")
    issued_at: str = Field(description="ISO timestamp выдачи токена.")
