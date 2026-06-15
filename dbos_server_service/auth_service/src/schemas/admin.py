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
