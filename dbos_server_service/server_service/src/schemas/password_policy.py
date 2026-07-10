"""Pydantic-схемы настраиваемой парольной политики (`/admin/password-policy`).

Платформенный singleton под `account_admin`. Описывает базовую политику для
ручного ввода пароля server-аккаунтов и IPMI-credentials: минимальную длину и
обязательность буквы/цифры. Границы `min_length` enforce'ятся на уровне поля
(`ge`/`le`); PUT частичный — любое поле можно опустить, тогда текущее значение
сохраняется.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.core.password_policy import (
    MAX_CONFIGURABLE_LENGTH,
    MIN_CONFIGURABLE_LENGTH,
)


class PasswordPolicyResponse(BaseModel):
    """Текущая базовая парольная политика."""

    min_length: int = Field(
        description="Минимальная длина пароля.",
    )
    require_letter: bool = Field(
        description="Требовать хотя бы одну букву.",
    )
    require_digit: bool = Field(
        description="Требовать хотя бы одну цифру.",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="Когда политику последний раз меняли (UTC).",
    )
    updated_by: str | None = Field(
        default=None,
        description="Actor-id платформенного админа, применившего последнее изменение.",
    )


class PasswordPolicyUpdate(BaseModel):
    """Тело PUT — частичное обновление базовой парольной политики.

    Любое поле можно опустить — тогда текущее значение сохраняется. Границы
    `min_length` проверяются здесь; полностью «пустая» политика (короткая длина
    без требований к символам) допустима, это осознанный выбор админа.
    """

    min_length: int | None = Field(
        default=None,
        ge=MIN_CONFIGURABLE_LENGTH,
        le=MAX_CONFIGURABLE_LENGTH,
        description=(
            "Минимальная длина пароля. "
            f"Диапазон {MIN_CONFIGURABLE_LENGTH}..{MAX_CONFIGURABLE_LENGTH}."
        ),
    )
    require_letter: bool | None = Field(
        default=None,
        description="Требовать хотя бы одну букву.",
    )
    require_digit: bool | None = Field(
        default=None,
        description="Требовать хотя бы одну цифру.",
    )
