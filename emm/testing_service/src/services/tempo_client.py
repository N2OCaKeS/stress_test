"""Клиент Tempo Timesheets REST API (§9.1 плана миграции — HR-отчёт по активности).

Перенос легаси `ReportTempo.tempo_parse`
(`allta_app/reports/departament_reports/libreport.py`): один `POST
.../worklogs/search` за весь месяц, авторизация — тот же `jira_token`, что и
`jira_report_client.py` (Tempo Timesheets — Jira-плагин, живёт на том же
хосте/токене). Заголовок — та же легаси-особенность без `Bearer `-префикса.

**Не проверено против живого Tempo** — построено по буквальному повторению
легаси-запроса, не по работающему ответу реального инстанса.
"""

from __future__ import annotations

import logging

import httpx

from src.core.config import get_settings
from src.core.exceptions import ServiceUnavailableError

logger = logging.getLogger("testing_service.tempo_client")

_WORKLOGS_SEARCH_PATH = "/rest/tempo-timesheets/4/worklogs/search"


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


def _base(base_url: str) -> str:
    return base_url.rstrip("/")


async def search_worklogs(
    *, base_url: str, jira_token: str, team_id: str, month: str,
) -> list[dict]:
    """`POST .../worklogs/search` за `month` ("YYYY-MM") → сырой список worklog-записей.

    Каждый элемент несёт `worker` (Tempo/Jira user key), `started`
    ("YYYY-MM-DD HH:MM:SS.ffffff"), `timeSpentSeconds`, `issue.key` — сведение
    в per-(worker, day) часы/задачи делает `services/activity_report.py`.
    `team_id` — легаси хардкодил список `["7"]`, здесь per-department строка,
    оборачивается в одноэлементный список тут же (Tempo API принимает только
    список).
    """
    settings = get_settings()
    url = f"{_base(base_url)}{_WORKLOGS_SEARCH_PATH}"
    headers = {"Authorization": jira_token, "Content-Type": "application/json"}
    payload = {"from": f"{month}-01", "to": f"{month}-30", "teamId": [team_id], "includeSubtasks": True}
    async with build_client(settings.tempo_request_timeout_seconds) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="TEMPO_UNREACHABLE",
                message=f"Unable to reach Tempo: {type(exc).__name__}",
            ) from exc
    if response.status_code != 200:
        logger.warning(
            "tempo: search_worklogs(team=%s, month=%s) failed status=%s",
            team_id, month, response.status_code,
        )
        raise ServiceUnavailableError(
            error_code="TEMPO_ERROR",
            message=f"Tempo returned {response.status_code} searching worklogs",
        )
    try:
        return response.json() or []
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="TEMPO_ERROR",
            message="Tempo returned a non-JSON body searching worklogs",
        ) from exc
