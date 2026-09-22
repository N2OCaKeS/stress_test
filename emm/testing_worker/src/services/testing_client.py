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

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME
from src.core.http import bearer_header

logger = logging.getLogger("testing_worker.testing_client")


class ClaimedQueueItem(BaseModel):
    """Форма одного item'а из ответа `claim()` (зеркалит `testing_service`
    `QueueClaimItem`).

    Раньше `claim()` отдавал сырой `dict`, и рассинхрон контракта (баг в
    `testing_service`, ручной хотфикс, неполные тестовые данные) валился
    голым `KeyError` где-то посреди `_run_one_item` — `report_completed`
    для такого item'а не уходил, и он тихо зависал в `RUNNING` навсегда.
    Обязательны здесь только поля, к которым код обращается напрямую через
    `[]` (`queue_item_id`/`host`/`test_username`/`test_ssh_private_key`/
    `command`) — остальное `_run_one_item` и так читает через `.get()` и
    переживает отсутствие.
    """

    model_config = ConfigDict(extra="ignore")

    queue_item_id: str
    host: str
    test_username: str
    test_ssh_private_key: str
    command: list[str]
    command_masked: list[str] = Field(default_factory=list)
    git_token_content: str | None = None
    git_token_filename: str | None = None
    dates_content: str | None = None
    dates_content_masked: str | None = None
    dates_filename: str | None = None
    command_timeout_seconds: int | None = None
    debug_mode: bool = False
    is_retry: bool = False
    prepare_only: bool = False
    starter_suffix: str = ""

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
    """Фабрика клиента. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


# Один клиент на процесс: item'ов параллельно столько же, сколько активных
# стендов, и у каждого свой опрос interrupt-check и поток log-chunk — новый
# клиент на каждый вызов означал бы новое соединение на каждый.
_shared: tuple[asyncio.AbstractEventLoop, httpx.AsyncClient] | None = None


def _client() -> httpx.AsyncClient:
    global _shared
    loop = asyncio.get_running_loop()
    if _shared is not None and _shared[0] is loop and not _shared[1].is_closed:
        return _shared[1]
    _shared = (loop, build_client(_REQUEST_TIMEOUT_SECONDS))
    return _shared[1]


@asynccontextmanager
async def _client_ctx() -> AsyncIterator[httpx.AsyncClient]:
    yield _client()


async def aclose() -> None:
    """Закрыть общий клиент при остановке воркера."""
    global _shared
    if _shared is None:
        return
    _, client = _shared
    _shared = None
    if not client.is_closed:
        await client.aclose()


def reset_client() -> None:
    """Сбросить общий клиент без закрытия — для тестов, подменяющих `build_client`."""
    global _shared
    _shared = None


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

    `None` означает «очередь пуста», «testing_service недоступен» и теперь
    ещё «ответ пришёл, но не прошёл валидацию формы» — во всех случаях
    вызывающий polling-loop делает одно и то же: спит
    `queue_poll_interval_seconds` и пробует снова. Различать их в возврате
    смысла нет, разница видна только в логах (WARNING/ERROR на сбое).

    Валидация — через `ClaimedQueueItem`: `testing_service` уже перевёл
    item в `RUNNING` к моменту, когда собрал этот ответ, так что провал
    формы здесь — не молчаливая потеря item'а, а явный `report_completed`
    с провалом, если `queue_item_id` вообще удалось прочитать (без него
    сообщать некому — это предельный случай, дальше уже ловит staleness на
    стороне `testing_service`).
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
        async with _client_ctx() as client:
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

    raw_item = body.get("item")
    if raw_item is None:
        return None

    try:
        item = ClaimedQueueItem.model_validate(raw_item)
    except ValidationError as exc:
        logger.error("testing_client.claim: malformed item in response: %s", exc)
        queue_item_id = raw_item.get("queue_item_id") if isinstance(raw_item, dict) else None
        if queue_item_id:
            await report_completed(
                queue_item_id, succeeded=False, exit_code=None,
                error=f"malformed claim response: {exc}"[:2048],
            )
        return None

    return item.model_dump()


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
        async with _client_ctx() as client:
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
    timed_out: bool = False,
) -> None:
    """POST /internal/queue/{queue_item_id}/completed.

    `interrupted` (`"skip"`/`"pause"`) — сессия оборвана по заявке оператора;
    в этом случае testing_service игнорирует `succeeded`/`exit_code`/`error`,
    исхода у теста нет.

    `timed_out=True` — провал вызван `command_timeout` в `ssh_executor`
    (`ExecutionResult.timed_out`), а не ненулевым кодом возврата/обрывом
    соединения; testing_service заводит item как `timed_out`, а не generic
    `failed`. Игнорируется при `succeeded=True`.

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
        "timed_out": timed_out,
    }

    try:
        async with _client_ctx() as client:
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
        async with _client_ctx() as client:
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
        async with _client_ctx() as client:
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
