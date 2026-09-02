"""Инстанс-уровневый ACL — точечные гранты роли на КОНКРЕТНЫЙ ресурс.

Слой поверх тип-wide матрицы `entity_permissions`. Одна строка раздаёт один
action одной роли на один конкретный объект (`resource_type` + `resource_id`),
а не на весь тип сразу. Каждая строка несёт `effect` — `allow` (добавить право
поверх тип-wide матрицы) либо `deny` (запретить его этой роли на этом ресурсе).
Эффективный доступ роли к ресурсу: `deny`-строка перекрывает всё, иначе
`allow`-строка разрешает, иначе действует база `entity_permissions`. Итог для
caller'а — OR по его ролям.

**Субъект гранта — РОЛЬ** (как и в `entity_permissions`). Пользователям и
группам роли назначает auth_service; server_service инстанс-гранты вешает на
имя роли.

**resource_type** — `server` либо `server_account` (см.
`constants.RESOURCE_ACL_TYPES`). `resource_id` — soft-FK на `servers.id` /
`server_accounts.id` своей БД; FK на уровне БД нет (как и у `entity_permissions`),
целостность держит application code + каскадная чистка при удалении ресурса.

**Department scope** — `department_id` nullable, та же семантика, что в
`entity_permissions`:

* `NULL` — system-wide грант (встроенные роли), матчится любому caller'у с
  ролью;
* не-NULL — per-department грант (кастомные роли), матчится только caller'у из
  этого отдела. Для инстанс-грантов per-dept берётся `department_id` самого
  ресурса (ресурс всегда принадлежит отделу).

Уникальность — два partial unique-индекса (как в `entity_permissions`):

* `uq_resource_role_permissions_global` — `(resource_type, resource_id, role,
  action)` WHERE `department_id IS NULL`.
* `uq_resource_role_permissions_per_dept` — `(resource_type, resource_id, role,
  action, department_id)` WHERE `department_id IS NOT NULL`.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ResourceRolePermission(Base):
    """Один инстанс-грант (resource_type, resource_id, role, action, dept?)."""

    __tablename__ = "resource_role_permissions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # server | server_account (constants.RESOURCE_ACL_TYPES). CHECK на стороне БД.
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # soft-FK на servers.id / server_accounts.id своей БД.
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    # allow — добавляет право поверх тип-wide матрицы; deny — запрещает его этой
    # роли на этом ресурсе (override базы). Дефолт allow — старая аддитивная
    # семантика. Precedence на чтении (services/permissions): deny > allow > база.
    effect: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="allow"
    )
    # NULL = system-wide (встроенные роли); не-NULL = per-department (кастомные).
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Soft-FK на auth_service identity (`usr_<hex>` / `bot_<hex>`); CHECK по
    # формату — ck_resource_role_permissions_granted_by_format.
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
        # Read-pattern для has_resource_action / effective: фильтр по
        # (resource_type, resource_id, role) + dept-scope.
        Index(
            "ix_resource_role_permissions_lookup",
            "resource_type",
            "resource_id",
            "role",
        ),
        # System-wide uniqueness.
        Index(
            "uq_resource_role_permissions_global",
            "resource_type",
            "resource_id",
            "role",
            "action",
            unique=True,
            postgresql_where=(department_id.is_(None)),
        ),
        # Per-department uniqueness.
        Index(
            "uq_resource_role_permissions_per_dept",
            "resource_type",
            "resource_id",
            "role",
            "action",
            "department_id",
            unique=True,
            postgresql_where=(department_id.is_not(None)),
        ),
        CheckConstraint(
            "effect IN ('allow', 'deny')",
            name="ck_resource_role_permissions_effect",
        ),
    )
