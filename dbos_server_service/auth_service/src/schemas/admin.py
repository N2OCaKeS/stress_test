"""Схемы платформенных admin-ручек (генератор мастер-ключей и т.д.)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GeneratedServiceKeyResponse(BaseModel):
    """Ответ POST /api/auth/v1/admin/service-keys/generate.

    `key_b64` показывается ровно один раз — auth_service его не хранит.
    Передаётся account_admin'ом в rotate-эндпоинт целевого сервиса.
    """

    key_b64: str = Field(
        ...,
        description=(
            "Свежий мастер-ключ: 32 случайных байта (CSPRNG), стандартный"
            " base64. Распространяется в keystore сервиса через его"
            " `POST /internal/encryption/rotate`."
        ),
    )
    key_bytes: int = Field(
        ..., description="Длина ключа в байтах до base64-кодирования (32 = AES-256)."
    )
    algorithm: str = Field(
        ..., description="Алгоритм, под который предназначен ключ (AES-256-GCM)."
    )


class LockoutPolicyResponse(BaseModel):
    """Ответ GET/PUT /api/auth/v1/admin/lockout-policy.

    Эффективные параметры brute-force lockout'а. `source="db"` — действует
    runtime-override из таблицы `lockout_policy`; `source="env"` — override'а
    нет, используются env-дефолты (`MAX_FAILED_LOGIN_ATTEMPTS` / `LOCKOUT_MINUTES`).
    """

    max_failed_attempts: int = Field(
        ..., description="Сколько подряд неудачных login'ов триггерят lockout."
    )
    lockout_minutes: int = Field(
        ..., description="Длительность lockout в минутах после достижения лимита."
    )
    source: str = Field(
        ..., description="Откуда взяты значения: `db` (runtime-override) или `env` (дефолт)."
    )


class LockoutPolicyUpdateRequest(BaseModel):
    """Тело PUT /api/auth/v1/admin/lockout-policy — runtime-override политики."""

    max_failed_attempts: int = Field(
        ..., ge=1, description="Лимит неудачных попыток до lockout'а (>= 1)."
    )
    lockout_minutes: int = Field(
        ..., ge=1, description="Длительность lockout в минутах (>= 1)."
    )
