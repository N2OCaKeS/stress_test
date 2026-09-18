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
запросов из `allta_app/statistics_conf.py`). Девять триггеров всего; все
восемь перенесены в `_CATEGORY_SPECS` ниже, вызов — `trigger_category_statistics`.

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

from src.core.exceptions import ServiceUnavailableError

logger = logging.getLogger("testing_service.statistics_client")

_ALL_STATISTICS_PATH = "/all-statistics"


class CategorySpec(NamedTuple):
    """Что именно слать во внешний сервис ради одного семейства тестов."""

    key: str
    label: str
    path: str
    title_statistics: str
    set_of_test_types: tuple[str, ...]
    comparison_list: tuple[tuple[str, ...], ...] | None = None
    comparison_kernel_list: tuple[str, ...] | None = None


# Транскрипция легаси один в один: маршруты и `title_statistics` — из
# `allta_app/allta_front.py:729-880` (девять Flask-роутов, которые и были теми
# самыми девятью кнопками), наборы типов тестов и списки сравнений — из
# `allta_app/statistics_conf.py`. Порядок — как в легаси-меню.
#
# Заметно, что три семейства (Apache/UnixBench/Системные службы) ходят в общий
# `/base-statistics` и различаются только `title_statistics` — внешний сервис
# выбирает парсер по нему (`statistics/main_api.py:46-60`), так что копировать
# это разделение обязательно.
#
# `Docker`/`Network` есть в `statistics_conf.py` и у внешнего сервиса
# (`/docker-statistics`, `/network-statistics`), но собственной кнопки в легаси
# у них не было — не добавляем и здесь, чтобы «девять кнопок» осталось девятью.
_CATEGORY_SPECS: tuple[CategorySpec, ...] = (
    CategorySpec(
        key="apache", label="Apache", path="/base-statistics",
        title_statistics="Apache",
        set_of_test_types=("apache-rp",),
    ),
    CategorySpec(
        key="freeipa", label="FreeIPA", path="/freeipa-statistics",
        title_statistics="FreeIPA",
        set_of_test_types=("FreeIPA auth", "FreeIPA c-users", "FreeIPA plugin"),
    ),
    CategorySpec(
        key="parsec", label="Parsec", path="/parsec-statistics",
        title_statistics="Parsec",
        set_of_test_types=(
            "parsec impact-fs", "parsec impact-fs aud-off", "raw-spin-lock", "digsig-cdt",
        ),
        comparison_list=(("parsec impact-fs", "parsec impact-fs aud-off"),),
    ),
    CategorySpec(
        key="postgresql", label="PostgreSQL", path="/postgresql-statistics",
        title_statistics="PostgreSQL",
        set_of_test_types=(
            "postgresql", "postgresql-sm", "postgresql-aud-off", "psql parsec",
            "psql vanilla", "tantor vanilla", "psql balance", "PSQL OLAP-hq",
            "psql info-sys", "psql info-sys-orel",
        ),
        comparison_list=(
            ("postgresql", "postgresql-sm"),
            ("postgresql", "postgresql-aud-off"),
            ("postgresql", "psql parsec"),
            ("postgresql", "psql vanilla"),
            ("psql vanilla", "postgresql-aud-off"),
            ("psql info-sys", "psql info-sys-orel"),
        ),
        comparison_kernel_list=("postgresql",),
    ),
    CategorySpec(
        key="virt", label="Qemu/KVM/Libvirt", path="/virt-statistics",
        title_statistics="Qemu/KVM/Libvirt",
        set_of_test_types=(
            "FIO", "vPingPong", "vUnixBench", "steal time", "steal time-sm", "FIO large",
        ),
        comparison_list=(("steal time", "steal time-sm"),),
    ),
    CategorySpec(
        key="unixbench", label="UnixBench", path="/base-statistics",
        title_statistics="UnixBench",
        set_of_test_types=("unix", "unix parsec"),
        comparison_list=(("unix", "unix parsec"),),
    ),
    CategorySpec(
        key="system_services", label="Системные службы", path="/base-statistics",
        title_statistics="Системные службы",
        set_of_test_types=(
            "auditd-p", "auditd-f", "auditd-u", "syslog-ng", "AOpenVPNcc",
            "Dovecot-IMAP", "Exim4-SMTP", "astraevents", "astraevents-sm",
        ),
        comparison_list=(("astraevents", "astraevents-sm"),),
    ),
    CategorySpec(
        key="filesystems", label="Файловые системы", path="/filesystems-statistics",
        title_statistics="Файловые системы",
        set_of_test_types=(
            "EXFAT", "EXT2", "EXT4", "EXT4 parsec", "FAT", "NTFS", "XFS",
            "XFS parsec", "OCFS2", "CEPH", "CEPH fio",
        ),
        comparison_list=(("EXT4", "XFS"), ("EXT4", "EXT4 parsec")),
    ),
)

CATEGORIES: dict[str, CategorySpec] = {spec.key: spec for spec in _CATEGORY_SPECS}


def category_choices() -> list[dict[str, str]]:
    """`[{key, label}]` в легаси-порядке — для выпадающего списка/кнопок в UI."""
    return [{"key": spec.key, "label": spec.label} for spec in _CATEGORY_SPECS]


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
    *, base_url: str, username: str, token: str, timeout: float, category: str,
) -> None:
    """Пересчёт одного семейства тестов — легаси-кнопки «Apache», «Parsec» и т.д.

    Тело запроса собирается из `_CATEGORY_SPECS`; необязательные
    `comparison_list`/`comparison_kernel_list` не отправляются вовсе, если у
    семейства их нет (у внешнего сервиса они `Optional[List] = None`), — так же,
    как легаси не клало в payload ключи, которых нет в `statistics_conf`.
    """
    spec = CATEGORIES.get(category)
    if spec is None:
        raise ServiceUnavailableError(
            error_code="STATISTICS_CATEGORY_UNKNOWN",
            message=f"unknown statistics category: {category}",
        )
    payload: dict = {
        "title_statistics": spec.title_statistics,
        "username": username,
        "token": token,
        "set_of_test_types": list(spec.set_of_test_types),
        "latest_stable_versions_bool": True,
    }
    if spec.comparison_list is not None:
        payload["comparison_list"] = [list(pair) for pair in spec.comparison_list]
    if spec.comparison_kernel_list is not None:
        payload["comparison_kernel_list"] = list(spec.comparison_kernel_list)
    await _post(base_url=base_url, path=spec.path, payload=payload, timeout=timeout)
