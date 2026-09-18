"""Легаси-совместимые read-only маршруты `allta_app` (§12 плана миграции).

Девять маршрутов `allta_app/allta_front.py`, у которых остаются внешние
потребители (телеграм-бот отдела, скрипты, скрипты публикации Confluence),
завязанные на конкретный путь и форму JSON-ответа. `get-times` в этот набор
не входит — исключён более ранним аудитом сознательно.

Все маршруты требуют обычной аутентификации testing_service, в отличие от
легаси (там это были анонимные Flask-роуты без какой-либо проверки) — эмм
не имеет анонимного публичного API нигде, кроме `/health`/`/ready`, заводить
для этой пригоршни справочных ручек исключение не стали. `get-jira-url`/
`get-confluence-url` дополнительно проверяют, что вызывающий — из того же
отдела, что и запрошенные настройки (как и остальной `/department-integration
-settings`).

Логика — в `services/legacy_compat.py`.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Path, Response

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.legacy_compat import AvailableKernelsResponse, LegacyUrlResponse
from src.services import legacy_compat as svc

router = APIRouter(prefix="/legacy-compat")


@router.get(
    "/get-repo-path-as-json",
    response_model=dict[str, list[str]],
    summary="[legacy] sources.list по версиям (JSON)",
    description=(
        "Легаси `GET /rest/api/get-repo-path-as-json`. `{версия: [строки "
        "sources.list]}` для всех OS-версий, у которых репозитории уже "
        "резолвлены в server_service (см. `os_version_repo_resolver`, порт "
        "легаси `ReleaseToRepo`)."
    ),
)
async def get_repo_path_as_json(_: CurrentUserIdentity) -> dict[str, list[str]]:
    return await svc.get_repo_paths()


@router.get(
    "/get-repo-path",
    summary="[legacy] sources.list по версиям (файл)",
    description=(
        "То же содержимое, что `get-repo-path-as-json`, но с "
        "`Content-Disposition: attachment` — легаси отдавал файл "
        "`releases.json` через `send_file`."
    ),
)
async def get_repo_path(_: CurrentUserIdentity) -> Response:
    data = await svc.get_repo_paths()
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=4),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="releases.json"'},
    )


@router.get(
    "/get-astra-config",
    response_model=dict,
    summary="[legacy] astra-config.json",
    description=(
        "Легаси `GET /rest/api/get-astra-config`. Живой прокси в репозиторий "
        "`astra-qa-stand` (порт `get_aqs_json()`), с in-process TTL-кэшем — "
        "не статический снепшот."
    ),
    responses={503: {"description": "ASTRA_QA_STAND_UNAVAILABLE/_INVALID — репозиторий недоступен или вернул не JSON."}},
)
async def get_astra_config(_: CurrentUserIdentity) -> dict:
    return await svc.get_astra_config()


@router.get(
    "/get-box-config",
    response_model=dict,
    summary="[legacy] box-config.json",
    description=(
        "Легаси `GET /rest/api/get-box-config`. Статика без живого продюсера "
        "ни в легаси, ни здесь — карта vagrant-боксов по версиям."
    ),
)
async def get_box_config(_: CurrentUserIdentity) -> dict:
    return svc.get_box_config()


@router.get(
    "/get-jira-url",
    response_model=LegacyUrlResponse,
    summary="[legacy] URL Jira отдела",
    description=(
        "Легаси `GET /rest/api/get-jira-url` отдавал глобальную голую "
        "строку без авторизации. Per-department контракт: `department_id` "
        "обязателен, ответ — JSON, доступен только своему отделу."
    ),
    responses={403: {"description": "DEPARTMENT_ISOLATION — настройки чужого отдела."}},
)
async def get_jira_url(
    identity: CurrentUserIdentity,
    department_id: str,
    db: AsyncSession = Depends(get_db),
) -> LegacyUrlResponse:
    url = await svc.get_jira_url(db, identity, department_id)
    return LegacyUrlResponse(department_id=department_id, url=url)


@router.get(
    "/get-confluence-url",
    response_model=LegacyUrlResponse,
    summary="[legacy] URL Confluence отдела",
    description="Аналог `get-jira-url` для `confluence_base_url`.",
    responses={403: {"description": "DEPARTMENT_ISOLATION — настройки чужого отдела."}},
)
async def get_confluence_url(
    identity: CurrentUserIdentity,
    department_id: str,
    db: AsyncSession = Depends(get_db),
) -> LegacyUrlResponse:
    url = await svc.get_confluence_url(db, identity, department_id)
    return LegacyUrlResponse(department_id=department_id, url=url)


@router.get(
    "/get-testname-columns",
    response_model=dict[str, str],
    summary="[legacy] короткие колонки СТП по имени теста",
    description="Легаси `GET /rest/api/get-testname-columns`. Статичный словарь без изменений.",
)
async def get_testname_columns(_: CurrentUserIdentity) -> dict[str, str]:
    return svc.get_testname_columns()


@router.get(
    "/get-stand",
    summary="[legacy] описание парка стендов (HTML)",
    description=(
        "Легаси `GET /rest/api/get-stand`. Замороженная HTML-легенда "
        "14 легаси-машин — сознательно не генерируется из `test_stand` "
        "(решение волны D2/D3 плана миграции: состав никогда не был "
        "синхронизирован с реальным парком)."
    ),
)
async def get_stand(_: CurrentUserIdentity) -> Response:
    return Response(content=svc.get_stand_description_html(), media_type="text/html; charset=utf-8")


@router.get(
    "/known-bugs",
    response_model=dict[str, dict[str, str]],
    summary="[legacy] известные баги по категории теста",
    description="Легаси `GET /rest/api/known-bugs`. Статичный словарь без изменений.",
)
async def get_known_bugs(_: CurrentUserIdentity) -> dict[str, dict[str, str]]:
    return svc.get_known_bugs()


@router.get(
    "/annotations",
    response_model=dict[str, str],
    summary="[legacy] аннотации к артефактам по категории",
    description="Легаси `GET /rest/api/annotations`. Статичный словарь без изменений.",
)
async def get_annotations(_: CurrentUserIdentity) -> dict[str, str]:
    return svc.get_annotations()


@router.post(
    "/available-kernels-from-{rc}",
    response_model=AvailableKernelsResponse,
    summary="[legacy] ядра, доступные для версии",
    description=(
        "Легаси `POST /rest/api/available-kernels-from-<rc>` (в основе — "
        "`liballta.get_kernels_from_rc`). `rc` — версия сборки (`\"1.8.5.46\"`), "
        "не внутренний `os_version_id`. Список ядер резолвится через "
        "server_service тем же путём, что и конструктор запуска теста."
    ),
    responses={404: {"description": "LEGACY_RC_NOT_FOUND — версия не найдена в каталоге."}},
)
async def available_kernels_from_rc(
    _: CurrentUserIdentity,
    rc: str = Path(description="Версия сборки, например `1.8.5.46`."),
) -> AvailableKernelsResponse:
    kernels = await svc.available_kernels_from_rc(rc)
    return AvailableKernelsResponse(rc=rc, kernels=kernels)
