"""Матрица entity-permissions — action-based fine-grained access control.

Одна строка раздаёт один action на один entity type одной роли. Эффективный
набор actions у пользователя — union по всем ролям, которые он имеет в
сервисе. Особые случаи (например, platform-роль account_admin) обходят
матрицу целиком в service-layer'е.

**Department scope** — `department_id` nullable.

* `department_id IS NULL` — *system-wide* grant. Применяется ко всем
  пользователям с ролью независимо от department'а. Зарезервировано под
  **встроенные роли** (`guest`/`reader`/`operator`/`admin`/`worker_bot`),
  чья семантика должна оставаться единой platform-wide. Писать такие строки
  может только ``account_admin``.
* `department_id IS NOT NULL` — *per-department* grant. Применяется только
  к пользователям из конкретного department'а. Используется для **кастомных
  ролей**, которыми управляет носитель сервисной роли ``admin`` или
  ``department_admin`` этого department'а.

Эффективный доступ для пользователя:
`(role IN identity.service_roles[server_service]) AND
 (department_id IS NULL OR department_id = identity.department_id)`.

Уникальность реализована двумя **partial unique-индексами** (`COALESCE` по
`department_id` не переносим между БД; partial-индексы — переносимы):

* `uq_entity_permissions_global` — `(entity_type, role, action)` WHERE
  `department_id IS NULL` — одна system-wide-строка на тройку.
* `uq_entity_permissions_per_dept` — `(entity_type, role, action,
  department_id)` WHERE `department_id IS NOT NULL` — одна на тройку per
  department.

System-wide и per-department строка для одной тройки могут сосуществовать —
by design, чтобы department-admin мог добавить *более узкий* per-dept grant
поверх system-wide без коллизии.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class EntityPermission(Base):
    """Одна (entity_type, role, action, department_id?) запись матрицы."""

    __tablename__ = "entity_permissions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL = system-wide (встроенные роли); не-NULL = per-department (кастомные).
    # FK на departments здесь нет — server_service держит свою БД, departments
    # принадлежат auth_service. Целостность обеспечивает application code,
    # который пишет ID'шники только из валидированного IdentityContext.
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
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
        # System-wide uniqueness: одна строка на (entity_type, role, action)
        # с department_id IS NULL. Встроенные роли живут тут.
        Index(
            "uq_entity_permissions_global",
            "entity_type",
            "role",
            "action",
            unique=True,
            postgresql_where=(department_id.is_(None)),
        ),
        # Per-department uniqueness: одна строка на (entity_type, role, action,
        # department_id) где department_id IS NOT NULL. Кастомные роли тут.
        Index(
            "uq_entity_permissions_per_dept",
            "entity_type",
            "role",
            "action",
            "department_id",
            unique=True,
            postgresql_where=(department_id.is_not(None)),
        ),
    )
