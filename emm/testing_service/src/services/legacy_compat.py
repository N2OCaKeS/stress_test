"""Сервисная логика легаси-совместимых read-only маршрутов (§12 плана миграции).

Девять маршрутов `allta_app/allta_front.py`, у которых внешние потребители
(телеграм-бот, `libconfluence.py`, скрипты отдела) продолжают ожидать
конкретный путь и форму ответа. `get-times` в этот список сознательно не
входит — исключён отдельным решением ещё до этой волны.

Два маршрута данных — `get-repo-path*` и `get-astra-config` — в легаси были
не статикой, а результатом живых продюсеров (`ReleaseToRepo`, `get_aqs_json()`
из `libs/liballta.py`). `ReleaseToRepo` уже перенесён раньше, в
`server_service.os_version_repo_resolver` (§H1 плана) — здесь он только
читается через `server_client`. `get_aqs_json()` портирован в этом модуле
как `astra_qa_stand_client` — тот же git-репозиторий, тот же формат ответа,
просто запрос идёт по требованию, а не на каждый деплой allta_app.

Остальные шесть маршрутов (`get-box-config`, `get-testname-columns`,
`get-stand`, `known-bugs`, `annotations` и статичная часть `get-astra-config`
не касается — она вся живая) в легаси не имели продюсера вообще — это были
модульные константы. Тут они так и остались константами
(`legacy_static_data.py`), заводить под них таблицы БД ради данных, которые
никто не редактирует через API, смысла нет.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.dependencies.auth import Identity
from src.services import astra_qa_stand_client, department_integration_settings, legacy_static_data, server_client


async def get_repo_paths() -> dict[str, list[str]]:
    """`{версия: [строки sources.list]}` для всех версий, у которых репозитории уже резолвлены.

    Источник — `os_versions.repositories` в server_service (см.
    `os_version_service.resolve_repositories`, порт легаси `ReleaseToRepo`).
    Версии, для которых репозитории ещё ни разу не резолвили explicit-вызовом
    `resolve-repositories`, в выдачу не попадают — маршрут не триггерит запись
    в чужую БД как побочный эффект read-запроса.
    """
    versions = await server_client.list_os_versions()
    result = {
        str(version["name"]): list(version.get("repositories") or [])
        for version in versions
        if version.get("name") and version.get("repositories")
    }
    return dict(sorted(result.items()))


async def get_astra_config() -> dict:
    """Живой `astra-config.json` из git (см. `astra_qa_stand_client`)."""
    return await astra_qa_stand_client.get_astra_config()


def get_box_config() -> dict:
    """Легаси `box-config.json` — статика, продюсера в легаси не было."""
    return legacy_static_data.BOX_CONFIG


def get_testname_columns() -> dict[str, str]:
    return legacy_static_data.TESTNAME_COLUMNS


def get_known_bugs() -> dict[str, dict[str, str]]:
    return legacy_static_data.KNOWN_BUGS


def get_annotations() -> dict[str, str]:
    return legacy_static_data.ANNOTATIONS


def get_stand_description_html() -> str:
    return legacy_static_data.STAND_DESCRIPTION_HTML


async def get_jira_url(db: AsyncSession, identity: Identity, department_id: str) -> str | None:
    """`jira_base_url` отдела — легаси-эквивалент был глобальной строкой без отдела."""
    settings = await department_integration_settings.get_effective_for(db, identity, department_id)
    return settings.get("jira_base_url")


async def get_confluence_url(db: AsyncSession, identity: Identity, department_id: str) -> str | None:
    settings = await department_integration_settings.get_effective_for(db, identity, department_id)
    return settings.get("confluence_base_url")


async def available_kernels_from_rc(rc: str) -> list[str]:
    """Ядра, доступные для версии `rc` (легаси `available-kernels-from-<rc>`).

    Легаси адресовал версию именем сборки (`"1.8.5.46"`), не внутренним
    `os_version_id` каталога — сначала резолвим id по имени, потом переиспользуем
    тот же путь, что и обычный конструктор теста (`server_client.resolve_os_kernels`).
    """
    version = await server_client.find_os_version_by_name(rc)
    if version is None:
        raise NotFoundError(
            error_code="LEGACY_RC_NOT_FOUND",
            message=f"OS version '{rc}' is not present in the catalog",
            details={"rc": rc},
        )
    return await server_client.resolve_os_kernels(str(version["id"]))
