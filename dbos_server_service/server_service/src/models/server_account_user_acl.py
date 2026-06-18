"""Модель ServerAccountUserAcl — прямой (per-account) грант на учётку.

Аддитивно к ролевой матрице: строка раздаёт конкретному пользователю набор
действий на одну конкретную учётку. Доступ пользователя к действию = роль
отдела даёт это действие ИЛИ есть прямой грант на эту учётку. Грант только
расширяет — deny/сужения тут нет.

Скоуп — отдел учётки: `department_id` денормализован с `server_account` и
держится сервисным слоем (выдавать грант можно только в пределах отдела
учётки, пользователю из того же отдела). UNIQUE(account_id, user_id) — один
грант на пару; повторная выдача обновляет набор флагов существующей строки.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ServerAccountUserAcl(Base):
    """Один per-account грант: (account_id, user_id) → набор разрешённых действий."""

    __tablename__ = "server_account_user_acl"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("server_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Soft-FK на auth_service identity (`usr_<hex>` / `bot_<hex>`). На практике
    # грантуют людям (usr_), но формат допускает и bot_ для forward-совместимости.
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Денормализованная копия отдела учётки — грант живёт в её отделе. Сервисный
    # слой следит, чтобы user_id был из этого же отдела.
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    can_view: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_view_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_console: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_update: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_provision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_deprovision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_rotate_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_grant_sudo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Soft-FK на auth_service identity того, кто выдал грант.
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("account_id", "user_id", name="uq_account_user_acl"),
    )
