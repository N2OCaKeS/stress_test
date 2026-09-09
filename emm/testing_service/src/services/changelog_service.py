"""Changelog-фильтр для генерации СТП (§1, §7 плана миграции).

Легаси (`allta_app/allta_image_conf.py::changelog_testcycle_handler`) зовёт
`GET {CHANGELOG_SERVICE_URL}/get_components_for_testrun_by_changelog` c
параметрами `astra_linux_build_version=<rc>&first_level_dependencies=true&
return_dct_component_with_packages=false` и ждёт
`{"status": "success"|..., "result": [...]}`. `result` — список изменившихся
компонентов; форма отдельного элемента в легаси не зафиксирована явно (в этой
кодовой базе не проверялась против живого сервиса) — здесь принимается и
плоская строка, и `{"component": "..."}`/`{"name": "..."}`, остальное
игнорируется как неопознанное.

Не-`success` статус (или сетевой сбой/сервис не настроен) → безопасный дефолт:
`None`, что означает у caller'а "фильтрация недоступна — берём всё" (§1: "лучше
лишний прогон, чем пропущенный" — тот же принцип, что и у
`test_definitions.changelog_component` пустого поля).
"""

from __future__ import annotations

import logging

import httpx

from src.core.config import get_settings

logger = logging.getLogger("testing_service.changelog_service")

_COMPONENTS_PATH = "/get_components_for_testrun_by_changelog"


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


def is_configured() -> bool:
    return bool(get_settings().changelog_service_url)


def _extract_component(entry) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("component") or entry.get("name")
    return None


async def fetch_changed_components(rc: str) -> list[str] | None:
    """Список изменившихся компонентов для `rc`, либо `None` — фильтрация недоступна.

    `None` — не значит "нет изменений", это "не знаем", caller должен
    трактовать `None` как "не фильтровать" (полный набор тестов), а не как
    "фильтровать в пустой список".
    """
    settings = get_settings()
    base = (settings.changelog_service_url or "").rstrip("/")
    if not base:
        return None

    async with build_client(settings.changelog_request_timeout_seconds) as client:
        try:
            response = await client.get(
                f"{base}{_COMPONENTS_PATH}",
                params={
                    "astra_linux_build_version": rc,
                    "first_level_dependencies": "true",
                    "return_dct_component_with_packages": "false",
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("changelog service unreachable for rc=%s: %s", rc, exc)
            return None

    if response.status_code != 200:
        logger.warning("changelog service returned %s for rc=%s", response.status_code, rc)
        return None

    try:
        body = response.json()
    except ValueError:
        logger.warning("changelog service returned a non-JSON body for rc=%s", rc)
        return None

    if body.get("status") != "success":
        # Легаси трактует не-success как "нет изменившихся компонентов"
        # (пустой список тестов на обычном RC) — воспроизведено буквально.
        return []

    result = body.get("result")
    if not isinstance(result, list):
        return []
    components = [c for c in (_extract_component(e) for e in result) if c]
    return components
