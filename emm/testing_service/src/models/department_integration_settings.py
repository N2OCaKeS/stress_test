"""Настройки интеграции отдела с Jira/Zephyr/Confluence/Bitbucket/Tempo (§2.4, §3.5, §6, §9 плана миграции).

Одна строка на `department_id`. Per-department, потому что у каждого отдела —
своя учётка Jira/Zephyr/Confluence и, возможно, свой URL Jira (не все отделы
обязаны сидеть на одном инстансе). `credential_id` — id credential в
`secret_service`, БЕЗ FK (другая БД); `jira_base_url`/`confluence_base_url` —
обычные строки, не секреты, поэтому хранятся прямо здесь, а не в
secret_service. `credential_id` должен указывать на credential со
scope=service — так бот `testing_service` (платформенный сервис-бот,
`is_service_bot=True`) получает read/reveal независимо от отдела-владельца;
завести такую credential — задача department_admin'а этого отдела (см.
`services/secret_client.py`).

Поля волны 10 (HR-отчёт по активности, §9.1, `services/activity_report.py`):

* `bitbucket_base_url`/`bitbucket_project_key`/`bitbucket_repo_slug` — куда и
  какой репозиторий опрашивать за коммитами. `bitbucket_credential_id` —
  ОТДЕЛЬНЫЙ credential (basic auth login/password), потому что у Bitbucket и
  Jira/Confluence этого же инстанса могут быть разные сервисные учётки; тот
  же credential_id, что уже есть выше, сюда не годится по умолчанию.

Поле C4 (`SERVICE_CREDENTIALS.md`, `services/run_summary.py`,
`services/stp_matrix.py`, `services/activity_report.py`):

* `confluence_credential_id` — отдельная учётка для всех Confluence-путей
  (end-of-run комментарий, HR-отчёт, СТП-матрица). Jira/Zephyr/Tempo
  по-прежнему используют `credential_id` без изменений. Пусто — Confluence-
  потребители используют `credential_id` (совместимость с прежней общей
  учёткой Jira+Confluence).
* `jira_board_id`/`tempo_team_id` — легаси-хардкоды (`340`/`["7"]`), теперь
  per-department (Tempo сам API поддерживает список teamId, но здесь одна
  отдельская команда — строка, не список; API-клиент оборачивает её в список
  сам, см. `services/tempo_client.py`).
* `confluence_report_page_space`/`confluence_report_parent_page_title` — куда
  публиковать HR-отчёт. `confluence_report_page_space` без дефолта — если не
  задан, `services/activity_report.py` отвечает явной ошибкой "не настроено",
  не тем же `space="AL"`, что у end-of-run комментария (§9.2) — разные
  Confluence-пространства, разные механизмы.

Поля §D2/D3 (публикация СТП-матрицы, `services/stp_matrix.py`):

* `stp_matrix_confluence_space`/`stp_matrix_confluence_root_page_title` —
  третье, отдельное от HR-отчёта и end-of-run комментария Confluence-
  назначение. Легаси хардкодил пространство `DEVQA` и grandparent-заголовок
  `'Состав тестового прогона'` платформенно на все отделы — здесь оба поля
  per-department, тот же credential_id, что и выше (Jira/Confluence общий
  на отдел).
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class DepartmentIntegrationSettings(Base):
    """Per-department credential_id + base URL'ы для Jira/Zephyr/Confluence/Bitbucket/Tempo."""

    __tablename__ = "department_integration_settings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # id credential в secret_service. Без FK — своя БД; nullable — отдел мог
    # ещё не настроить интеграцию.
    credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    jira_base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    confluence_base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Отдельная учётка для Confluence (C4) — отдел может завести Jira и
    # Confluence на разных сервисных записях. Пусто — потребители, публикующие
    # в Confluence, падают обратно на `credential_id` (совместимость с прежней
    # общей учёткой).
    confluence_credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── HR-отчёт по активности (§9.1, волна 10) ──────────────────────────────
    bitbucket_base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    bitbucket_project_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bitbucket_repo_slug: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Отдельный credential от `credential_id` выше — у Bitbucket этого же
    # инстанса может быть своя сервисная учётка, отличная от Jira/Confluence.
    bitbucket_credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    jira_board_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tempo_team_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confluence_report_page_space: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confluence_report_parent_page_title: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # ── СТП-матрица (§D2/D3) ──────────────────────────────────────────────
    stp_matrix_confluence_space: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stp_matrix_confluence_root_page_title: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
