"""Клиент внешнего сервиса статистики (§2.7, §9.3 плана миграции).

Сервис живёт в этом же монорепо, но в отдельной ветке — `statistics`
(`statistics/main_api.py`, FastAPI, порт 7777 в `statistics/docker-compose.yml`).
Все его эндпоинты (`/base-statistics`, `/freeipa-statistics`, `/virt-statistics`,
`/parsec-statistics`, `/postgresql-statistics`, `/docker-statistics`,
`/filesystems-statistics`, `/network-statistics`, `/all-statistics`)
синхронные (`def`, не `async def`), без фоновой очереди внутри самого
сервиса — HTTP-соединение блокируется на всё время пересчёта.
`/all-statistics` пересчитывает все семейства последовательно в одном
запросе и может идти минутами.

Легаси (`allta_app/allta_back.py:413-423 calc_all_statistics()`) дёргал этот
же `/all-statistics` синхронно в конце каждого прогона, блокируя дальнейший
запуск тестов на всё время пересчёта. По требованию владельца это поведение
не переносится: вызывающий код (`services/statistics_recalc.py`) обязан
звать этот клиент из фоновой задачи, не из request-response цикла — сам
клиент этого не гарантирует, он просто делает один HTTP-запрос.

Аутентификация к `/all-statistics` — та же пара Confluence username/token,
что уже резолвится в EMM через `department_integration_settings.credential_id`
+ `secret_client.reveal_credential` (модель `Auth` в `statistics/main_api.py`
принимает `{username, token, latest_stable_versions_bool}`).
"""

from __future__ import annotations

import logging

import httpx

from src.core.exceptions import ServiceUnavailableError

logger = logging.getLogger("testing_service.statistics_client")

_ALL_STATISTICS_PATH = "/all-statistics"


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


async def trigger_all_statistics(
    *, base_url: str, username: str, token: str, timeout: float,
) -> None:
    """`POST {base_url}/all-statistics` — синхронный полный пересчёт статистики.

    Поднимает `ServiceUnavailableError` на сеть/таймаут/неожиданный код ответа
    — вызывающий код (`services/statistics_recalc.py`) сам решает, что делать
    со статусом (записывает `failed` + текст причины), сюда не пробрасывается
    наружу дальше вызывающей фоновой задачи.
    """
    payload = {"username": username, "token": token, "latest_stable_versions_bool": True}
    async with build_client(timeout) as client:
        try:
            response = await client.post(
                f"{base_url.rstrip('/')}{_ALL_STATISTICS_PATH}", json=payload,
            )
        except httpx.TimeoutException as exc:
            raise ServiceUnavailableError(
                error_code="STATISTICS_SERVICE_TIMEOUT",
                message="statistics service did not respond in time",
            ) from exc
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="STATISTICS_SERVICE_UNREACHABLE",
                message=f"Unable to reach statistics service: {type(exc).__name__}",
            ) from exc
    if response.status_code >= 300:
        logger.warning(
            "statistics: all-statistics returned %s body=%s",
            response.status_code, response.text[:500],
        )
        raise ServiceUnavailableError(
            error_code="STATISTICS_SERVICE_ERROR",
            message=f"statistics service returned {response.status_code}",
        )
