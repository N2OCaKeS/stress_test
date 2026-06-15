"""Модель CredentialUserACL — per-credential доступ конкретному пользователю.

Отдельный от RoleACL слой: RoleACL выдаёт доступ роли внутри департамента,
а UserACL — поимённо одному `user_id`. Нужен для personal-кред, где владелец
хочет пустить конкретных коллег, не плодя для этого service-роли.

`(cred_id, user_id)` уникально — у одного пользователя ровно одна запись прав
на креду. `granted_by_user_id` без cascade'а на auth.users (симметрия с
RoleACL): удаление выдавшего не отзывает выданное. Cred удаляется CASCADE.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class CredentialUserACL(Base):
    """Доступ конкретного пользователя к конкретной credential."""

    __tablename__ = "user_acls"

    # id формата `uacl_<32 hex>`.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    cred_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Soft-FK на auth.users.id (`usr_…`). Бот сюда не попадает — у него нет
    # user-identity, и personal-доступ ему не выдают.
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)

    can_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Soft-FK на auth identity (`usr_…`/`bot_…`). Без cascade'а — решение
    # остаётся в силе даже если granter удалён.
    granted_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("cred_id", "user_id", name="uq_user_acl_cred_user"),
    )
