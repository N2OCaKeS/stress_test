"""Unit-тесты резолвера repo-URL для OS-версий.

Резолвер строит строки sources.list из индекса релизов. Сеть замокана —
подменяем `_fetch_index_from_network`, реального похода в
`releases.devos.astralinux.ru` нет.
"""

from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import (
    BadRequestError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.services import os_version_repo_resolver as resolver

BASE = "https://releases.devos.astralinux.ru"


def _files(*mount_points: str) -> dict:
    return {"files": [{"mount_point": mp} for mp in mount_points]}


def _index() -> dict:
    return {
        "releases": {
            "1.7": {
                # Свежая 1.7 — только base-repository оставляется.
                "1.7.5": {
                    "1.7.5.6": _files(
                        "frozen/1.7/1.7.5/1.7.5.6/base-repository",
                        "frozen/1.7/1.7.5/1.7.5.6/installation",
                        "frozen/1.7/1.7.5/1.7.5.6/update-repository",
                    ),
                },
                # Легаси 1.7.0 — base-repository нет, режем installation/update-repository.
                "1.7.0": {
                    "1.7.0.18": _files(
                        "frozen/1.7/1.7.0/1.7.0.18/main-repository",
                        "frozen/1.7/1.7.0/1.7.0.18/installation",
                        "frozen/1.7/1.7.0/1.7.0.18/update-repository",
                    ),
                },
                # Легаси UU-вариант.
                "1.7.3": {
                    "1.7.3.UU.1": _files(
                        "frozen/1.7/1.7.3/1.7.3.UU.1/main-repository",
                        "frozen/1.7/1.7.3/1.7.3.UU.1/installation",
                    ),
                },
            },
            "1.8": {
                "1.8.1": {
                    "1.8.1.6": _files(
                        "1.8/1.8.1/1.8.1.6/main-repository",
                        "1.8/1.8.1/1.8.1.6/installation-di",
                        "1.8/1.8.1/1.8.1.6/installation",
                    ),
                },
            },
            "2.0": {
                "2.0.0": {
                    "2.0.0.1": _files(
                        "2.0/2.0.0/2.0.0.1/main-repository",
                        "2.0/2.0.0/2.0.0.1/installation-di",
                    ),
                },
            },
        },
    }


@pytest.fixture(autouse=True)
def _mock_index(monkeypatch, request):
    """Подменяем сетевой fetch на канонический индекс и чистим кэш.

    Тесты сетевого слоя (проверяют реальный `_fetch_index_from_network`)
    подмену не получают — определяем по имени теста.
    """
    resolver.clear_index_cache()

    if "raises_service_unavailable" not in request.function.__name__:
        async def _fake_fetch() -> dict:
            return _index()

        monkeypatch.setattr(resolver, "_fetch_index_from_network", _fake_fetch)
    yield
    resolver.clear_index_cache()


async def test_17_keeps_only_base_repository():
    repos = await resolver.resolve_repository_urls("1.7.5.6")
    assert repos == [
        f"deb {BASE}/frozen/1.7/1.7.5/1.7.5.6/base-repository 1.7_x86-64 main contrib non-free"
    ]


async def test_17_legacy_drops_installation_and_update_repository():
    repos = await resolver.resolve_repository_urls("1.7.0.18")
    # main-repository остаётся, installation и update-repository выкинуты.
    assert repos == [
        f"deb {BASE}/frozen/1.7/1.7.0/1.7.0.18/main-repository 1.7_x86-64 main contrib non-free"
    ]


async def test_17_legacy_uu_variant():
    repos = await resolver.resolve_repository_urls("1.7.3.UU.1")
    assert repos == [
        f"deb {BASE}/frozen/1.7/1.7.3/1.7.3.UU.1/main-repository 1.7_x86-64 main contrib non-free"
    ]


async def test_18_drops_installation_di_only():
    repos = await resolver.resolve_repository_urls("1.8.1.6")
    # installation-di выкинут, installation и main-repository остаются.
    assert repos == [
        f"deb {BASE}/1.8/1.8.1/1.8.1.6/main-repository 1.8_x86-64 main contrib non-free",
        f"deb {BASE}/1.8/1.8.1/1.8.1.6/installation 1.8_x86-64 main contrib non-free",
    ]


async def test_other_family_keeps_all():
    repos = await resolver.resolve_repository_urls("2.0.0.1")
    assert repos == [
        f"deb {BASE}/2.0/2.0.0/2.0.0.1/main-repository 2.0_x86-64 main contrib non-free",
        f"deb {BASE}/2.0/2.0.0/2.0.0.1/installation-di 2.0_x86-64 main contrib non-free",
    ]


async def test_unknown_build_version_raises_not_found():
    with pytest.raises(NotFoundError) as exc:
        await resolver.resolve_repository_urls("1.7.9.99")
    assert exc.value.error_code == "OS_RELEASE_NOT_FOUND"


async def test_unknown_release_raises_not_found():
    with pytest.raises(NotFoundError) as exc:
        await resolver.resolve_repository_urls("9.9.9.9")
    assert exc.value.error_code == "OS_RELEASE_NOT_FOUND"


@pytest.mark.parametrize("bad", ["", "1.7", "1..7.5", " "])
async def test_malformed_build_version_raises_bad_request(bad):
    with pytest.raises(BadRequestError):
        await resolver.resolve_repository_urls(bad)


async def test_network_failure_raises_service_unavailable(monkeypatch):
    resolver.clear_index_cache()

    async def _boom() -> dict:
        raise ServiceUnavailableError(
            error_code="OS_RELEASES_INDEX_UNAVAILABLE",
            message="down",
        )

    monkeypatch.setattr(resolver, "_fetch_index_from_network", _boom)
    with pytest.raises(ServiceUnavailableError) as exc:
        await resolver.resolve_repository_urls("1.7.5.6")
    assert exc.value.error_code == "OS_RELEASES_INDEX_UNAVAILABLE"


async def test_index_cached_within_ttl(monkeypatch):
    """Второй резолв не ходит в сеть повторно — индекс берётся из кэша."""
    resolver.clear_index_cache()
    calls = {"n": 0}

    async def _counting_fetch() -> dict:
        calls["n"] += 1
        return _index()

    monkeypatch.setattr(resolver, "_fetch_index_from_network", _counting_fetch)
    await resolver.resolve_repository_urls("1.7.5.6")
    await resolver.resolve_repository_urls("1.8.1.6")
    assert calls["n"] == 1


async def test_fetch_non_200_raises_service_unavailable(monkeypatch):
    """Сетевой слой: non-200 от индекса → 503 (проверяем реальный _fetch)."""
    resolver.clear_index_cache()

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="nope")

    transport = httpx.MockTransport(_handler)
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(resolver.httpx, "AsyncClient", _factory)
    with pytest.raises(ServiceUnavailableError) as exc:
        await resolver._fetch_index_from_network()
    assert exc.value.error_code == "OS_RELEASES_INDEX_UNAVAILABLE"
