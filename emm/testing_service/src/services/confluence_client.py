"""Клиент Confluence Server/Data Center REST API v1 (§2.7, §9.2 плана миграции).

Все операции принимают `base_url`/`bearer_token` параметрами — резолв кред
(department_integration_settings + secret_client.reveal_credential) остаётся
зоной ответственности вызывающего сервиса (`services/run_summary.py`), этот
модуль ничего не знает про departments. Тот же паттерн, что `zephyr_client.py`.

**Не проверено против живой Confluence** — реализация построена по публичной
документации Confluence Server/Data Center REST API (`/wiki/rest/api/content`),
не по работающему легаси-коду (легаси использовал `atlassian-python-api`,
здесь — прямой `httpx`, по требованию сессии). Особенно не проверено:
optimistic-locking контракт `update_comment` (требуется `version.number` на
единицу больше текущего — реализовано согласно документации, но не обкатано
на реальном инстансе) и структура `container` при создании комментария к
blog-посту. Перед реальным end-to-end использованием стоит свериться вручную
с актуальной Confluence.
"""

from __future__ import annotations

import logging

import httpx

from src.core.config import get_settings
from src.core.exceptions import ServiceUnavailableError
from src.core.http import bearer_header

logger = logging.getLogger("testing_service.confluence_client")

_CONTENT_PATH = "/wiki/rest/api/content"
_PAGE_SIZE = 50


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


def _base(base_url: str) -> str:
    return base_url.rstrip("/")


async def find_page_id(
    *, base_url: str, bearer_token: str, space: str, title: str,
) -> str | None:
    """`GET /wiki/rest/api/content?spaceKey=..&title=..&type=page` → id страницы.

    `None` — страница не найдена, сеть недоступна, либо неожиданный ответ.
    Best-effort: caller (`services/run_summary.py`) трактует это как
    `skipped_no_stp_page`, не как ошибку прогона.
    """
    settings = get_settings()
    async with build_client(settings.confluence_request_timeout_seconds) as client:
        try:
            response = await client.get(
                f"{_base(base_url)}{_CONTENT_PATH}",
                params={"spaceKey": space, "title": title, "type": "page"},
                headers=bearer_header(bearer_token),
            )
        except httpx.HTTPError as exc:
            logger.warning("confluence: find_page_id(%s) unreachable: %s", title, exc)
            return None
    if response.status_code != 200:
        logger.warning(
            "confluence: find_page_id(%s) returned %s", title, response.status_code,
        )
        return None
    try:
        results = response.json().get("results") or []
    except ValueError:
        return None
    for item in results:
        if item.get("title") == title:
            return item.get("id")
    return None


async def find_blogpost_id(
    *, base_url: str, bearer_token: str, space: str, title: str,
) -> str | None:
    """`GET /wiki/rest/api/content?spaceKey=..&type=blogpost` постранично → id поста.

    Точное совпадение заголовка, тот же приём, что легаси `libconfluence.py`
    (перебор всех blog-постов пространства, а не поиск по `title`-параметру —
    Confluence не гарантирует точное совпадение заголовка для blogpost через
    `title=`, поэтому фильтруем сами по каждой странице результатов).
    """
    settings = get_settings()
    start = 0
    async with build_client(settings.confluence_request_timeout_seconds) as client:
        while True:
            try:
                response = await client.get(
                    f"{_base(base_url)}{_CONTENT_PATH}",
                    params={
                        "spaceKey": space, "type": "blogpost",
                        "start": start, "limit": _PAGE_SIZE,
                    },
                    headers=bearer_header(bearer_token),
                )
            except httpx.HTTPError as exc:
                logger.warning("confluence: find_blogpost_id(%s) unreachable: %s", title, exc)
                return None
            if response.status_code != 200:
                logger.warning(
                    "confluence: find_blogpost_id(%s) returned %s", title, response.status_code,
                )
                return None
            try:
                body = response.json()
            except ValueError:
                return None
            results = body.get("results") or []
            for item in results:
                if item.get("title") == title:
                    return item.get("id")
            if len(results) < _PAGE_SIZE:
                return None
            start += _PAGE_SIZE


