"""ORM-модель `AccountNopasswdSudoSettings` — per-department NOPASSWD sudo для тестовых учёток.

`server_account.has_sudo=True` сегодня только добавляет учётку в группу `sudo`
— на Debian/Astra это всё равно спрашивает пароль на каждый `sudo`-вызов
(обычная политика `%sudo ALL=(ALL:ALL) ALL` без `NOPASSWD`). Для нагрузочных
тестов, где сервер/ВМ — это одноразовый управляемый тестовый контур, а не
чья-то рабочая станция, отделу может быть удобнее выключить эти запросы
полностью. Не всем отделам это нужно — опция per-department, дефолт `False`
(текущее поведение с паролем сохраняется, пока отдел явно не включит).

Живёт в `server_service`, а не в `testing_service`: сама механика (кому класть
`/etc/sudoers.d/<login>-nopasswd` при provision/prepare) — код server_worker,
управляющий OS-учётками на боксах, тот же домен, что и `has_sudo`/`unix_groups`
на `server_account`. `testing_service.department_test_settings` — про сценарии
прогона теста (STP/RC/ядра), не про то, как заводится OS-пользователь.

По структуре — копия `HostServicesSettings` (department_id как PK, не
отдельный id+unique — здесь ровно один флаг на отдел, обогащать нечем) и
`AcsDepartmentAccess` (soft-reference на department_id — своей таблицы
`departments` у server_service нет, тот же приём, что и `servers.department_id`).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class AccountNopasswdSudoSettings(Base):
    """Флаг «NOPASSWD sudo для sudo-аккаунтов при provision» для одного отдела."""

    __tablename__ = "account_nopasswd_sudo_settings"

    department_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # Soft-FK на auth_service identity (`usr_<hex>` / `bot_<hex>`), как и у
    # остальных department-level настроек server_service.
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
