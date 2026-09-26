"""Исходящие вызовы в testing_service — очередь заданий и логи.

`claim()`, `report_completed()`, `interrupt-check`, `log_chunk()`,
`log_segment()` — все через один shared-secret канал (`Authorization:
Bearer <TESTING_SERVICE_INTERNAL_API_KEY>` + `X-Service-Identity:
testing_worker`).

Сетевые сбои не поднимаются как исключения — логируются и проглатываются,
чтобы недоступность testing_service не роняла polling-loop. Единственный
caller — сам loop, поэтому «не падать на транзиентных сбоях» держим внутри
клиента, а не отдаём вызывающему коду.
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


class ClaimFile(BaseModel):
    """Файл задания: путь на стенде, содержимое, права, секретность."""

    model_config = ConfigDict(extra="ignore")

    path: str
    content: str
    mode: str = "0644"
    sensitive: bool = False


class ClaimedQueueItem(BaseModel):
    """Форма одного item'а из ответа `claim()` (зеркалит `testing_service` `QueueClaimItem`).

    Обязательны только поля, к которым код обращается напрямую через `[]`
    (`queue_item_id`/`host`/`test_username`/`test_ssh_private_key`/
    `command`) — остальное читается через `.get()`.
    """

    model_config = ConfigDict(extra="ignore")

    queue_item_id: str
    host: str
    test_username: str
    test_ssh_private_key: str
    # файлы для SFTP в порядке записи, команды запуска
    # и остановки — всё собрано testing_service по профилю запуска. Своих
    # путей и `starter.sh` у воркера нет.
    files: list[ClaimFile]
    cleanup_globs: list[str] = Field(default_factory=list)
    launch_command: str
    launch_command_masked: str = ""
    stop_command: str
    use_pty: bool = True
    redact_values: list[str] = Field(default_factory=list)
    launch_profile_version_id: str | None = None
    log_chunk_interval_seconds: float | None = None
    log_chunk_max_bytes: int | None = None
    command_timeout_seconds: int | None = None
    debug_mode: bool = False
    is_retry: bool = False
    prepare_only: bool = False
    # настройки preflight отдела стенда. Разбирает
    # их `preflight.resolve_config`; нет поля — воркер берёт env-фолбэк.
    preflight: dict | None = None
    # шаг многоступенчатого теста — `{index, count,
    # name}`. Воркер им только подписывает сегмент лога: исполнение шага —
    # то же задание, переходы между шагами делает testing_service.
    step: dict | None = None


_CLAIM_PATH = "/internal/queue/claim"
_COMPLETED_PATH_TEMPLATE = "/internal/queue/{queue_item_id}/completed"
_INTERRUPT_CHECK_PATH_TEMPLATE = "/internal/queue/{queue_item_id}/interrupt-check"
_LOG_CHUNK_PATH_TEMPLATE = "/internal/queue/{queue_item_id}/log-chunk"
_LOG_SEGMENT_PATH_TEMPLATE = "/internal/queue/{queue_item_id}/log-segment"
_PREFLIGHT_STATE_PATH_TEMPLATE = "/internal/queue/{queue_item_id}/preflight-state"

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

    `None` — очередь пуста, testing_service недоступен или ответ не прошёл
    валидацию (`ClaimedQueueItem`); во всех случаях polling-loop просто
    спит и пробует снова, разница видна только в логах. Провал валидации,
    если удалось прочитать `queue_item_id`, уходит явным `report_completed`
    с провалом, а не молчаливой потерей item'а.
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
            if isinstance(raw_item, dict) and "command" in raw_item and "files" not in raw_item:
                # Задание до (argv + git_token_*/dates_*): переходный формат
                # удалён вместе с `starter.sh` воркера.
                error = "claim in legacy format (command/dates_*): update testing_service"
            else:
                error = f"malformed claim response: {exc}"
            await report_completed(
                queue_item_id, succeeded=False, exit_code=None, error=error[:2048],
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

    `interrupted` (`"skip"`/`"pause"`) — сессия оборвана оператором,
    `succeeded`/`exit_code`/`error` игнорируются. `timed_out=True` — провал
    от `command_timeout`, а не от кода возврата; заводит item как
    `timed_out`, а не generic `failed`.

    Best-effort: сетевой сбой логируется как WARNING и проглатывается,
    следующий цикл уйдёт на новый `claim()`. Зависший `running`-item на
    стороне testing_service без symmetричного sweep-механизма — известный
    tech debt, не повод падать воркеру.
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


async def report_preflight_state(queue_item_id: str, *, waiting: bool, unavailable: list[str] | None = None) -> None:
    """POST /internal/queue/{queue_item_id}/preflight-state.

    `waiting=True` — ждём внешние сервисы перед запуском item'а (UI показывает
    «тестирование приостановлено»), `False` — ожидание закончено. Best-effort:
    статус для UI не повод срывать ни ожидание, ни сам тест; запись на стороне
    testing_service живёт с TTL, так что потерянное `ok` само истечёт.
    """
    base = _base_url()
    headers = _headers()
    if base is None or headers is None:
        return

    path = _PREFLIGHT_STATE_PATH_TEMPLATE.format(queue_item_id=queue_item_id)
    body = {"state": "waiting" if waiting else "ok", "unavailable": list(unavailable or [])}
    try:
        async with _client_ctx() as client:
            response = await client.post(f"{base}{path}", headers=headers, json=body)
    except httpx.HTTPError as exc:
        logger.warning(
            "testing_client.report_preflight_state: unreachable (%s), queue_item_id=%s",
            type(exc).__name__,
            queue_item_id,
        )
        return

    if response.status_code != 200:
        logger.warning(
            "testing_client.report_preflight_state: testing_service returned %s for queue_item_id=%s",
            response.status_code,
            queue_item_id,
        )
