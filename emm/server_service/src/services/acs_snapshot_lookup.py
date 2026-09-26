"""Выбор снимка ACS для стенда по правилу `{hostname}-{version}`.

Своей таблицы снимков нет: ACS отдаёт плоский список имён, снимок сервера —
имя с префиксом `{hostname}-`, хвост — версия РЦ (легаси `LowServer-1710rc52`
= hostname `LowServer` + версия `1710rc52`).

Каталог хранит версии нормализованными (`normalize_os_version_name`), а
снимки на ACS могут быть названы и так, и так — сравниваем после
нормализации, но в restore уходит фактический хвост найденного снимка.
Несколько кандидатов одной версии — точное совпадение хвоста, иначе первый
по сортировке имён.

Используется `prepare_for_test.start` и обоими эндпоинтами списка снимков
(user-facing и internal), чтобы очередь и restore видели одно и то же.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.core.known_os import normalize_os_version_name
from src.services import acs_client
from src.services import acs_settings as acs_settings_svc

logger = logging.getLogger(__name__)

ERROR_SNAPSHOT_NOT_FOUND = "ACS_SNAPSHOT_NOT_FOUND"


@dataclass(frozen=True)
class HostSnapshot:
    """Снимок ACS одного сервера.

    `version_name` — хвост имени после `{hostname}-` как есть (его и передаём
    ACS на restore), `normalized_version` — тот же хвост после
    `normalize_os_version_name` (по нему сравниваем с каталогом).
    """

    name: str
    version_name: str
    normalized_version: str


def snapshot_name(hostname: str, version_name: str) -> str:
    """Имя снимка в терминах ACS — `{hostname}-{version_name}`."""
    return f"{hostname}-{version_name}"


def host_snapshots(names: Iterable[str], hostname: str) -> list[HostSnapshot]:
    """Снимки этого сервера из общего списка ACS, отсортированные по имени.

    Префикс — `{hostname}-` целиком, с дефисом: снимки хоста с похожим именем
    (`LowServer2-…` для `LowServer`) не подхватываются. Имя, в котором после
    префикса ничего нет, снимком версии не считается.
    """
    prefix = f"{hostname}-"
    items = []
    for name in names:
        if not name.startswith(prefix) or len(name) == len(prefix):
            continue
        tail = name[len(prefix):]
        items.append(HostSnapshot(
            name=name,
            version_name=tail,
            normalized_version=normalize_os_version_name(tail),
        ))
    return sorted(items, key=lambda item: item.name)


def pick_snapshot(
    snapshots: Iterable[HostSnapshot], version_name: str,
) -> HostSnapshot | None:
    """Снимок версии `version_name` (имя из каталога) или `None`.

    Сравнение — после нормализации обеих сторон. Из нескольких кандидатов
    приоритет у точного совпадения хвоста с `version_name`, иначе первый по
    сортировке имён.
    """
    wanted = normalize_os_version_name(version_name)
    candidates = sorted(
        (item for item in snapshots if item.normalized_version == wanted),
        key=lambda item: item.name,
    )
    if not candidates:
        return None
    for item in candidates:
        if item.version_name == version_name:
            return item
    return candidates[0]


async def list_acs_snapshot_names(db: AsyncSession) -> list[str]:
    """Живой список имён снимков ACS (все серверы).

    Ошибки настроек/сети пробрасываются как есть: `ACS_DISABLED`,
    `ACS_TIMEOUT`, `ACS_UNREACHABLE`, `ACS_ERROR` (`ServiceUnavailableError`).
    """
    acs_url, acs_password = await acs_settings_svc.get_acs_credentials(db)
    return await acs_client.list_snapshots(acs_url, acs_password)


async def find_snapshot_for_restore(
    db: AsyncSession, *, hostname: str, version_name: str,
) -> HostSnapshot:
    """Найти в ACS снимок `{hostname}-{version_name}` для restore.

    Снимка нет — `NotFoundError(ACS_SNAPSHOT_NOT_FOUND)` с полным искомым
    именем в сообщении (видно, какой снимок снять). Выбранный снимок пишется в
    лог — при нескольких кандидатах по нему видно, какой ушёл в ACS.
    """
    names = await list_acs_snapshot_names(db)
    candidates = host_snapshots(names, hostname)
    chosen = pick_snapshot(candidates, version_name)
    expected = snapshot_name(hostname, version_name)
    if chosen is None:
        raise NotFoundError(
            error_code=ERROR_SNAPSHOT_NOT_FOUND,
            message=f"ACS snapshot {expected!r} not found",
            details={
                "expected_name": expected,
                "hostname": hostname,
                "version_name": version_name,
                "host_snapshots": [item.name for item in candidates],
            },
        )
    logger.info(
        "acs snapshot chosen for %s: %s (version_name=%r, candidates=%s)",
        expected, chosen.name, chosen.version_name,
        [item.name for item in candidates
         if item.normalized_version == chosen.normalized_version],
    )
    return chosen


__all__ = [
    "ERROR_SNAPSHOT_NOT_FOUND",
    "HostSnapshot",
    "find_snapshot_for_restore",
    "host_snapshots",
    "list_acs_snapshot_names",
    "pick_snapshot",
    "snapshot_name",
]
