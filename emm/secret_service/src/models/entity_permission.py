"""Матрица entity-permissions — тип-wide action-based доступ.

Одна строка раздаёт один action на один entity type одной роли. Эффективный
набор actions у пользователя — union по всем его ролям в secret_service. У
secret_service единственный тип сущности — `secret`, поэтому матрица описывает
роль × action на неличные секреты отдела.

**Department scope** — `department_id` nullable.

* `department_id IS NULL` — *system-wide* grant. Применяется ко всем
  пользователям с ролью независимо от отдела. Зарезервировано под системные
  роли (`guest`/`admin`), чья семантика едина platform-wide; такие строки
  сеются миграциями.
* `department_id IS NOT NULL` — *per-department* grant для кастомных ролей;
  им управляет `admin` secret_service'а или `department_admin` этого отдела.

Эффективный доступ:
`(role IN identity.service_roles[secret_service]) AND
 (department_id IS NULL OR department_id = <owner_dept>)`.

Уникальность — двумя partial unique-индексами (COALESCE по nullable не
переносим между БД, partial-индексы — переносимы):

* `uq_secret_entity_permissions_global` — `(entity_type, role, action)` WHERE
  `department_id IS NULL`.
* `uq_secret_entity_permissions_per_dept` — `(entity_type, role, action,
  department_id)` WHERE `department_id IS NOT NULL`.

System-wide и per-department строка для одной тройки могут сосуществовать — by
design, чтобы department-admin мог добавить более узкий per-dept grant поверх
system-wide без коллизии.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class EntityPermission(Base):
    """Одна (entity_type, role, action, department_id?) запись матрицы."""

    __tablename__ = "entity_permissions"

    # id формата `prm_<32 hex>`.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL = system-wide (встроенные роли); не-NULL = per-department (кастомные).
    # FK на departments здесь нет — departments принадлежат auth_service,
    # целостность обеспечивает application code (пишет ID'шники только из
    # валидированного Identity).
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Soft-FK на auth identity (`usr_…`/`bot_…`); NULL для seed-данных.
    granted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
        Index(
            "uq_secret_entity_permissions_global",
            "entity_type",
            "role",
            "action",
            unique=True,
            postgresql_where=(department_id.is_(None)),
        ),
        Index(
            "uq_secret_entity_permissions_per_dept",
            "entity_type",
            "role",
            "action",
            "department_id",
            unique=True,
            postgresql_where=(department_id.is_not(None)),
        ),
    )
