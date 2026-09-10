"""Клиент Jira-эндпоинтов, специфичных для HR-отчёта по активности (§9.1 плана миграции).

Отдельный файл от `zephyr_client.py`: тот покрывает Zephyr Scale ATM
(тест-раны/результаты, §6), этот — GreenHopper-спринты/issue-комментарии,
нужные только для подсчёта продуктивности (легаси `ReportJira.sprints_id`/
`issues_comments`, `allta_app/reports/departament_reports/libreport.py`).
Разные наборы Jira REST API этого же инстанса, разные вызывающие сервисы —
не стоило смешивать их в одном модуле.

**Важная деталь легаси, воспроизведённая буквально**: заголовок
`Authorization: {jira_token}` — токен кладётся В ГОЛОМ ВИДЕ, БЕЗ префикса
`Bearer `. Это особенность конкретной инсталляции Jira (PAT, который сама
Jira трактует как персональный токен без схемы), не общая практика — поэтому
здесь НЕ переиспользуется `core.http.bearer_header` (тот всегда добавляет
`Bearer `), а собственный маленький хелпер ниже.

**Не проверено против живой Jira** — реализация построена по буквальному
повторению легаси-эндпоинтов и их параметров, не по работающему запросу к
реальному инстансу.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from src.core.config import get_settings
from src.core.exceptions import ServiceUnavailableError

logger = logging.getLogger("testing_service.jira_report_client")


def _raw_auth_header(jira_token: str) -> dict[str, str]:
    """`Authorization: <token>` без `Bearer `-префикса — легаси-особенность инстанса."""
    return {"Authorization": jira_token, "Accept": "application/json, text/plain, */*"}


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


def _base(base_url: str) -> str:
    return base_url.rstrip("/")


async def get_sprint_ids(*, base_url: str, jira_token: str, board_id: str) -> list[int]:
    """`GET /rest/greenhopper/1.0/sprintquery/{board_id}` → id всех спринтов доски.

    Легаси `ReportJira.sprints_id` (первая половина — сбор id, фильтр по
    месяцу отчёта делает `services/activity_report.py` после
    `get_sprint_details` на каждый id).
    """
    settings = get_settings()
    url = f"{_base(base_url)}/rest/greenhopper/1.0/sprintquery/{board_id}"
    async with build_client(settings.zephyr_request_timeout_seconds) as client:
        try:
            response = await client.get(
                url,
                params={"includeHistoricalSprints": "true", "includeFutureSprints": "true"},
                headers=_raw_auth_header(jira_token),
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="JIRA_UNREACHABLE",
                message=f"Unable to reach Jira: {type(exc).__name__}",
            ) from exc
    if response.status_code != 200:
        logger.warning(
            "jira_report: get_sprint_ids(board=%s) failed status=%s", board_id, response.status_code,
        )
        raise ServiceUnavailableError(
            error_code="JIRA_ERROR",
            message=f"Jira returned {response.status_code} listing sprint ids",
        )
    try:
        sprints = response.json().get("sprints") or []
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="JIRA_ERROR",
            message="Jira returned a non-JSON body listing sprint ids",
        ) from exc
    return [s["id"] for s in sprints if "id" in s]


async def get_sprint_details(*, base_url: str, jira_token: str, sprint_id: int) -> dict:
    """`GET /rest/agile/latest/sprint/{id}` → детали спринта (`startDate`/`completeDate`)."""
    settings = get_settings()
    url = f"{_base(base_url)}/rest/agile/latest/sprint/{sprint_id}"
    async with build_client(settings.zephyr_request_timeout_seconds) as client:
        try:
            response = await client.get(url, headers=_raw_auth_header(jira_token))
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="JIRA_UNREACHABLE",
                message=f"Unable to reach Jira: {type(exc).__name__}",
            ) from exc
    if response.status_code != 200:
        logger.warning(
            "jira_report: get_sprint_details(%s) failed status=%s", sprint_id, response.status_code,
        )
        raise ServiceUnavailableError(
            error_code="JIRA_ERROR",
            message=f"Jira returned {response.status_code} fetching sprint {sprint_id}",
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="JIRA_ERROR",
            message="Jira returned a non-JSON body fetching sprint details",
        ) from exc


def sprint_matches_month(sprint: dict, year: int, month: int) -> bool:
    """True, если `startDate` ИЛИ `completeDate` спринта попадает в `year-month`.

    Легаси `ReportJira.sprints_id` фильтрует ровно так же — спринт "за месяц",
    если он либо начался, либо завершился в этом месяце (не обязательно оба).
    """
    for field in ("startDate", "completeDate"):
        raw = sprint.get(field)
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if parsed.year == year and parsed.month == month:
            return True
    return False


async def search_sprint_issues(*, base_url: str, jira_token: str, sprint_id: int, max_results: int = 500) -> list[dict]:
    """`GET /rest/api/latest/search?jql=sprint={id}` → issues спринта (сырые, `values`/`issues`)."""
    settings = get_settings()
    url = f"{_base(base_url)}/rest/api/latest/search"
    async with build_client(settings.zephyr_request_timeout_seconds) as client:
        try:
            response = await client.get(
                url,
                params={"jql": f"sprint={sprint_id}", "maxResults": max_results},
                headers=_raw_auth_header(jira_token),
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="JIRA_UNREACHABLE",
                message=f"Unable to reach Jira: {type(exc).__name__}",
            ) from exc
    if response.status_code != 200:
        logger.warning(
            "jira_report: search_sprint_issues(%s) failed status=%s", sprint_id, response.status_code,
        )
        raise ServiceUnavailableError(
            error_code="JIRA_ERROR",
            message=f"Jira returned {response.status_code} searching sprint {sprint_id} issues",
        )
    try:
        return response.json().get("issues") or []
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="JIRA_ERROR",
            message="Jira returned a non-JSON body searching sprint issues",
        ) from exc


async def get_issue_comments(*, base_url: str, jira_token: str, issue_key: str) -> list[dict]:
    """`GET /rest/api/latest/issue/{key}/comment` → комментарии issue (сырые, `comments`)."""
    settings = get_settings()
    url = f"{_base(base_url)}/rest/api/latest/issue/{issue_key}/comment"
    async with build_client(settings.zephyr_request_timeout_seconds) as client:
        try:
            response = await client.get(url, headers=_raw_auth_header(jira_token))
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="JIRA_UNREACHABLE",
                message=f"Unable to reach Jira: {type(exc).__name__}",
            ) from exc
    if response.status_code != 200:
        logger.warning(
            "jira_report: get_issue_comments(%s) failed status=%s", issue_key, response.status_code,
        )
        raise ServiceUnavailableError(
            error_code="JIRA_ERROR",
            message=f"Jira returned {response.status_code} fetching comments for {issue_key}",
        )
    try:
        return response.json().get("comments") or []
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="JIRA_ERROR",
            message="Jira returned a non-JSON body fetching issue comments",
        ) from exc
