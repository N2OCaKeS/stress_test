"""Исходящие вызовы в testing_service — очередь заданий и логи (§5.5, §8 плана
миграции).

Четыре эндпоинта, зафиксированные `testing_service` (см. `testing_service/
src/api/v1/endpoints/internal_queue.py` + `internal_log.py`,
`src/schemas/queue.py` + `src/schemas/test_log.py`):

* `POST /internal/queue/claim` — без тела, отдаёт готовое задание либо
  `item: null`, если очередь пуста.
* `POST /internal/queue/{queue_item_id}/completed` — сообщает исход
  SSH-исполнения (успех/провал + exit_code + короткая причина), либо факт
  прерывания по заявке оператора (`interrupted`).
* `GET /internal/queue/{queue_item_id}/interrupt-check` — просили ли снять
  текущий тест с исполнения; опрашивается по таймеру, пока тест идёт.
* `POST /internal/queue/{queue_item_id}/log-chunk` — сырой инкрементальный
  вывод ещё выполняющейся команды (живое наблюдение, §8.6).
* `POST /internal/queue/{queue_item_id}/log-segment` — один уже завершённый
  шаг целиком (formatted-блок собирает сам `testing_service`).

Все четыре идут через один и тот же shared-secret канал: `Authorization:
Bearer <TESTING_SERVICE_INTERNAL_API_KEY>` + `X-Service-Identity:
testing_worker`.

Сетевые сбои (testing_service временно недоступен) не поднимаются как
исключения наружу ни для одного из четырёх вызовов — они логируются и
проглатываются, чтобы одна недоступность внешнего сервиса не роняла весь
polling-loop воркера. Для `claim()`/`report_completed()` это уже было так;
`log_chunk()`/`log_segment()` следуют тому же принципу тем более строго —
живой лог это best-effort наблюдаемость, не источник истины об исходе
теста, и его недоставка не должна мешать `completed` уйти. Это отличает
данный клиент от `server_client.py` в `testing_service`, который вызывающий
код (сервисный слой с транзакциями) сам решает как обрабатывать — здесь
единственный caller — сам polling-loop, и его контракт («не падать на
транзиентных сбоях») удобнее держать внутри клиента.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME
from src.core.http import bearer_header

logger = logging.getLogger("testing_worker.testing_client")

_CLAIM_PATH = "/internal/queue/claim"
_COMPLETED_PATH_TEMPLATE = "/internal/queue/{queue_item_id}/completed"
_INTERRUPT_CHECK_PATH_TEMPLATE = "/internal/queue/{queue_item_id}/interrupt-check"
_LOG_CHUNK_PATH_TEMPLATE = "/internal/queue/{queue_item_id}/log-chunk"
_LOG_SEGMENT_PATH_TEMPLATE = "/internal/queue/{queue_item_id}/log-segment"

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


async def check_interrupt(queue_item_id: str) -> str | None:
    """GET /internal/queue/{queue_item_id}/interrupt-check.

    `"skip"`/`"pause"` — оператор просит снять тест с исполнения. `None` —
    прерывать нечего, а также любой сбой самого опроса: этот вызов идёт по
    таймеру параллельно с уже работающим тестом, и недоступность
    testing_service не повод обрывать исполнение — следующий опрос через
    интервал попробует снова.
    """
    base = _base_url()
    headers = _headers()
    if base is None or headers is None:
        return None

    path = _INTERRUPT_CHECK_PATH_TEMPLATE.format(queue_item_id=queue_item_id)
    try:
        async with build_client(_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{base}{path}", headers=headers)
    except httpx.HTTPError as exc:
        logger.warning(
            "testing_client.check_interrupt: unreachable (%s), queue_item_id=%s",
            type(exc).__name__,
            queue_item_id,
        )
        return None

    if response.status_code != 200:
        logger.warning(
            "testing_client.check_interrupt: testing_service returned %s for queue_item_id=%s",
            response.status_code,
            queue_item_id,
        )
        return None

    try:
        body = response.json()
    except ValueError:
        logger.warning("testing_client.check_interrupt: non-JSON response body")
        return None

    action = body.get("action")
    return action if action in ("skip", "pause") else None


async def report_completed(
    queue_item_id: str,
    *,
    succeeded: bool,
    exit_code: int | None,
    error: str | None,
    interrupted: str | None = None,
) -> None:
    """POST /internal/queue/{queue_item_id}/completed.

    `interrupted` (`"skip"`/`"pause"`) — сессия оборвана по заявке оператора;
    в этом случае testing_service игнорирует `succeeded`/`exit_code`/`error`,
    исхода у теста нет.

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
    body = {
        "succeeded": succeeded,
        "exit_code": exit_code,
        "error": error,
        "interrupted": interrupted,
    }

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


async def log_chunk(queue_item_id: str, text: str) -> None:
    """POST /internal/queue/{queue_item_id}/log-chunk.

    Best-effort — см. module docstring. Пустой `text` не отправляется, нечего
    накапливать.
    """
    if not text:
        return

    base = _base_url()
    headers = _headers()
    if base is None or headers is None:
        logger.warning(
            "testing_client.log_chunk: TESTING_SERVICE_URL/TESTING_SERVICE_INTERNAL_API_KEY "
            "not configured, skipping (queue_item_id=%s)",
            queue_item_id,
        )
        return

    path = _LOG_CHUNK_PATH_TEMPLATE.format(queue_item_id=queue_item_id)
    try:
        async with build_client(_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{base}{path}", headers=headers, json={"text": text})
    except httpx.HTTPError as exc:
        logger.warning(
            "testing_client.log_chunk: unreachable (%s), queue_item_id=%s",
            type(exc).__name__,
            queue_item_id,
        )
        return

    if response.status_code != 200:
        logger.warning(
            "testing_client.log_chunk: testing_service returned %s for queue_item_id=%s",
            response.status_code,
            queue_item_id,
        )


async def log_segment(
    queue_item_id: str,
    *,
    kind: str,
    label: str,
    status: str,
    command_text_masked: str | None,
    output: str,
    host: str,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    """POST /internal/queue/{queue_item_id}/log-segment. Best-effort — см. module docstring."""
    base = _base_url()
    headers = _headers()
    if base is None or headers is None:
        logger.warning(
            "testing_client.log_segment: TESTING_SERVICE_URL/TESTING_SERVICE_INTERNAL_API_KEY "
            "not configured, skipping (queue_item_id=%s)",
            queue_item_id,
        )
        return

    path = _LOG_SEGMENT_PATH_TEMPLATE.format(queue_item_id=queue_item_id)
    body = {
        "kind": kind,
        "label": label,
        "status": status,
        "command_text_masked": command_text_masked,
        "output": output,
        "host": host,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
    }

    try:
        async with build_client(_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{base}{path}", headers=headers, json=body)
    except httpx.HTTPError as exc:
        logger.warning(
            "testing_client.log_segment: unreachable (%s), queue_item_id=%s",
            type(exc).__name__,
            queue_item_id,
        )
        return

    if response.status_code != 200:
        logger.warning(
            "testing_client.log_segment: testing_service returned %s for queue_item_id=%s",
            response.status_code,
            queue_item_id,
        )
