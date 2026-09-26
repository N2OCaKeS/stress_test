"""Профиль подготовки стенда.

Что считать «стенд поднялся» после перезагрузки и надо ли править PAM —
данные, а не код server_worker. В `prepare-for-test` уходят значения
: server_service профилей не знает.

`department_id IS NULL` — общий профиль по умолчанию (сид миграции
`tp10_tp11_stand_setup_provisioning`, легаси `socket_available()`
`emm/allta_app_full/backup_image.py:554-600` и PAM-правка
`backup_image.py:807-808`). У отдела может быть свой, `is_default` — для
тестов без `test_definitions.provisioning_profile_id`.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ProvisioningProfile(Base):
    __tablename__ = "provisioning_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Упавшие юниты, с которыми `degraded` всё равно считается готовностью.
    allowed_failed_units: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Сколько раз перезагружать при ином `degraded`.
    degraded_reboot_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # Закомментировать `pam_lastlog.so inactive=` в common-auth.
    disable_pam_lastlog_inactive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Сколько ждать подъёма после перезагрузки; NULL — бюджет server_worker.
    boot_wait_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
