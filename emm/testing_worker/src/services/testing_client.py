"""Исходящие вызовы в testing_service — очередь заданий (§5.5 плана миграции).

Два эндпоинта, зафиксированные `testing_service` (см. `testing_service/src/
api/v1/endpoints/internal_queue.py` + `src/schemas/queue.py`, отчёт
`emm/obsidian/reports/2026-09-09-204902-testing-service-queue-prepare.md`):

* `POST /internal/queue/claim` — без тела, отдаёт готовое задание либо
  `item: null`, если очередь пуста.
* `POST /internal/queue/{queue_item_id}/completed` — сообщает исход
  SSH-исполнения (успех/провал + exit_code + короткая причина), без
  полного лога — потоковое сохранение вывода появится отдельной волной.

Оба вызова идут через shared-secret канал: `Authorization: Bearer
<TESTING_SERVICE_INTERNAL_API_KEY>` + `X-Service-Identity: testing_worker`.

Сетевые сбои (testing_service временно недоступен) не поднимаются как
исключения наружу — `claim()`/`report_completed()` их логируют и
возвращают безопасный результат (`None` / просто ничего не делают), чтобы
одна недоступность внешнего сервиса не роняла весь polling-loop воркера.
Это отличает данный клиент от `server_client.py` в `testing_service`,
который вызывающий код (сервисный слой с транзакциями) сам решает как
обрабатывать — здесь единственный caller — сам polling-loop, и его
контракт («не падать на транзиентных сбоях») удобнее держать внутри
клиента.
"""

from __future__ import annotations

import logging

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME
from src.core.http import bearer_header

logger = logging.getLogger("testing_worker.testing_client")

_CLAIM_PATH = "/internal/queue/claim"
_COMPLETED_PATH_TEMPLATE = "/internal/queue/{queue_item_id}/completed"

# Таймаут HTTP-вызовов к testing_service. Небольшой — это s2s-канал внутри
# кластера, долгий ответ означает проблему, а не медленную сеть до стенда
# (SSH-таймауты — отдельные, гораздо более щедрые, см. `core/config.py`).
_REQUEST_TIMEOUT_SECONDS = 10.0


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


def _headers() -> dict[str, str] | None:
    settings = get_settings()
    if not settings.testing_service_internal_api_key:
        return None
    return {
        **bearer_header(settings.testing_service_internal_api_key),
        "X-Service-Identity": SERVICE_NAME,
    }


def _base_url() -> str | None:
    settings = get_settings()
    base = (settings.testing_service_url or "").rstrip("/")
    return base or None


async def claim() -> dict | None:
    """POST /internal/queue/claim. Возвращает `item`-словарь или `None`.

    `None` означает и «очередь пуста», и «testing_service недоступен» —
    вызывающий polling-loop в обоих случаях делает одно и то же: спит
    `queue_poll_interval_seconds` и пробует снова. Различать эти два случая
    в возврате смысла нет, разница видна только в логах (WARNING на сбое).
    """
    base = _base_url()
    headers = _headers()
    if base is None or headers is None:
        logger.warning(
            "testing_client.claim: TESTING_SERVICE_URL/TESTING_SERVICE_INTERNAL_API_KEY "
            "not configured, skipping"
        )
        return None

    try:
        async with build_client(_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{base}{_CLAIM_PATH}", headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("testing_client.claim: unreachable (%s)", type(exc).__name__)
        return None

    if response.status_code != 200:
        logger.warning(
            "testing_client.claim: testing_service returned %s", response.status_code,
        )
        return None

    try:
        body = response.json()
    except ValueError:
        logger.warning("testing_client.claim: non-JSON response body")
        return None

    return body.get("item")


async def report_completed(
    queue_item_id: str,
    *,
    succeeded: bool,
    exit_code: int | None,
    error: str | None,
) -> None:
    """POST /internal/queue/{queue_item_id}/completed.

    Best-effort: сетевой сбой логируется как WARNING и проглатывается —
    следующий цикл всё равно уйдёт на новый `claim()`, а зависший
    `running`-item на стороне testing_service — известный tech debt
    (нет symmetричного sweep-механизма, см. отчёт предыдущей волны),
    падать самому воркеру из-за этого незачем.
    """
    base = _base_url()
    headers = _headers()
    if base is None or headers is None:
        logger.warning(
            "testing_client.report_completed: TESTING_SERVICE_URL/"
            "TESTING_SERVICE_INTERNAL_API_KEY not configured, skipping (queue_item_id=%s)",
            queue_item_id,
        )
        return

    path = _COMPLETED_PATH_TEMPLATE.format(queue_item_id=queue_item_id)
    body = {"succeeded": succeeded, "exit_code": exit_code, "error": error}

    try:
        async with build_client(_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{base}{path}", headers=headers, json=body)
    except httpx.HTTPError as exc:
        logger.warning(
            "testing_client.report_completed: unreachable (%s), queue_item_id=%s",
            type(exc).__name__,
            queue_item_id,
        )
        return

    if response.status_code != 200:
        logger.warning(
            "testing_client.report_completed: testing_service returned %s for queue_item_id=%s",
            response.status_code,
            queue_item_id,
        )
