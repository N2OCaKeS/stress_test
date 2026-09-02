"""Авто-построение repo-URL для OS-версии из индекса релизов.

Порт логики `ReleaseToRepo` из allta_app: по build-версии `X.Y.Z.W` тянем
общий индекс релизов (`releases.devos.astralinux.ru/index.json`), навигируем
до списка файлов конкретной сборки и строим строки sources.list вида

    deb https://releases.devos.astralinux.ru/<mount_point> <release>_x86-64 main contrib non-free

Эти строки кладутся в `os_versions.repositories` и потом отдаются боксу
(`tee /etc/apt/sources.list` → `astra-update -A -T -r`).

Индекс большой и общий для всех версий, поэтому кэшируется в процессе с TTL
(`OS_RELEASES_INDEX_CACHE_TTL_SECONDS`). URL индекса — `OS_RELEASES_INDEX_URL`.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from src.core.config import get_settings
from src.core.exceptions import (
    BadRequestError,
    NotFoundError,
    ServiceUnavailableError,
)

logger = logging.getLogger("server_service.os_version_repo_resolver")

_DEB_SUFFIX = "_x86-64 main contrib non-free"

# Легаси-сборки 1.7, у которых нет отдельного base-repository: для них фильтр
# работает наоборот — выкидываем installation и update-repository, а не
# оставляем только base-repository. Список перенесён из `__repo_filter`.
_NO_BASE_REPO_LEGACY: frozenset[str] = frozenset({
    "1.7.4", "1.7.3.UU.2", "1.7.3.UU.1", "1.7.3",
    "1.7.2.UU.1", "1.7.2", "1.7.1", "1.7.0",
})

# In-process кэш индекса: {"data": dict, "fetched_at": monotonic-секунды}.
_index_cache: dict[str, object] = {}
_index_lock = asyncio.Lock()


def clear_index_cache() -> None:
    """Сбросить кэш индекса (для тестов и ручного перерезолва после релиза)."""
    _index_cache.clear()


def _releases_base_url() -> str:
    """Базовый URL раздачи релизов — dirname от URL индекса.

    Для `https://releases.devos.astralinux.ru/index.json` → без хвостового
    сегмента остаётся `https://releases.devos.astralinux.ru`. mount_point
    из индекса дописывается к этому базису.
    """
    index_url = get_settings().os_releases_index_url.rstrip("/")
    base, _, _ = index_url.rpartition("/")
    return base or index_url


async def _fetch_index_from_network() -> dict:
    """GET индекса релизов. Сетевые сбои → ServiceUnavailableError.

    Отдельная функция — единственная точка реального HTTP-вызова, её удобно
    подменять в тестах (без похода в сеть).
    """
    settings = get_settings()
    url = settings.os_releases_index_url
    timeout = settings.os_releases_index_timeout_seconds
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        logger.warning("GET releases index failed: %s: %s", type(exc).__name__, exc)
        raise ServiceUnavailableError(
            error_code="OS_RELEASES_INDEX_UNAVAILABLE",
            message="releases index is unreachable",
        ) from exc
    if resp.status_code != 200:
        logger.warning("releases index returned %s", resp.status_code)
        raise ServiceUnavailableError(
            error_code="OS_RELEASES_INDEX_UNAVAILABLE",
            message=f"releases index returned {resp.status_code}",
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="OS_RELEASES_INDEX_INVALID",
            message="releases index is not valid JSON",
        ) from exc
    if not isinstance(data, dict):
        raise ServiceUnavailableError(
            error_code="OS_RELEASES_INDEX_INVALID",
            message="releases index has unexpected shape",
        )
    return data


async def _load_index() -> dict:
    """Индекс из кэша или из сети. TTL из настроек; 0 отключает кэш."""
    ttl = get_settings().os_releases_index_cache_ttl_seconds
    now = time.monotonic()
    cached = _index_cache.get("data")
    if cached is not None and ttl > 0:
        age = now - float(_index_cache.get("fetched_at", 0.0))
        if age < ttl:
            return cached  # type: ignore[return-value]
    async with _index_lock:
        cached = _index_cache.get("data")
        if cached is not None and ttl > 0:
            age = time.monotonic() - float(_index_cache.get("fetched_at", 0.0))
            if age < ttl:
                return cached  # type: ignore[return-value]
        data = await _fetch_index_from_network()
        _index_cache["data"] = data
        _index_cache["fetched_at"] = time.monotonic()
        return data


def _normalize_build_version(build_version: str) -> str:
    """Проверить формат build-версии. Минимум три сегмента (нужны release+update).

    `1.7.5.6` и легаси `1.7.3.UU.1` валидны; `1.7` — нет (не из чего взять
    update-сегмент).
    """
    value = (build_version or "").strip()
    if not value:
        raise BadRequestError(
            error_code="OS_BUILD_VERSION_REQUIRED",
            message="build version is required",
        )
    parts = value.split(".")
    if len(parts) < 3 or any(not p for p in parts):
        raise BadRequestError(
            error_code="OS_BUILD_VERSION_INVALID",
            message="build version must look like X.Y.Z or X.Y.Z.W",
            details={"build_version": build_version},
        )
    return value


def _is_legacy_17(build_version: str) -> bool:
    """Легаси-сборка 1.7 (0..4 / UU), у которой нет base-repository.

    Список `_NO_BASE_REPO_LEGACY` хранит идентификаторы update-уровня (`1.7.0`)
    и UU-варианты. Реальный вход — полный build `X.Y.Z.W`, поэтому сверяем и по
    update-префиксу (`X.Y.Z`), и по самой build-строке (для UU).
    """
    update = ".".join(build_version.split(".")[:3])
    return build_version in _NO_BASE_REPO_LEGACY or update in _NO_BASE_REPO_LEGACY


def _filter_repos(build_version: str, deb_lines: list[str]) -> list[str]:
    """Фильтр репозиториев по правилам `__repo_filter` (1.7 / легаси-1.7 / 1.8)."""
    if build_version.startswith("1.7"):
        if _is_legacy_17(build_version):
            return [
                line for line in deb_lines
                if "installation" not in line and "update-repository" not in line
            ]
        return [line for line in deb_lines if "base-repository" in line]
    if build_version.startswith("1.8"):
        return [line for line in deb_lines if "installation-di" not in line]
    return list(deb_lines)


def _build_deb_lines(index: dict, build_version: str) -> list[str]:
    """Собрать строки sources.list по файлам сборки из индекса.

    Отсутствие release/update/build-ключа или неожиданная форма записи →
    NotFoundError (понятная 404, не 500).
    """
    segments = build_version.split(".")
    release = ".".join(segments[:2])
    update = ".".join(segments[:3])
    base = _releases_base_url()

    releases = index.get("releases")
    if not isinstance(releases, dict):
        raise ServiceUnavailableError(
            error_code="OS_RELEASES_INDEX_INVALID",
            message="releases index has no 'releases' map",
        )
    try:
        build_entry = releases[release][update][build_version]
    except (KeyError, TypeError) as exc:
        raise NotFoundError(
            error_code="OS_RELEASE_NOT_FOUND",
            message="build version is not present in the releases index",
            details={
                "build_version": build_version,
                "release": release,
                "update": update,
            },
        ) from exc

    files = build_entry.get("files") if isinstance(build_entry, dict) else None
    if not isinstance(files, list):
        raise NotFoundError(
            error_code="OS_RELEASE_NOT_FOUND",
            message="build version has no files in the releases index",
            details={"build_version": build_version},
        )

    lines: list[str] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        mount_point = entry.get("mount_point")
        if not mount_point:
            continue
        lines.append(f"deb {base}/{mount_point} {release}{_DEB_SUFFIX}")
    return lines


async def resolve_repository_urls(build_version: str) -> list[str]:
    """Построить строки sources.list для build-версии `X.Y.Z.W`.

    Тянет индекс релизов (кэш с TTL), навигирует до файлов сборки, строит
    deb-строки и прогоняет их через фильтр репозиториев. Неизвестная версия →
    404 OS_RELEASE_NOT_FOUND; недоступный индекс → 503.
    """
    normalized = _normalize_build_version(build_version)
    index = await _load_index()
    deb_lines = _build_deb_lines(index, normalized)
    return _filter_repos(normalized, deb_lines)
