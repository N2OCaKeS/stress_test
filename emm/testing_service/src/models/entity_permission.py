"""Матрица entity-permissions — action-based fine-grained access control.

Структура повторяет одноимённые таблицы `server_service`/`secret_service`:
одна строка раздаёт один action на один entity type одной роли, эффективный
набор действий пользователя — union по всем его ролям в этом сервисе.

**Department scope** — `department_id` nullable.

* `department_id IS NULL` — *system-wide* grant. Применяется ко всем
  пользователям с ролью независимо от отдела. Зарезервировано под системные
  роли (`guest`/`admin`), чья семантика едина platform-wide.
* `department_id IS NOT NULL` — *per-department* grant, для кастомных ролей
  отдела.

Эффективный доступ:
`(role IN identity.service_roles[testing_service]) AND
 (department_id IS NULL OR department_id = identity.department_id)`.

Уникальность — два partial unique-индекса (COALESCE по nullable-колонке не
переносим между БД, partial-индексы переносимы):

* `uq_entity_permissions_global` — `(entity_type, role, action)` WHERE
  `department_id IS NULL`;
* `uq_entity_permissions_per_dept` — `(entity_type, role, action,
  department_id)` WHERE `department_id IS NOT NULL`.

System-wide и per-department строка для одной тройки сосуществуют by design:
отдел может добавить более узкий grant поверх общего без коллизии.
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
    # FK на departments нет — departments принадлежат auth_service, у нас своя
    # БД. Целостность держит application code, который пишет сюда только id из
    # провалидированного identity.
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Soft-FK на auth_service.users.id / bot_accounts.id (`usr_<hex>`/`bot_<hex>`).
    # У сидированных миграцией строк пусто.
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
            "uq_entity_permissions_global",
            "entity_type",
            "role",
            "action",
            unique=True,
            postgresql_where=(department_id.is_(None)),
        ),
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
