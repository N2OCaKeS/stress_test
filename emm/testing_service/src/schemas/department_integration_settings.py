"""Pydantic-схемы для /department-integration-settings (§2.4, §3.5 плана миграции)."""

import ipaddress
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Секрет отдела (Bearer PAT / basic-auth) уходит вместе с каждым запросом на
# эти base URL — опечатка или намеренная подмена на внутренний адрес не
# просто SSRF, а SSRF с приложенным живым токеном. Проверка нарочно грубая
# (схема + запрет на приватные/loopback/link-local диапазоны и localhost/
# .local), не полноценный allow-list — этого достаточно, чтобы отсечь
# случайную/злонамеренную опечатку на internal-хост, включая другие сервисы
# этого же кластера.
_LOCAL_HOST_SUFFIXES = (".local", ".internal")


def _validate_integration_base_url(value: str | None) -> str | None:
    if value is None:
        return value
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ValueError("base URL must start with https://")
    host = parsed.hostname
    if not host:
        raise ValueError("base URL must include a host")
    host = host.lower()
    if host == "localhost" or any(host.endswith(suffix) for suffix in _LOCAL_HOST_SUFFIXES):
        raise ValueError("base URL must not point to a local/internal host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    ):
        raise ValueError("base URL must not point to a private/internal address")
    return value


class DepartmentIntegrationSettingsUpdate(BaseModel):
    """Тело PUT /department-integration-settings/{department_id}. Upsert — все поля опциональны."""

    credential_id: str | None = Field(
        default=None, max_length=64,
        description="Id credential в secret_service (Jira/Zephyr токен отдела).",
    )
    jira_base_url: str | None = Field(
        default=None, max_length=256, description="Base URL Jira этого отдела.",
    )
    confluence_base_url: str | None = Field(
        default=None, max_length=256, description="Base URL Confluence этого отдела.",
    )
    confluence_credential_id: str | None = Field(
        default=None, max_length=64,
        description=(
            "Id credential в secret_service для Confluence (может отличаться от Jira). "
            "Пусто — Confluence-публикации используют credential_id."
        ),
    )
    bitbucket_base_url: str | None = Field(
        default=None, max_length=256, description="Base URL Bitbucket Server этого отдела.",
    )
    bitbucket_project_key: str | None = Field(
        default=None, max_length=64, description="Project key репозитория с коммитами отдела.",
    )
    bitbucket_repo_slug: str | None = Field(
        default=None, max_length=128, description="Slug репозитория с коммитами отдела.",
    )
    bitbucket_credential_id: str | None = Field(
        default=None, max_length=64,
        description="Id credential в secret_service для Bitbucket basic auth (может отличаться от Jira/Confluence).",
    )
    git_credential_id: str | None = Field(
        default=None, max_length=64,
        description=(
            "Id credential в secret_service для клонирования репозитория на стенде. "
            "Секрет подставляется в заголовок `Authorization` целиком, поэтому "
            "хранить нужно значение со схемой (`Bearer <токен>`). Пусто — "
            "используется bitbucket_credential_id."
        ),
    )
    jira_board_id: str | None = Field(
        default=None, max_length=32, description="Id доски Jira (Scrum board) для подсчёта спринтов отдела.",
    )
    tempo_team_id: str | None = Field(
        default=None, max_length=32, description="Id команды Tempo отдела.",
    )
    confluence_report_page_space: str | None = Field(
        default=None, max_length=64,
        description="Confluence space для публикации HR-отчёта. Пусто — отчёт для отдела недоступен.",
    )
    confluence_report_parent_page_title: str | None = Field(
        default=None, max_length=256,
        description="Заголовок родительской страницы, под которой заводится месячная страница отчёта.",
    )
    stp_matrix_confluence_space: str | None = Field(
        default=None, max_length=64,
        description="Confluence space для публикации сводной СТП-матрицы. Пусто — публикация недоступна.",
    )
    stp_matrix_confluence_root_page_title: str | None = Field(
        default=None, max_length=256,
        description="Заголовок корневой (grandparent) страницы иерархии СТП-матрицы.",
    )

    @field_validator("jira_base_url", "confluence_base_url", "bitbucket_base_url")
    @classmethod
    def _check_base_url(cls, value: str | None) -> str | None:
        return _validate_integration_base_url(value)


class DepartmentIntegrationSettingsResponse(BaseModel):
    """Карточка настроек интеграции отдела. Если строки в БД нет — отдаётся с пустыми полями."""

    model_config = ConfigDict(from_attributes=True)

    id: str | None = Field(default=None, description="None, если строка ещё не создана.")
    department_id: str
    credential_id: str | None = Field(default=None)
    jira_base_url: str | None = Field(default=None)
    confluence_base_url: str | None = Field(default=None)
    confluence_credential_id: str | None = Field(default=None)
    bitbucket_base_url: str | None = Field(default=None)
    bitbucket_project_key: str | None = Field(default=None)
    bitbucket_repo_slug: str | None = Field(default=None)
    bitbucket_credential_id: str | None = Field(default=None)
    git_credential_id: str | None = Field(default=None)
    jira_board_id: str | None = Field(default=None)
    tempo_team_id: str | None = Field(default=None)
    confluence_report_page_space: str | None = Field(default=None)
    confluence_report_parent_page_title: str | None = Field(default=None)
    stp_matrix_confluence_space: str | None = Field(default=None)
    stp_matrix_confluence_root_page_title: str | None = Field(default=None)
    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)
