"""Pydantic-схемы `/provisioning-profiles`."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProvisioningProfileFields(BaseModel):
    allowed_failed_units: list[str] = Field(
        default_factory=lambda: ["astra-mount-lock.service"], max_length=64,
        description="Упавшие юниты, с которыми `degraded` всё равно считается готовностью.",
    )
    degraded_reboot_attempts: int = Field(default=3, ge=0, le=20, description="Перезагрузок при ином `degraded`.")
    disable_pam_lastlog_inactive: bool = Field(
        default=True, description="Закомментировать `pam_lastlog.so inactive=` в /etc/pam.d/common-auth.",
    )
    boot_wait_timeout_seconds: int | None = Field(
        default=None, ge=60, le=86400, description="Сколько ждать подъёма после перезагрузки; пусто — по умолчанию.",
    )

    @field_validator("allowed_failed_units")
    @classmethod
    def _units(cls, value: list[str]) -> list[str]:
        units = [u.strip() for u in value if u.strip()]
        for unit in units:
            if len(unit) > 256 or any(ch.isspace() for ch in unit):
                raise ValueError(f"invalid unit name: {unit!r}")
        return units


class ProvisioningProfileCreate(ProvisioningProfileFields):
    department_id: str | None = Field(default=None, max_length=64, description="null — общий профиль.")
    name: str = Field(..., min_length=1, max_length=128)
    is_default: bool = False


class ProvisioningProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    is_default: bool | None = None
    allowed_failed_units: list[str] | None = Field(default=None, max_length=64)
    degraded_reboot_attempts: int | None = Field(default=None, ge=0, le=20)
    disable_pam_lastlog_inactive: bool | None = None
    boot_wait_timeout_seconds: int | None = Field(default=None, ge=60, le=86400)

    @field_validator("allowed_failed_units")
    @classmethod
    def _units(cls, value):
        return None if value is None else ProvisioningProfileFields._units(value)


class ProvisioningProfileResponse(ProvisioningProfileFields):
    model_config = ConfigDict(from_attributes=True)

    id: str
    department_id: str | None
    name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class ProvisioningProfileListResponse(BaseModel):
    items: list[ProvisioningProfileResponse]
