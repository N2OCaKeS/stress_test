"""Справочник категорий серверов по мощности. servers.category_id ссылается сюда."""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class ServerCategory(Base):
    """Категория стенда по мощности: машинный код + человекочитаемое имя.

    Каталог платформенный, без привязки к отделу — «мощность» одинаково
    трактуется всеми. Стартовый набор (low/middle/high/workstation) сидится
    миграцией, дальше пополняется через API: список категорий не фиксирован
    кодом, чтобы новая категория заводилась данными, а не релизом.
    """

    __tablename__ = "server_categories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # Soft-FK на auth_service identity (`usr_<hex>` / `bot_<hex>`).
    # У сидированных миграцией категорий пусто.
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    servers: Mapped[list["Server"]] = relationship(  # noqa: F821
        "Server", back_populates="category"
    )
