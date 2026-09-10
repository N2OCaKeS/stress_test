"""Сотрудник отдела для HR-отчёта по активности (§9.1 плана миграции).

Легаси (`libreport.py`) хардкодил три словаря сразу в коде: Bitbucket-логин →
ФИО, `JIRAUSER...` id → ФИО, ФИО → ФИО (для Jira-комментариев, где ключ уже
`displayName`). Здесь это одна таблица, редактируемая через API, а не через
правку кода при каждом кадровом изменении.

Все три идентификатора-источника (`bitbucket_username`/`jira_author_name`/
`jira_tempo_worker_key`) nullable и независимы — сотрудник может, например,
не коммитить в Bitbucket, но комментировать в Jira. `services/activity_report.py`
сводит метрики по каждому непустому идентификатору отдельно, `display_name` —
общий человекочитаемый ключ строки итоговой таблицы.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class DepartmentReportMember(Base):
    """Одна строка — один сотрудник отдела, учитываемый в HR-отчёте."""

    __tablename__ = "department_report_members"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # `commit['author']['name']` в Bitbucket Server REST API.
    bitbucket_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # `comment['author']['displayName']` в Jira REST API — то же самое имя,
    # что и `commit['author']['name']` у легаси-Jira-коммитов, но это не одно
    # и то же поле, поэтому отдельная колонка.
    jira_author_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # `entry['worker']` в ответе Tempo worklogs/search (обычно `JIRAUSER...`).
    jira_tempo_worker_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Soft-FK на auth_service identity (`usr_<hex>`/`bot_<hex>`).
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
