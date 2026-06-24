"""Макросы консоли — готовые команды для интерактивного терминала.

Два скоупа в одной таблице:

* личный — `is_system=false`, `user_id` указывает владельца, видит и правит
  только он. `department_id` проставляется для удобства фильтрации, но
  видимость личного макроса определяет именно `user_id`.
* системный — `is_system=true`, `department_id` указывает отдел, `user_id`
  NULL. Виден всем в отделе, создаёт/правит department_admin отдела.

Целостность скоупа держат CHECK-констрейнты в миграции: личный → user_id
NOT NULL и is_system=false; системный → department_id NOT NULL, user_id NULL
и is_system=true.
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ConsoleMacro(Base):
    """Сохранённая команда консоли: личная или системная (общая в отделе)."""

    __tablename__ = "console_macros"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    command_text: Mapped[str] = mapped_column(Text, nullable=False)
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Личный макрос: владелец. Для системного — NULL.
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Системный макрос: отдел. Для личного проставляется для удобства, но не
    # обязателен (может быть NULL у личного, если у автора нет отдела).
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "(is_system = false AND user_id IS NOT NULL) "
            "OR (is_system = true AND user_id IS NULL AND department_id IS NOT NULL)",
            name="ck_console_macro_scope",
        ),
        Index("ix_console_macros_system_dept", "is_system", "department_id"),
    )
