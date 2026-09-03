"""ORM-модель `AcsDepartmentAccess` — per-department opt-in для снимков ACS.

Сам ACS и креды доступа к нему — общие на всю платформу (`AcsSettings`), но
возможность делать/восстанавливать снимки через него включается по отделам:
пока отдел не включён здесь явно, dispatch create/restore не пройдёт, даже
если у вызывающего есть право на действие в матрице (доп. ворота поверх
обычной action-матрицы, а не замена ей).

По структуре — копия `auth_service/src/models/department_docker_registry.py`,
но без FK на `departments`: server_service не имеет прямого доступа к БД
auth_service через границу сервисов (тот же подход, что и у
`servers.department_id` — soft-reference, просто `String`).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class AcsDepartmentAccess(Base):
    """Флаг доступности снимков ACS для конкретного отдела."""

    __tablename__ = "acs_department_access"
    __table_args__ = (
        UniqueConstraint("department_id", name="uq_acs_department_access_department"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # Soft-FK на auth_service identity (`usr_<hex>` / `bot_<hex>`), как в
    # остальных таблицах server_service (см. `vm_ip_pool.created_by`).
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