async def get_comments(
    *, base_url: str, bearer_token: str, content_id: str,
) -> list[dict]:
    """`GET /wiki/rest/api/content/{id}/child/comment?expand=version,body.storage`.

    Возвращает все дочерние комментарии контента (нужно найти текущий
    `version.number` своего комментария перед `update_comment`). Поднимает
    `ServiceUnavailableError` на сбой — в отличие от `find_*`, вызывается
    только когда мы уже точно знаем, что пост существует и есть, что
    обновлять, поэтому сбой здесь — реальная ошибка, а не штатный "не найдено".
    """
    settings = get_settings()
    async with build_client(settings.confluence_request_timeout_seconds) as client:
        try:
            response = await client.get(
                f"{_base(base_url)}{_CONTENT_PATH}/{content_id}/child/comment",
                params={"expand": "version,body.storage"},
                headers=bearer_header(bearer_token),
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="CONFLUENCE_UNREACHABLE",
                message=f"Unable to reach Confluence: {type(exc).__name__}",
            ) from exc
    if response.status_code != 200:
        logger.warning(
            "confluence: get_comments(%s) failed status=%s", content_id, response.status_code,
        )
        raise ServiceUnavailableError(
            error_code="CONFLUENCE_GET_COMMENTS_FAILED",
            message=f"Confluence returned {response.status_code} listing comments",
        )
    try:
        return response.json().get("results") or []
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="CONFLUENCE_ERROR",
            message="Confluence returned a non-JSON body",
        ) from exc


async def add_comment(
    *, base_url: str, bearer_token: str, content_id: str, body_html: str,
    container_type: str = "blogpost",
) -> str:
    """`POST /wiki/rest/api/content` (`type=comment`) → id созданного комментария.

    `container_type` — тип контента, к которому крепится комментарий; здесь
    всегда `"blogpost"` (единственный вызывающий сценарий — комментарий к
    blog-посту релиза), параметризовано на случай будущего переиспользования.
    """
    settings = get_settings()
    payload = {
        "type": "comment",
        "container": {"id": content_id, "type": container_type},
        "body": {"storage": {"value": body_html, "representation": "storage"}},
    }
    async with build_client(settings.confluence_request_timeout_seconds) as client:
        try:
            response = await client.post(
                f"{_base(base_url)}{_CONTENT_PATH}",
                json=payload, headers=bearer_header(bearer_token),
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="CONFLUENCE_UNREACHABLE",
                message=f"Unable to reach Confluence: {type(exc).__name__}",
            ) from exc
    if response.status_code not in (200, 201):
        logger.warning(
            "confluence: add_comment failed status=%s body=%s",
            response.status_code, response.text[:500],
        )
        raise ServiceUnavailableError(
            error_code="CONFLUENCE_ADD_COMMENT_FAILED",
            message=f"Confluence returned {response.status_code} adding a comment",
        )
    try:
        comment_id = response.json().get("id")
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="CONFLUENCE_ERROR",
            message="Confluence returned a non-JSON body",
        ) from exc
    if not comment_id:
        raise ServiceUnavailableError(
            error_code="CONFLUENCE_ERROR",
            message="Confluence response is missing the comment id",
        )
    return comment_id


async def update_comment(
    *, base_url: str, bearer_token: str, comment_id: str, body_html: str, version: int,
) -> None:
    """`PUT /wiki/rest/api/content/{id}` — заменить тело существующего комментария.

    `version` — ТЕКУЩИЙ `version.number` комментария (как вернул
    `get_comments`), не следующий: Confluence требует в теле запроса
    `version.number` строго на единицу больше текущего (optimistic locking),
    прибавление единицы сделано здесь, чтобы вызывающий код не должен был
    об этом помнить.
    """
    settings = get_settings()
    payload = {
        "id": comment_id,
        "type": "comment",
        "version": {"number": version + 1},
        "body": {"storage": {"value": body_html, "representation": "storage"}},
    }
    async with build_client(settings.confluence_request_timeout_seconds) as client:
        try:
            response = await client.put(
                f"{_base(base_url)}{_CONTENT_PATH}/{comment_id}",
                json=payload, headers=bearer_header(bearer_token),
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="CONFLUENCE_UNREACHABLE",
                message=f"Unable to reach Confluence: {type(exc).__name__}",
            ) from exc
    if response.status_code >= 300:
        logger.warning(
            "confluence: update_comment(%s) failed status=%s body=%s",
            comment_id, response.status_code, response.text[:500],
        )
        raise ServiceUnavailableError(
            error_code="CONFLUENCE_UPDATE_COMMENT_FAILED",
            message=f"Confluence returned {response.status_code} updating a comment",
        )
