"""Исходящий callback в testing_service о завершении `prepare-for-test`.

Единственный на сегодня outbound-канал server_service к testing_service —
POST `{TESTING_SERVICE_URL}/internal/prepare-for-test/{prepare_request_id}/
completed`. Аутентификация — тот же shared-secret паттерн, что у audit-emit
в loging_service: `Authorization: Bearer <TESTING_SERVICE_API_KEY>` плюс
`X-Service-Identity: server_service`.

Пул тут не заводим: канал редкий (один POST на многочасовой пайплайн),
per-call клиент дешевле, чем ещё один долгоживущий сокет-пул под
неподнятого пока потребителя. Retry-политика — та же форма, что у
audit-emit: несколько попыток с exponential backoff + jitter, потом сдаёмся
и оставляем след в `server_prepare_for_test_requests` (`callback_attempts`,
`callback_last_error`). Пайплайн от недоставленного callback'а не падает:
результат уже зафиксирован в БД, testing_service может добрать его
`GET /internal/servers/{id}/prepare-for-test/{prepare_request_id}`.

Потребителя ещё нет — сервис `testing_service` пока не написан. Пустой
`TESTING_SERVICE_URL`/`TESTING_SERVICE_API_KEY` это штатное состояние: тогда
callback не отправляется, и функция честно возвращает «не доставлено».
"""

from __future__ import annotations

import asyncio
import logging
import random

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME
from src.core.http import bearer_header

logger = logging.getLogger("server_service.testing_client")

# Бэкоффы между попытками. Длина списка задаёт число дополнительных попыток
# сверх первой; итого 3 попытки, как у audit-emit.
_RETRY_DELAYS = (0.5, 1.5)

# Повторяем на всём, что выглядит как временная недоступность потребителя.
# 4xx (кроме 429) не ретраим — это контрактная ошибка, лишние попытки её не
# починят.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один callback. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


def callback_path(prepare_request_id: str) -> str:
    """Путь callback'а на стороне testing_service. Зафиксирован контрактом."""
    return f"/internal/prepare-for-test/{prepare_request_id}/completed"


def is_configured() -> bool:
    """Настроен ли канал (URL + ключ). Без него callback молча пропускается."""
    settings = get_settings()
    return bool(settings.testing_service_url and settings.testing_service_api_key)


async def send_prepare_for_test_completed(
    prepare_request_id: str, body: dict,
) -> tuple[bool, int, str | None]:
    """Отправить итог пайплайна в testing_service.

    Возвращает `(delivered, attempts, last_error)`. `delivered=False` при
    неподнятом канале (тогда `attempts=0`), исчерпанных ретраях или
    не-2xx ответе. Исключений наружу не поднимает: callback — best-effort
    хвост уже завершившегося пайплайна, ронять из-за него worker-callback
    нельзя.
    """
    settings = get_settings()
    base = (settings.testing_service_url or "").rstrip("/")
    api_key = settings.testing_service_api_key
    if not base or not api_key:
        logger.info(
            "prepare-for-test callback skipped: TESTING_SERVICE_URL/API_KEY "
            "are not configured (request_id=%s)",
            prepare_request_id,
        )
        return False, 0, "TESTING_SERVICE_NOT_CONFIGURED"

    url = f"{base}{callback_path(prepare_request_id)}"
    headers = {**bearer_header(api_key), "X-Service-Identity": SERVICE_NAME}
    attempts = 0
    last_error: str | None = None

    async with build_client(settings.testing_callback_timeout_seconds) as client:
        for attempt in range(len(_RETRY_DELAYS) + 1):
            attempts += 1
            try:
                response = await client.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}"
            else:
                if response.status_code < 300:
                    return True, attempts, None
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in _RETRYABLE_STATUSES:
                    break
            if attempt < len(_RETRY_DELAYS):
                # Jitter — чтобы пачка одновременно завершившихся пайплайнов
                # не долбила поднимающийся testing_service синхронно.
                await _sleep(_RETRY_DELAYS[attempt] * random.uniform(0.8, 1.2))

    logger.warning(
        "prepare-for-test callback to testing_service failed after %s attempt(s): "
        "%s (request_id=%s)",
        attempts, last_error, prepare_request_id,
    )
    return False, attempts, last_error


async def _sleep(seconds: float) -> None:
    """Отдельная функция — тестам проще подменить её, чем asyncio.sleep глобально."""
    await asyncio.sleep(seconds)
