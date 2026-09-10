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

Ответ кэшируется в `changelog_cache` по `build_version` (RC), см.
`ChangelogCache`/`repositories/changelog_cache.py`: RC не переиздаётся задним
числом, поэтому TTL практически бессрочный (`changelog_cache_ttl_seconds`,
дефолт 90 дней) — повторный вызов для того же RC обычно вообще не бьёт по
сети.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.repositories import changelog_cache as changelog_cache_repo

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


def _components_from_body(body: dict) -> list[str]:
    if body.get("status") != "success":
        # Легаси трактует не-success как "нет изменившихся компонентов"
        # (пустой список тестов на обычном RC) — воспроизведено буквально.
        return []
    result = body.get("result")
    if not isinstance(result, list):
        return []
    return [c for c in (_extract_component(e) for e in result) if c]


def _is_fresh(fetched_at: datetime, ttl_seconds: float) -> bool:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_at < timedelta(seconds=ttl_seconds)


async def fetch_changed_components(db: AsyncSession, rc: str) -> list[str] | None:
    """Список изменившихся компонентов для `rc`, либо `None` — фильтрация недоступна.

    `None` — не значит "нет изменений", это "не знаем", caller должен
    трактовать `None` как "не фильтровать" (полный набор тестов), а не как
    "фильтровать в пустой список". Сначала проверяется `changelog_cache` —
    свежая запись возвращается без сетевого вызова.
    """
    settings = get_settings()

    cached = await changelog_cache_repo.get_by_build_version(db, rc)
    if cached is not None and _is_fresh(cached.fetched_at, settings.changelog_cache_ttl_seconds):
        return _components_from_body(cached.response_json)

    base = (settings.changelog_service_url or "").rstrip("/")
    if not base:
        # Не настроено — если есть хоть устаревшая запись, лучше отдать её,
        # чем откатиться к "фильтрация недоступна" на ровном месте.
        if cached is not None:
            return _components_from_body(cached.response_json)
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
            if cached is not None:
                return _components_from_body(cached.response_json)
            return None

    if response.status_code != 200:
        logger.warning("changelog service returned %s for rc=%s", response.status_code, rc)
        if cached is not None:
            return _components_from_body(cached.response_json)
        return None

    try:
        body = response.json()
    except ValueError:
        logger.warning("changelog service returned a non-JSON body for rc=%s", rc)
        if cached is not None:
            return _components_from_body(cached.response_json)
        return None

    await changelog_cache_repo.upsert(db, rc, body)
    await db.commit()
    return _components_from_body(body)
