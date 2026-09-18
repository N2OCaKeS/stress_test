"""Клиент `astra-config.json` из репозитория `astra-qa-stand` (§12 плана миграции).

Порт легаси `get_aqs_json()` (`allta_app/libs/liballta.py:1407-1430`), которую
`allta_app/restart_services.py` запускал на каждом деплое, перезаписывая
локальный `astra-config.json` свежей версией из git. Тот файл ни разу не
редактировался вручную — источник истины всегда был этот git-репозиторий,
легаси-файл был просто последним снятым снепшотом. Здесь снепшот не нужен:
запрос идёт напрямую в git при обращении к маршруту, с in-process TTL-кэшем,
чтобы не дёргать git.astralinux.ru на каждый вызов.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from src.core.config import get_settings
from src.core.exceptions import ServiceUnavailableError

logger = logging.getLogger("testing_service.astra_qa_stand_client")

_cache: dict[str, object] = {}
_lock = asyncio.Lock()


def clear_cache() -> None:
    """Сбросить кэш (тесты, ручной форс-рефреш)."""
    _cache.clear()


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


async def _fetch_from_network() -> dict:
    settings = get_settings()
    url = settings.astra_qa_stand_config_url
    headers = {}
    if settings.astra_qa_stand_auth_token:
        headers["Authorization"] = settings.astra_qa_stand_auth_token
    try:
        async with build_client(settings.astra_qa_stand_request_timeout_seconds) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("GET astra-config.json failed: %s: %s", type(exc).__name__, exc)
        raise ServiceUnavailableError(
            error_code="ASTRA_QA_STAND_UNAVAILABLE",
            message="astra-qa-stand repository is unreachable",
        ) from exc
    if response.status_code != 200:
        logger.warning("astra-config.json fetch returned %s", response.status_code)
        raise ServiceUnavailableError(
            error_code="ASTRA_QA_STAND_UNAVAILABLE",
            message=f"astra-qa-stand repository returned {response.status_code}",
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="ASTRA_QA_STAND_INVALID",
            message="astra-config.json is not valid JSON",
        ) from exc
    if not isinstance(data, dict):
        raise ServiceUnavailableError(
            error_code="ASTRA_QA_STAND_INVALID",
            message="astra-config.json has unexpected shape",
        )
    return data


async def get_astra_config() -> dict:
    """Текущий `astra-config.json` — из кэша или свежим запросом в git."""
    ttl = get_settings().astra_qa_stand_cache_ttl_seconds
    now = time.monotonic()
    cached = _cache.get("data")
    if cached is not None and ttl > 0:
        age = now - float(_cache.get("fetched_at", 0.0))
        if age < ttl:
            return cached  # type: ignore[return-value]
    async with _lock:
        cached = _cache.get("data")
        if cached is not None and ttl > 0:
            age = time.monotonic() - float(_cache.get("fetched_at", 0.0))
            if age < ttl:
                return cached  # type: ignore[return-value]
        data = await _fetch_from_network()
        _cache["data"] = data
        _cache["fetched_at"] = time.monotonic()
        return data
