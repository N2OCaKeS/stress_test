"""Настройки интеграции отдела с Jira/Zephyr/Confluence (§2.4, §3.5, §6 плана миграции).

Одна строка на `department_id`. Per-department, потому что у каждого отдела —
своя учётка Jira/Zephyr/Confluence и, возможно, свой URL Jira (не все отделы
обязаны сидеть на одном инстансе). `credential_id` — id credential в
`secret_service`, БЕЗ FK (другая БД); `jira_base_url`/`confluence_base_url` —
обычные строки, не секреты, поэтому хранятся прямо здесь, а не в
secret_service. Механизм, которым бот `testing_service` получает доступ к
чужой (department-owned) кред'е secret_service по `credential_id`, на момент
этой волны ещё не спроектирован — см. `services/secret_client.py`.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class DepartmentIntegrationSettings(Base):
    """Per-department credential_id + base URL'ы для Jira/Zephyr/Confluence."""

    __tablename__ = "department_integration_settings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # id credential в secret_service. Без FK — своя БД; nullable — отдел мог
    # ещё не настроить интеграцию.
    credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    jira_base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    confluence_base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
