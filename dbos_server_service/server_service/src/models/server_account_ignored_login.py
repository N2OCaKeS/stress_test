"""Модель ServerAccountIgnoredLogin — список логинов, которые инвентаризация
не считает «незнакомыми».

Скоуп — отдел: один и тот же login на разных серверах одного отдела игнорится
единой записью. Reconcile и discovery пропускают логины из этого списка: они
не попадают в `unknown_users` ответа коллбэка и не дрейфятся как unknown_login.
Это про штатные системные/служебные учётки, которые заводить в БД смысла нет
(`nobody`, мониторинг-агенты и т.п.), но которые проходят UID-фильтр воркера.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ServerAccountIgnoredLogin(Base):
    """Один игнор-логин в пределах отдела.

    UNIQUE(department_id, login) — повторно заигнорить тот же логин в отделе
    нельзя; снять и добавить заново — можно.
    """

    __tablename__ = "server_account_ignored_login"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    login: Mapped[str] = mapped_column(String(128), nullable=False)
    # Свободный комментарий оператора — зачем логин в игноре. Опционален.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft-FK на auth_service identity (`usr_<hex>` / `bot_<hex>`).
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "department_id", "login", name="uq_ignored_login_dept_login"
        ),
    )
