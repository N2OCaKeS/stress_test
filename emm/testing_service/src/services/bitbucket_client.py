"""Клиент Bitbucket Server REST API (§9.1 плана миграции — HR-отчёт по активности).

Перенос легаси `ReportGit.get_branches`/`get_commits`
(`allta_app/reports/departament_reports/libreport.py`): basic auth
(login/password из отдельного `department_integration_settings.
bitbucket_credential_id`, резолв — зона ответственности вызывающего
`services/activity_report.py`, этот модуль ничего не знает про departments).

Креды необязательны. Легаси ходил сюда с пустым паролем
(`monthly_report.py:23` `PASSWORD = ''` → `libreport.py:427,449`
`auth=(username, '')`), то есть фактически без секрета — для репозитория,
открытого на чтение, этого хватало. Если вызывающий не передал ни логина, ни
пароля, запрос уходит вообще без `Authorization`.

`bitbucket_credential_id` — именно basic-auth пара, а не заголовок. Секрет для
клонирования на стенде лежит отдельно (`git_credential_id`, см.
`services/queue.py::resolve_git_token`): там значение уходит в
`Authorization` целиком и обязано нести схему, здесь схема в пароле сломает
запрос. Разделять их приходится потому, что легаси тоже держало эти значения
врозь.

Легаси считает коммиты по ВСЕМ веткам репозитория (сначала список веток,
потом `commits?until=<branch>` на каждую) — тот же приём здесь, без
дедупликации коммитов, видимых сразу в нескольких ветках (легаси тоже их не
дедуплицирует, один и тот же коммит на двух ветках считается дважды).

**Не проверено против живого Bitbucket** — реализация построена по
буквальному повторению легаси-эндпоинтов, не по работающему запросу к
реальному инстансу. Перед реальным end-to-end использованием стоит свериться
вручную.
"""

from __future__ import annotations

import logging

import httpx

from src.core.config import get_settings
from src.core.exceptions import ServiceUnavailableError

logger = logging.getLogger("testing_service.bitbucket_client")

_DEFAULT_BRANCHES_LIMIT = 100
_DEFAULT_COMMITS_LIMIT = 10000


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


def _base(base_url: str) -> str:
    return base_url.rstrip("/")


def _auth(username: str | None, password: str | None) -> tuple[str, str] | None:
    """Пара для basic auth, либо `None` — тогда запрос уходит анонимно."""
    if not username and not password:
        return None
    return (username or "", password or "")


async def get_branches(
    *, base_url: str, username: str | None, password: str | None, project_key: str, repo_slug: str,
    limit: int = _DEFAULT_BRANCHES_LIMIT,
) -> list[str]:
    """`GET .../branches` → список `displayId`. Легаси `ReportGit.get_branches`."""
    settings = get_settings()
    url = f"{_base(base_url)}/rest/api/latest/projects/{project_key}/repos/{repo_slug}/branches"
    async with build_client(settings.bitbucket_request_timeout_seconds) as client:
        try:
            response = await client.get(
                url, params={"limit": limit}, auth=_auth(username, password),
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="BITBUCKET_UNREACHABLE",
                message=f"Unable to reach Bitbucket: {type(exc).__name__}",
            ) from exc
    if response.status_code != 200:
        logger.warning(
            "bitbucket: get_branches(%s/%s) failed status=%s",
            project_key, repo_slug, response.status_code,
        )
        raise ServiceUnavailableError(
            error_code="BITBUCKET_ERROR",
            message=f"Bitbucket returned {response.status_code} listing branches",
        )
    try:
        values = response.json().get("values") or []
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="BITBUCKET_ERROR",
            message="Bitbucket returned a non-JSON body listing branches",
        ) from exc
    return [b["displayId"] for b in values if b.get("displayId")]


async def get_commits(
    *, base_url: str, username: str | None, password: str | None, project_key: str, repo_slug: str,
    branch: str, limit: int = _DEFAULT_COMMITS_LIMIT,
) -> list[dict]:
    """`GET .../commits?until=<branch>` → сырые записи `values` (легаси `fetch_commits`).

    Каждый элемент несёт `author.name` и `authorTimestamp` (мс) — сведение в
    per-(author, day) счётчик делает `services/activity_report.py`, здесь
    только сырой pass-through ответа.
    """
    settings = get_settings()
    url = f"{_base(base_url)}/rest/api/latest/projects/{project_key}/repos/{repo_slug}/commits"
    async with build_client(settings.bitbucket_request_timeout_seconds) as client:
        try:
            response = await client.get(
                url, params={"until": branch, "limit": limit}, auth=_auth(username, password),
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="BITBUCKET_UNREACHABLE",
                message=f"Unable to reach Bitbucket: {type(exc).__name__}",
            ) from exc
    if response.status_code != 200:
        logger.warning(
            "bitbucket: get_commits(%s/%s, branch=%s) failed status=%s",
            project_key, repo_slug, branch, response.status_code,
        )
        raise ServiceUnavailableError(
            error_code="BITBUCKET_ERROR",
            message=f"Bitbucket returned {response.status_code} listing commits",
        )
    try:
        return response.json().get("values") or []
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="BITBUCKET_ERROR",
            message="Bitbucket returned a non-JSON body listing commits",
        ) from exc
