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

Кроме полного пересчёта легаси давало восемь отдельных кнопок по семействам
тестов (`allta_app/allta_front.py:729-880` — по Flask-роуту на кнопку, тела
запросов из `allta_app/statistics_conf.py`). Раньше они жили здесь константой;
по решению D18 это справочник `statistics_categories` в БД (сид —
те же восемь семейств, миграция `tp17_statistics_categories`), редактируемый
в настройках статистики. `load_categories` читает его, вызов одного
семейства — `trigger_category_statistics(spec=...)`.

Аутентификация одинаковая для всех маршрутов — та же пара Confluence
username/token, что уже резолвится в EMM через
`department_integration_settings.credential_id` + `secret_client.reveal_credential`
(модели `Auth`/`Statistics` в `statistics/main_api.py` — обе принимают
`{username, token, latest_stable_versions_bool}`, пер-категорийная дополняет
это `title_statistics`/`set_of_test_types`/`comparison_*`).
"""

from __future__ import annotations

import logging
from typing import NamedTuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ServiceUnavailableError
from src.models.statistics_category import StatisticsCategory
from src.repositories import statistics_category as category_repo

logger = logging.getLogger("testing_service.statistics_client")

_ALL_STATISTICS_PATH = "/all-statistics"


class CategorySpec(NamedTuple):
    """Что именно слать во внешний сервис ради одного семейства тестов.

    Снимок строки `statistics_categories`, отвязанный от сессии БД: фоновая
    задача пересчёта живёт дольше сессии вызывающего.
    """

    key: str
    label: str
    path: str
    title_statistics: str
    set_of_test_types: tuple[str, ...]
    comparison_list: tuple[tuple[str, ...], ...] | None = None
    comparison_kernel_list: tuple[str, ...] | None = None


def spec_from_row(row: StatisticsCategory) -> CategorySpec:
    """Строка справочника → неизменяемый `CategorySpec`."""
    return CategorySpec(
        key=row.key,
        label=row.label,
        path=row.path,
        title_statistics=row.title_statistics,
        set_of_test_types=tuple(row.set_of_test_types or ()),
        comparison_list=(
            tuple(tuple(group) for group in row.comparison_list)
            if row.comparison_list is not None else None
        ),
        comparison_kernel_list=(
            tuple(row.comparison_kernel_list) if row.comparison_kernel_list is not None else None
        ),
    )


async def load_categories(db: AsyncSession, *, enabled_only: bool = True) -> list[CategorySpec]:
    """Семейства из справочника `statistics_categories` в порядке `sort_order`."""
    rows = await category_repo.list_all(db, enabled_only=enabled_only)
    return [spec_from_row(row) for row in rows]


async def category_choices(db: AsyncSession) -> list[dict[str, str]]:
    """`[{key, label}]` включённых семейств в порядке справочника."""
    return [{"key": spec.key, "label": spec.label} for spec in await load_categories(db)]


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


async def _post(*, base_url: str, path: str, payload: dict, timeout: float) -> None:
    """Один POST во внешний сервис. Любой не-2xx и любой сетевой сбой — исключение.

    Поднимает `ServiceUnavailableError` на сеть/таймаут/неожиданный код ответа
    — вызывающий код (`services/statistics_recalc.py`) сам решает, что делать
    со статусом (записывает `failed` + текст причины), сюда не пробрасывается
    наружу дальше вызывающей фоновой задачи.
    """
    async with build_client(timeout) as client:
        try:
            response = await client.post(f"{base_url.rstrip('/')}{path}", json=payload)
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
            "statistics: %s returned %s body=%s",
            path, response.status_code, response.text[:500],
        )
        raise ServiceUnavailableError(
            error_code="STATISTICS_SERVICE_ERROR",
            message=f"statistics service returned {response.status_code}",
        )


async def trigger_all_statistics(
    *, base_url: str, username: str, token: str, timeout: float,
) -> None:
    """`POST {base_url}/all-statistics` — синхронный полный пересчёт статистики."""
    await _post(
        base_url=base_url, path=_ALL_STATISTICS_PATH, timeout=timeout,
        payload={"username": username, "token": token, "latest_stable_versions_bool": True},
    )


async def trigger_category_statistics(
    *, base_url: str, username: str, token: str, timeout: float, spec: CategorySpec,
) -> None:
    """Пересчёт одного семейства тестов — легаси-кнопки «Apache», «Parsec» и т.д.

    Тело запроса собирается из строки справочника (`spec`); необязательные
    `comparison_list`/`comparison_kernel_list` не отправляются вовсе, если у
    семейства их нет (у внешнего сервиса они `Optional[List] = None`), — так же,
    как легаси не клало в payload ключи, которых нет в `statistics_conf`.
    """
    payload: dict = {
        "title_statistics": spec.title_statistics,
        "username": username,
        "token": token,
        "set_of_test_types": list(spec.set_of_test_types),
        "latest_stable_versions_bool": True,
    }
    if spec.comparison_list is not None:
        payload["comparison_list"] = [list(group) for group in spec.comparison_list]
    if spec.comparison_kernel_list is not None:
        payload["comparison_kernel_list"] = list(spec.comparison_kernel_list)
    await _post(base_url=base_url, path=spec.path, payload=payload, timeout=timeout)
